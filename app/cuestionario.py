# -*- coding: utf-8 -*-
"""Formulario del postulante: Competencias + DISC (15-16/10/2026).

Después de analizar el CV, el postulante recibe (WhatsApp y correo, vía n8n) un
enlace público a este formulario. Al enviarlo, MICELIO calcula el nivel por
competencia frente al que exige el cargo, el perfil DISC, una calificación
final y una conclusión (automática al instante; la IA de n8n la reemplaza por
una más completa y además EVALÚA los ejemplos abiertos).

Cómo se evita que todos se pongan «5» (16/10):
- Competencias: por cada una el postulante lee 4 frases (una por nivel, en orden
  aleatorio y sin número) y elige la que MÁS y la que MENOS lo describe.
- El ejemplo real es obligatorio y se evalúa: el nivel que cuenta es el MENOR entre
  el que eligió y el que su ejemplo sustenta (`nivel_sustentado`).
- DISC: elección forzada (palabra que MÁS y que MENOS te describe en 20 grupos).
- Si casi todas sus elecciones son el nivel más alto, se avisa a RR.HH.

Los resultados quedan en `entrevista_data["competencias"|"disc"]` (formato que ya
leen Selección y la ficha) y el detalle completo en `cuestionario_data`.
"""
import datetime
import json
import os
import random
import re
import unicodedata
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .auth import require_perm
from .competencias_preguntas import asegurar_preguntas, NIVELES
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


# ---------------------------------------------------------------- utilidades

def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip().upper()


_DISTRITOS_CACHE: list[str] | None = None


def lista_distritos() -> list[str]:
    """Todos los distritos del Perú (para sugerir al escribir)."""
    global _DISTRITOS_CACHE
    if _DISTRITOS_CACHE is None:
        try:
            with open(os.path.join(BASE_DIR, "static", "ubigeo_peru.json"), encoding="utf-8") as f:
                datos = json.load(f)
            nombres = {d for dep in datos for prov in dep["provincias"] for d in prov["distritos"]}
            _DISTRITOS_CACHE = sorted(nombres)
        except Exception:
            _DISTRITOS_CACHE = []
    return _DISTRITOS_CACHE


def calidad_ejemplo_local(texto: str) -> str:
    """'nula' si el ejemplo está vacío o es ruido evidente; 'pendiente' si hay
    que dejar que la IA lo evalúe. Es una red de seguridad barata: la
    evaluación de verdad la hace la IA (flujo 08)."""
    t = (texto or "").strip()
    if len(t) < 25:
        return "nula"
    palabras = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}", t)
    if len(palabras) < 5 or len({p.lower() for p in palabras}) <= 2:
        return "nula"
    letras = [c for c in t.lower() if c.isalpha()]
    if letras and sum(1 for c in letras if c in "aeiouáéíóú") / len(letras) < 0.2:
        return "nula"
    return "pendiente"


def nivel_considerado(c: dict) -> int:
    """El menor entre el nivel que eligió y el que su ejemplo sustenta."""
    elegido = int(c.get("nivel") or 0)
    sustentado = c.get("nivel_sustentado")
    return min(elegido, int(sustentado)) if sustentado is not None else elegido


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


def zona_de(lead: LeadCandidato, distrito: str) -> dict:
    base = lead.pedido.base if lead.pedido else None
    if not base:
        return {"base": None, "distrito": distrito, "dentro": None}
    zona = {_norm(d) for d in (base.distritos or [])}
    return {"base": base.nombre, "distrito": distrito, "dentro": (_norm(distrito) in zona) if zona else None}


# ---------------------------------------------------------------- cálculo

