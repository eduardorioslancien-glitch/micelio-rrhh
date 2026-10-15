# -*- coding: utf-8 -*-
"""ENVIAR A ENTREVISTA (15/10/2026).

Reemplaza «Entrevistar + primera entrevista de RR.HH. + aprobar y segunda
entrevista». El entrevistador es siempre la persona que generó el pedido de
personal. Al enviar al postulante a entrevista:

1. n8n crea la cita en el calendario de trabajaconnosotros@digetelgroup.com,
   invitando al postulante y al entrevistador, el día hábil siguiente entre las
   15:00 y las 17:00 (hora de Lima), y avisa a ambos por WhatsApp y correo.
2. El lead pasa a SELECCIÓN: se crea su legajo pendiente con las condiciones del
   pedido, los resultados del formulario y la conclusión, y el lead queda en la
   etapa «oferta» (así no se borra ni se vuelve a entrevistar).
"""
import datetime
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .auth import require_perm
from .database import get_db
from .models import Employee, LeadCandidato, PedidoPersonal, User, STATUS_PENDIENTE
from . import n8n

router = APIRouter()


def _celular_de_ficha(ficha: dict) -> str:
    codigo = (ficha.get("celular_codigo") or "+51").lstrip("+")
    numero = "".join(ch for ch in (ficha.get("celular") or "") if ch.isdigit())
    return f"{codigo}{numero}" if numero else ""


def entrevistador_de(db: Session, pedido: PedidoPersonal | None) -> dict:
    """La persona que generó el pedido: su nombre, correo y celular (de su
    ficha en Personal). Siempre devuelve un dict; `email`/`celular` pueden venir vacíos."""
    vacio = {"nombre": "", "email": "", "celular": "", "employee": None, "user": None, "completo": False}
    if not pedido:
        return vacio
    user = db.query(User).get(pedido.creado_por_user_id) if pedido.creado_por_user_id else None
    if not user and pedido.registrado_por:
        user = db.query(User).filter(User.nombre_completo == pedido.registrado_por).first()
    emp = None
    if user:
        emp = db.query(Employee).get(user.employee_id) if user.employee_id else None
        if not emp:
            emp = db.query(Employee).filter(Employee.nombre_completo == user.nombre_completo).first()
    nombre = (emp.nombre_completo if emp else (user.nombre_completo if user else (pedido.registrado_por or pedido.solicitante or "")))
    email = (emp.email or "") if emp else ""
    celular = _celular_de_ficha(emp.ficha_data or {}) if emp else ""
    return {"nombre": nombre, "email": email, "celular": celular, "employee": emp, "user": user,
            "completo": bool(email and celular)}


def _payload_entrevista(lead: LeadCandidato, entr: dict) -> dict:
    from .reclutamiento import _celular_lead_completo
    pedido = lead.pedido
    return {
        "lead_id": lead.id,
        "nombre_completo": lead.nombre_completo,
        "primer_nombre": (lead.nombre_completo or "").split()[0] if lead.nombre_completo else "",
        "email": lead.email,
        "celular": _celular_lead_completo(lead),
        "cargo": pedido.cargo_solicitado if pedido else None,
        "cargo_legible": (pedido.cargo_solicitado or "").title() if pedido else "",
        "area": pedido.area if pedido else None,
        "empresa": pedido.empresa.nombre if pedido and pedido.empresa else None,
        "pedido_codigo": pedido.codigo if pedido else None,
        "entrevistador_nombre": entr["nombre"],
        "entrevistador_primer_nombre": (entr["nombre"] or "").split()[0] if entr["nombre"] else "",
        "entrevistador_email": entr["email"],
        "entrevistador_celular": entr["celular"],
        "calificacion_final": lead.calificacion_final,
        "conclusion": (lead.conclusion_ia or "")[:900],
    }


