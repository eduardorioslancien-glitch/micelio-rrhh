# -*- coding: utf-8 -*-
"""Formulario del postulante: Competencias + DISC (15/10/2026).

Después de analizar el CV, el postulante recibe (WhatsApp y correo, vía n8n) un
enlace público a este formulario. Al enviarlo, MICELIO calcula el nivel por
competencia frente al que exige el cargo, el perfil DISC, una calificación
final y una conclusión (automática al instante; la IA de n8n la reemplaza por
una más completa cuando responde). Los resultados quedan en
`entrevista_data["competencias"|"disc"]` (mismo formato que ya leen Selección
y la ficha) y el detalle completo en `cuestionario_data`.
"""
import datetime
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .auth import require_perm
from .competencias_preguntas import asegurar_preguntas, incoherente, nivel_estimado, NIVELES
from .database import get_db
from .models import (Cargo, Competencia, CompetenciaPregunta, LeadCandidato, User, lead_incompleto)
from .rrhh import _ctx, _public_base_url, _a_lima
from . import disc as disc_mod
from . import n8n
from .telefono import solo_digitos_whatsapp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.filters["lima"] = _a_lima
templates.env.globals["interpretar_disc"] = disc_mod.interpretar
router = APIRouter()

PESO_CV = 0.35  # el resto (0.65) es el ajuste de competencias


# ---------------------------------------------------------------- datos del formulario

def competencias_requeridas(db: Session, lead: LeadCandidato) -> list[dict]:
    """Competencias que exige el cargo del pedido (con su nivel requerido) y sus
    preguntas. Vacío si el cargo no figura en el MOF o no exige ninguna."""
    if not lead.pedido or not lead.pedido.cargo_solicitado:
        return []
    cargo = db.query(Cargo).filter(Cargo.nombre == lead.pedido.cargo_solicitado).first()
    if not cargo:
        return []
    salida = []
    for req in cargo.requisitos_competencias:
        comp = req.competencia
        if not comp or not comp.activo:
            continue
        if asegurar_preguntas(db, comp):
            db.commit()
        preguntas = db.query(CompetenciaPregunta).filter(
            CompetenciaPregunta.competencia_id == comp.id, CompetenciaPregunta.activo == True  # noqa: E712
        ).order_by(CompetenciaPregunta.nivel).all()
        salida.append({
            "competencia": comp, "requerido": req.nivel_requerido,
            "afirmaciones": [(p.id, p.nivel, p.texto) for p in preguntas if p.tipo == "afirmacion"],
            "ejemplo": next(((p.id, p.texto) for p in preguntas if p.tipo == "ejemplo"), None),
        })
    return salida


def asegurar_token(lead: LeadCandidato) -> str:
    if not lead.cuestionario_token:
        lead.cuestionario_token = uuid.uuid4().hex
    return lead.cuestionario_token


def enlace_cuestionario(lead: LeadCandidato) -> str:
    return _public_base_url() + f"cuestionario/{lead.cuestionario_token}"


def _lead_por_token(db: Session, token: str) -> LeadCandidato | None:
    if not token or len(token) < 16:
        return None
    return db.query(LeadCandidato).filter(LeadCandidato.cuestionario_token == token).first()


# ---------------------------------------------------------------- cálculo

def calcular_calificacion(lead: LeadCandidato, competencias: list[dict]) -> dict:
    """Calificación final 1-5: ajuste de competencias (65 %) + estrellas del CV
    (35 %). Si no hay análisis de CV, pesa solo el ajuste de competencias."""
    ratios = []
    for c in competencias:
        req = max(int(c.get("requerido") or 1), 1)
        ratios.append(min(1.0, (c.get("nivel") or 0) / req))
    ajuste = (sum(ratios) / len(ratios)) if ratios else None
    puntaje_comp = round(1 + 4 * ajuste, 1) if ajuste is not None else None
    cv = lead.estrellas
    if puntaje_comp is not None and cv:
        final = PESO_CV * cv + (1 - PESO_CV) * puntaje_comp
    else:
        final = puntaje_comp if puntaje_comp is not None else cv
    return {
        "final": round(final, 1) if final is not None else None,
        "cv": cv, "competencias": puntaje_comp,
        "ajuste_pct": round(ajuste * 100) if ajuste is not None else None,
        "peso_cv": PESO_CV if (puntaje_comp is not None and cv) else (1.0 if cv and puntaje_comp is None else 0.0),
    }


