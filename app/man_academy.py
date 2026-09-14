# -*- coding: utf-8 -*-
"""Integración con Man Academy (proyecto/hosting aparte, ver rrhh.py:
MAN_ACADEMY_URL): dos formas de entrar, según lo que definió Eduardo el
2026-09-14.

  1. Desde MICELIO ("Capacitación > Man Academy" en el sidebar, o el botón
     dentro de la propia ficha): el trabajador entra sin loguearse de nuevo.
     MICELIO firma un token con MAN_ACADEMY_SSO_SECRET diciéndole a Man
     Academy quién es y a qué cursos/rutas puede acceder (ver
     ManAcademyAcceso, asignados a mano por un administrador en la ficha del
     trabajador). Man Academy verifica la firma en su propio sso.php.

  2. Directo en Man Academy (URL + usuario/clave): para gente que NO
     necesariamente es trabajador de DIGETEL GROUP. Ese flujo vive
     enteramente del lado de Man Academy (admin/access.php allá) — acá no
     hay nada que hacer.

El secreto (MAN_ACADEMY_SSO_SECRET) y la URL (MAN_ACADEMY_URL) deben ser
EXACTAMENTE los mismos valores configurados del lado de Man Academy
(includes/config.local.php o variables de entorno allá).
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from .database import get_db
from .models import Employee, ManAcademyAcceso, ManAcademyCatalogItem
from .auth import require_login, require_role
from .rrhh import MAN_ACADEMY_URL

router = APIRouter()

MAN_ACADEMY_SSO_SECRET = os.environ.get("MAN_ACADEMY_SSO_SECRET", "")
SSO_TOKEN_TTL_SECONDS = 120  # el enlace de ingreso vale por 2 minutos


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _build_sso_token(payload: dict) -> str:
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)
    sig = hmac.new(MAN_ACADEMY_SSO_SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def _employee_email(emp: Employee, fallback_username: str) -> str:
    """El SSO necesita algo con forma de correo para identificar al usuario
    en Man Academy. Se prioriza el correo real del trabajador; si no tiene
    ninguno cargado, se arma uno sintético (nunca se envía nada a esa
    dirección, solo sirve de identificador único)."""
    if emp and emp.email and "@" in emp.email:
        return emp.email
    if emp and emp.ficha_data:
        correo = (emp.ficha_data or {}).get("correo_personal") or ""
        if "@" in correo:
            return correo
    return f"{fallback_username}@micelio.local"


def _config_error_page(request: Request) -> HTMLResponse:
    return HTMLResponse(
        "<div style='font-family:sans-serif;max-width:520px;margin:80px auto;text-align:center'>"
        "<h3>Man Academy no está configurado todavía</h3>"
        "<p>Falta definir <code>MAN_ACADEMY_URL</code> y/o <code>MAN_ACADEMY_SSO_SECRET</code> "
        "en el servidor. Avisa al administrador del sistema.</p>"
        "<p><a href='/rrhh'>&larr; Volver a MICELIO</a></p></div>",
        status_code=503,
    )


@router.get("/rrhh/man-academy/entrar")
def man_academy_entrar(request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    """Botón "Man Academy" del sidebar / de la ficha propia: entra sin pedir
    clave, con exactamente los cursos/rutas que le asignó un administrador."""
    if not MAN_ACADEMY_URL or not MAN_ACADEMY_SSO_SECRET:
        return _config_error_page(request)

    emp = db.query(Employee).get(user.employee_id) if user.employee_id else None

    payload = {
        "email": _employee_email(emp, user.username),
        "name": (emp.nombre_completo if emp else user.nombre_completo),
        "external_id": f"MICELIO-{emp.id}" if emp else f"MICELIO-user-{user.id}",
        "role": "admin" if user.rol == "administrador" else "employee",
        "exp": int(time.time()) + SSO_TOKEN_TTL_SECONDS,
    }
    if emp:
        payload["contrata"] = emp.empresa or None

    if user.rol != "administrador":
        accesos = db.query(ManAcademyAcceso).filter(ManAcademyAcceso.employee_id == (emp.id if emp else -1)).all()
        payload["course_ids"] = [a.man_id for a in accesos if a.tipo == "curso"]
        payload["path_ids"] = [a.man_id for a in accesos if a.tipo == "ruta"]

    token = _build_sso_token(payload)
    return RedirectResponse(f"{MAN_ACADEMY_URL.rstrip('/')}/sso.php?token={token}")


@router.post("/rrhh/man-academy/sincronizar")
def man_academy_sincronizar(request: Request, redirect_to: str = Form("/rrhh"),
                             db: Session = Depends(get_db), user=Depends(require_role("administrador"))):
    """Trae de Man Academy la lista actual de cursos/rutas activos (llamada
    servidor-a-servidor a api_catalog.php) y refresca la caché local
    (ManAcademyCatalogItem) que alimenta el selector de accesos por
    trabajador. No toca accesos ya asignados."""
    if not MAN_ACADEMY_URL or not MAN_ACADEMY_SSO_SECRET:
        return _config_error_page(request)

    url = f"{MAN_ACADEMY_URL.rstrip('/')}/api_catalog.php?secret={MAN_ACADEMY_SSO_SECRET}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise HTTPException(502, f"No se pudo sincronizar con Man Academy: {exc}")

    vistos = set()
    for c in data.get("courses", []):
        vistos.add(("curso", int(c["id"])))
        _upsert_catalogo(db, "curso", int(c["id"]), c.get("title", ""), c.get("category"))
    for p in data.get("learning_paths", []):
        vistos.add(("ruta", int(p["id"])))
        _upsert_catalogo(db, "ruta", int(p["id"]), p.get("title", ""), None)

    # Quitar del catálogo local lo que ya no está activo en Man Academy (no
    # borra accesos ya otorgados — solo dejan de aparecer como opción nueva).
    for item in db.query(ManAcademyCatalogItem).all():
        if (item.tipo, item.man_id) not in vistos:
            db.delete(item)
    db.commit()

    return RedirectResponse(redirect_to, status_code=303)


def _upsert_catalogo(db: Session, tipo: str, man_id: int, titulo: str, categoria):
    item = db.query(ManAcademyCatalogItem).filter(
        ManAcademyCatalogItem.tipo == tipo, ManAcademyCatalogItem.man_id == man_id
    ).first()
    if item:
        item.titulo = titulo
        item.categoria = categoria
    else:
        db.add(ManAcademyCatalogItem(tipo=tipo, man_id=man_id, titulo=titulo, categoria=categoria))


@router.post("/rrhh/personal/{employee_id}/man-academy")
def man_academy_guardar_accesos(employee_id: int,
                                 curso_ids: list[str] = Form([]),
                                 ruta_ids: list[str] = Form([]),
                                 db: Session = Depends(get_db), user=Depends(require_role("administrador"))):
    """Guarda, en un solo golpe, a qué cursos y rutas de Man Academy puede
    entrar este trabajador (checkboxes de la pestaña "Man Academy" en su
    ficha). Reemplaza por completo la lista anterior — igual que hace Man
    Academy con lo que le llega en el token SSO."""
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)

    db.query(ManAcademyAcceso).filter(ManAcademyAcceso.employee_id == employee_id).delete()
    for cid in curso_ids:
        db.add(ManAcademyAcceso(employee_id=employee_id, tipo="curso", man_id=int(cid)))
    for pid in ruta_ids:
        db.add(ManAcademyAcceso(employee_id=employee_id, tipo="ruta", man_id=int(pid)))
    db.commit()

    return RedirectResponse(f"/rrhh/personal/{employee_id}#man-academy", status_code=303)
