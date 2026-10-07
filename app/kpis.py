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


def _hoy_lima() -> datetime.date:
    """datetime.date.today() usa la hora del SERVIDOR (UTC en producción),
    no la de Lima (UTC-5) — mismo bug ya corregido en Asistencia y en el
    saludo de cumpleaños (15-16/09): entre las 19:00 y medianoche hora
    Lima, el servidor ya está en el día siguiente, corriendo un día hacia
    adelante estos indicadores (ausentismo, edad promedio, altas por mes)."""
    return (datetime.datetime.utcnow() - datetime.timedelta(hours=5)).date()


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


def _emps(db: Session, empresa_ids=None):
    """Consulta base de trabajadores. `empresa_ids` (set) la acota a esas
    empresas: así el gerente de una empresa solo ve los indicadores de la
    suya; None = todo el holding."""
    q = db.query(Employee)
    if empresa_ids is not None:
        q = q.filter(Employee.empresa_id.in_(list(empresa_ids) or [-1]))
    return q


def headcount_activo(db: Session, empresa_ids=None) -> int:
    return _emps(db, empresa_ids).filter(Employee.estado == "activo").count()


def headcount_por_empresa(db: Session, empresa_ids=None):
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
    if empresa_ids is not None:
        empresas = [e for e in empresas if e.id in empresa_ids]
    return [(e.nombre, db.query(Employee).filter(Employee.empresa_id == e.id, Employee.estado == "activo").count())
            for e in empresas]


def headcount_por_unidad(db: Session, empresa_ids=None):
    unidades = db.query(UnidadNegocio).filter(UnidadNegocio.activo == True).order_by(UnidadNegocio.nombre).all()  # noqa: E712
    resultado = []
    for u in unidades:
        ids = [e.id for e in u.empresas if empresa_ids is None or e.id in empresa_ids]
        if empresa_ids is not None and not ids:
            continue  # unidad sin ninguna empresa de su alcance: ni siquiera se nombra
        count = db.query(Employee).filter(Employee.empresa_id.in_(ids), Employee.estado == "activo").count() if ids else 0
        resultado.append((u.nombre, count))
    return resultado


def altas_periodo(db: Session, dias: int, empresa_ids=None) -> int:
    """Incorporaciones: trabajadores creados en la BD maestra dentro del periodo."""
    desde = datetime.datetime.utcnow() - datetime.timedelta(days=dias)
    return _emps(db, empresa_ids).filter(Employee.created_at >= desde).count()


def bajas_periodo(db: Session, dias: int, empresa_ids=None) -> int:
    desde = datetime.datetime.utcnow() - datetime.timedelta(days=dias)
    return _emps(db, empresa_ids).filter(Employee.fecha_baja.isnot(None), Employee.fecha_baja >= desde).count()


def rotacion_pct(db: Session, dias: int, empresa_ids=None) -> float:
    """Rotación simplificada = bajas del periodo / headcount activo actual × 100.
    (Aproximación de prototipo; la fórmula clásica usa el promedio de activos
    al inicio y al fin del periodo — se puede afinar cuando haya más historia.)"""
    activos = headcount_activo(db, empresa_ids)
    bajas = bajas_periodo(db, dias, empresa_ids)
    if activos == 0:
        return 0.0
    return round(bajas / activos * 100, 1)


def ausentismo_pct(db: Session, dias: int, empresa_ids=None):
    """% de días-trabajador hábiles del periodo en que un trabajador activo
    NO marcó su entrada. Devuelve (pct, dias_esperados, dias_sin_marcar)."""
    hoy = _hoy_lima()
    desde = hoy - datetime.timedelta(days=dias)
    dias_habiles = [desde + datetime.timedelta(days=i) for i in range((hoy - desde).days + 1) if _dia_habil(desde + datetime.timedelta(days=i))]
    if not dias_habiles:
        return 0.0, 0, 0

    activos = _emps(db, empresa_ids).filter(Employee.estado == "activo").all()
    if not activos:
        return 0.0, 0, 0

    desde_dt = datetime.datetime.combine(desde, datetime.time.min)
    ids_activos = [e.id for e in activos]
    registros = db.query(AsistenciaRegistro).filter(
        AsistenciaRegistro.tipo == "entrada", AsistenciaRegistro.timestamp >= desde_dt,
        AsistenciaRegistro.employee_id.in_(ids_activos),
    ).all()
    marcados = {(r.employee_id, r.timestamp.date()) for r in registros}

    esperados = len(activos) * len(dias_habiles)
    sin_marcar = sum(1 for e in activos for d in dias_habiles if (e.id, d) not in marcados)
    pct = round(sin_marcar / esperados * 100, 1) if esperados else 0.0
    return pct, esperados, sin_marcar


