# -*- coding: utf-8 -*-
"""Cálculo de indicadores (KPIs) de RR.HH. para el dashboard: ausentismo,
rotación, incorporación (altas) y headcount por empresa/unidad de negocio.
Punto 7 del pedido del usuario.

Son fórmulas estándar simplificadas para un prototipo (documentadas en cada
función); cuando haya más historia de datos real, se pueden afinar."""
import datetime

from sqlalchemy.orm import Session

from .models import Employee, Empresa, UnidadNegocio, AsistenciaRegistro


MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _dia_habil(d: datetime.date) -> bool:
    return d.weekday() < 5  # lunes(0)..viernes(4)


def _parse_fecha(valor: str):
    try:
        return datetime.datetime.strptime(valor or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_monto(valor) -> float:
    """Convierte valores tipo "S/ 1,200.50" (guardados como texto libre en la
    ficha) a float; 0.0 si no se puede interpretar."""
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    limpio = str(valor).replace("S/", "").replace(",", "").strip()
    try:
        return float(limpio)
    except ValueError:
        return 0.0


def headcount_activo(db: Session) -> int:
    return db.query(Employee).filter(Employee.estado == "activo").count()


def headcount_por_empresa(db: Session):
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
    return [(e.nombre, db.query(Employee).filter(Employee.empresa_id == e.id, Employee.estado == "activo").count())
            for e in empresas]


def headcount_por_unidad(db: Session):
    unidades = db.query(UnidadNegocio).filter(UnidadNegocio.activo == True).order_by(UnidadNegocio.nombre).all()  # noqa: E712
    resultado = []
    for u in unidades:
        empresa_ids = [e.id for e in u.empresas]
        count = db.query(Employee).filter(Employee.empresa_id.in_(empresa_ids), Employee.estado == "activo").count() if empresa_ids else 0
        resultado.append((u.nombre, count))
    return resultado


def altas_periodo(db: Session, dias: int) -> int:
    """Incorporaciones: trabajadores creados en la BD maestra dentro del periodo."""
    desde = datetime.datetime.utcnow() - datetime.timedelta(days=dias)
    return db.query(Employee).filter(Employee.created_at >= desde).count()


def bajas_periodo(db: Session, dias: int) -> int:
    desde = datetime.datetime.utcnow() - datetime.timedelta(days=dias)
    return db.query(Employee).filter(Employee.fecha_baja.isnot(None), Employee.fecha_baja >= desde).count()


def rotacion_pct(db: Session, dias: int) -> float:
    """Rotación simplificada = bajas del periodo / headcount activo actual × 100.
    (Aproximación de prototipo; la fórmula clásica usa el promedio de activos
    al inicio y al fin del periodo — se puede afinar cuando haya más historia.)"""
    activos = headcount_activo(db)
    bajas = bajas_periodo(db, dias)
    if activos == 0:
        return 0.0
    return round(bajas / activos * 100, 1)


def ausentismo_pct(db: Session, dias: int):
    """% de días-trabajador hábiles del periodo en que un trabajador activo
    NO marcó su entrada. Devuelve (pct, dias_esperados, dias_sin_marcar)."""
    hoy = datetime.date.today()
    desde = hoy - datetime.timedelta(days=dias)
    dias_habiles = [desde + datetime.timedelta(days=i) for i in range((hoy - desde).days + 1) if _dia_habil(desde + datetime.timedelta(days=i))]
    if not dias_habiles:
        return 0.0, 0, 0

    activos = db.query(Employee).filter(Employee.estado == "activo").all()
    if not activos:
        return 0.0, 0, 0

    desde_dt = datetime.datetime.combine(desde, datetime.time.min)
    registros = db.query(AsistenciaRegistro).filter(
        AsistenciaRegistro.tipo == "entrada", AsistenciaRegistro.timestamp >= desde_dt,
    ).all()
    marcados = {(r.employee_id, r.timestamp.date()) for r in registros}

    esperados = len(activos) * len(dias_habiles)
    sin_marcar = sum(1 for e in activos for d in dias_habiles if (e.id, d) not in marcados)
    pct = round(sin_marcar / esperados * 100, 1) if esperados else 0.0
    return pct, esperados, sin_marcar


def pct_activos(db: Session) -> float:
    """% de activos sobre el total de trabajadores que ha pasado alguna vez
    por la planilla (activos + cesados), como en el reporte de referencia."""
    total = db.query(Employee).count()
    if total == 0:
        return 0.0
    return round(headcount_activo(db) / total * 100, 1)


def planilla_activa_soles(db: Session) -> float:
    """Suma de la remuneración (ficha_data.remuneracion) de los trabajadores activos."""
    activos = db.query(Employee).filter(Employee.estado == "activo").all()
    return round(sum(_parse_monto((e.ficha_data or {}).get("remuneracion")) for e in activos), 2)


def edad_promedio(db: Session):
    """Edad promedio de los trabajadores activos con fecha de nacimiento registrada."""
    activos = db.query(Employee).filter(Employee.estado == "activo").all()
    hoy = datetime.date.today()
    edades = []
    for e in activos:
        nac = _parse_fecha((e.ficha_data or {}).get("fecha_nacimiento"))
        if nac:
            edades.append((hoy - nac).days / 365.25)
    if not edades:
        return None
    return round(sum(edades) / len(edades), 1)


def _conteo_por_campo(db: Session, campo: str, solo_activos: bool = True, top: int = None):
    """Cuenta trabajadores agrupados por un campo de ficha_data (p.ej. 'area',
    'sexo', 'afp'), ordenado de mayor a menor. Ignora vacíos."""
    query = db.query(Employee)
    if solo_activos:
        query = query.filter(Employee.estado == "activo")
    conteo = {}
    for e in query.all():
        valor = (e.ficha_data or {}).get(campo)
        if not valor:
            continue
        conteo[valor] = conteo.get(valor, 0) + 1
    resultado = sorted(conteo.items(), key=lambda kv: kv[1], reverse=True)
    return resultado[:top] if top else resultado


def por_area(db: Session):
    return _conteo_por_campo(db, "area")


def por_sexo(db: Session):
    return _conteo_por_campo(db, "sexo")


def por_nacionalidad(db: Session):
    total_con_dato = 0
    conteo = _conteo_por_campo(db, "nacionalidad")
    total_con_dato = sum(c for _, c in conteo)
    if not total_con_dato:
        return []
    return [(pais, c, round(c / total_con_dato * 100, 1)) for pais, c in conteo]


def por_sistema_pension(db: Session):
    """(AFP, ONP, Sin dato) entre los trabajadores activos."""
    activos = db.query(Employee).filter(Employee.estado == "activo").all()
    afp = onp = sin_dato = 0
    for e in activos:
        sistema = (e.ficha_data or {}).get("sistema_pension")
        if sistema == "AFP":
            afp += 1
        elif sistema == "ONP":
            onp += 1
        else:
            sin_dato += 1
    return [("AFP", afp), ("ONP", onp), ("Sin dato", sin_dato)]


def por_afp(db: Session):
    """Personas por administradora de AFP (solo entre quienes tienen sistema AFP)."""
    activos = db.query(Employee).filter(Employee.estado == "activo").all()
    conteo = {}
    for e in activos:
        f = e.ficha_data or {}
        if f.get("sistema_pension") != "AFP":
            continue
        nombre = f.get("afp")
        if not nombre or nombre == "No aplica":
            continue
        conteo[nombre] = conteo.get(nombre, 0) + 1
    return sorted(conteo.items(), key=lambda kv: kv[1], reverse=True)


def incorporaciones_por_mes(db: Session, meses: int = 24):
    """Incorporaciones (altas) por mes calendario, últimos N meses, según
    ficha_data.fecha_ingreso. Devuelve lista de (etiqueta 'ene 2025', cantidad)
    en orden cronológico, incluyendo meses en cero."""
    hoy = datetime.date.today()
    periodos = []
    y, m = hoy.year, hoy.month
    for _ in range(meses):
        periodos.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    periodos.reverse()
    conteo = {p: 0 for p in periodos}

    for e in db.query(Employee).all():
        ingreso = _parse_fecha((e.ficha_data or {}).get("fecha_ingreso"))
        if ingreso and (ingreso.year, ingreso.month) in conteo:
            conteo[(ingreso.year, ingreso.month)] += 1

    return [(f"{MESES_ES[m - 1]} {y}", conteo[(y, m)]) for y, m in periodos]


def resumen_dashboard(db: Session, dias: int = 30):
    aus_pct, aus_esp, aus_sin = ausentismo_pct(db, dias)
    return {
        "dias": dias,
        "headcount": headcount_activo(db),
        "headcount_empresa": headcount_por_empresa(db),
        "headcount_unidad": headcount_por_unidad(db),
        "altas": altas_periodo(db, dias),
        "bajas": bajas_periodo(db, dias),
        "rotacion_pct": rotacion_pct(db, dias),
        "ausentismo_pct": aus_pct,
        "ausentismo_esperados": aus_esp,
        "ausentismo_sin_marcar": aus_sin,
        "pct_activos": pct_activos(db),
        "planilla_activa": planilla_activa_soles(db),
        "edad_promedio": edad_promedio(db),
        "por_area": por_area(db),
        "por_sexo": por_sexo(db),
        "por_nacionalidad": por_nacionalidad(db),
        "por_sistema_pension": por_sistema_pension(db),
        "por_afp": por_afp(db),
        "incorporaciones_por_mes": incorporaciones_por_mes(db, 24),
    }