def conclusion_automatica(lead: LeadCandidato, datos: dict) -> str:
    cargo = lead.pedido.cargo_solicitado if lead.pedido else "el cargo"
    comp = datos.get("competencias") or []
    cal = datos.get("calificacion") or {}
    partes = [f"Postulación a {cargo}."]
    if lead.estrellas:
        partes.append(f"CV: {lead.estrellas:g}/5 según el análisis automático.")
    if comp:
        cumplen = [c for c in comp if (c.get("nivel") or 0) >= (c.get("requerido") or 1)]
        brechas = [c for c in comp if (c.get("nivel") or 0) < (c.get("requerido") or 1)]
        partes.append(f"Competencias: alcanza el nivel exigido en {len(cumplen)} de {len(comp)}.")
        if brechas:
            detalle = "; ".join(f"{b['nombre'].title()} (nivel {b.get('nivel') or 0} de {b.get('requerido')} requerido)" for b in brechas)
            partes.append(f"Brechas a profundizar en la entrevista: {detalle}.")
        fuertes = [c for c in cumplen if (c.get("nivel") or 0) > (c.get("requerido") or 1)]
        if fuertes:
            partes.append("Por encima del nivel exigido: " + ", ".join(c["nombre"].title() for c in fuertes) + ".")
        incoh = [c["nombre"].title() for c in comp if c.get("incoherente")]
        if incoh:
            partes.append("Respuestas poco consistentes (contrastar con ejemplos en la entrevista): " + ", ".join(incoh) + ".")
    else:
        partes.append("El cargo no tiene competencias exigidas cargadas en el MOF: la evaluación se basa en CV y DISC.")
    info = disc_mod.interpretar(datos.get("disc"))
    if info:
        partes.append("DISC: " + info["resumen"])
    final = cal.get("final")
    if final is not None:
        if final >= 4:
            rec = "Recomendación: avanzar a entrevista."
        elif final >= 3:
            rec = "Recomendación: avanzar a entrevista profundizando en las brechas indicadas."
        else:
            rec = "Recomendación: revisar con cuidado; el ajuste al cargo es bajo."
        partes.append(f"Calificación final: {final:g}/5. {rec}")
    partes.append("Esta lectura es automática y de apoyo: la decisión la toma Recursos Humanos.")
    return " ".join(partes)


def payload_conclusion(lead: LeadCandidato) -> dict:
    d = lead.cuestionario_data or {}
    return {
        "lead_id": lead.id, "nombre_completo": lead.nombre_completo,
        "cargo": lead.pedido.cargo_solicitado if lead.pedido else None,
        "empresa": lead.pedido.empresa.nombre if lead.pedido and lead.pedido.empresa else None,
        "cv": {"estrellas": lead.estrellas, "analisis": lead.analisis_ia},
        "competencias": [
            {k: c.get(k) for k in ("nombre", "requerido", "nivel", "ejemplo", "incoherente")}
            for c in (d.get("competencias") or [])
        ],
        "disc": d.get("disc"), "calificacion": d.get("calificacion"),
        "conclusion_automatica": lead.conclusion_ia,
    }


def payload_envio(lead: LeadCandidato) -> dict:
    nombre = (lead.nombre_completo or "").strip()
    primer = nombre.split()[0] if nombre and not nombre.startswith("(") else "postulante"
    return {
        "lead_id": lead.id, "nombre_completo": nombre, "primer_nombre": primer, "email": lead.email,
        "celular_whatsapp": solo_digitos_whatsapp(lead.celular or "") or "",
        "cargo": lead.pedido.cargo_solicitado if lead.pedido else None,
        "cargo_legible": (lead.pedido.cargo_solicitado or "").title() if lead.pedido else "tu postulación",
        "empresa": lead.pedido.empresa.nombre if lead.pedido and lead.pedido.empresa else None,
        "enlace": enlace_cuestionario(lead),
    }


def _enviar_n8n(payload: dict) -> None:
    n8n.llamar_webhook("N8N_WEBHOOK_CUESTIONARIO_URL", payload, evento="cuestionario")


def _conclusion_n8n(payload: dict) -> None:
    n8n.llamar_webhook("N8N_WEBHOOK_CONCLUSION_URL", payload, evento="conclusion")


def preparar_envio(db: Session, lead: LeadCandidato) -> dict:
    """Marca el formulario como enviado y arma el payload para n8n. No hace
    commit del aviso: quien llama decide."""
    asegurar_token(lead)
    lead.cuestionario_enviado_at = datetime.datetime.utcnow()
    db.commit()
    return payload_envio(lead)


# ---------------------------------------------------------------- rutas públicas

def _contexto_form(db: Session, lead: LeadCandidato, error: str = "", previo: dict | None = None) -> dict:
    return {
        "lead": lead, "bloques": competencias_requeridas(db, lead),
        "disc_preguntas": _disc_preguntas(), "niveles": NIVELES, "error": error, "previo": previo or {},
        "cargo": lead.pedido.cargo_solicitado if lead.pedido else "",
        "empresa": lead.pedido.empresa.nombre if lead.pedido and lead.pedido.empresa else "DIGETEL GROUP",
    }


def _disc_preguntas():
    from .reclutamiento import DISC_PREGUNTAS
    return DISC_PREGUNTAS


@router.get("/cuestionario/{token}", response_class=HTMLResponse)
def cuestionario_form(request: Request, token: str, db: Session = Depends(get_db)):
    lead = _lead_por_token(db, token)
    if not lead:
        raise HTTPException(404)
    if lead.cuestionario_respondido_at:
        return templates.TemplateResponse(request, "cuestionario_gracias.html", {"request": request, "lead": lead, "ya": True})
    return templates.TemplateResponse(request, "cuestionario_publico.html", {
        "request": request, "token": token, **_contexto_form(db, lead)})


