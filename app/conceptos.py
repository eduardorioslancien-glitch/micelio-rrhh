# -*- coding: utf-8 -*-
"""Remuneraciones > Conceptos (08/10/2026): catálogo de conceptos de
remuneración. Cada concepto tiene un Nombre, un Tipo de Concepto
(Remunerativo / No Remunerativo), el Código PLAME y el Tipo de Dato (Fijo /
Calculado). Es la lista que se elige en Registro de Pedidos para armar las
condiciones económicas de una vacante."""
import os
import re
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from .models import ConceptoRemunerativo, PedidoPersonal, User, TIPOS_CONCEPTO_PEDIDO, TIPOS_DATO_CONCEPTO
from .auth import require_perm
from .rrhh import _ctx, _a_lima

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.filters["lima"] = _a_lima
router = APIRouter()
RUTA = "/rrhh/remuneraciones/conceptos"
_TIPOS = {k for k, _ in TIPOS_CONCEPTO_PEDIDO}
_DATOS = {k for k, _ in TIPOS_DATO_CONCEPTO}
_PLAME = re.compile(r"^\d{4}$")  # los códigos de concepto del PLAME son de 4 dígitos (p. ej. 0121)


def _volver(error: str = "", ok: str = ""):
    url = RUTA
    if error:
        url += "?error=" + quote(error)
    elif ok:
        url += "?ok=" + quote(ok)
    return RedirectResponse(url, status_code=303)


def _validar(db: Session, nombre: str, tipo: str, codigo: str, tipo_dato: str, excluir_id=None) -> str:
    if not nombre:
        return "Ponle un nombre al concepto."
    if tipo not in _TIPOS:
        return "Elige el tipo de concepto: Remunerativo o No Remunerativo."
    if tipo_dato not in _DATOS:
        return "Elige el tipo de dato: Fijo o Calculado."
    if codigo and not _PLAME.match(codigo):
        return "El código PLAME debe tener 4 dígitos (por ejemplo 0121)."
    q = db.query(ConceptoRemunerativo).filter(ConceptoRemunerativo.nombre.ilike(nombre))
    if excluir_id:
        q = q.filter(ConceptoRemunerativo.id != excluir_id)
    if q.first():
        return "Ya existe un concepto con ese nombre."
    return ""


def _usos(db: Session) -> dict:
    """Cuántos pedidos usan cada concepto (por id guardado, o por nombre en los
    pedidos que lo guardaron sin id)."""
    conteo = {}
    for (conceptos,) in db.query(PedidoPersonal.conceptos).filter(PedidoPersonal.conceptos.isnot(None)).all():
        for c in (conceptos or []):
            clave = c.get("concepto_id") or (c.get("concepto") or "").casefold()
            conteo[clave] = conteo.get(clave, 0) + 1
    return conteo


@router.get(RUTA, response_class=HTMLResponse)
def conceptos_list(request: Request, error: str = "", ok: str = "", db: Session = Depends(get_db),
                   user: User = Depends(require_perm("conceptos", "ver"))):
    conceptos = db.query(ConceptoRemunerativo).order_by(ConceptoRemunerativo.nombre).all()
    usos = _usos(db)
    en_uso = {c.id: usos.get(c.id, 0) + usos.get(c.nombre.casefold(), 0) for c in conceptos}
    return templates.TemplateResponse(request, "rrhh_conceptos.html", _ctx(
        request, user, conceptos=conceptos, en_uso=en_uso, tipos=TIPOS_CONCEPTO_PEDIDO, tipos_dato=TIPOS_DATO_CONCEPTO,
        error=error, ok=ok, active="conceptos",
    ))


@router.post(RUTA)
def concepto_crear(nombre: str = Form(""), tipo: str = Form(""), codigo_plame: str = Form(""), tipo_dato: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require_perm("conceptos", "editar"))):
    nombre, codigo_plame = nombre.strip(), codigo_plame.strip()
    error = _validar(db, nombre, tipo, codigo_plame, tipo_dato)
    if error:
        return _volver(error=error)
    db.add(ConceptoRemunerativo(nombre=nombre, tipo=tipo, codigo_plame=codigo_plame or None, tipo_dato=tipo_dato, activo=True))
    db.commit()
    return _volver(ok=f"Concepto «{nombre}» creado.")


@router.post(RUTA + "/{concepto_id}/editar")
def concepto_editar(concepto_id: int, nombre: str = Form(""), tipo: str = Form(""), codigo_plame: str = Form(""),
                    tipo_dato: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(require_perm("conceptos", "editar"))):
    c = db.query(ConceptoRemunerativo).get(concepto_id)
    if not c:
        return _volver(error="Ese concepto ya no existe.")
    nombre, codigo_plame = nombre.strip(), codigo_plame.strip()
    error = _validar(db, nombre, tipo, codigo_plame, tipo_dato, concepto_id)
    if error:
        return _volver(error=error)
    c.nombre, c.tipo, c.codigo_plame, c.tipo_dato = nombre, tipo, codigo_plame or None, tipo_dato
    db.commit()
    return _volver(ok=f"Concepto «{nombre}» actualizado.")


@router.post(RUTA + "/{concepto_id}/toggle")
def concepto_toggle(concepto_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_perm("conceptos", "editar"))):
    c = db.query(ConceptoRemunerativo).get(concepto_id)
    if c:
        c.activo = not c.activo
        db.commit()
    return _volver()


@router.post(RUTA + "/{concepto_id}/eliminar")
def concepto_eliminar(concepto_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_perm("conceptos", "editar"))):
    c = db.query(ConceptoRemunerativo).get(concepto_id)
    if not c:
        return _volver()
    usos = _usos(db)
    if usos.get(c.id, 0) + usos.get(c.nombre.casefold(), 0):
        return _volver(error="Este concepto ya se usa en pedidos de personal: desactívalo en vez de eliminarlo.")
    db.delete(c)
    db.commit()
    return _volver(ok="Concepto eliminado.")
