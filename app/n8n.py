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
problema de la integración externa)."""
import os

import requests

TIMEOUT_SEGUNDOS = 20


def _url(env_var: str) -> str:
    return (os.environ.get(env_var) or "").strip()


def webhook_configurado(env_var: str) -> bool:
    return bool(_url(env_var))


def llamar_webhook(env_var: str, payload: dict) -> dict | None:
    """POST síncrono al webhook de n8n en la variable de entorno `env_var`
    (p.ej. N8N_WEBHOOK_ENTREVISTAR_URL). Autentica con el secreto
    compartido N8N_WEBHOOK_SECRET (header X-Webhook-Secret) si está
    configurado — el workflow de n8n debe validarlo con un nodo IF antes de
    hacer nada, para que no cualquiera pueda dispararlo si la URL se filtra.
    Devuelve el JSON de respuesta de n8n, o None si no se pudo completar."""
    url = _url(env_var)
    if not url:
        return None
    headers = {}
    secreto = os.environ.get("N8N_WEBHOOK_SECRET")
    if secreto:
        headers["X-Webhook-Secret"] = secreto
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_SEGUNDOS)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None