def calcular_calificacion(lead: LeadCandidato, competencias: list[dict]) -> dict:
    """Calificación final 1-5: ajuste de competencias (65 %) + estrellas del CV
    (35 %). El ajuste usa el nivel CONSIDERADO (lo que elige y su ejemplo sustenta)."""
    ratios = []
    for c in competencias:
        req = max(int(c.get("requerido") or 1), 1)
        ratios.append(min(1.0, nivel_considerado(c) / req))
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
    zona = datos.get("zona") or {}
    partes = [f"Postulación a {cargo}."]
    if lead.estrellas:
        partes.append(f"CV: {lead.estrellas:g}/5 según el análisis automático.")
    if zona.get("distrito"):
        if zona.get("dentro") is True:
            partes.append(f"Vive en {zona['distrito'].title()}, dentro de la zona de la base {zona['base']}.")
        elif zona.get("dentro") is False:
            partes.append(f"Vive en {zona['distrito'].title()}, FUERA de la zona de la base {zona['base']}: confirmar en la entrevista si puede desplazarse.")
        else:
            partes.append(f"Distrito de residencia: {zona['distrito'].title()}.")
    if comp:
        cumplen = [c for c in comp if nivel_considerado(c) >= (c.get("requerido") or 1)]
        brechas = [c for c in comp if nivel_considerado(c) < (c.get("requerido") or 1)]
        partes.append(f"Competencias: alcanza el nivel exigido en {len(cumplen)} de {len(comp)} (considerando lo que eligió y lo que su ejemplo sustenta).")
        if brechas:
            detalle = "; ".join(f"{b['nombre'].title()} (nivel {nivel_considerado(b)} de {b.get('requerido')} requerido)" for b in brechas)
            partes.append(f"Brechas a profundizar en la entrevista: {detalle}.")
        sin_ejemplo = [c["nombre"].title() for c in comp if c.get("ejemplo_calidad") == "nula"]
        if sin_ejemplo:
            partes.append("No presentó un ejemplo válido en: " + ", ".join(sin_ejemplo) + ".")
        sobrestimadas = [c["nombre"].title() for c in comp if c.get("nivel_sustentado") is not None and c["nivel_sustentado"] < (c.get("nivel") or 0) and c.get("ejemplo_calidad") != "nula"]
        if sobrestimadas:
            partes.append("Su ejemplo sustenta menos de lo que eligió en: " + ", ".join(sobrestimadas) + ".")
        if datos.get("muy_favorable"):
            partes.append("Eligió el nivel más alto en casi todas las competencias: contrastar con ejemplos concretos en la entrevista.")
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
        "zona": d.get("zona"), "muy_favorable": bool(d.get("muy_favorable")),
        "competencias": [
            {"nombre": c.get("nombre"), "requerido": c.get("requerido"), "elegido": c.get("nivel"),
             "ejemplo": c.get("ejemplo"), "ejemplo_calidad_local": c.get("ejemplo_calidad")}
            for c in (d.get("competencias") or [])
        ],
        "disc": d.get("disc"), "calificacion": d.get("calificacion"),
        "conclusion_automatica": lead.conclusion_ia,
    }


def aplicar_evaluacion_ia(db: Session, lead: LeadCandidato, texto: str, evaluaciones: list | None, preguntas: list | None) -> None:
    """Guarda lo que devuelve la IA (flujo 08): conclusión, evaluación de cada
    ejemplo y preguntas sugeridas para la entrevista; recalcula la calificación."""
    datos = dict(lead.cuestionario_data or {})
    comp = [dict(c) for c in (datos.get("competencias") or [])]
    por_nombre = {_norm(c["nombre"]): c for c in comp}
    for ev in (evaluaciones or []):
        c = por_nombre.get(_norm(str(ev.get("nombre") or "")))
        if not c:
            continue
        try:
            nivel = int(ev.get("nivel_sustentado"))
            if 0 <= nivel <= 4:
                c["nivel_sustentado"] = nivel
        except (TypeError, ValueError):
            pass
        if ev.get("evidencia"):
            c["evidencia"] = str(ev["evidencia"])[:20]
        if ev.get("comentario"):
            c["analisis"] = str(ev["comentario"])[:600]
    datos["competencias"] = comp
    if preguntas:
        datos["preguntas_entrevista"] = [str(p)[:300] for p in preguntas][:8]
    datos["calificacion"] = calcular_calificacion(lead, comp)
    lead.cuestionario_data = datos
    lead.calificacion_final = datos["calificacion"]["final"]
    if texto:
        lead.conclusion_ia = texto[:6000]
        lead.conclusion_origen = "ia"
        lead.conclusion_at = datetime.datetime.utcnow()
    db.commit()


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
    """Marca el formulario como enviado y arma el payload para n8n."""
    asegurar_token(lead)
    lead.cuestionario_enviado_at = datetime.datetime.utcnow()
    db.commit()
    return payload_envio(lead)


# ---------------------------------------------------------------- rutas públicas

