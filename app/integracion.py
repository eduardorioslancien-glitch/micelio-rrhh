# -*- coding: utf-8 -*-
"""Reclutamiento y Selección › Integración n8n / WhatsApp (08/10/2026).

Pantalla solo del administrador para saber, sin adivinar, si la automatización
de WhatsApp está viva: qué está configurado (sin mostrar nunca los valores),
las últimas llamadas de MICELIO a n8n y de n8n a MICELIO con su resultado, y un
botón de prueba por webhook."""
import datetime
import os
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from .models import LlamadaN8n, User
from .auth import require_admin
from .rrhh import _ctx, _a_lima
from . import n8n

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.filters["lima"] = _a_lima
router = APIRouter()
RUTA = "/rrhh/reclutamiento/integracion"

# (variable de entorno, etiqueta, para qué sirve, qué pasa si falta)
CONFIGURACION = [
    ("LEADS_API_KEY", "Clave del API de MICELIO (LEADS_API_KEY)",
     "La usa n8n (header X-API-Key) para crear y completar leads en MICELIO.",
     "n8n no puede escribir en MICELIO: el API responde 503."),
    ("N8N_WEBHOOK_SECRET", "Secreto compartido con n8n (N8N_WEBHOOK_SECRET)",
     "MICELIO lo manda a n8n (header X-Webhook-Secret); el flujo de n8n debe validarlo.",
     "Los avisos salen sin clave: cualquiera con la URL podría disparar el flujo."),
    ("N8N_WEBHOOK_NUEVO_LEAD_URL", "Webhook «Nuevo lead»",
     "Avisa a n8n que entró un candidato con celular para iniciar la conversación de WhatsApp.",
     "No se inicia ninguna conversación de WhatsApp."),
    ("N8N_WEBHOOK_ENTREVISTAR_URL", "Webhook «Entrevistar»",
     "n8n agenda la entrevista (Calendar + Meet) y avisa por WhatsApp.",
     "El botón Entrevistar cae al correo manual."),
    ("N8N_WEBHOOK_DESCARTAR_URL", "Webhook «Descartar»",
     "n8n avisa el descarte por correo y WhatsApp.",
     "El botón Descartar cae al correo manual."),
    ("N8N_WEBHOOK_SELECCION_APROBADO_URL", "Webhook «Selección aprobado»",
     "n8n le escribe al candidato por WhatsApp con la plantilla «seleccion» (nombre y puesto).",
     "Confirmar APROBADO solo manda el correo y muestra el enlace wa.me manual."),
    ("N8N_WEBHOOK_ANALIZAR_CV_URL", "Webhook «Analizar CV»",
     "n8n lee el CV, lo compara con los requisitos del cargo con IA y devuelve estrellas y resumen.",
     "El CV no se analiza solo: el botón «Analizar CV» de la ficha del lead no hace nada."),
    ("N8N_WEBHOOK_CUESTIONARIO_URL", "Webhook «Enviar formulario»",
     "n8n manda al postulante el enlace del formulario de Competencias y DISC por WhatsApp y correo.",
     "El formulario no se envía solo: en la ficha del lead queda el enlace para copiarlo y mandarlo a mano."),
    ("N8N_WEBHOOK_CONCLUSION_URL", "Webhook «Conclusión con IA»",
     "n8n redacta con IA la conclusión (CV + competencias + DISC + cargo) cuando el postulante responde.",
     "Queda la conclusión automática de MICELIO, más breve y sin IA."),
]
PRUEBAS = {
    "nuevo_lead": "N8N_WEBHOOK_NUEVO_LEAD_URL",
    "entrevistar": "N8N_WEBHOOK_ENTREVISTAR_URL",
    "descartar": "N8N_WEBHOOK_DESCARTAR_URL",
    "seleccion_aprobado": "N8N_WEBHOOK_SELECCION_APROBADO_URL",
    "analizar_cv": "N8N_WEBHOOK_ANALIZAR_CV_URL",
    "cuestionario": "N8N_WEBHOOK_CUESTIONARIO_URL",
    "conclusion": "N8N_WEBHOOK_CONCLUSION_URL",
}


