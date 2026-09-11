# -*- coding: utf-8 -*-
"""API de ingesta externa de Leads — para centralizar en MICELIO a los
candidatos que llegan por canales fuera de "Trabaja con Nosotros" (hoy:
el correo trabajaconnosotros@digetelperu.com vía n8n; a futuro, WhatsApp
Business API con el mismo endpoint).

No usa sesión de usuario (quien llama es un sistema externo, no una
persona logueada) — se protege con una clave compartida en el header
`X-API-Key`, comparada contra la variable de entorno LEADS_API_KEY. Si
esa variable no está configurada, el endpoint rechaza todo (fail-closed),
igual criterio que ANTHROPIC_API_KEY para el análisis de CV.
"""
import os
import uuid

from fastapi import APIRouter, Depends, Form, File, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .database import get_db
from .models import Cargo, LeadCandidato, PedidoPersonal
from .cv_analysis import analizar_cv, extraer_texto_cv
from .public_landing import CV_DIR, EXTENSIONES_CV_VALIDAS, TAMANO_MAXIMO_CV

router = APIRouter()


def _verificar_api_key(x_api_key: str = Header(None)):
    clave = os.environ.get("LEADS_API_KEY")
    if not clave:
        raise HTTPException(503, "LEADS_API_KEY no está configurada en el servidor.")
    if x_api_key != clave:
        raise HTTPException(401, "API key inválida.")


@router.get("/api/pedidos-abiertos")
def api_pedidos_abiertos(db: Session = Depends(get_db), _=Depends(_verificar_api_key)):
    """Pedidos abiertos/en proceso, para que la automatización externa (n8n)
    pueda saber a qué código de pedido corresponde un correo/mensaje y
    enviarlo en la creación del lead."""
    pedidos = (
        db.query(PedidoPersonal)
        .filter(PedidoPersonal.estado.in_(["abierto", "en_proceso"]))
        .order_by(PedidoPersonal.created_at.desc())
        .all()
    )
    return [
        {
            "codigo": p.codigo,
            "cargo": p.cargo_solicitado,
            "empresa": p.empresa.nombre if p.empresa else None,
            "base": p.base.nombre if p.base else None,
            "area": p.area,
            "cantidad": p.cantidad,
            "estado": p.estado,
        }
        for p in pedidos
    ]


@router.post("/api/leads")
async def api_crear_lead(
    nombre_completo: str = Form(...),
    email: str = Form(""),
    celular: str = Form(""),
    origen: str = Form("Correo (trabajaconnosotros@digetelperu.com)"),
    codigo_pedido: str = Form(""),
    notas: str = Form(""),
    cv: UploadFile = File(None),
    db: Session = Depends(get_db),
    _=Depends(_verificar_api_key),
):
    """Crea un Lead en Gestión de Leads desde una automatización externa
    (n8n leyendo el correo de trabajaconnosotros@digetelperu.com, y a
    futuro un webhook de WhatsApp). Si viene con CV y se pudo asociar a un
    pedido con Cargo definido, corre la calificación de IA de una vez —
    igual que una postulación por Trabaja con Nosotros."""
    pedido = None
    if codigo_pedido.strip():
        pedido = db.query(PedidoPersonal).filter(PedidoPersonal.codigo == codigo_pedido.strip()).first()

    lead = LeadCandidato(
        nombre_completo=nombre_completo.strip(),
        email=email.strip() or None,
        celular=celular.strip() or None,
        origen=origen.strip() or None,
        pedido_id=pedido.id if pedido else None,
        notas=notas.strip() or None,
        registrado_por="Automatización externa (n8n)",
    )

    nombre_archivo = (cv.filename or "") if cv else ""
    if nombre_archivo:
        if not nombre_archivo.lower().endswith(EXTENSIONES_CV_VALIDAS):
            raise HTTPException(400, "Formato de CV no válido. Solo se acepta PDF o Word (.pdf, .doc, .docx).")
        contenido = await cv.read()
        if len(contenido) > TAMANO_MAXIMO_CV:
            raise HTTPException(400, "El CV supera el tamaño máximo permitido (8 MB).")
        nombre_seguro = f"{uuid.uuid4().hex[:10]}_{nombre_archivo}"
        ruta = os.path.join(CV_DIR, nombre_seguro)
        with open(ruta, "wb") as f:
            f.write(contenido)
        lead.cv_path = ruta
        lead.cv_filename = nombre_archivo

    db.add(lead)
    db.commit()
    db.refresh(lead)

    resultado = {
        "id": lead.id, "nombre_completo": lead.nombre_completo,
        "pedido_id": lead.pedido_id, "cv_adjunto": bool(lead.cv_path),
        "estrellas": None,
    }

    if lead.cv_path and pedido:
        cargo = db.query(Cargo).filter(Cargo.nombre == pedido.cargo_solicitado).first()
        if cargo:
            texto_cv = extraer_texto_cv(lead.cv_path, cv.content_type if cv else None)
            estrellas, analisis = analizar_cv(texto_cv, cargo)
            lead.estrellas = estrellas
            lead.analisis_ia = analisis
            db.commit()
            resultado["estrellas"] = estrellas

    return resultado
