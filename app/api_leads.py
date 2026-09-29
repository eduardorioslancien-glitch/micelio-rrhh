# -*- coding: utf-8 -*-
"""API de ingesta externa de Leads — para centralizar en MICELIO a los
candidatos que llegan por canales fuera de "Trabaja con Nosotros" (hoy: el
correo trabajaconnosotros@digetelperu.com y, desde el 21/09, la
automatización de Reclutamiento y Selección en n8n: conversación de
WhatsApp para completar datos, calificación de CV por IA, y agendar/
descartar — ver app/n8n.py para el sentido inverso, MICELIO llamando a n8n).

No usa sesión de usuario (quien llama es un sistema externo, no una
persona logueada) — se protege con una clave compartida en el header
`X-API-Key`, comparada contra la variable de entorno LEADS_API_KEY. Si
esa variable no está configurada, el endpoint rechaza todo (fail-closed),
igual criterio que N8N_WEBHOOK_SECRET para las llamadas en el otro sentido.
"""
import datetime
import os
import uuid

from fastapi import APIRouter, Depends, Form, File, Header, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from .database import get_db
from .models import Cargo, LeadCandidato, PedidoPersonal, LEAD_NOMBRE_PENDIENTE, lead_incompleto
from .public_landing import CV_DIR, EXTENSIONES_CV_VALIDAS, TAMANO_MAXIMO_CV
from .rrhh import _pedido_recibio_lead

router = APIRouter()


def _verificar_api_key(x_api_key: str = Header(None)):
    clave = os.environ.get("LEADS_API_KEY")
    if not clave:
        raise HTTPException(503, "LEADS_API_KEY no está configurada en el servidor.")
    if x_api_key != clave:
        raise HTTPException(401, "API key inválida.")


@router.get("/api/pedidos-abiertos")
def api_pedidos_abiertos(db: Session = Depends(get_db), _=Depends(_verificar_api_key)):
    """Pedidos abiertos, para que la automatización externa (n8n) pueda
    saber a qué código de pedido corresponde un correo/mensaje y enviarlo
    en la creación del lead."""
    pedidos = (
        db.query(PedidoPersonal)
        .filter(PedidoPersonal.estado == "abierto")
        .order_by(PedidoPersonal.created_at.desc())
        .all()
    )
    return [
        {
            "codigo": p.codigo,
            "cargo": p.cargo_solicitado,
            "empresa": p.empresa.nombre if p.empresa else None,
            "base": p.base.nombre if p.base else None,
            # Distritos que cubre la base del pedido (28/09) — para que la IA
            # compare contra el distrito que le pregunte al candidato y avise
            # si vive dentro de la zona. Vacío si el pedido no tiene base.
            "base_distritos": (p.base.distritos or []) if p.base else [],
            "area": p.area,
            "cantidad": p.cantidad,
            "estado": p.estado,
        }
        for p in pedidos
    ]


def _guardar_cv_lead(lead: LeadCandidato, nombre_archivo: str, contenido: bytes) -> None:
    if not nombre_archivo.lower().endswith(EXTENSIONES_CV_VALIDAS):
        raise HTTPException(400, "Formato de CV no válido. Solo se acepta PDF o Word (.pdf, .doc, .docx).")
    if len(contenido) > TAMANO_MAXIMO_CV:
        raise HTTPException(400, "El CV supera el tamaño máximo permitido (20 MB).")
    nombre_seguro = f"{uuid.uuid4().hex[:10]}_{nombre_archivo}"
    ruta = os.path.join(CV_DIR, nombre_seguro)
    with open(ruta, "wb") as f:
        f.write(contenido)
    lead.cv_path = ruta
    lead.cv_filename = nombre_archivo