def _hace(dt) -> str:
    if not dt:
        return "nunca"
    seg = int((datetime.datetime.utcnow() - dt).total_seconds())
    if seg < 90:
        return "hace un momento"
    if seg < 3600:
        return f"hace {seg // 60} min"
    if seg < 86400:
        return f"hace {seg // 3600} h"
    return f"hace {seg // 86400} días"


@router.get(RUTA, response_class=HTMLResponse)
def integracion_pantalla(request: Request, msg: str = "", db: Session = Depends(get_db),
                         user: User = Depends(require_admin)):
    config = [{"env": env, "etiqueta": et, "para": para, "falta": falta, "ok": bool((os.environ.get(env) or "").strip())}
              for env, et, para, falta in CONFIGURACION]
    smtp_ok = all((os.environ.get(v) or "").strip() for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM"))
    salientes = db.query(LlamadaN8n).filter(LlamadaN8n.sentido == "saliente").order_by(LlamadaN8n.id.desc()).limit(30).all()
    entrantes = db.query(LlamadaN8n).filter(LlamadaN8n.sentido == "entrante").order_by(LlamadaN8n.id.desc()).limit(30).all()

    def ultima(sentido, solo_ok=True, evento=None):
        q = db.query(LlamadaN8n).filter(LlamadaN8n.sentido == sentido)
        if solo_ok:
            q = q.filter(LlamadaN8n.ok == True)  # noqa: E712
        if evento:
            q = q.filter(LlamadaN8n.evento == evento)
        return q.order_by(LlamadaN8n.id.desc()).first()

    ult_entrante_ok = ultima("entrante")
    resumen_eventos = []
    for evento, env in PRUEBAS.items():
        ok_ = ultima("saliente", True, evento)
        ult = ultima("saliente", False, evento)
        resumen_eventos.append({
            "evento": evento, "configurado": n8n.webhook_configurado(env),
            "ultimo_ok": _hace(ok_.created_at) if ok_ else "nunca",
            "ultimo": ult,
        })
    return templates.TemplateResponse(request, "rrhh_integracion.html", _ctx(
        request, user, config=config, smtp_ok=smtp_ok, salientes=salientes, entrantes=entrantes,
        n8n_llamo=_hace(ult_entrante_ok.created_at) if ult_entrante_ok else "nunca",
        resumen_eventos=resumen_eventos, msg=msg, active="integracion",
    ))


@router.post(RUTA + "/probar/{evento}")
def integracion_probar(evento: str, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Manda a n8n un aviso marcado como PRUEBA (sin lead ni celular reales).
    El flujo de n8n debe responder rápido cuando `prueba` sea true, sin
    escribirle a nadie (un nodo IF al comienzo)."""
    env = PRUEBAS.get(evento)
    if not env:
        return RedirectResponse(RUTA, status_code=303)
    payload = {
        "prueba": True, "evento": evento, "origen": "MICELIO",
        "enviado_por": user.nombre_completo,
        "mensaje": "Aviso de prueba desde MICELIO: no hagas nada con esto, solo responde ok.",
    }
    n8n.llamar_webhook(env, payload, evento=f"prueba:{evento}")
    ultima = db.query(LlamadaN8n).order_by(LlamadaN8n.id.desc()).first()
    if ultima and ultima.ok:
        msg = f"✅ n8n respondió ({ultima.http_status}) en {ultima.duracion_ms} ms."
    elif ultima:
        msg = f"❌ {ultima.error or 'La prueba falló.'}"
    else:
        msg = "No se pudo registrar la prueba."
    return RedirectResponse(f"{RUTA}?msg={quote(msg)}", status_code=303)
