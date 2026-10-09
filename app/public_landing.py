# -*- coding: utf-8 -*-
"""Landing pública "Trabaja con Nosotros" (punto 6.1-6.3 del pedido de
Reclutamiento y Selección): lista las vacantes abiertas (Pedidos de Personal
+ datos del Cargo) y permite postular subiendo un CV. Punto del pedido
(16/09): la evaluación inicial del CV la hace personal de RR.HH. mirándolo,
no una calificación automática por IA — ese análisis se quitó por completo.

Sin autenticación — mismo criterio que las rutas públicas /f/{token} de
main.py, pero acá no hace falta token porque no hay datos sensibles del
trabajador todavía, solo la postulación misma."""
import datetime
import os
import uuid
from urllib.parse import quote_plus

from fastapi import APIRouter, BackgroundTasks, Request, Depends, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from .models import PedidoPersonal, LeadCandidato, Cargo, Empresa, TIPOS_DOCUMENTO_POSTULANTE, lead_incompleto
from .rrhh import _pedido_recibio_lead, _notificar_lead_incompleto_n8n, _payload_lead_para_n8n
from .telefono import formato_celular, ERROR_CELULAR

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CV_DIR = os.path.join(BASE_DIR, "cv_postulantes")
os.makedirs(CV_DIR, exist_ok=True)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
router = APIRouter()

CONTACTO_EMAIL = os.environ.get("TRABAJA_CON_NOSOTROS_EMAIL", "trabajaconnosotros@digetelperu.com")
EXTENSIONES_CV_VALIDAS = (".pdf", ".doc", ".docx")
TAMANO_MAXIMO_CV = 20 * 1024 * 1024  # 20 MB — bug del 15/09: un CV de 19MB daba 413 en nginx (ver client_max_body_size)


def _vacantes_abiertas(db: Session):
    pedidos = (
        db.query(PedidoPersonal)
        .filter(PedidoPersonal.estado == "abierto")
        .order_by(PedidoPersonal.created_at.desc())
        .all()
    )
    resultado = []
    for p in pedidos:
        cargo = db.query(Cargo).filter(Cargo.nombre == p.cargo_solicitado, Cargo.activo == True).first()  # noqa: E712
        resultado.append({"pedido": p, "cargo": cargo, "base_nombre": p.base.nombre if p.base else None})
    return resultado


ACTUALIZACION_LEGAL = "9 de octubre de 2026"


def _datos_empresa_legal(db: Session) -> dict:
    """Quién es el responsable de los datos, tomado de Parámetros › Empresas
    (Digetel; si no está, la primera empresa activa con RUC) — así las páginas
    legales nunca quedan desactualizadas respecto a los datos reales."""
    emp = db.query(Empresa).filter(Empresa.nombre == "Digetel").first()
    if not emp or not emp.ruc:
        emp = db.query(Empresa).filter(Empresa.activo == True, Empresa.ruc.isnot(None)).first() or emp  # noqa: E712
    return {
        "razon_social": (emp.razon_social or emp.nombre) if emp else "DIGETEL GROUP",
        "ruc": emp.ruc if emp else None,
        "domicilio": emp.domicilio_fiscal if emp else None,
    }


@router.get("/privacidad", response_class=HTMLResponse)
def pagina_privacidad(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "public_privacidad.html", {
        "empresa": _datos_empresa_legal(db), "contacto_email": CONTACTO_EMAIL, "actualizado": ACTUALIZACION_LEGAL,
    })


@router.get("/terminos", response_class=HTMLResponse)
def pagina_terminos(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "public_terminos.html", {
        "empresa": _datos_empresa_legal(db), "contacto_email": CONTACTO_EMAIL, "actualizado": ACTUALIZACION_LEGAL,
    })


@router.get("/trabaja-con-nosotros", response_class=HTMLResponse)
def landing_vacantes(request: Request, db: Session = Depends(get_db)):
    vacantes = _vacantes_abiertas(db)
    return templates.TemplateResponse(request, "public_vacantes.html", {
        "vacantes": vacantes, "contacto_email": CONTACTO_EMAIL,
    })