@router.post("/cuestionario/{token}", response_class=HTMLResponse)
async def cuestionario_enviar(request: Request, token: str, background_tasks: BackgroundTasks,
                               db: Session = Depends(get_db)):
    lead = _lead_por_token(db, token)
    if not lead:
        raise HTTPException(404)
    if lead.cuestionario_respondido_at:
        return templates.TemplateResponse(request, "cuestionario_gracias.html", {"request": request, "lead": lead, "ya": True})
    form = await request.form()
    previo = {k: v for k, v in form.items()}
    bloques = competencias_requeridas(db, lead)
    disc_preguntas = _disc_preguntas()

    faltantes = []
    competencias = []
    for b in bloques:
        comp = b["competencia"]
        respuestas = {}
        for _pid, nivel, _txt in b["afirmaciones"]:
            v = form.get(f"a_{comp.id}_{nivel}")
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = None
            if v is None or not 1 <= v <= 5:
                faltantes.append(f"{comp.nombre.title()} (nivel {nivel})")
            respuestas[nivel] = v
        ejemplo = (form.get(f"e_{comp.id}") or "").strip()
        nivel = nivel_estimado({n: p for n, p in respuestas.items()})
        competencias.append({
            "id": comp.id, "nombre": comp.nombre, "tipo": comp.tipo, "requerido": b["requerido"],
            "respuestas": {str(n): p for n, p in respuestas.items()}, "nivel": nivel,
            "ejemplo": ejemplo[:2000], "incoherente": incoherente(respuestas),
        })
    disc_resp = {}
    for pid, _dim, _txt in disc_preguntas:
        v = form.get(f"disc_{pid}")
        if v in ("1", "2", "3", "4", "5"):
            disc_resp[pid] = v
        else:
            faltantes.append("DISC")
            break
    if faltantes:
        msg = "Falta responder: " + ", ".join(dict.fromkeys(faltantes)) + ". Revisa los campos marcados."
        return templates.TemplateResponse(request, "cuestionario_publico.html", {
            "request": request, "token": token, **_contexto_form(db, lead, error=msg, previo=previo)})

    from .reclutamiento import _calcular_disc
    disc = _calcular_disc(disc_resp)
    datos = {"competencias": competencias, "disc": disc, "disc_respuestas": disc_resp}
    datos["calificacion"] = calcular_calificacion(lead, competencias)
    ahora = datetime.datetime.utcnow()
    datos["respondido_at"] = ahora.isoformat(timespec="seconds")
    lead.cuestionario_data = datos
    lead.cuestionario_respondido_at = ahora
    lead.calificacion_final = datos["calificacion"]["final"]
    lead.conclusion_ia = conclusion_automatica(lead, datos)
    lead.conclusion_origen = "auto"
    lead.conclusion_at = ahora

    ed = dict(lead.entrevista_data or {})
    ed["competencias"] = {str(c["id"]): {"nivel_observado": c["nivel"], "notas": c["ejemplo"] or None} for c in competencias}
    ed["disc"] = disc
    ed["disc_respuestas"] = disc_resp
    lead.entrevista_data = ed
    db.commit()
    db.refresh(lead)
    background_tasks.add_task(_conclusion_n8n, payload_conclusion(lead))
    return templates.TemplateResponse(request, "cuestionario_gracias.html", {"request": request, "lead": lead, "ya": False})


# ---------------------------------------------------------------- rutas internas (RR.HH.)

@router.post("/rrhh/reclutamiento/leads/{lead_id}/enviar-cuestionario")
def enviar_cuestionario(lead_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
                         user: User = Depends(require_perm("leads", "editar"))):
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    if not lead.pedido_id:
        return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok=cuestionario_sin_pedido", status_code=303)
    if lead.cuestionario_respondido_at:
        return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok=cuestionario_ya_respondido", status_code=303)
    payload = preparar_envio(db, lead)
    configurado = n8n.webhook_configurado("N8N_WEBHOOK_CUESTIONARIO_URL")
    if configurado:
        background_tasks.add_task(_enviar_n8n, payload)
    return RedirectResponse(
        f"/rrhh/reclutamiento/leads/{lead_id}?ok={'cuestionario_enviado' if configurado else 'cuestionario_enlace'}",
        status_code=303)


@router.post("/rrhh/reclutamiento/leads/{lead_id}/regenerar-conclusion")
def regenerar_conclusion(lead_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
                          user: User = Depends(require_perm("leads", "editar"))):
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead or not lead.cuestionario_respondido_at:
        raise HTTPException(404)
    datos = dict(lead.cuestionario_data or {})
    datos["calificacion"] = calcular_calificacion(lead, datos.get("competencias") or [])
    lead.cuestionario_data = datos
    lead.calificacion_final = datos["calificacion"]["final"]
    lead.conclusion_ia = conclusion_automatica(lead, datos)
    lead.conclusion_origen = "auto"
    lead.conclusion_at = datetime.datetime.utcnow()
    db.commit()
    background_tasks.add_task(_conclusion_n8n, payload_conclusion(lead))
    return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok=conclusion_solicitada", status_code=303)
