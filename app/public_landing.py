# -*- coding: utf-8 -*-
"""Landing pública "Trabaja con Nosotros" (punto 6.1-6.3 del pedido de
Reclutamiento y Selección): lista las vacantes abiertas (Pedidos de Personal
+ datos del Cargo), permite postular subiendo un CV, y ese CV se analiza
automáticamente (app/cv_analysis.py) contra los requisitos del cargo.

Sin autenticación — mismo criterio que las rutas públicas /f/{token} de
main.py, pero acá no hace falta token porque no hay datos sensibles del
trabajador todavía, solo la postulación misma."""
import os
import uuid

from fastapi import APIRouter, Request, Depends, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from .models import PedidoPersonal, LeadCandidato, Cargo, TIPOS_DOCUMENTO_POSTULANTE
from .cv_analysis import extraer_texto_cv, analizar_cv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CV_DIR = os.path.join(BASE_DIR, "cv_postulantes")
os.makedirs(CV_DIR, exist_ok=True)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
router = APIRouter()

CONTACTO_EMAIL = os.environ.get("TRABAJA_CON_NOSOTROS_EMAIL", "erios@digetelgroup.com")
EXTENSIONES_CV_VALIDAS = (".pdf", ".doc", ".docx")
TAMANO_MAXIMO_CV = 8 * 1024 * 1024  # 8 MB


def _vacantes_abiertas(db: Session):
    pedidos = (
        db.query(PedidoPersonal)
        .filter(PedidoPersonal.estado.in_(["abierto", "en_proceso"]))
        .order_by(PedidoPersonal.created_at.desc())
        .all()
    )
    resultado = []
    for p in pedidos:
        cargo = db.query(Cargo).filter(Cargo.nombre == p.cargo_solicitado, Cargo.activo == True).first()  # noqa: E712
        resultado.append({"pedido": p, "cargo": cargo})
    return resultado


@router.get("/trabaja-con-nosotros", response_class=HTMLResponse)
def landing_vacantes(request: Request, db: Session = Depends(get_db)):
    vacantes = _vacantes_abiertas(db)
    return templates.TemplateResponse(request, "public_vacantes.html", {
        "vacantes": vacantes, "contacto_email": CONTACTO_EMAIL,
    })


@router.get("/trabaja-con-nosotros/{pedido_id}", response_class=HTMLResponse)
def landing_vacante_detalle(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoPersonal).get(pedido_id)
    if not pedido or pedido.estado not in ("abierto", "en_proceso"):
        raise HTTPException(404)
    cargo = db.query(Cargo).filter(Cargo.nombre == pedido.cargo_solicitado, Cargo.activo == True).first()  # noqa: E712
    return templates.TemplateResponse(request, "public_vacante_detalle.html", {
        "pedido": pedido, "cargo": cargo, "contacto_email": CONTACTO_EMAIL,
        "tipos_documento": TIPOS_DOCUMENTO_POSTULANTE, "error": request.query_params.get("error"),
    })


@router.post("/trabaja-con-nosotros/{pedido_id}/postular")
async def landing_postular(request: Request, pedido_id: int, nombre_completo: str = Form(...),
                            documento_tipo: str = Form(...), documento_numero: str = Form(...),
                            email: str = Form(...), celular: str = Form(""),
                            cv: UploadFile = File(...), db: Session = Depends(get_db)):
    pedido = db.query(PedidoPersonal).get(pedido_id)
    if not pedido or pedido.estado not in ("abierto", "en_proceso"):
        raise HTTPException(404)

    nombre_archivo = cv.filename or ""
    if not nombre_archivo.lower().endswith(EXTENSIONES_CV_VALIDAS):
        return RedirectResponse(
            f"/trabaja-con-nosotros/{pedido_id}?error=Formato+de+archivo+no+valido.+Solo+se+aceptan+PDF+o+Word.",
            status_code=303,
        )
    contenido = await cv.read()
    if len(contenido) > TAMANO_MAXIMO_CV:
        return RedirectResponse(
            f"/trabaja-con-nosotros/{pedido_id}?error=El+archivo+supera+el+tamano+maximo+permitido+(8+MB).",
            status_code=303,
        )

    nombre_seguro = f"{uuid.uuid4().hex[:10]}_{nombre_archivo}"
    ruta = os.path.join(CV_DIR, nombre_seguro)
    with open(ruta, "wb") as f:
        f.write(contenido)

    lead = LeadCandidato(
        pedido_id=pedido.id, nombre_completo=nombre_completo.strip(),
        email=email.strip() or None, celular=celular.strip() or None,
        documento_tipo=documento_tipo or None, documento_numero=documento_numero.strip() or None,
        origen="Trabaja con Nosotros", etapa="nuevo",
        cv_path=ruta, cv_filename=nombre_archivo,
        registrado_por="Landing Trabaja con Nosotros",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    cargo = db.query(Cargo).filter(Cargo.nombre == pedido.cargo_solicitado).first()
    if cargo:
        texto_cv = extraer_texto_cv(ruta, cv.content_type)
        estrellas, analisis = analizar_cv(texto_cv, cargo)
        lead.estrellas = estrellas
        lead.analisis_ia = analisis
        db.commit()

    return RedirectResponse(f"/trabaja-con-nosotros/{pedido_id}/gracias", status_code=303)


@router.get("/trabaja-con-nosotros/{pedido_id}/gracias", response_class=HTMLResponse)
def landing_gracias(request: Request, pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(PedidoPersonal).get(pedido_id)
    if not pedido:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "public_postulacion_ok.html", {
        "pedido": pedido, "contacto_email": CONTACTO_EMAIL,
    })
