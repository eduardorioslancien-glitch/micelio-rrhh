# -*- coding: utf-8 -*-
"""Clima y Cultura (Fase 3): Encuesta 360 e Indicadores de Gestión.

La Encuesta 360 es deliberadamente genérica: RR.HH. define las preguntas al
crear cada campaña (escala 1-5), en vez de asumir un set fijo de
competencias — eso vive en "Principios, Valores y Competencias" de
Parámetros, todavía pendiente. Las respuestas las carga RR.HH. (mismo patrón
que Bitácora/Asistencia manual); un portal público para que cada evaluador
responda por su cuenta queda como posible siguiente paso.
"""
import datetime
import os

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from . import kpis as kpis_module
from .models import (
    EncuestaCampana, EncuestaRespuesta, Employee, User,
    Anuncio, Holding, UnidadNegocio, Empresa,
    ESTADOS_ENCUESTA, RELACIONES_ENCUESTA, AMBITOS_ANUNCIO, AMBITO_ANUNCIO_KEYS,
)
from .auth import require_role
from .rrhh import _ctx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
router = APIRouter()

ESTADO_ENCUESTA_LABELS = dict(ESTADOS_ENCUESTA)


def _promedio_por_pregunta(campana: EncuestaCampana):
    """Lista de promedios (uno por pregunta) sobre todas las respuestas de la
    campaña; None en las preguntas sin ningún puntaje todavía."""
    n = len(campana.preguntas or [])
    sumas = [0.0] * n
    conteos = [0] * n
    for r in campana.respuestas:
        for i, score in enumerate(r.respuestas or []):
            if i < n and isinstance(score, (int, float)):
                sumas[i] += score
                conteos[i] += 1
    return [round(sumas[i] / conteos[i], 2) if conteos[i] else None for i in range(n)]