@router.post("/api/leads")
async def api_crear_lead(
    nombre_completo: str = Form(""),
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
    (n8n leyendo el correo de trabajaconnosotros@digetelperu.com, o el
    primer contacto de WhatsApp de la automatización de Reclutamiento y
    Selección). `nombre_completo` es opcional a propósito: si un lead entra
    por WhatsApp y todavía no se sabe el nombre, se crea igual (con el
    sentinel LEAD_NOMBRE_PENDIENTE) y n8n lo completa después con
    PATCH /api/leads/{id} a medida que avanza la conversación — así RR.HH.
    ya lo ve en Gestión de Leads, marcado "incompleto", desde el primer
    contacto."""
    pedido = None
    if codigo_pedido.strip():
        pedido = db.query(PedidoPersonal).filter(PedidoPersonal.codigo == codigo_pedido.strip()).first()

    lead = LeadCandidato(
        nombre_completo=nombre_completo.strip() or LEAD_NOMBRE_PENDIENTE,
        email=email.strip() or None,
        celular=celular.strip() or None,
        origen=origen.strip() or None,
        pedido_id=pedido.id if pedido else None,
        notas=notas.strip() or None,
        registrado_por="Automatización externa (n8n)",
    )

    nombre_archivo = (cv.filename or "") if cv else ""
    if nombre_archivo:
        contenido = await cv.read()
        _guardar_cv_lead(lead, nombre_archivo, contenido)

    db.add(lead)
    db.commit()
    db.refresh(lead)

    _pedido_recibio_lead(pedido)
    db.commit()

    return {
        "id": lead.id, "nombre_completo": lead.nombre_completo,
        "pedido_id": lead.pedido_id, "cv_adjunto": bool(lead.cv_path),
        "incompleto": lead_incompleto(lead),
    }


@router.patch("/api/leads/{lead_id}")
async def api_actualizar_lead(lead_id: int, request: Request, db: Session = Depends(get_db),
                               _=Depends(_verificar_api_key)):
    """n8n va completando acá los datos que obtiene en la conversación de
    WhatsApp (nombre, correo, celular, distrito) — solo actualiza los campos
    que vengan en el body JSON, deja el resto tal cual. Campos aceptados:
    nombre_completo, email, celular, documento_tipo, documento_numero,
    distrito, notas. El CV se sube aparte, con POST /api/leads/{id}/cv
    (multipart), no acá.

    `codigo_pedido` (28/09) es aparte: liga el lead a la vacante recién
    elegida cuando alguien escribió primero por WhatsApp sin venir de una
    postulación puntual (n8n le pregunta "a qué postulación va" con la
    lista de GET /api/pedidos-abiertos). Si el código no existe, se ignora
    en silencio — no rompe la conversación por un typo del lado de n8n."""
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404, "Lead no encontrado.")
    payload = await request.json()
    for campo in ("nombre_completo", "email", "celular", "documento_tipo", "documento_numero", "distrito", "notas"):
        if campo in payload and (payload[campo] or "").strip():
            setattr(lead, campo, payload[campo].strip())
    codigo_pedido = (payload.get("codigo_pedido") or "").strip()
    if codigo_pedido and not lead.pedido_id:
        pedido = db.query(PedidoPersonal).filter(PedidoPersonal.codigo == codigo_pedido).first()
        if pedido:
            lead.pedido_id = pedido.id
    db.commit()
    db.refresh(lead)
    return {"id": lead.id, "nombre_completo": lead.nombre_completo, "incompleto": lead_incompleto(lead)}


@router.post("/api/leads/{lead_id}/cv")
async def api_subir_cv_lead(lead_id: int, cv: UploadFile = File(...), db: Session = Depends(get_db),
                             _=Depends(_verificar_api_key)):
    """Sube (o reemplaza) el CV de un lead ya creado — para cuando llega
    por WhatsApp después del primer contacto, en vez de junto con el resto
    de los datos en POST /api/leads."""
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404, "Lead no encontrado.")
    contenido = await cv.read()
    _guardar_cv_lead(lead, cv.filename or "cv", contenido)
    db.commit()
    return {"id": lead.id, "cv_adjunto": True, "incompleto": lead_incompleto(lead)}


@router.post("/api/leads/{lead_id}/conversacion")
async def api_agregar_mensaje_conversacion(lead_id: int, request: Request, db: Session = Depends(get_db),
                                            _=Depends(_verificar_api_key)):
    """n8n deja acá cada mensaje de la conversación de WhatsApp (en ambos
    sentidos) como sustento del proceso — body JSON:
    {"rol": "sistema"|"candidato", "texto": "...", "ts": "2026-09-21T10:00:00"}
    (`ts` es opcional; si no viene, se usa la hora del servidor)."""
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404, "Lead no encontrado.")
    payload = await request.json()
    rol = (payload.get("rol") or "").strip()
    texto = (payload.get("texto") or "").strip()
    if rol not in ("sistema", "candidato") or not texto:
        raise HTTPException(400, "Se requiere 'rol' ('sistema' o 'candidato') y 'texto'.")
    conversacion = list(lead.conversacion_whatsapp or [])
    conversacion.append({
        "rol": rol, "texto": texto,
        "ts": payload.get("ts") or datetime.datetime.utcnow().isoformat(),
    })
    lead.conversacion_whatsapp = conversacion
    db.commit()
    return {"id": lead.id, "mensajes": len(conversacion)}


@router.get("/api/cargos/{nombre}/requisitos")
def api_cargo_requisitos(nombre: str, db: Session = Depends(get_db), _=Depends(_verificar_api_key)):
    """Expone el MOF del Cargo (descripción, funciones, responsabilidades,
    requisitos académicos/experiencia/conocimientos y las competencias
    exigidas con su nivel 1-4) para que n8n arme el prompt de la IA que
    califica el CV — punto 3.2 del pedido de automatización RyS (21/09).
    MICELIO nunca llama a la IA; solo entrega estos datos."""
    cargo = db.query(Cargo).filter(Cargo.nombre == nombre, Cargo.activo == True).first()  # noqa: E712
    if not cargo:
        raise HTTPException(404, "Cargo no encontrado o inactivo.")
    return {
        "nombre": cargo.nombre,
        "descripcion": cargo.descripcion,
        "funciones": cargo.funciones or [],
        "responsabilidades": cargo.responsabilidades or [],
        "requisito_academico": cargo.requisito_academico,
        "requisito_experiencia": cargo.requisito_experiencia,
        "requisito_conocimientos": cargo.requisito_conocimientos,
        "competencias": [
            {"nombre": r.competencia.nombre, "nivel_requerido": r.nivel_requerido}
            for r in cargo.requisitos_competencias
        ],
    }


@router.post("/api/leads/{lead_id}/analisis-ia")
async def api_guardar_analisis_ia(lead_id: int, request: Request, db: Session = Depends(get_db),
                                   _=Depends(_verificar_api_key)):
    """n8n empuja acá el resultado de calificar el CV contra los requisitos
    del Cargo — punto 3.2 del pedido de automatización RyS (21/09). Body
    JSON: {"estrellas": 1-5 (admite un decimal, ej. 3.8), "resumen": "texto
    explicando la calificación"}. Este resumen es lo que se muestra en
    Gestión de Leads antes de los botones Entrevistar/Descartar."""
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404, "Lead no encontrado.")
    payload = await request.json()
    estrellas = payload.get("estrellas")
    if estrellas is not None:
        try:
            estrellas = round(float(estrellas), 1)
        except (TypeError, ValueError):
            raise HTTPException(400, "'estrellas' debe ser un número de 1 a 5 (admite un decimal).")
        if not (1 <= estrellas <= 5):
            raise HTTPException(400, "'estrellas' debe estar entre 1 y 5.")
        lead.estrellas = estrellas
    if payload.get("resumen"):
        lead.analisis_ia = str(payload["resumen"]).strip()
    db.commit()
    return {"id": lead.id, "estrellas": lead.estrellas}
