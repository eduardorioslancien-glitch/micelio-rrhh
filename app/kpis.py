# -*- coding: utf-8 -*-
"""Cálculo de indicadores (KPIs) de RR.HH. para el dashboard: ausentismo,
rotación, incorporación (altas) y headcount por empresa/unidad de negocio.
Punto 7 del pedido del usuario.

Son fórmulas estándar simplificadas para un prototipo (documentadas en cada
función); cuando haya más historia de datos real, se pueden afinar."""
import datetime

from sqlalchemy.orm import Session

from .models import Employee, Empresa, UnidadNegocio, AsistenciaRegistro


def _dia_habil(d: datetime.date) -> bool:
    return d.weekday() < 5  # lunes(0)..viernes(4)


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
    }
