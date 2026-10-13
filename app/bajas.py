# -*- coding: utf-8 -*-
"""BAJA automática de candidatos (13/10/2026).

Quien escribe «BAJA» por WhatsApp pide que se eliminen sus datos y que no se le
vuelva a escribir. n8n llama a POST /api/leads/{id}/baja y acá se resuelve:

- Si el candidato todavía está en la etapa de leads, se borra TODO lo suyo:
  el lead, su CV (archivo), la conversación de WhatsApp y cualquier registro
  del Historial de Descartados con el mismo celular o correo. De su número
  solo queda un hash SHA-256 en `bajas_contacto` (nunca el número) para que la
  automatización no le vuelva a escribir ni le cree otro lead.
- Si ya está en Selección/Oferta (hay un legajo en proceso) NO se borra sola:
  se marca `baja_solicitada_at` y RR.HH. decide a mano, porque puede haber
  documentación laboral que la ley obliga a conservar.
"""
import hashlib
import os
import re
import zipfile
import html

from sqlalchemy.orm import Session

from .models import BajaContacto, HistorialDescarte, LeadCandidato, LlamadaN8n
from .telefono import solo_digitos_whatsapp

ETAPAS_PROTEGIDAS = ("oferta", "contratado")


def _digitos(celular) -> str:
    texto = celular or ""
    return solo_digitos_whatsapp(texto) or "".join(ch for ch in texto if ch.isdigit())


def hash_celular(celular) -> str | None:
    d = _digitos(celular)
    return hashlib.sha256(d.encode("utf-8")).hexdigest() if d else None


def esta_en_bajas(db: Session, celular) -> bool:
    h = hash_celular(celular)
    return bool(h and db.query(BajaContacto).filter(BajaContacto.celular_hash == h).first())


def quitar_de_bajas(db: Session, celular) -> None:
    """Quien postula de nuevo por su cuenta (con consentimiento nuevo) vuelve a ser contactable."""
    h = hash_celular(celular)
    if h:
        db.query(BajaContacto).filter(BajaContacto.celular_hash == h).delete(synchronize_session=False)


def _borrar_archivo(ruta) -> None:
    try:
        if ruta and os.path.isfile(ruta):
            os.remove(ruta)
    except OSError:
        pass


def ejecutar_baja(db: Session, lead: LeadCandidato) -> dict:
    import datetime
    if lead.etapa in ETAPAS_PROTEGIDAS:
        lead.baja_solicitada_at = datetime.datetime.utcnow()
        db.commit()
        return {"eliminado": False, "motivo": "en_seleccion", "mensaje":
                "El candidato ya está en Selección: RR.HH. debe resolver su baja a mano."}

    celular_d = _digitos(lead.celular)
    email = (lead.email or "").strip().lower()
    hermanos = []
    for otro in db.query(LeadCandidato).filter(LeadCandidato.id != lead.id).all():
        mismo_cel = bool(celular_d) and _digitos(otro.celular) == celular_d
        mismo_mail = bool(email) and (otro.email or "").strip().lower() == email
        if mismo_cel or mismo_mail:
            hermanos.append(otro)

    ids, borrados, protegidos = [], 0, 0
    for l in [lead] + hermanos:
        if l.etapa in ETAPAS_PROTEGIDAS:
            l.baja_solicitada_at = datetime.datetime.utcnow()
            protegidos += 1
            continue
        _borrar_archivo(l.cv_path)
        ids.append(l.id)
        db.delete(l)
        borrados += 1

    historial = 0
    for h in db.query(HistorialDescarte).all():
        mismo_cel = bool(celular_d) and _digitos(h.celular) == celular_d
        mismo_mail = bool(email) and (h.email or "").strip().lower() == email
        if mismo_cel or mismo_mail:
            db.delete(h)
            historial += 1

    if ids:
        db.query(LlamadaN8n).filter(LlamadaN8n.lead_id.in_(ids)).update({LlamadaN8n.lead_id: None}, synchronize_session=False)
    hsh = hash_celular(lead.celular)
    if hsh and not db.query(BajaContacto).filter(BajaContacto.celular_hash == hsh).first():
        db.add(BajaContacto(celular_hash=hsh))
    db.commit()
    return {"eliminado": True, "leads_borrados": borrados, "historial_borrado": historial,
            "leads_protegidos": protegidos}


# ---------------------------------------------------------------- texto del CV

_MAX_TEXTO = 20000


def extraer_texto_cv(ruta: str) -> str:
    """Texto plano de un CV .docx (sin dependencias) o .pdf con texto (pypdf).
    Un PDF escaneado devuelve vacío: n8n lo manda a la IA como archivo."""
    nombre = (ruta or "").lower()
    texto = ""
    try:
        if nombre.endswith(".docx"):
            with zipfile.ZipFile(ruta) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            xml = re.sub(r"<w:tab/>", "\t", xml)
            xml = re.sub(r"<[^>]+>", "", xml)
            texto = html.unescape(xml)
        elif nombre.endswith(".pdf"):
            from pypdf import PdfReader
            texto = "\n".join((p.extract_text() or "") for p in PdfReader(ruta).pages)
    except Exception:
        texto = ""
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
    return texto[:_MAX_TEXTO]