@router.get("/trabaja-con-nosotros/{pedido_id}", response_class=HTMLResponse)
def landing_vacante_detalle(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoPersonal).get(pedido_id)
    if not pedido or pedido.estado != "abierto":
        raise HTTPException(404)
    # El match del Cargo es por nombre exacto; si no calza (o el cargo está
    # inactivo) igual se muestra la vacante con los datos del pedido.
    cargo = db.query(Cargo).filter(Cargo.nombre == pedido.cargo_solicitado, Cargo.activo == True).first()  # noqa: E712
    base_nombre = pedido.base.nombre if pedido.base else None
    return templates.TemplateResponse(request, "public_vacante_detalle.html", {
        "pedido": pedido, "cargo": cargo, "base_nombre": base_nombre, "contacto_email": CONTACTO_EMAIL,
        "tipos_documento": TIPOS_DOCUMENTO_POSTULANTE, "error": request.query_params.get("error"),
    })


@router.post("/trabaja-con-nosotros/{pedido_id}/postular")
async def landing_postular(request: Request, background_tasks: BackgroundTasks, pedido_id: int,
                            nombre_completo: str = Form(...),
                            documento_tipo: str = Form(...), documento_numero: str = Form(...),
                            email: str = Form(...), celular: str = Form(...), acepta_privacidad: str = Form(""),
                            cv: UploadFile = File(...), db: Session = Depends(get_db)):
    pedido = db.query(PedidoPersonal).get(pedido_id)
    if not pedido or pedido.estado != "abierto":
        raise HTTPException(404)

    if not acepta_privacidad:
        return RedirectResponse(
            f"/trabaja-con-nosotros/{pedido_id}?error=" + quote_plus(
                "Para postular debes aceptar la Política de Privacidad y el contacto por WhatsApp."),
            status_code=303)

    # El celular es obligatorio y se guarda en formato internacional: es por donde
    # le escribe la automatización de WhatsApp (sin celular el candidato quedaría
    # sin respuesta para siempre).
    celular_norm = formato_celular(celular)
    if not celular_norm:
        return RedirectResponse(f"/trabaja-con-nosotros/{pedido_id}?error=" + quote_plus(ERROR_CELULAR), status_code=303)

    nombre_archivo = cv.filename or ""
    if not nombre_archivo.lower().endswith(EXTENSIONES_CV_VALIDAS):
        return RedirectResponse(
            f"/trabaja-con-nosotros/{pedido_id}?error=Formato+de+archivo+no+valido.+Solo+se+aceptan+PDF+o+Word.",
            status_code=303,
        )
    contenido = await cv.read()
    if len(contenido) > TAMANO_MAXIMO_CV:
        return RedirectResponse(
            f"/trabaja-con-nosotros/{pedido_id}?error=El+archivo+supera+el+tamano+maximo+permitido+(20+MB).",
            status_code=303,
        )

    nombre_seguro = f"{uuid.uuid4().hex[:10]}_{nombre_archivo}"
    ruta = os.path.join(CV_DIR, nombre_seguro)
    with open(ruta, "wb") as f:
        f.write(contenido)

    lead = LeadCandidato(
        pedido_id=pedido.id, nombre_completo=nombre_completo.strip(),
        email=email.strip() or None, celular=celular_norm,
        documento_tipo=documento_tipo or None, documento_numero=documento_numero.strip() or None,
        origen="Trabaja con Nosotros", etapa="nuevo",
        cv_path=ruta, cv_filename=nombre_archivo,
        registrado_por="Landing Trabaja con Nosotros",
        consentimiento_at=datetime.datetime.utcnow(),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    _pedido_recibio_lead(pedido)
    db.commit()
    # Se arma el payload (y se lee lead_incompleto/pedido.empresa) ACÁ, con la
    # sesión todavía abierta — la BackgroundTask ya no puede tocar objetos ORM.
    payload_n8n = _payload_lead_para_n8n(lead, pedido)
    background_tasks.add_task(_notificar_lead_incompleto_n8n, payload_n8n, lead_incompleto(lead))

    return RedirectResponse(f"/trabaja-con-nosotros/{pedido_id}/gracias", status_code=303)


@router.get("/trabaja-con-nosotros/{pedido_id}/gracias", response_class=HTMLResponse)
def landing_gracias(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoPersonal).get(pedido_id)
    if not pedido:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "public_postulacion_ok.html", {
        "pedido": pedido, "contacto_email": CONTACTO_EMAIL,
    })
