# -*- coding: utf-8 -*-
"""Router del Sistema de Administración de Personal DIGETEL GROUP:
login/control de accesos, parametrización (empresas/unidades de negocio),
base de datos maestra de personal (foto, bitácora, documentos, altas/bajas)
y gestión de usuarios del sistema."""
import datetime
import os
import uuid

from fastapi import APIRouter, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    Employee, UnidadNegocio, Empresa, User, BitacoraEntry, Attachment, AsistenciaRegistro, Catalogo,
    OnboardingRegistro, Competencia, Cargo, CargoRequisitoCompetencia, ATTACHMENT_TYPES, REGIMENES_LABORALES,
    ROLES, TIPOS_BITACORA, CATALOGO_TIPOS, CATALOGO_TIPO_KEYS, ETAPAS_ONBOARDING, ETAPA_ONBOARDING_KEYS,
    ESTADOS_ONBOARDING, TIPOS_COMPETENCIA, TIPO_COMPETENCIA_KEYS, TIPOS_LICENCIA, NIVELES_EDUCATIVOS,
)
from .auth import (
    get_current_user, require_login, require_role, hash_password, verify_password,
    can_see_planilla, can_see_operativo, is_staff,
)
from . import kpis as kpis_module

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOTOS_DIR = os.path.join(BASE_DIR, "fotos")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
FIRMAS_EMPRESA_DIR = os.path.join(BASE_DIR, "firmas_empresa")
os.makedirs(FOTOS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(FIRMAS_EMPRESA_DIR, exist_ok=True)

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
router = APIRouter()

ATTACHMENT_LABELS = dict(ATTACHMENT_TYPES)
ROLE_LABELS = dict(ROLES)

# Man Academy vive en un proyecto/hosting aparte (no se reconstruye acá); el
# menú de Capacitación solo enlaza hacia allá. Se deja vacío por defecto para
# no inventar una URL — definir MAN_ACADEMY_URL como variable de entorno
# cuando se sepa el dominio final.
MAN_ACADEMY_URL = os.environ.get("MAN_ACADEMY_URL", "")


def _ctx(request: Request, user, **extra):
    base = {"user": user, "role_labels": ROLE_LABELS, "man_academy_url": MAN_ACADEMY_URL}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/rrhh", db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/rrhh")
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...),
                  next: str = Form("/rrhh"), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username.strip()).first()
    if not user or not user.activo or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "login.html", {
            "next": next, "error": "Usuario o contraseña incorrectos, o cuenta desactivada.",
        }, status_code=400)
    request.session["user_id"] = user.id
    user.last_login_at = datetime.datetime.utcnow()
    db.commit()
    return RedirectResponse(next or "/rrhh", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


# ---------------------------------------------------------------------------
# Dashboard / entrada
# ---------------------------------------------------------------------------
@router.get("/rrhh")
def rrhh_home(user: User = Depends(require_login)):
    if user.rol == "usuario":
        return RedirectResponse("/rrhh/mi-perfil")
    return RedirectResponse("/rrhh/personal")


# ---------------------------------------------------------------------------
# Parametrización (solo administrador): cada catálogo tiene su propia página
# en el menú, con alta/edición/desactivación/eliminación. La eliminación (y
# la desactivación) se bloquea si hay datos de personal que dependen del
# registro — ver los helpers _empresa_tiene_activos / _unidad_tiene_empresas /
# _catalogo_en_uso.
# ---------------------------------------------------------------------------
def _con_error(url: str, mensaje: str) -> str:
    """Agrega un mensaje de error a una URL de redirección, para que la
    página de listado lo muestre en vez de tirar un error crudo (por ejemplo,
    al intentar eliminar algo que todavía está en uso)."""
    from urllib.parse import quote
    return f"{url}?error={quote(mensaje)}"


def _empresa_tiene_activos(db: Session, empresa_id: int) -> bool:
    return db.query(Employee).filter(Employee.empresa_id == empresa_id, Employee.estado == "activo").count() > 0


def _unidad_tiene_empresas(db: Session, unidad_id: int) -> bool:
    return db.query(Empresa).filter(Empresa.unidad_negocio_id == unidad_id).count() > 0


def _competencia_en_uso(db: Session, competencia_id: int) -> bool:
    """True si algún Cargo la exige como requisito."""
    return db.query(CargoRequisitoCompetencia).filter(
        CargoRequisitoCompetencia.competencia_id == competencia_id).count() > 0


def _cargo_en_uso(db: Session, cargo_id: int, cargo_nombre: str) -> bool:
    """True si otro cargo le reporta, o si algún trabajador tiene este cargo
    (o puesto, que reutiliza el mismo catálogo) guardado en su ficha."""
    if db.query(Cargo).filter(Cargo.reporta_a_id == cargo_id).count() > 0:
        return True
    for e in db.query(Employee).all():
        f = e.ficha_data or {}
        if f.get("cargo") == cargo_nombre or f.get("puesto") == cargo_nombre:
            return True
    return False


def _catalogo_en_uso(db: Session, tipo: str, nombre: str) -> bool:
    """True si algún trabajador (activo o cesado) tiene este valor guardado
    en su ficha — no es una FK real (ficha_data es JSON de texto libre), pero
    igual bloqueamos el borrado para no perder de vista que sigue en uso."""
    campos = {
        "area": ["area"], "gerencia": ["gerencia"], "sede": ["sede"],
        "banco": ["banco_haberes", "banco_cts"],
    }.get(tipo, [])
    if not campos:
        return False
    for e in db.query(Employee).all():
        f = e.ficha_data or {}
        if any(f.get(c) == nombre for c in campos):
            return True
    return False


@router.get("/rrhh/parametrizacion", response_class=HTMLResponse)
def parametrizacion(request: Request, user: User = Depends(require_role("administrador"))):
    return templates.TemplateResponse(request, "rrhh_parametrizacion.html", _ctx(
        request, user, catalogo_tipos=CATALOGO_TIPOS, active="parametrizacion",
    ))


@router.get("/rrhh/parametrizacion/unidades", response_class=HTMLResponse)
def unidades_list(request: Request, error: str = "", db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador"))):
    unidades = db.query(UnidadNegocio).order_by(UnidadNegocio.nombre).all()
    bloqueadas = {u.id: _unidad_tiene_empresas(db, u.id) for u in unidades}
    return templates.TemplateResponse(request, "rrhh_unidades.html", _ctx(
        request, user, unidades=unidades, bloqueadas=bloqueadas, error=error, active="unidades",
    ))


@router.post("/rrhh/parametrizacion/unidad")
def crear_unidad(nombre: str = Form(...), descripcion: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    db.add(UnidadNegocio(nombre=nombre.strip(), descripcion=descripcion.strip() or None))
    db.commit()
    return RedirectResponse("/rrhh/parametrizacion/unidades", status_code=303)


@router.post("/rrhh/parametrizacion/unidad/{unidad_id}/editar")
def editar_unidad(unidad_id: int, nombre: str = Form(...), descripcion: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    u = db.query(UnidadNegocio).get(unidad_id)
    if u:
        u.nombre = nombre.strip()
        u.descripcion = descripcion.strip() or None
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/unidades", status_code=303)


@router.post("/rrhh/parametrizacion/unidad/{unidad_id}/toggle")
def toggle_unidad(unidad_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador"))):
    u = db.query(UnidadNegocio).get(unidad_id)
    if u:
        if u.activo and _unidad_tiene_empresas(db, unidad_id):
            return RedirectResponse(_con_error("/rrhh/parametrizacion/unidades",
                "No se puede desactivar: todavía tiene empresas asignadas."), status_code=303)
        u.activo = not u.activo
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/unidades", status_code=303)


@router.post("/rrhh/parametrizacion/unidad/{unidad_id}/eliminar")
def eliminar_unidad(unidad_id: int, db: Session = Depends(get_db),
                     user: User = Depends(require_role("administrador"))):
    u = db.query(UnidadNegocio).get(unidad_id)
    if u:
        if _unidad_tiene_empresas(db, unidad_id):
            return RedirectResponse(_con_error("/rrhh/parametrizacion/unidades",
                "No se puede eliminar: todavía tiene empresas asignadas."), status_code=303)
        db.delete(u)
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/unidades", status_code=303)


@router.get("/rrhh/parametrizacion/empresas", response_class=HTMLResponse)
def empresas_list(request: Request, error: str = "", db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador"))):
    empresas = db.query(Empresa).order_by(Empresa.nombre).all()
    unidades = db.query(UnidadNegocio).order_by(UnidadNegocio.nombre).all()
    bloqueadas = {e.id: _empresa_tiene_activos(db, e.id) for e in empresas}
    return templates.TemplateResponse(request, "rrhh_empresas.html", _ctx(
        request, user, empresas=empresas, unidades=unidades, regimenes=REGIMENES_LABORALES,
        bloqueadas=bloqueadas, error=error, active="empresas",
    ))


@router.post("/rrhh/parametrizacion/empresa")
def crear_empresa(nombre: str = Form(...), razon_social: str = Form(""), ruc: str = Form(""),
                   unidad_negocio_id: int = Form(...), regimen_laboral: str = Form(""),
                   representante_legal: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    db.add(Empresa(
        nombre=nombre.strip(), razon_social=razon_social.strip() or None, ruc=ruc.strip() or None,
        unidad_negocio_id=unidad_negocio_id, regimen_laboral=regimen_laboral or None,
        representante_legal=representante_legal.strip() or None,
    ))
    db.commit()
    return RedirectResponse("/rrhh/parametrizacion/empresas", status_code=303)


@router.post("/rrhh/parametrizacion/empresa/{empresa_id}/editar")
def editar_empresa(empresa_id: int, nombre: str = Form(...), razon_social: str = Form(""), ruc: str = Form(""),
                    unidad_negocio_id: int = Form(...), regimen_laboral: str = Form(""),
                    representante_legal: str = Form(""),
                    db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    e = db.query(Empresa).get(empresa_id)
    if e:
        e.nombre = nombre.strip()
        e.razon_social = razon_social.strip() or None
        e.ruc = ruc.strip() or None
        e.unidad_negocio_id = unidad_negocio_id
        e.regimen_laboral = regimen_laboral or None
        e.representante_legal = representante_legal.strip() or None
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/empresas", status_code=303)


@router.post("/rrhh/parametrizacion/empresa/{empresa_id}/firma")
async def subir_firma_empresa(empresa_id: int, firma: UploadFile = File(...), db: Session = Depends(get_db),
                               user: User = Depends(require_role("administrador"))):
    e = db.query(Empresa).get(empresa_id)
    if not e:
        raise HTTPException(404)
    dest = os.path.join(FIRMAS_EMPRESA_DIR, f"{empresa_id}.png")
    content = await firma.read()
    with open(dest, "wb") as f:
        f.write(content)
    e.firma_representante_path = dest
    db.commit()
    return RedirectResponse("/rrhh/parametrizacion/empresas", status_code=303)


@router.get("/rrhh/parametrizacion/empresa/{empresa_id}/firma")
def ver_firma_empresa(empresa_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)):
    e = db.query(Empresa).get(empresa_id)
    if not e or not e.firma_representante_path or not os.path.exists(e.firma_representante_path):
        raise HTTPException(404)
    return FileResponse(e.firma_representante_path, media_type="image/png")


@router.post("/rrhh/parametrizacion/empresa/{empresa_id}/toggle")
def toggle_empresa(empresa_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_role("administrador"))):
    e = db.query(Empresa).get(empresa_id)
    if e:
        if e.activo and _empresa_tiene_activos(db, empresa_id):
            return RedirectResponse(_con_error("/rrhh/parametrizacion/empresas",
                "No se puede desactivar: hay trabajadores activos en esta empresa."), status_code=303)
        e.activo = not e.activo
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/empresas", status_code=303)


@router.post("/rrhh/parametrizacion/empresa/{empresa_id}/eliminar")
def eliminar_empresa(empresa_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_role("administrador"))):
    e = db.query(Empresa).get(empresa_id)
    if e:
        if _empresa_tiene_activos(db, empresa_id):
            return RedirectResponse(_con_error("/rrhh/parametrizacion/empresas",
                "No se puede eliminar: hay trabajadores activos en esta empresa."), status_code=303)
        db.delete(e)
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/empresas", status_code=303)


@router.get("/rrhh/parametrizacion/catalogo/{tipo}", response_class=HTMLResponse)
def catalogo_list(request: Request, tipo: str, error: str = "", db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador"))):
    if tipo not in CATALOGO_TIPO_KEYS:
        raise HTTPException(404)
    label = dict(CATALOGO_TIPOS)[tipo]
    items = db.query(Catalogo).filter(Catalogo.tipo == tipo).order_by(Catalogo.nombre).all()
    bloqueados = {i.id: _catalogo_en_uso(db, tipo, i.nombre) for i in items}
    return templates.TemplateResponse(request, "rrhh_catalogo.html", _ctx(
        request, user, tipo=tipo, label=label, items=items, bloqueados=bloqueados, error=error, active=tipo,
    ))


@router.post("/rrhh/parametrizacion/catalogo")
def crear_item_catalogo(tipo: str = Form(...), nombre: str = Form(...),
                         db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    if tipo not in CATALOGO_TIPO_KEYS:
        raise HTTPException(400, "Tipo de catálogo inválido.")
    db.add(Catalogo(tipo=tipo, nombre=nombre.strip()))
    db.commit()
    return RedirectResponse(f"/rrhh/parametrizacion/catalogo/{tipo}", status_code=303)


@router.post("/rrhh/parametrizacion/catalogo/{item_id}/editar")
def editar_item_catalogo(item_id: int, nombre: str = Form(...), db: Session = Depends(get_db),
                          user: User = Depends(require_role("administrador"))):
    item = db.query(Catalogo).get(item_id)
    if item:
        item.nombre = nombre.strip()
        db.commit()
        return RedirectResponse(f"/rrhh/parametrizacion/catalogo/{item.tipo}", status_code=303)
    return RedirectResponse("/rrhh/parametrizacion", status_code=303)


@router.post("/rrhh/parametrizacion/catalogo/{item_id}/toggle")
def toggle_item_catalogo(item_id: int, db: Session = Depends(get_db),
                          user: User = Depends(require_role("administrador"))):
    item = db.query(Catalogo).get(item_id)
    if item:
        if item.activo and _catalogo_en_uso(db, item.tipo, item.nombre):
            return RedirectResponse(_con_error(f"/rrhh/parametrizacion/catalogo/{item.tipo}",
                "No se puede desactivar: hay personal registrado con este valor."), status_code=303)
        item.activo = not item.activo
        db.commit()
        return RedirectResponse(f"/rrhh/parametrizacion/catalogo/{item.tipo}", status_code=303)
    return RedirectResponse("/rrhh/parametrizacion", status_code=303)


@router.post("/rrhh/parametrizacion/catalogo/{item_id}/eliminar")
def eliminar_item_catalogo(item_id: int, db: Session = Depends(get_db),
                            user: User = Depends(require_role("administrador"))):
    item = db.query(Catalogo).get(item_id)
    if item:
        if _catalogo_en_uso(db, item.tipo, item.nombre):
            return RedirectResponse(_con_error(f"/rrhh/parametrizacion/catalogo/{item.tipo}",
                "No se puede eliminar: hay personal registrado con este valor."), status_code=303)
        tipo = item.tipo
        db.delete(item)
        db.commit()
        return RedirectResponse(f"/rrhh/parametrizacion/catalogo/{tipo}", status_code=303)
    return RedirectResponse("/rrhh/parametrizacion", status_code=303)


# ---------------------------------------------------------------------------
# Principios, Valores y Competencias
# ---------------------------------------------------------------------------
@router.get("/rrhh/parametrizacion/competencias", response_class=HTMLResponse)
def competencias_list(request: Request, error: str = "", db: Session = Depends(get_db),
                       user: User = Depends(require_role("administrador"))):
    items = db.query(Competencia).order_by(Competencia.tipo, Competencia.nombre).all()
    return templates.TemplateResponse(request, "rrhh_competencias.html", _ctx(
        request, user, items=items, tipos=TIPOS_COMPETENCIA, error=error, active="competencias",
    ))


@router.post("/rrhh/parametrizacion/competencia")
def crear_competencia(tipo: str = Form(...), nombre: str = Form(...), descripcion: str = Form(""),
                       nivel_1: str = Form(""), nivel_2: str = Form(""), nivel_3: str = Form(""),
                       nivel_4: str = Form(""), db: Session = Depends(get_db),
                       user: User = Depends(require_role("administrador"))):
    if tipo not in TIPO_COMPETENCIA_KEYS:
        raise HTTPException(400, "Tipo inválido.")
    db.add(Competencia(
        tipo=tipo, nombre=nombre.strip(), descripcion=descripcion.strip() or None,
        nivel_1=nivel_1.strip() or None, nivel_2=nivel_2.strip() or None,
        nivel_3=nivel_3.strip() or None, nivel_4=nivel_4.strip() or None,
    ))
    db.commit()
    return RedirectResponse("/rrhh/parametrizacion/competencias", status_code=303)


@router.post("/rrhh/parametrizacion/competencia/{item_id}/editar")
def editar_competencia(item_id: int, tipo: str = Form(...), nombre: str = Form(...), descripcion: str = Form(""),
                        nivel_1: str = Form(""), nivel_2: str = Form(""), nivel_3: str = Form(""),
                        nivel_4: str = Form(""), db: Session = Depends(get_db),
                        user: User = Depends(require_role("administrador"))):
    item = db.query(Competencia).get(item_id)
    if item and tipo in TIPO_COMPETENCIA_KEYS:
        item.tipo = tipo
        item.nombre = nombre.strip()
        item.descripcion = descripcion.strip() or None
        item.nivel_1 = nivel_1.strip() or None
        item.nivel_2 = nivel_2.strip() or None
        item.nivel_3 = nivel_3.strip() or None
        item.nivel_4 = nivel_4.strip() or None
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/competencias", status_code=303)


@router.post("/rrhh/parametrizacion/competencia/{item_id}/toggle")
def toggle_competencia(item_id: int, db: Session = Depends(get_db),
                        user: User = Depends(require_role("administrador"))):
    item = db.query(Competencia).get(item_id)
    if item:
        if item.activo and _competencia_en_uso(db, item_id):
            return RedirectResponse(_con_error("/rrhh/parametrizacion/competencias",
                "No se puede desactivar: está exigida como requisito de al menos un cargo."), status_code=303)
        item.activo = not item.activo
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/competencias", status_code=303)


@router.post("/rrhh/parametrizacion/competencia/{item_id}/eliminar")
def eliminar_competencia(item_id: int, db: Session = Depends(get_db),
                          user: User = Depends(require_role("administrador"))):
    item = db.query(Competencia).get(item_id)
    if item:
        if _competencia_en_uso(db, item_id):
            return RedirectResponse(_con_error("/rrhh/parametrizacion/competencias",
                "No se puede eliminar: está exigida como requisito de al menos un cargo."), status_code=303)
        db.delete(item)
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/competencias", status_code=303)


# ---------------------------------------------------------------------------
# Cargos y Funciones (MOF)
# ---------------------------------------------------------------------------
def _lista_desde_textarea(texto: str) -> list:
    return [linea.strip() for linea in (texto or "").splitlines() if linea.strip()]


@router.get("/rrhh/parametrizacion/cargos", response_class=HTMLResponse)
def cargos_list(request: Request, error: str = "", db: Session = Depends(get_db),
                 user: User = Depends(require_role("administrador"))):
    cargos = db.query(Cargo).order_by(Cargo.nombre).all()
    bloqueados = {c.id: _cargo_en_uso(db, c.id, c.nombre) for c in cargos}
    return templates.TemplateResponse(request, "rrhh_cargos.html", _ctx(
        request, user, cargos=cargos, bloqueados=bloqueados, error=error, active="cargos",
    ))


@router.post("/rrhh/parametrizacion/cargo")
def crear_cargo(nombre: str = Form(...), db: Session = Depends(get_db),
                 user: User = Depends(require_role("administrador"))):
    cargo = Cargo(nombre=nombre.strip())
    db.add(cargo)
    db.commit()
    db.refresh(cargo)
    return RedirectResponse(f"/rrhh/parametrizacion/cargo/{cargo.id}", status_code=303)


@router.get("/rrhh/parametrizacion/cargo/{cargo_id}", response_class=HTMLResponse)
def cargo_detalle(request: Request, cargo_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador"))):
    cargo = db.query(Cargo).get(cargo_id)
    if not cargo:
        raise HTTPException(404)
    otros_cargos = db.query(Cargo).filter(Cargo.id != cargo_id).order_by(Cargo.nombre).all()
    competencias = db.query(Competencia).filter(Competencia.tipo == "competencia", Competencia.activo == True).order_by(Competencia.nombre).all()  # noqa: E712
    return templates.TemplateResponse(request, "rrhh_cargo_detalle.html", _ctx(
        request, user, cargo=cargo, otros_cargos=otros_cargos, competencias=competencias, active="cargos",
    ))


@router.post("/rrhh/parametrizacion/cargo/{cargo_id}/editar")
def editar_cargo(cargo_id: int, nombre: str = Form(...), descripcion: str = Form(""),
                  funciones: str = Form(""), responsabilidades: str = Form(""),
                  reporta_a_id: str = Form(""), requisito_academico: str = Form(""),
                  requisito_experiencia: str = Form(""), requisito_conocimientos: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    cargo = db.query(Cargo).get(cargo_id)
    if not cargo:
        raise HTTPException(404)
    nuevo_reporta_a = int(reporta_a_id) if reporta_a_id else None
    if nuevo_reporta_a == cargo_id:
        raise HTTPException(400, "Un cargo no puede reportarse a sí mismo.")
    cargo.nombre = nombre.strip()
    cargo.descripcion = descripcion.strip() or None
    cargo.funciones = _lista_desde_textarea(funciones)
    cargo.responsabilidades = _lista_desde_textarea(responsabilidades)
    cargo.reporta_a_id = nuevo_reporta_a
    cargo.requisito_academico = requisito_academico.strip() or None
    cargo.requisito_experiencia = requisito_experiencia.strip() or None
    cargo.requisito_conocimientos = requisito_conocimientos.strip() or None
    db.commit()
    return RedirectResponse(f"/rrhh/parametrizacion/cargo/{cargo_id}", status_code=303)


@router.post("/rrhh/parametrizacion/cargo/{cargo_id}/competencia")
def agregar_requisito_competencia(cargo_id: int, competencia_id: int = Form(...), nivel_requerido: int = Form(...),
                                   db: Session = Depends(get_db),
                                   user: User = Depends(require_role("administrador"))):
    if not db.query(Cargo).get(cargo_id):
        raise HTTPException(404)
    if nivel_requerido not in (1, 2, 3, 4):
        raise HTTPException(400, "El nivel requerido debe ser entre 1 y 4.")
    existente = db.query(CargoRequisitoCompetencia).filter(
        CargoRequisitoCompetencia.cargo_id == cargo_id,
        CargoRequisitoCompetencia.competencia_id == competencia_id).first()
    if existente:
        existente.nivel_requerido = nivel_requerido
    else:
        db.add(CargoRequisitoCompetencia(cargo_id=cargo_id, competencia_id=competencia_id, nivel_requerido=nivel_requerido))
    db.commit()
    return RedirectResponse(f"/rrhh/parametrizacion/cargo/{cargo_id}", status_code=303)


@router.post("/rrhh/parametrizacion/cargo/{cargo_id}/competencia/{req_id}/eliminar")
def eliminar_requisito_competencia(cargo_id: int, req_id: int, db: Session = Depends(get_db),
                                    user: User = Depends(require_role("administrador"))):
    req = db.query(CargoRequisitoCompetencia).get(req_id)
    if req and req.cargo_id == cargo_id:
        db.delete(req)
        db.commit()
    return RedirectResponse(f"/rrhh/parametrizacion/cargo/{cargo_id}", status_code=303)


@router.post("/rrhh/parametrizacion/cargo/{cargo_id}/toggle")
def toggle_cargo(cargo_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_role("administrador"))):
    cargo = db.query(Cargo).get(cargo_id)
    if cargo:
        if cargo.activo and _cargo_en_uso(db, cargo_id, cargo.nombre):
            return RedirectResponse(_con_error("/rrhh/parametrizacion/cargos",
                "No se puede desactivar: hay personal o cargos que dependen de este."), status_code=303)
        cargo.activo = not cargo.activo
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/cargos", status_code=303)


@router.post("/rrhh/parametrizacion/cargo/{cargo_id}/eliminar")
def eliminar_cargo(cargo_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_role("administrador"))):
    cargo = db.query(Cargo).get(cargo_id)
    if cargo:
        if _cargo_en_uso(db, cargo_id, cargo.nombre):
            return RedirectResponse(_con_error("/rrhh/parametrizacion/cargos",
                "No se puede eliminar: hay personal o cargos que dependen de este."), status_code=303)
        db.delete(cargo)
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/cargos", status_code=303)


# ---------------------------------------------------------------------------
# Usuarios del sistema (solo administrador)
# ---------------------------------------------------------------------------
@router.get("/rrhh/usuarios", response_class=HTMLResponse)
def usuarios_list(request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador"))):
    usuarios = db.query(User).order_by(User.username).all()
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
    empleados = db.query(Employee).order_by(Employee.nombre_completo).all()
    return templates.TemplateResponse(request, "rrhh_usuarios.html", _ctx(
        request, user, usuarios=usuarios, empresas=empresas, roles=ROLES, empleados=empleados, active="usuarios",
    ))


@router.post("/rrhh/usuarios/nuevo")
def crear_usuario(username: str = Form(...), password: str = Form(...), nombre_completo: str = Form(...),
                   rol: str = Form(...), empresa_id: str = Form(""), employee_id: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    if db.query(User).filter(User.username == username.strip()).first():
        raise HTTPException(400, "Ese nombre de usuario ya existe.")
    db.add(User(
        username=username.strip(), password_hash=hash_password(password), nombre_completo=nombre_completo.strip(),
        rol=rol, empresa_id=int(empresa_id) if empresa_id else None,
        employee_id=int(employee_id) if employee_id else None, activo=True,
    ))
    db.commit()
    return RedirectResponse("/rrhh/usuarios", status_code=303)


@router.post("/rrhh/usuarios/{user_id}/toggle")
def toggle_usuario(user_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_role("administrador"))):
    u = db.query(User).get(user_id)
    if u and u.id != user.id:  # no permitir autodesactivarse
        u.activo = not u.activo
        db.commit()
    return RedirectResponse("/rrhh/usuarios", status_code=303)


@router.post("/rrhh/usuarios/{user_id}/reset-password")
def reset_password(user_id: int, nueva_password: str = Form(...), db: Session = Depends(get_db),
                    user: User = Depends(require_role("administrador"))):
    u = db.query(User).get(user_id)
    if u:
        u.password_hash = hash_password(nueva_password)
        u.must_change_password = True
        db.commit()
    return RedirectResponse("/rrhh/usuarios", status_code=303)


@router.get("/rrhh/mi-cuenta", response_class=HTMLResponse)
def mi_cuenta(request: Request, forzado: str = "", user: User = Depends(require_login)):
    return templates.TemplateResponse(request, "rrhh_mi_cuenta.html", _ctx(
        request, user, error=None, ok=None, forzado=bool(forzado),
    ))


@router.post("/rrhh/mi-cuenta/password")
def cambiar_mi_password(request: Request, actual: str = Form(...), nueva: str = Form(...),
                         db: Session = Depends(get_db), user: User = Depends(require_login)):
    if not verify_password(actual, user.password_hash):
        return templates.TemplateResponse(request, "rrhh_mi_cuenta.html",
            _ctx(request, user, error="La contraseña actual no es correcta.", ok=None, forzado=False), status_code=400)
    if len(nueva) < 8:
        return templates.TemplateResponse(request, "rrhh_mi_cuenta.html",
            _ctx(request, user, error="La nueva contraseña debe tener al menos 8 caracteres.", ok=None, forzado=False), status_code=400)
    user.password_hash = hash_password(nueva)
    user.must_change_password = False
    db.commit()
    return templates.TemplateResponse(request, "rrhh_mi_cuenta.html",
        _ctx(request, user, error=None, ok="Contraseña actualizada correctamente.", forzado=False))


# ---------------------------------------------------------------------------
# Administración de Personal (BD maestra)
# ---------------------------------------------------------------------------
@router.get("/rrhh/personal", response_class=HTMLResponse)
def personal_list(request: Request, empresa_id: str = "", unidad_id: str = "", q: str = "",
                   estado: str = "", db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    query = db.query(Employee)
    if empresa_id:
        query = query.filter(Employee.empresa_id == int(empresa_id))
    if unidad_id:
        query = query.join(Empresa, Employee.empresa_id == Empresa.id).filter(Empresa.unidad_negocio_id == int(unidad_id))
    if estado:
        query = query.filter(Employee.estado == estado)
    if q:
        query = query.filter(Employee.nombre_completo.ilike(f"%{q}%"))
    # Activos primero, cesados al final, para que se distingan de un vistazo.
    empleados = query.order_by((Employee.estado != "activo"), Employee.nombre_completo).all()

    unidades = db.query(UnidadNegocio).order_by(UnidadNegocio.nombre).all()
    empresas = db.query(Empresa).order_by(Empresa.nombre).all()
    return templates.TemplateResponse(request, "rrhh_personal_list.html", _ctx(
        request, user, empleados=empleados, unidades=unidades, empresas=empresas,
        f_empresa=empresa_id, f_unidad=unidad_id, f_q=q, f_estado=estado,
        can_planilla=can_see_planilla(user), active="personal",
    ))


@router.get("/rrhh/personal/nuevo", response_class=HTMLResponse)
def personal_nuevo_form(request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require_role("administrador", "opeoka"))):
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
    return templates.TemplateResponse(request, "rrhh_personal_nuevo.html", _ctx(request, user, empresas=empresas, active="personal"))


@router.post("/rrhh/personal/nuevo")
def personal_nuevo_crear(nombre_completo: str = Form(...), email: str = Form(""), empresa_id: str = Form(""),
                          db: Session = Depends(get_db), user: User = Depends(require_role("administrador", "opeoka"))):
    empresa = db.query(Empresa).get(int(empresa_id)) if empresa_id else None
    emp = Employee(
        nombre_completo=nombre_completo.strip(), email=email.strip() or None,
        empresa_id=empresa.id if empresa else None, empresa=empresa.nombre if empresa else None,
        estado="activo", status="completo",
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return RedirectResponse(f"/rrhh/personal/{emp.id}/ficha", status_code=303)


def _check_own_or_staff(user: User, employee_id: int):
    if is_staff(user):
        return
    if user.employee_id != employee_id:
        raise HTTPException(403, "No tienes permiso para ver esta información.")


@router.get("/rrhh/personal/{employee_id}", response_class=HTMLResponse)
def personal_detalle(request: Request, employee_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_login)):
    _check_own_or_staff(user, employee_id)
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
    ultima_marca = emp.asistencia[0] if emp.asistencia else None
    puede_marcar_entrada = not ultima_marca or ultima_marca.tipo == "salida"
    return templates.TemplateResponse(request, "rrhh_personal_detalle.html", _ctx(
        request, user, e=emp, empresas=empresas, attachment_types=ATTACHMENT_TYPES,
        attachment_labels=ATTACHMENT_LABELS, tipos_bitacora=TIPOS_BITACORA,
        can_planilla=can_see_planilla(user), can_operativo=can_see_operativo(user),
        can_edit=is_staff(user), can_mark_own=(user.employee_id == employee_id),
        asistencia_reciente=emp.asistencia[:10], puede_marcar_entrada=puede_marcar_entrada,
        familia=emp.familia_data or [], educacion=emp.educacion_data or [],
        experiencia=emp.experiencia_data or [], capacitaciones=emp.capacitaciones_data or [],
        etapas_onboarding=ETAPAS_ONBOARDING, estados_onboarding=ESTADOS_ONBOARDING,
        etapa_onboarding_labels=dict(ETAPAS_ONBOARDING), estado_onboarding_labels=dict(ESTADOS_ONBOARDING),
        active="personal",
    ))


@router.get("/rrhh/personal/{employee_id}/ficha", response_class=HTMLResponse)
def personal_ficha_editar(request: Request, employee_id: int, db: Session = Depends(get_db),
                           user: User = Depends(require_role("administrador", "opeoka"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    catalogos = {
        tipo: [c.nombre for c in db.query(Catalogo).filter(Catalogo.tipo == tipo, Catalogo.activo == True)  # noqa: E712
               .order_by(Catalogo.nombre).all()]
        for tipo in CATALOGO_TIPO_KEYS
    }
    cargos_activos = [c.nombre for c in db.query(Cargo).filter(Cargo.activo == True)  # noqa: E712
                       .order_by(Cargo.nombre).all()]
    empleados_activos = [
        nombre for (nombre,) in db.query(Employee.nombre_completo)
        .filter(Employee.estado == "activo", Employee.status == "completo", Employee.id != employee_id)
        .order_by(Employee.nombre_completo).all()
    ]
    return templates.TemplateResponse(request, "rrhh_personal_ficha.html", _ctx(
        request, user, e=emp, catalogos=catalogos, cargos_activos=cargos_activos,
        empleados_activos=empleados_activos, tipos_licencia=TIPOS_LICENCIA,
        niveles_educativos=NIVELES_EDUCATIVOS, active="personal",
    ))


@router.post("/rrhh/personal/{employee_id}/ficha")
async def personal_ficha_guardar(employee_id: int, request: Request, db: Session = Depends(get_db),
                                  user: User = Depends(require_role("administrador", "opeoka"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    payload = await request.json()
    emp.ficha_data = payload.get("ficha", {})

    # Mismo cálculo que en el flujo de Selección (main.py): el código de
    # trabajador lo genera el sistema, no se escribe a mano.
    numero_doc = (emp.ficha_data.get("numero_documento") or "").strip()
    empresa_nombre = emp.empresa or emp.ficha_data.get("empresa") or ""
    prefijo = "".join(ch for ch in empresa_nombre if ch.isalpha())[:2].upper()
    if numero_doc and prefijo:
        emp.ficha_data["codigo_trabajador"] = f"{prefijo}{numero_doc}"

    emp.familia_data = payload.get("familia", [])
    emp.educacion_data = payload.get("educacion", [])
    emp.experiencia_data = payload.get("experiencia", [])
    emp.capacitaciones_data = payload.get("capacitaciones", [])
    if payload.get("nombre_completo"):
        emp.nombre_completo = payload["nombre_completo"]
    db.commit()
    return {"ok": True}


@router.get("/rrhh/mi-perfil")
def mi_perfil(user: User = Depends(require_login)):
    if not user.employee_id:
        raise HTTPException(404, "Tu usuario todavía no está vinculado a un registro de trabajador. Pide a RR.HH. que lo vincule.")
    return RedirectResponse(f"/rrhh/personal/{user.employee_id}")


@router.get("/rrhh/personal/{employee_id}/foto")
def personal_foto(employee_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)):
    _check_own_or_staff(user, employee_id)
    emp = db.query(Employee).get(employee_id)
    if not emp or not emp.foto_path or not os.path.exists(emp.foto_path):
        raise HTTPException(404)
    return FileResponse(emp.foto_path)


@router.post("/rrhh/personal/{employee_id}/foto")
async def subir_foto(employee_id: int, foto: UploadFile = File(...), db: Session = Depends(get_db),
                      user: User = Depends(require_role("administrador", "opeoka"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    ext = os.path.splitext(foto.filename or "")[1].lower() or ".jpg"
    dest = os.path.join(FOTOS_DIR, f"{employee_id}{ext}")
    content = await foto.read()
    with open(dest, "wb") as f:
        f.write(content)
    emp.foto_path = dest
    db.commit()
    return RedirectResponse(f"/rrhh/personal/{employee_id}", status_code=303)


@router.post("/rrhh/personal/{employee_id}/bitacora")
def agregar_bitacora(employee_id: int, tipo: str = Form(...), texto: str = Form(...),
                      db: Session = Depends(get_db),
                      user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    db.add(BitacoraEntry(employee_id=employee_id, tipo=tipo, texto=texto.strip(), autor=user.nombre_completo))
    db.commit()
    return RedirectResponse(f"/rrhh/personal/{employee_id}#bitacora", status_code=303)


@router.post("/rrhh/personal/{employee_id}/onboarding")
def agregar_onboarding(employee_id: int, etapa: str = Form(...), estado: str = Form("pendiente"),
                        fecha: str = Form(""), responsable: str = Form(""), notas: str = Form(""),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    if etapa not in ETAPA_ONBOARDING_KEYS:
        raise HTTPException(400, "Etapa de onboarding inválida.")
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    fecha_dt = None
    if fecha:
        try:
            fecha_dt = datetime.datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError:
            fecha_dt = None
    db.add(OnboardingRegistro(
        employee_id=employee_id, etapa=etapa, estado=estado or "pendiente", fecha=fecha_dt,
        responsable=responsable.strip() or None, notas=notas.strip() or None,
        registrado_por=user.nombre_completo,
    ))
    db.commit()
    return RedirectResponse(f"/rrhh/personal/{employee_id}#onboarding", status_code=303)


@router.post("/rrhh/personal/{employee_id}/documentos")
async def subir_documento_rrhh(employee_id: int, tipo: str = Form(...), archivo: UploadFile = File(...),
                                 db: Session = Depends(get_db),
                                 user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    emp_dir = os.path.join(UPLOADS_DIR, emp.token)
    os.makedirs(emp_dir, exist_ok=True)
    safe_name = f"{tipo}_{uuid.uuid4().hex[:8]}_{archivo.filename}"
    dest_path = os.path.join(emp_dir, safe_name)
    content = await archivo.read()
    with open(dest_path, "wb") as f:
        f.write(content)
    db.add(Attachment(
        employee_id=emp.id, tipo=tipo, filename=archivo.filename, file_path=dest_path,
        content_type=archivo.content_type, subido_por=user.nombre_completo,
    ))
    db.commit()
    return RedirectResponse(f"/rrhh/personal/{employee_id}#documentos", status_code=303)


@router.post("/rrhh/personal/{employee_id}/empresa")
def asignar_empresa(employee_id: int, empresa_id: str = Form(""), db: Session = Depends(get_db),
                     user: User = Depends(require_role("administrador"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    empresa = db.query(Empresa).get(int(empresa_id)) if empresa_id else None
    emp.empresa_id = empresa.id if empresa else None
    emp.empresa = empresa.nombre if empresa else None
    db.commit()
    return RedirectResponse(f"/rrhh/personal/{employee_id}", status_code=303)


@router.post("/rrhh/personal/{employee_id}/baja")
def dar_de_baja(employee_id: int, fecha_baja: str = Form(...), motivo_baja: str = Form(...),
                 db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    emp.estado = "cesado"
    emp.fecha_baja = datetime.datetime.strptime(fecha_baja, "%Y-%m-%d")
    emp.motivo_baja = motivo_baja.strip()
    db.commit()
    return RedirectResponse(f"/rrhh/personal/{employee_id}", status_code=303)


@router.post("/rrhh/personal/{employee_id}/reactivar")
def reactivar(employee_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    emp.estado = "activo"
    emp.fecha_baja = None
    emp.motivo_baja = None
    db.commit()
    return RedirectResponse(f"/rrhh/personal/{employee_id}", status_code=303)


# ---------------------------------------------------------------------------
# Control de Asistencia (punto 6 del pedido): marcado de entrada/salida
# ---------------------------------------------------------------------------
def _puede_marcar(user: User, employee_id: int):
    if is_staff(user):
        return
    if user.employee_id != employee_id:
        raise HTTPException(403, "Solo puedes marcar tu propia asistencia.")


@router.post("/rrhh/personal/{employee_id}/asistencia/marcar")
def marcar_asistencia(request: Request, employee_id: int, tipo: str = Form(...),
                       db: Session = Depends(get_db), user: User = Depends(require_login)):
    _puede_marcar(user, employee_id)
    if tipo not in ("entrada", "salida"):
        raise HTTPException(400, "Tipo de marcación inválido.")
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    ip = request.client.host if request.client else None
    db.add(AsistenciaRegistro(
        employee_id=employee_id, tipo=tipo, ip_address=ip, registrado_por=user.nombre_completo,
    ))
    db.commit()
    return RedirectResponse(f"/rrhh/personal/{employee_id}#asistencia", status_code=303)


@router.get("/rrhh/asistencia", response_class=HTMLResponse)
def asistencia_list(request: Request, fecha: str = "", empresa_id: str = "",
                     db: Session = Depends(get_db),
                     user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    dia = datetime.date.today()
    if fecha:
        try:
            dia = datetime.datetime.strptime(fecha, "%Y-%m-%d").date()
        except ValueError:
            pass
    inicio = datetime.datetime.combine(dia, datetime.time.min)
    fin = datetime.datetime.combine(dia, datetime.time.max)

    query = db.query(AsistenciaRegistro).filter(
        AsistenciaRegistro.timestamp >= inicio, AsistenciaRegistro.timestamp <= fin,
    )
    if empresa_id:
        query = query.join(Employee, AsistenciaRegistro.employee_id == Employee.id).filter(
            Employee.empresa_id == int(empresa_id))
    registros = query.order_by(AsistenciaRegistro.timestamp.desc()).all()

    empleados_activos = db.query(Employee).filter(Employee.estado == "activo")
    if empresa_id:
        empleados_activos = empleados_activos.filter(Employee.empresa_id == int(empresa_id))
    empleados_activos = empleados_activos.order_by(Employee.nombre_completo).all()
    marcaron_ids = {r.employee_id for r in registros if r.tipo == "entrada"}
    sin_marcar = [e for e in empleados_activos if e.id not in marcaron_ids]

    empresas = db.query(Empresa).order_by(Empresa.nombre).all()
    return templates.TemplateResponse(request, "rrhh_asistencia.html", _ctx(
        request, user, registros=registros, dia=dia, empresas=empresas, f_empresa=empresa_id,
        sin_marcar=sin_marcar, total_activos=len(empleados_activos), active="asistencia",
    ))


@router.post("/rrhh/asistencia/manual")
def asistencia_manual(employee_id: int = Form(...), tipo: str = Form(...), fecha: str = Form(...),
                       hora: str = Form(...), nota: str = Form(""), db: Session = Depends(get_db),
                       user: User = Depends(require_role("administrador", "opeoka"))):
    if tipo not in ("entrada", "salida"):
        raise HTTPException(400, "Tipo de marcación inválido.")
    ts = datetime.datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M")
    db.add(AsistenciaRegistro(
        employee_id=employee_id, tipo=tipo, timestamp=ts,
        registrado_por=f"{user.nombre_completo} (registro manual)",
        nota=nota.strip() or None,
    ))
    db.commit()
    return RedirectResponse(f"/rrhh/asistencia?fecha={fecha}", status_code=303)


# ---------------------------------------------------------------------------
# Dashboard de KPIs (punto 7 del pedido)
# ---------------------------------------------------------------------------
@router.get("/rrhh/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, dias: int = 30, db: Session = Depends(get_db),
              user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    data = kpis_module.resumen_dashboard(db, dias=dias)
    max_empresa = max([c for _, c in data["headcount_empresa"]], default=0) or 1
    max_unidad = max([c for _, c in data["headcount_unidad"]], default=0) or 1
    return templates.TemplateResponse(request, "rrhh_dashboard.html", _ctx(
        request, user, data=data, max_empresa=max_empresa, max_unidad=max_unidad, active="dashboard",
    ))