def pct_activos(db: Session, empresa_ids=None) -> float:
    """% de activos sobre el total de trabajadores que ha pasado alguna vez
    por la planilla (activos + cesados), como en el reporte de referencia."""
    total = _emps(db, empresa_ids).count()
    if total == 0:
        return 0.0
    return round(headcount_activo(db, empresa_ids) / total * 100, 1)


def planilla_activa_soles(db: Session, empresa_ids=None) -> float:
    """Suma de la remuneración (ficha_data.remuneracion) de los trabajadores activos."""
    activos = _emps(db, empresa_ids).filter(Employee.estado == "activo").all()
    return round(sum(_parse_monto((e.ficha_data or {}).get("remuneracion")) for e in activos), 2)


def edad_promedio(db: Session, empresa_ids=None):
    """Edad promedio de los trabajadores activos con fecha de nacimiento registrada."""
    activos = _emps(db, empresa_ids).filter(Employee.estado == "activo").all()
    hoy = _hoy_lima()
    edades = []
    for e in activos:
        nac = _parse_fecha((e.ficha_data or {}).get("fecha_nacimiento"))
        if nac:
            edades.append((hoy - nac).days / 365.25)
    if not edades:
        return None
    return round(sum(edades) / len(edades), 1)


def _conteo_por_campo(db: Session, campo: str, solo_activos: bool = True, top: int = None, empresa_ids=None):
    """Cuenta trabajadores agrupados por un campo de ficha_data (p.ej. 'area',
    'sexo', 'afp'), ordenado de mayor a menor. Ignora vacíos."""
    query = _emps(db, empresa_ids)
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


def por_area(db: Session, empresa_ids=None):
    return _conteo_por_campo(db, "area", empresa_ids=empresa_ids)


def por_sexo(db: Session, empresa_ids=None):
    return _conteo_por_campo(db, "sexo", empresa_ids=empresa_ids)


def por_nacionalidad(db: Session, empresa_ids=None):
    total_con_dato = 0
    conteo = _conteo_por_campo(db, "nacionalidad", empresa_ids=empresa_ids)
    total_con_dato = sum(c for _, c in conteo)
    if not total_con_dato:
        return []
    return [(pais, c, round(c / total_con_dato * 100, 1)) for pais, c in conteo]


def por_sistema_pension(db: Session, empresa_ids=None):
    """(AFP, ONP, Sin dato) entre los trabajadores activos."""
    activos = _emps(db, empresa_ids).filter(Employee.estado == "activo").all()
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


def por_afp(db: Session, empresa_ids=None):
    """Personas por administradora de AFP (solo entre quienes tienen sistema AFP)."""
    activos = _emps(db, empresa_ids).filter(Employee.estado == "activo").all()
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


def incorporaciones_por_mes(db: Session, meses: int = 24, empresa_ids=None):
    """Incorporaciones (altas) por mes calendario, últimos N meses, según
    ficha_data.fecha_ingreso. Devuelve lista de (etiqueta 'ene 2025', cantidad)
    en orden cronológico, incluyendo meses en cero."""
    hoy = _hoy_lima()
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

    for e in _emps(db, empresa_ids).all():
        ingreso = _parse_fecha((e.ficha_data or {}).get("fecha_ingreso"))
        if ingreso and (ingreso.year, ingreso.month) in conteo:
            conteo[(ingreso.year, ingreso.month)] += 1

    return [(f"{MESES_ES[m - 1]} {y}", conteo[(y, m)]) for y, m in periodos]


def resumen_dashboard(db: Session, dias: int = 30, empresa_ids=None):
    """`empresa_ids` (set) acota todos los indicadores a esas empresas (alcance
    del gerente); None = todo el holding."""
    aus_pct, aus_esp, aus_sin = ausentismo_pct(db, dias, empresa_ids)
    return {
        "dias": dias,
        "headcount": headcount_activo(db, empresa_ids),
        "headcount_empresa": headcount_por_empresa(db, empresa_ids),
        "headcount_unidad": headcount_por_unidad(db, empresa_ids),
        "altas": altas_periodo(db, dias, empresa_ids),
        "bajas": bajas_periodo(db, dias, empresa_ids),
        "rotacion_pct": rotacion_pct(db, dias, empresa_ids),
        "ausentismo_pct": aus_pct,
        "ausentismo_esperados": aus_esp,
        "ausentismo_sin_marcar": aus_sin,
        "pct_activos": pct_activos(db, empresa_ids),
        "planilla_activa": planilla_activa_soles(db, empresa_ids),
        "edad_promedio": edad_promedio(db, empresa_ids),
        "por_area": por_area(db, empresa_ids),
        "por_sexo": por_sexo(db, empresa_ids),
        "por_nacionalidad": por_nacionalidad(db, empresa_ids),
        "por_sistema_pension": por_sistema_pension(db, empresa_ids),
        "por_afp": por_afp(db, empresa_ids),
        "incorporaciones_por_mes": incorporaciones_por_mes(db, 24, empresa_ids),
    }