@router.post("/rrhh/reclutamiento/leads/{lead_id}/enviar-entrevista")
def enviar_a_entrevista(lead_id: int, sin_formulario: str = Form(""), db: Session = Depends(get_db),
                         user: User = Depends(require_perm("leads", "editar"))):
    from .reclutamiento import (_competencias_resueltas, _ficha_desde_pedido, _ensure_documents)
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    destino = f"/rrhh/reclutamiento/leads/{lead_id}"
    if lead.etapa in ("oferta", "contratado", "descartado"):
        return RedirectResponse(f"{destino}?ok=entrevista_ya_enviada", status_code=303)
    if not lead.pedido:
        return RedirectResponse(f"{destino}?ok=cuestionario_sin_pedido", status_code=303)
    if not lead.cuestionario_respondido_at and sin_formulario != "1":
        return RedirectResponse(f"{destino}?ok=entrevista_sin_formulario", status_code=303)
    if not lead.email:
        return RedirectResponse(f"{destino}?ok=entrevista_sin_correo", status_code=303)

    entr = entrevistador_de(db, lead.pedido)
    resultado = n8n.llamar_webhook("N8N_WEBHOOK_ENTREVISTAR_URL", _payload_entrevista(lead, entr), evento="entrevistar")
    if not (resultado and resultado.get("ok") and resultado.get("meet_link")):
        motivo = (resultado or {}).get("detalle") if isinstance(resultado, dict) else None
        from urllib.parse import quote
        return RedirectResponse(f"{destino}?ok=entrevista_fallo&motivo={quote(motivo or '')}", status_code=303)

    ahora = (datetime.datetime.utcnow() - datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
    cita = {
        "fecha_hora": resultado.get("fecha_hora"), "meet_link": resultado.get("meet_link"),
        "calendar_event_id": resultado.get("calendar_event_id"), "agendado_at": ahora,
        "entrevistador_nombre": entr["nombre"], "entrevistador_email": entr["email"],
    }
    ed = dict(lead.entrevista_data or {})
    ed["coordinacion"] = cita
    lead.entrevista_data = ed

    pedido = lead.pedido
    empresa = pedido.empresa
    ficha = {}
    if pedido.codigo:
        ficha["codigo_pedido_seleccion"] = pedido.codigo
    ficha["vacante_cargo"] = pedido.cargo_solicitado
    ficha["vacante_area"] = pedido.area
    ficha.update(_ficha_desde_pedido(pedido))
    if lead.entrevista_data.get("disc"):
        ficha["disc_resultado"] = lead.entrevista_data["disc"]
    comp = _competencias_resueltas(lead, db)
    if comp:
        ficha["competencias_entrevista"] = comp
    # El celular del postulante viaja a su ficha: hace falta para mandarle por
    # WhatsApp la ficha y el contrato antes de que la llene.
    from .telefono import solo_digitos_whatsapp
    digitos = solo_digitos_whatsapp(lead.celular or "") or ""
    if digitos:
        if len(digitos) > 9:
            ficha["celular_codigo"] = "+" + digitos[:-9]
            ficha["celular"] = digitos[-9:]
        else:
            ficha["celular_codigo"] = "+51"
            ficha["celular"] = digitos
    ficha.update({
        "lead_origen_id": lead.id,
        "conclusion_ia": lead.conclusion_ia, "calificacion_final": lead.calificacion_final,
        "cv_estrellas": lead.estrellas, "cv_analisis": lead.analisis_ia,
        "entrevista_cita": cita,
        "entrevistador_pedido_nombre": entr["nombre"],
        "entrevistador_pedido_email": entr["email"],
        "entrevistador_pedido_celular": entr["celular"],
        "seleccion_enviada_por": user.nombre_completo,
    })
    emp = Employee(
        nombre_completo=lead.nombre_completo.strip(), email=lead.email or None,
        empresa_id=empresa.id if empresa else None, empresa=empresa.nombre if empresa else None,
        estado="activo", status=STATUS_PENDIENTE, ficha_data=ficha,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    _ensure_documents(db, emp)
    lead.etapa = "oferta"
    db.commit()
    return RedirectResponse(f"{destino}?ok=entrevista_agendada&seleccion={emp.id}", status_code=303)
