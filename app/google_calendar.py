# -*- coding: utf-8 -*-
"""Integración con Google Calendar para "Coordinar Meet" en Gestión de
Leads (punto del pedido 16/09): en vez de mandar un link genérico de Meet,
agenda de verdad una reunión en un horario libre del calendario de
erios@digetelgroup.com y genera el link de Meet real de esa reunión.

Necesita una Cuenta de Servicio de Google Cloud con Delegación en todo el
dominio (Domain-Wide Delegation) autorizada para el scope
https://www.googleapis.com/auth/calendar, actuando en nombre de
GOOGLE_CALENDAR_IMPERSONATE (la cuenta cuyo calendario se usa). El archivo
JSON de la cuenta de servicio se sube A MANO al servidor (nunca va en git,
nunca lo maneja el asistente) — la ruta se indica en
GOOGLE_CALENDAR_CREDENTIALS_FILE.

Degrada con gracia si falta la configuración: agendar_reunion() devuelve
None y quien llama debe seguir usando el link genérico de respaldo
(meet.google.com/new), como se hacía antes de esta integración."""
import datetime
import os

CREDENTIALS_FILE = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS_FILE", "")
IMPERSONATE_EMAIL = os.environ.get("GOOGLE_CALENDAR_IMPERSONATE", "")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Lima es UTC-5 todo el año (no tiene horario de verano).
_TZ_OFFSET = datetime.timedelta(hours=-5)
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def calendar_configurado() -> bool:
    return bool(CREDENTIALS_FILE and os.path.exists(CREDENTIALS_FILE) and IMPERSONATE_EMAIL)


def _servicio():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    credenciales = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=SCOPES,
    ).with_subject(IMPERSONATE_EMAIL)
    return build("calendar", "v3", credentials=credenciales, cache_discovery=False)


def _horario_libre_siguiente(servicio, duracion_minutos: int):
    """Busca el próximo horario libre en el calendario de IMPERSONATE_EMAIL,
    en horario de oficina (14:00-18:00 hora de Lima, de lunes a viernes —
    "la tarde del día siguiente" era el pedido original), empezando mañana
    y avanzando día por día hasta encontrar uno libre. Devuelve
    (inicio_utc, fin_utc) sin tzinfo (naive, como el resto de la app), o
    (None, None) si no encontró nada en 10 días hábiles."""
    ahora_lima = datetime.datetime.utcnow() + _TZ_OFFSET
    dia = ahora_lima.date() + datetime.timedelta(days=1)

    for _ in range(10):
        if dia.weekday() < 5:  # lunes(0)-viernes(4)
            inicio_ventana_lima = datetime.datetime.combine(dia, datetime.time(14, 0))
            fin_ventana_lima = datetime.datetime.combine(dia, datetime.time(18, 0))
            inicio_utc = inicio_ventana_lima - _TZ_OFFSET
            fin_utc = fin_ventana_lima - _TZ_OFFSET

            resultado = servicio.freebusy().query(body={
                "timeMin": inicio_utc.isoformat() + "Z",
                "timeMax": fin_utc.isoformat() + "Z",
                "items": [{"id": IMPERSONATE_EMAIL}],
            }).execute()
            ocupado = resultado.get("calendars", {}).get(IMPERSONATE_EMAIL, {}).get("busy", [])

            def _libre(slot_inicio, slot_fin):
                for b in ocupado:
                    b_inicio = datetime.datetime.fromisoformat(b["start"].replace("Z", "+00:00")).replace(tzinfo=None)
                    b_fin = datetime.datetime.fromisoformat(b["end"].replace("Z", "+00:00")).replace(tzinfo=None)
                    if slot_inicio < b_fin and slot_fin > b_inicio:
                        return False
                return True

            slot = inicio_utc
            paso = datetime.timedelta(minutes=30)
            duracion = datetime.timedelta(minutes=duracion_minutos)
            while slot + duracion <= fin_utc:
                if _libre(slot, slot + duracion):
                    return slot, slot + duracion
                slot += paso
        dia += datetime.timedelta(days=1)
    return None, None


def agendar_reunion(titulo: str, descripcion: str, invitado_email: str = None, duracion_minutos: int = 45):
    """Agenda la reunión y devuelve un dict con inicio/fin en hora de Lima
    (naive) y el link de Meet real, o None si Calendar no está configurado,
    no hay horario libre, o algo falló (nunca lanza — quien llama debe usar
    el link genérico de respaldo si esto devuelve None)."""
    if not calendar_configurado():
        return None
    try:
        servicio = _servicio()
        inicio_utc, fin_utc = _horario_libre_siguiente(servicio, duracion_minutos)
        if not inicio_utc:
            return None

        evento = {
            "summary": titulo,
            "description": descripcion,
            "start": {"dateTime": inicio_utc.isoformat() + "Z", "timeZone": "UTC"},
            "end": {"dateTime": fin_utc.isoformat() + "Z", "timeZone": "UTC"},
            "conferenceData": {
                "createRequest": {
                    "requestId": f"micelio-{int(inicio_utc.timestamp())}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        if invitado_email:
            evento["attendees"] = [{"email": invitado_email}]

        creado = servicio.events().insert(
            calendarId=IMPERSONATE_EMAIL, body=evento, conferenceDataVersion=1, sendUpdates="none",
        ).execute()

        meet_link = creado.get("hangoutLink")
        for punto in creado.get("conferenceData", {}).get("entryPoints", []):
            if punto.get("entryPointType") == "video":
                meet_link = punto.get("uri")
                break
        if not meet_link:
            return None

        inicio_lima = inicio_utc + _TZ_OFFSET
        fin_lima = fin_utc + _TZ_OFFSET
        return {
            "inicio_lima": inicio_lima,
            "fin_lima": fin_lima,
            "fecha_texto": f"{_DIAS_ES[inicio_lima.weekday()]} {inicio_lima.strftime('%d/%m/%Y')}",
            "hora_texto": f"{inicio_lima.strftime('%H:%M')} a {fin_lima.strftime('%H:%M')} (hora de Lima)",
            "meet_link": meet_link,
            "evento_id": creado.get("id"),
        }
    except Exception:
        return None