@router.get("/rrhh/clima/encuestas", response_class=HTMLResponse)
def encuestas_list(request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    campanas = db.query(EncuestaCampana).order_by(EncuestaCampana.created_at.desc()).all()
    return templates.TemplateResponse(request, "rrhh_encuestas.html", _ctx(
        request, user, campanas=campanas, estado_labels=ESTADO_ENCUESTA_LABELS, active="encuestas",
    ))


@router.post("/rrhh/clima/encuestas/nueva")
def encuestas_crear(nombre: str = Form(...), descripcion: str = Form(""), preguntas: str = Form(...),
                     db: Session = Depends(get_db),
                     user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    lista_preguntas = [p.strip() for p in preguntas.splitlines() if p.strip()]
    if not lista_preguntas:
        raise HTTPException(400, "Agrega al menos una pregunta.")
    db.add(EncuestaCampana(
        nombre=nombre.strip(), descripcion=descripcion.strip() or None, preguntas=lista_preguntas,
        creado_por=user.nombre_completo,
    ))
    db.commit()
    return RedirectResponse("/rrhh/clima/encuestas", status_code=303)


@router.post("/rrhh/clima/encuestas/{campana_id}/estado")
def encuestas_cambiar_estado(campana_id: int, estado: str = Form(...), db: Session = Depends(get_db),
                              user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    if estado not in dict(ESTADOS_ENCUESTA):
        raise HTTPException(400, "Estado inválido.")
    campana = db.query(EncuestaCampana).get(campana_id)
    if not campana:
        raise HTTPException(404)
    campana.estado = estado
    campana.fecha_fin = datetime.datetime.utcnow() if estado == "cerrada" else None
    db.commit()
    return RedirectResponse("/rrhh/clima/encuestas", status_code=303)


@router.get("/rrhh/clima/encuestas/{campana_id}", response_class=HTMLResponse)
def encuesta_detalle(request: Request, campana_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    campana = db.query(EncuestaCampana).get(campana_id)
    if not campana:
        raise HTTPException(404)
    empleados = db.query(Employee).filter(Employee.estado == "activo").order_by(Employee.nombre_completo).all()
    promedios = _promedio_por_pregunta(campana)
    promedio_general = round(sum(p for p in promedios if p is not None) / len([p for p in promedios if p is not None]), 2) \
        if any(p is not None for p in promedios) else None
    return templates.TemplateResponse(request, "rrhh_encuesta_detalle.html", _ctx(
        request, user, campana=campana, empleados=empleados, relaciones=RELACIONES_ENCUESTA,
        promedios=promedios, promedio_general=promedio_general, active="encuestas",
    ))


@router.post("/rrhh/clima/encuestas/{campana_id}/respuesta")
async def encuesta_agregar_respuesta(campana_id: int, request: Request, db: Session = Depends(get_db),
                                      user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    campana = db.query(EncuestaCampana).get(campana_id)
    if not campana:
        raise HTTPException(404)
    form = await request.form()
    evaluado_id = form.get("evaluado_id")
    if not evaluado_id:
        raise HTTPException(400, "Falta seleccionar a quién se evalúa.")
    scores = []
    for i in range(len(campana.preguntas or [])):
        raw = form.get(f"pregunta_{i}")
        try:
            scores.append(int(raw))
        except (TypeError, ValueError):
            scores.append(None)
    db.add(EncuestaRespuesta(
        campana_id=campana_id, evaluado_id=int(evaluado_id), relacion=form.get("relacion") or None,
        respuestas=scores, comentario=(form.get("comentario") or "").strip() or None,
        registrado_por=user.nombre_completo,
    ))
    db.commit()
    return RedirectResponse(f"/rrhh/clima/encuestas/{campana_id}", status_code=303)


# ---------------------------------------------------------------------------
# Anuncios (punto 4 del pedido): por Holding, por Unidad de Negocio o por
# Empresa — determina a quién se le muestra en la pantalla de inicio (news).
# ---------------------------------------------------------------------------
@router.get("/rrhh/clima/anuncios", response_class=HTMLResponse)
def anuncios_list(request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    anuncios = db.query(Anuncio).order_by(Anuncio.created_at.desc()).all()
    holdings = db.query(Holding).filter(Holding.activo == True).order_by(Holding.nombre).all()  # noqa: E712
    unidades = db.query(UnidadNegocio).filter(UnidadNegocio.activo == True).order_by(UnidadNegocio.nombre).all()  # noqa: E712
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
    return templates.TemplateResponse(request, "rrhh_anuncios.html", _ctx(
        request, user, anuncios=anuncios, ambitos=AMBITOS_ANUNCIO,
        holdings=holdings, unidades=unidades, empresas=empresas, active="anuncios",
    ))


@router.post("/rrhh/clima/anuncios/nuevo")
def anuncios_crear(titulo: str = Form(...), cuerpo: str = Form(...), ambito: str = Form(...),
                    holding_id: str = Form(""), unidad_negocio_id: str = Form(""), empresa_id: str = Form(""),
                    db: Session = Depends(get_db),
                    user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    if ambito not in AMBITO_ANUNCIO_KEYS:
        raise HTTPException(400, "Ámbito inválido.")
    if ambito == "unidad" and not unidad_negocio_id:
        raise HTTPException(400, "Elige la unidad de negocio.")
    if ambito == "empresa" and not empresa_id:
        raise HTTPException(400, "Elige la empresa.")
    db.add(Anuncio(
        titulo=titulo.strip(), cuerpo=cuerpo.strip(), ambito=ambito,
        holding_id=int(holding_id) if (ambito == "holding" and holding_id) else None,
        unidad_negocio_id=int(unidad_negocio_id) if ambito == "unidad" else None,
        empresa_id=int(empresa_id) if ambito == "empresa" else None,
        autor=user.nombre_completo,
    ))
    db.commit()
    return RedirectResponse("/rrhh/clima/anuncios", status_code=303)


@router.post("/rrhh/clima/anuncios/{anuncio_id}/toggle")
def anuncios_toggle(anuncio_id: int, db: Session = Depends(get_db),
                     user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    a = db.query(Anuncio).get(anuncio_id)
    if a:
        a.activo = not a.activo
        db.commit()
    return RedirectResponse("/rrhh/clima/anuncios", status_code=303)


@router.post("/rrhh/clima/anuncios/{anuncio_id}/eliminar")
def anuncios_eliminar(anuncio_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    a = db.query(Anuncio).get(anuncio_id)
    if a:
        db.delete(a)
        db.commit()
    return RedirectResponse("/rrhh/clima/anuncios", status_code=303)


# ---------------------------------------------------------------------------
# Indicadores de Gestión
# ---------------------------------------------------------------------------
@router.get("/rrhh/clima/indicadores", response_class=HTMLResponse)
def indicadores(request: Request, dias: int = 30, db: Session = Depends(get_db),
                 user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    data = kpis_module.resumen_dashboard(db, dias=dias)

    total_activos = data["headcount"]
    campanas_resumen = []
    for c in db.query(EncuestaCampana).order_by(EncuestaCampana.created_at.desc()).limit(5).all():
        evaluados_unicos = {r.evaluado_id for r in c.respuestas}
        proms = _promedio_por_pregunta(c)
        proms_validos = [p for p in proms if p is not None]
        promedio_general = round(sum(proms_validos) / len(proms_validos), 2) if proms_validos else None
        participacion_pct = round(len(evaluados_unicos) / total_activos * 100, 1) if total_activos else 0.0
        campanas_resumen.append({
            "c": c, "respuestas": len(c.respuestas), "evaluados_unicos": len(evaluados_unicos),
            "participacion_pct": participacion_pct, "promedio_general": promedio_general,
        })

    return templates.TemplateResponse(request, "rrhh_indicadores.html", _ctx(
        request, user, data=data, campanas_resumen=campanas_resumen, active="indicadores",
    ))
