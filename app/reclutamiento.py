# -*- coding: utf-8 -*-
"""Reclutamiento y Selección — Registro de Pedidos de Personal (Fase 3).

Primer módulo nuevo del árbol de navegación pedido por el usuario: RR.HH.
(o el área que necesita cubrir una posición) registra un pedido de personal,
le hace seguimiento de estado (abierto -> en proceso -> cubierto/cancelado),
y más adelante (Control de Leads) se le podrán asociar candidatos.
"""
import datetime
import os

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    PedidoPersonal, LeadCandidato, Empresa, Employee, User, Cargo, Catalogo,
    ESTADOS_PEDIDO, ESTADO_PEDIDO_KEYS, MOTIVOS_PEDIDO, URGENCIAS_PEDIDO,
    ETAPAS_LEAD, ETAPA_LEAD_KEYS, ORIGENES_LEAD, ETAPAS_ONBOARDING,
)
from .auth import require_role
from .rrhh import _ctx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
router = APIRouter()

ESTADO_LABELS = dict(ESTADOS_PEDIDO)
ETAPA_LABELS = dict(ETAPAS_LEAD)


def _parse_fecha(s: str):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


@router.get("/rrhh/reclutamiento/pedidos", response_class=HTMLResponse)
def pedidos_list(request: Request, estado: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    query = db.query(PedidoPersonal)
    if estado:
        query = query.filter(PedidoPersonal.estado == estado)
    pedidos = query.order_by(PedidoPersonal.created_at.desc()).all()
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
    cargos = db.query(Cargo).filter(Cargo.activo == True).order_by(Cargo.nombre).all()  # noqa: E712
    areas = db.query(Catalogo).filter(Catalogo.tipo == "area", Catalogo.activo == True).order_by(Catalogo.nombre).all()  # noqa: E712
    empleados = db.query(Employee).filter(Employee.estado == "activo").order_by(Employee.nombre_completo).all()
    compensacion_por_cargo = {
        c.nombre: {
            "sueldo_base": c.sueldo_base_sugerido, "comision": c.comision_sugerida,
            "movilidad": c.movilidad_sugerida, "otros": c.otros_ingresos_sugerido,
        } for c in cargos
    }
    return templates.TemplateResponse(request, "rrhh_pedidos.html", _ctx(
        request, user, pedidos=pedidos, empresas=empresas, estados=ESTADOS_PEDIDO,
        estado_labels=ESTADO_LABELS, motivos=MOTIVOS_PEDIDO, urgencias=URGENCIAS_PEDIDO,
        cargos=cargos, areas=areas, empleados=empleados, compensacion_por_cargo=compensacion_por_cargo,
        f_estado=estado, active="pedidos",
    ))


def _monto_o_none(texto: str):
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        return None


@router.post("/rrhh/reclutamiento/pedidos/nuevo")
def pedidos_crear(cargo_solicitado: str = Form(...), area: str = Form(""), empresa_id: str = Form(""),
                   cantidad: int = Form(1), motivo: str = Form(""), urgencia: str = Form(""),
                   solicitante: str = Form(""), fecha_requerida: str = Form(""), observaciones: str = Form(""),
                   sueldo_base_ofrecido: str = Form(""), comision_ofrecida: str = Form(""),
                   movilidad_ofrecida: str = Form(""), otros_ingresos_ofrecido: str = Form(""),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    db.add(PedidoPersonal(
        cargo_solicitado=cargo_solicitado.strip(), area=area.strip() or None,
        empresa_id=int(empresa_id) if empresa_id else None, cantidad=max(cantidad, 1),
        motivo=motivo or None, urgencia=urgencia or None, solicitante=solicitante.strip() or None,
        fecha_requerida=_parse_fecha(fecha_requerida), observaciones=observaciones.strip() or None,
        sueldo_base_ofrecido=_monto_o_none(sueldo_base_ofrecido), comision_ofrecida=_monto_o_none(comision_ofrecida),
        movilidad_ofrecida=_monto_o_none(movilidad_ofrecida), otros_ingresos_ofrecido=_monto_o_none(otros_ingresos_ofrecido),
        registrado_por=user.nombre_completo,
    ))
    db.commit()
    return RedirectResponse("/rrhh/reclutamiento/pedidos", status_code=303)


@router.post("/rrhh/reclutamiento/pedidos/{pedido_id}/estado")
def pedidos_cambiar_estado(pedido_id: int, estado: str = Form(...), db: Session = Depends(get_db),
                            user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    if estado not in ESTADO_PEDIDO_KEYS:
        raise HTTPException(400, "Estado inválido.")
    pedido = db.query(PedidoPersonal).get(pedido_id)
    if not pedido:
        raise HTTPException(404)
    pedido.estado = estado
    pedido.cerrado_at = datetime.datetime.utcnow() if estado in ("cubierto", "cancelado") else None
    db.commit()
    return RedirectResponse("/rrhh/reclutamiento/pedidos", status_code=303)


# ---------------------------------------------------------------------------
# Control de Leads (candidatos)
# ---------------------------------------------------------------------------
@router.get("/rrhh/reclutamiento/leads", response_class=HTMLResponse)
def leads_list(request: Request, etapa: str = "", pedido_id: str = "", db: Session = Depends(get_db),
                user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    query = db.query(LeadCandidato)
    if etapa:
        query = query.filter(LeadCandidato.etapa == etapa)
    if pedido_id:
        query = query.filter(LeadCandidato.pedido_id == int(pedido_id))
    leads = query.order_by(LeadCandidato.created_at.desc()).all()
    pedidos_abiertos = (
        db.query(PedidoPersonal)
        .filter(PedidoPersonal.estado.in_(["abierto", "en_proceso"]))
        .order_by(PedidoPersonal.created_at.desc()).all()
    )
    return templates.TemplateResponse(request, "rrhh_leads.html", _ctx(
        request, user, leads=leads, pedidos_abiertos=pedidos_abiertos, etapas=ETAPAS_LEAD,
        etapa_labels=ETAPA_LABELS, origenes=ORIGENES_LEAD, f_etapa=etapa, f_pedido=pedido_id,
        active="leads",
    ))


@router.post("/rrhh/reclutamiento/leads/nuevo")
def leads_crear(nombre_completo: str = Form(...), email: str = Form(""), celular: str = Form(""),
                 origen: str = Form(""), pedido_id: str = Form(""), notas: str = Form(""),
                 db: Session = Depends(get_db),
                 user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    db.add(LeadCandidato(
        nombre_completo=nombre_completo.strip(), email=email.strip() or None, celular=celular.strip() or None,
        origen=origen or None, pedido_id=int(pedido_id) if pedido_id else None, notas=notas.strip() or None,
        registrado_por=user.nombre_completo,
    ))
    db.commit()
    return RedirectResponse("/rrhh/reclutamiento/leads", status_code=303)


@router.post("/rrhh/reclutamiento/leads/{lead_id}/etapa")
def leads_cambiar_etapa(lead_id: int, etapa: str = Form(...), db: Session = Depends(get_db),
                         user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    if etapa not in ETAPA_LEAD_KEYS:
        raise HTTPException(400, "Etapa inválida.")
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    lead.etapa = etapa
    db.commit()
    return RedirectResponse("/rrhh/reclutamiento/leads", status_code=303)


# ---------------------------------------------------------------------------
# Onboarding — vista general de avance por trabajador (el detalle y la carga
# de cada registro vive en la ficha del trabajador, /rrhh/personal/{id}).
# ---------------------------------------------------------------------------
@router.get("/rrhh/reclutamiento/onboarding", response_class=HTMLResponse)
def onboarding_overview(request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    empleados = (
        db.query(Employee)
        .filter(Employee.estado == "activo")
        .order_by(Employee.created_at.desc())
        .limit(100).all()
    )
    total_etapas = len(ETAPAS_ONBOARDING)
    resumen = []
    for e in empleados:
        completadas = {r.etapa for r in e.onboarding if r.estado == "completado"}
        resumen.append({"e": e, "completadas": len(completadas), "total": total_etapas})
    return templates.TemplateResponse(request, "rrhh_onboarding.html", _ctx(
        request, user, resumen=resumen, active="onboarding",
    ))