def _contexto_form(db: Session, lead: LeadCandidato, token: str, error: str = "", previo: dict | None = None) -> dict:
    bloques = []
    for b in competencias_requeridas(db, lead):
        opciones = list(b["afirmaciones"])
        random.Random(f"{token}-c{b['competencia'].id}").shuffle(opciones)
        bloques.append({**b, "opciones": opciones})
    grupos = []
    for gid, palabras in disc_mod.DISC_GRUPOS:
        items = list(palabras.items())
        random.Random(f"{token}-d{gid}").shuffle(items)
        grupos.append((gid, items))
    return {
        "lead": lead, "bloques": bloques, "grupos": grupos, "error": error, "previo": previo or {},
        "distritos": lista_distritos(), "distrito_previo": (previo or {}).get("distrito") or lead.distrito or "",
        "cargo": lead.pedido.cargo_solicitado if lead.pedido else "",
        "base": lead.pedido.base.nombre if lead.pedido and lead.pedido.base else None,
        "empresa": lead.pedido.empresa.nombre if lead.pedido and lead.pedido.empresa else "DIGETEL GROUP",
    }


@router.get("/cuestionario/{token}", response_class=HTMLResponse)
def cuestionario_form(request: Request, token: str, db: Session = Depends(get_db)):
    lead = _lead_por_token(db, token)
    if not lead:
        raise HTTPException(404)
    if lead.cuestionario_respondido_at:
        return templates.TemplateResponse(request, "cuestionario_gracias.html", {"request": request, "lead": lead, "ya": True})
    return templates.TemplateResponse(request, "cuestionario_publico.html", {
        "request": request, "token": token, **_contexto_form(db, lead, token)})


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
    faltantes = []

    distrito = re.sub(r"\s+", " ", (form.get("distrito") or "").strip())
    if len(distrito) < 3:
        faltantes.append("el distrito donde vives")

    competencias = []
    for b in competencias_requeridas(db, lead):
        comp = b["competencia"]
        niveles_validos = {n for _p, n, _t in b["afirmaciones"]}
        try:
            mas = int(form.get(f"mas_{comp.id}"))
            menos = int(form.get(f"menos_{comp.id}"))
        except (TypeError, ValueError):
            mas = menos = None
        if mas not in niveles_validos or menos not in niveles_validos or mas == menos:
            faltantes.append(f"{comp.nombre.title()} (elige una frase que MÁS y otra que MENOS te describa)")
            mas = menos = 0
        ejemplo = (form.get(f"e_{comp.id}") or "").strip()
        if not ejemplo:
            faltantes.append(f"{comp.nombre.title()} (tu ejemplo)")
        calidad = calidad_ejemplo_local(ejemplo)
        item = {
            "id": comp.id, "nombre": comp.nombre, "tipo": comp.tipo, "requerido": b["requerido"],
            "nivel": mas, "menos": menos, "ejemplo": ejemplo[:2000], "ejemplo_calidad": calidad,
            "nivel_sustentado": 0 if calidad == "nula" else None,
        }
        if calidad == "nula":
            item["analisis"] = "No presentó un ejemplo válido."
        competencias.append(item)

    disc_resp = {}
    for gid, palabras in disc_mod.DISC_GRUPOS:
        mas, menos = form.get(f"dm_{gid}"), form.get(f"dl_{gid}")
        if mas in palabras and menos in palabras and mas != menos:
            disc_resp[str(gid)] = {"mas": mas, "menos": menos}
    if len(disc_resp) < len(disc_mod.DISC_GRUPOS):
        faltantes.append("la parte de palabras (marca una que MÁS y otra que MENOS te describa en cada grupo)")

    if faltantes:
        msg = "Falta completar: " + "; ".join(dict.fromkeys(faltantes)) + "."
        return templates.TemplateResponse(request, "cuestionario_publico.html", {
            "request": request, "token": token, **_contexto_form(db, lead, token, error=msg, previo=previo)})

    disc = disc_mod.calcular_grupos(disc_resp)
    n_altos = sum(1 for c in competencias if c["nivel"] == 4)
    datos = {
        "competencias": competencias, "disc": disc, "disc_respuestas": disc_resp,
        "zona": zona_de(lead, distrito.upper()),
        "muy_favorable": len(competencias) >= 3 and n_altos / len(competencias) >= 0.7,
    }
    datos["calificacion"] = calcular_calificacion(lead, competencias)
    ahora = datetime.datetime.utcnow()
    datos["respondido_at"] = ahora.isoformat(timespec="seconds")
    lead.distrito = distrito.upper()
    lead.cuestionario_data = datos
    lead.cuestionario_respondido_at = ahora
    lead.calificacion_final = datos["calificacion"]["final"]
    lead.conclusion_ia = conclusion_automatica(lead, datos)
    lead.conclusion_origen = "auto"
    lead.conclusion_at = ahora

    ed = dict(lead.entrevista_data or {})
    ed["competencias"] = {str(c["id"]): {"nivel_observado": nivel_considerado(c), "notas": c["ejemplo"] or None} for c in competencias}
    ed["disc"] = disc
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
