# -*- coding: utf-8 -*-
"""Llamadas salientes de MICELIO hacia n8n (automatización de Reclutamiento
y Selección, punto del pedido del 21/09).

n8n es quien tiene las credenciales de verdad (WhatsApp Business, Google
Calendar de trabajaconnosotros@digetelgroup.com, el modelo de IA para
calificar CVs) — MICELIO nunca las maneja. Cada acción de RR.HH. que
depende de una de esas credenciales (Entrevistar, Descartar) dispara un
webhook HTTP síncrono hacia n8n, que responde con el resultado en la misma
llamada (sin necesitar un endpoint de callback aparte).

Si la URL del webhook correspondiente no está configurada (todavía no se
armó ese flujo en n8n), o la llamada falla por cualquier motivo, se
devuelve None — el código que llama decide el fallback (igual criterio que
google_calendar.py y build_pdf: nunca bloquea la acción de RR.HH. por un
problema de la integración externa).

Desde el 08/10 cada llamada queda registrada (tabla llamadas_n8n) para poder
ver en Reclutamiento y Selección › Integración n8n si n8n responde, y por qué
no cuando falla. Nunca se guarda el secreto, el cuerpo ni la URL completa."""
import os
import time
from urllib.parse import urlparse

import requests

TIMEOUT_SEGUNDOS = 20

EVENTOS = {
    "N8N_WEBHOOK_NUEVO_LEAD_URL": "nuevo_lead",
    "N8N_WEBHOOK_ENTREVISTAR_URL": "entrevistar",
    "N8N_WEBHOOK_DESCARTAR_URL": "descartar",
    "N8N_WEBHOOK_SELECCION_APROBADO_URL": "seleccion_aprobado",
    "N8N_WEBHOOK_ANALIZAR_CV_URL": "analizar_cv",
}
MAX_REGISTROS = 500


def _url(env_var: str) -> str:
    return (os.environ.get(env_var) or "").strip()


def webhook_configurado(env_var: str) -> bool:
    return bool(_url(env_var))


def _url_enmascarada(url: str) -> str:
    """host + '/…' + 4 últimos caracteres: lo justo para reconocer a qué flujo
    se llamó sin dejar la URL (a veces es un secreto) en la base de datos."""
    p = urlparse(url)
    cola = (p.path or "")[-4:]
    return f"{p.scheme}://{p.netloc}/…{cola}" if p.netloc else "(URL inválida)"


def registrar_llamada(*, sentido: str, evento: str, destino: str, ok: bool, http_status: int | None = None,
                      error: str | None = None, duracion_ms: int | None = None, lead_id: int | None = None) -> None:
    """Guarda una fila en llamadas_n8n. Nunca lanza: un fallo al registrar no
    puede romper la acción de RR.HH. ni la respuesta a n8n."""
    try:
        from .database import SessionLocal
        from .models import LlamadaN8n
        with SessionLocal() as db:
            db.add(LlamadaN8n(sentido=sentido, evento=evento[:60], destino=destino[:300], ok=ok,
                              http_status=http_status, error=(error or None) and error[:300],
                              duracion_ms=duracion_ms, lead_id=lead_id))
            db.commit()
            exceso = db.query(LlamadaN8n).count() - MAX_REGISTROS
            if exceso > 0:
                viejos = db.query(LlamadaN8n.id).order_by(LlamadaN8n.id.asc()).limit(exceso).all()
                db.query(LlamadaN8n).filter(LlamadaN8n.id.in_([i for (i,) in viejos])).delete(synchronize_session=False)
                db.commit()
    except Exception:
        pass


def llamar_webhook(env_var: str, payload: dict, evento: str | None = None) -> dict | None:
    """POST síncrono al webhook de n8n en la variable de entorno `env_var`
    (p.ej. N8N_WEBHOOK_ENTREVISTAR_URL). Autentica con el secreto
    compartido N8N_WEBHOOK_SECRET (header X-Webhook-Secret) si está
    configurado — el workflow de n8n debe validarlo con un nodo IF antes de
    hacer nada, para que no cualquiera pueda dispararlo si la URL se filtra.
    Devuelve el JSON de respuesta de n8n, o None si no se pudo completar
    (y deja el motivo registrado)."""
    evento = evento or EVENTOS.get(env_var, env_var)
    url = _url(env_var)
    lead_id = payload.get("lead_id") if isinstance(payload, dict) else None
    if not url:
        registrar_llamada(sentido="saliente", evento=evento, destino="(sin configurar)", ok=False,
                          error=f"Falta configurar {env_var} en el servidor.", lead_id=lead_id)
        return None
    headers = {}
    secreto = os.environ.get("N8N_WEBHOOK_SECRET")
    if secreto:
        headers["X-Webhook-Secret"] = secreto
    destino = _url_enmascarada(url)
    inicio = time.monotonic()
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_SEGUNDOS)
    except requests.exceptions.Timeout:
        registrar_llamada(sentido="saliente", evento=evento, destino=destino, ok=False,
                          error=f"n8n no respondió en {TIMEOUT_SEGUNDOS} s (¿el flujo está activo y responde con «Respond to Webhook»?).",
                          duracion_ms=int((time.monotonic() - inicio) * 1000), lead_id=lead_id)
        return None
    except requests.exceptions.RequestException as e:
        registrar_llamada(sentido="saliente", evento=evento, destino=destino, ok=False,
                          error=f"No se pudo conectar con n8n ({type(e).__name__}): revisa la URL y que n8n esté encendido.",
                          duracion_ms=int((time.monotonic() - inicio) * 1000), lead_id=lead_id)
        return None
    ms = int((time.monotonic() - inicio) * 1000)
    if not r.ok:
        pista = {
            401: "n8n rechazó la clave (¿el secreto N8N_WEBHOOK_SECRET coincide con el del flujo?).",
            403: "n8n rechazó la clave (¿el secreto N8N_WEBHOOK_SECRET coincide con el del flujo?).",
            404: "n8n no encontró ese webhook: ¿el flujo está ACTIVADO (publicado) y la URL es la de producción, no la de prueba?",
        }.get(r.status_code, "n8n respondió con error: mira la ejecución en n8n.")
        registrar_llamada(sentido="saliente", evento=evento, destino=destino, ok=False, http_status=r.status_code,
                          error=pista, duracion_ms=ms, lead_id=lead_id)
        return None
    try:
        resultado = r.json()
    except ValueError:
        # El aviso de "nuevo lead" no necesita respuesta (n8n solo arranca la
        # conversación); en Entrevistar/Descartar MICELIO espera el resultado.
        solo_aviso = evento in ("nuevo_lead", "seleccion_aprobado", "analizar_cv") or evento.startswith("prueba")
        registrar_llamada(sentido="saliente", evento=evento, destino=destino, ok=solo_aviso, http_status=r.status_code,
                          error=None if solo_aviso else "n8n recibió el aviso pero no devolvió JSON (¿falta el nodo «Respond to Webhook»?).",
                          duracion_ms=ms, lead_id=lead_id)
        return None
    registrar_llamada(sentido="saliente", evento=evento, destino=destino, ok=True, http_status=r.status_code,
                      duracion_ms=ms, lead_id=lead_id)
    return resultado
