# -*- coding: utf-8 -*-
"""Router del Sistema de Administración de Personal DIGETEL GROUP:
login/control de accesos, parametrización (empresas/unidades de negocio),
base de datos maestra de personal (foto, bitácora, documentos, altas/bajas)
y gestión de usuarios del sistema."""
import datetime
import os
import uuid

from fastapi import APIRouter, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    Employee, UnidadNegocio, Empresa, User, BitacoraEntry, Attachment, AsistenciaRegistro, Catalogo,
    OnboardingRegistro, Competencia, Cargo, CargoRequisitoCompetencia, ContratoRenovacion,
    Holding, LineaProducto, Anuncio, SaludoCumpleanos, SolicitudRenovacion, AnuncioVista, AnuncioLike,
    ATTACHMENT_TYPES, REGIMENES_LABORALES, DOC_TYPES,
    ROLES, TIPOS_BITACORA, CATALOGO_TIPOS, CATALOGO_TIPO_KEYS, ETAPAS_ONBOARDING, ETAPA_ONBOARDING_KEYS,
    ESTADOS_ONBOARDING, TIPOS_COMPETENCIA, TIPO_COMPETENCIA_KEYS, TIPOS_LICENCIA, NIVELES_EDUCATIVOS,
    STATUS_PENDIENTE, AMBITOS_ANUNCIO, AMBITO_ANUNCIO_KEYS,
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
GENERATED_DIR = os.path.join(BASE_DIR, "generated")
os.makedirs(FOTOS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(FIRMAS_EMPRESA_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
router = APIRouter()

ATTACHMENT_LABELS = dict(ATTACHMENT_TYPES)
ROLE_LABELS = dict(ROLES)


def _ensure_documents(db: Session, employee: Employee):
    """Crea los 5 documentos legales pendientes de un trabajador que va a
    completar su ficha vía Selección (duplica la lógica de main.py:
    ensure_documents — se mantiene acá aparte para no generar un import
    circular entre rrhh.py y main.py)."""
    existing = {d.doc_type for d in employee.documents}
    from .models import Document
    for key, _label in DOC_TYPES:
        if key not in existing:
            db.add(Document(employee_id=employee.id, doc_type=key, status=STATUS_PENDIENTE))
    db.commit()


def _enviar_correo(destinatarios: list, asunto: str, cuerpo: str, cc: list = None) -> bool:
    """Correo de texto plano (mismo mecanismo SMTP_* que main.py:
    send_completion_email — se duplica acá para no crear un import circular
    entre rrhh.py y main.py). No bloquea el flujo si SMTP_HOST no está
    configurado: devuelve False y quien llama decide qué avisarle al usuario."""
    import smtplib
    from email.message import EmailMessage
    host = os.environ.get("SMTP_HOST")
    destinatarios = [d for d in (destinatarios or []) if d]
    if not host or not destinatarios:
        return False
    port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    sender = os.environ.get("SMTP_FROM", smtp_user)

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = sender
    msg["To"] = ", ".join(destinatarios)
    cc = [c for c in (cc or []) if c]
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(cuerpo)

    with smtplib.SMTP(host, port, timeout=20) as server:
        server.starttls()
        if smtp_user and smtp_pass:
            server.login(smtp_user, smtp_pass)
        server.send_message(msg, to_addrs=destinatarios + cc)
    return True


def _documento_duplicado(db: Session, tipo_documento: str, numero_documento: str, excluir_employee_id: int = None) -> bool:
    """Punto 2 del pedido: no puede haber dos trabajadores con el mismo tipo
    y número de documento de identidad. Compara contra todos los demás
    registros (ficha_data es JSON, no se puede indexar en SQLite) — a la
    escala de un solo grupo empresarial esto es rápido de sobra."""
    tipo = (tipo_documento or "").strip().lower()
    numero = (numero_documento or "").strip().lower()
    if not tipo or not numero:
        return False
    for other in db.query(Employee).all():
        if excluir_employee_id and other.id == excluir_employee_id:
            continue
        f = other.ficha_data or {}
        if (f.get("tipo_documento") or "").strip().lower() == tipo and (f.get("numero_documento") or "").strip().lower() == numero:
            return True
    return False

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
@router.get("/rrhh", response_class=HTMLResponse)
def rrhh_home(request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)):
    # Punto 3 del pedido: pantalla de inicio tipo "noticias" — cumpleaños de
    # la semana (con foto y saludo de RR.HH. para el/los de hoy, donde otros
    # pueden dejar su propio saludo) y los anuncios de Clima y Cultura que le
    # correspondan a este usuario según su ámbito.
    empresa_id = user.employee.empresa_id if (user.rol == "usuario" and user.employee) else None
    cumple_hoy, cumple_semana = _cumpleanos_de_la_semana(db, empresa_id=empresa_id)
    for item in cumple_hoy:
        item["mensaje_rrhh"] = _mensaje_cumple_rrhh(item["employee"])
        item["saludos"] = db.query(SaludoCumpleanos).filter(
            SaludoCumpleanos.employee_id == item["employee"].id
        ).order_by(SaludoCumpleanos.created_at.desc()).all()
    anuncios = _anuncios_visibles(db, user)
    # Punto 4 del pedido: se marca "visto" la primera vez que a este usuario
    # le aparece el anuncio en su pantalla de inicio; y se arma el contador
    # de "me gusta" (más si este usuario ya lo dio) para el corazón.
    likes_usuario = {r.anuncio_id for r in db.query(AnuncioLike).filter(AnuncioLike.user_id == user.id).all()}
    conteos = {}
    for a in anuncios:
        if not db.query(AnuncioVista).filter(AnuncioVista.anuncio_id == a.id, AnuncioVista.user_id == user.id).first():
            db.add(AnuncioVista(anuncio_id=a.id, user_id=user.id))
        conteos[a.id] = {
            "vistas": db.query(AnuncioVista).filter(AnuncioVista.anuncio_id == a.id).count(),
            "likes": db.query(AnuncioLike).filter(AnuncioLike.anuncio_id == a.id).count(),
            "le_gusta": a.id in likes_usuario,
        }
    db.commit()
    return templates.TemplateResponse(request, "rrhh_home.html", _ctx(
        request, user, cumple_hoy=cumple_hoy, cumple_semana=cumple_semana, anuncios=anuncios,
        conteos_anuncio=conteos, active="home",
    ))


@router.post("/rrhh/saludos/{employee_id}")
def agregar_saludo_cumpleanos(employee_id: int, mensaje: str = Form(...),
                               db: Session = Depends(get_db), user: User = Depends(require_login)):
    mensaje = mensaje.strip()
    if not mensaje:
        raise HTTPException(400, "El saludo no puede estar vacío.")
    if not db.query(Employee).get(employee_id):
        raise HTTPException(404)
    db.add(SaludoCumpleanos(employee_id=employee_id, autor=user.nombre_completo, mensaje=mensaje))
    db.commit()
    return RedirectResponse("/rrhh", status_code=303)


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


def _holding_tiene_unidades(db: Session, holding_id: int) -> bool:
    return db.query(UnidadNegocio).filter(UnidadNegocio.holding_id == holding_id).count() > 0


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


SALUDOS_CUMPLEANOS_RRHH = [
    "¡Feliz cumpleaños, {nombre}! Todo el equipo de Recursos Humanos te desea un día espectacular, rodeado de "
    "quienes más quieres. ¡Que este nuevo año te traiga muchos éxitos!",
    "Hoy es un día especial para ti, {nombre}. Desde Recursos Humanos te mandamos un fuerte abrazo y las mejores "
    "energías para este nuevo año de vida. ¡Feliz cumpleaños!",
    "{nombre}, ¡feliz cumpleaños! Gracias por ser parte de este equipo — esperamos que tengas un día tan grande "
    "como tú. Un cariñoso saludo de Recursos Humanos.",
    "En Recursos Humanos queremos celebrar contigo, {nombre}. ¡Feliz cumpleaños! Que se cumplan todas tus metas "
    "este nuevo año.",
    "¡Feliz vuelta al sol, {nombre}! Que tengas un día lleno de alegría y buenos momentos. Con cariño, tu equipo "
    "de Recursos Humanos.",
    "{nombre}, hoy celebramos contigo un año más de vida. ¡Feliz cumpleaños! Gracias por tu aporte al equipo. "
    "Un abrazo de Recursos Humanos.",
]


def _mensaje_cumple_rrhh(employee: Employee) -> str:
    primer_nombre = (employee.nombre_completo or "").split()[0] if employee.nombre_completo else ""
    plantilla = SALUDOS_CUMPLEANOS_RRHH[employee.id % len(SALUDOS_CUMPLEANOS_RRHH)]
    return plantilla.format(nombre=primer_nombre or employee.nombre_completo)


def _fecha_nacimiento(employee: Employee):
    f = (employee.ficha_data or {}).get("fecha_nacimiento") or ""
    try:
        return datetime.datetime.strptime(f, "%Y-%m-%d").date()
    except ValueError:
        return None


def _cumpleanos_de_la_semana(db: Session, empresa_id: int = None):
    """Punto 3 del pedido: cumpleaños de la semana (lunes a domingo actual),
    separando el/los de hoy. Si se pasa empresa_id, se acota a esa empresa
    (para el rol 'usuario', que solo debería ver a sus propios compañeros);
    sin empresa_id, es para RR.HH./administrador, que ve a todo el personal."""
    hoy = datetime.date.today()
    lunes = hoy - datetime.timedelta(days=hoy.weekday())
    dias_semana = [lunes + datetime.timedelta(days=i) for i in range(7)]

    query = db.query(Employee).filter(Employee.estado == "activo")
    if empresa_id:
        query = query.filter(Employee.empresa_id == empresa_id)

    hoy_lista, semana_lista = [], []
    for e in query.all():
        nac = _fecha_nacimiento(e)
        if not nac:
            continue
        for dia in dias_semana:
            if nac.month == dia.month and nac.day == dia.day:
                item = {"employee": e, "fecha": dia}
                (hoy_lista if dia == hoy else semana_lista).append(item)
                break
    semana_lista.sort(key=lambda i: i["fecha"])
    return hoy_lista, semana_lista


def _anuncios_visibles(db: Session, user: User, limite: int = 12):
    """Punto 4 del pedido: RR.HH./administrador ve todos los anuncios (los
    publica y gestiona); un usuario de autoservicio ('usuario') solo ve los
    de su propio ámbito (su empresa, la unidad de negocio de su empresa, o
    "todo el holding" — incluyendo los anuncios de holding sin uno
    específico elegido, que se toman como "para todo el grupo")."""
    from .models import Anuncio
    query = db.query(Anuncio).filter(Anuncio.activo == True)  # noqa: E712
    if is_staff(user):
        return query.order_by(Anuncio.created_at.desc()).limit(limite).all()

    empresa = user.employee.empresa_rel if user.employee else None
    unidad_id = empresa.unidad_negocio_id if empresa else None
    holding_id = empresa.unidad_negocio.holding_id if (empresa and empresa.unidad_negocio) else None

    visibles = []
    for a in query.order_by(Anuncio.created_at.desc()).all():
        if a.ambito == "holding" and (a.holding_id is None or a.holding_id == holding_id):
            visibles.append(a)
        elif a.ambito == "unidad" and unidad_id and a.unidad_negocio_id == unidad_id:
            visibles.append(a)
        elif a.ambito == "empresa" and empresa and a.empresa_id == empresa.id:
            visibles.append(a)
        if len(visibles) >= limite:
            break
    return visibles


def _organigrama_de(db: Session, employee: Employee):
    """Punto 11 del pedido: a partir del Cargo de la persona (ficha_data.cargo)
    y su empresa, resuelve quién es su jefe y quiénes son sus subordinados —
    buscando, dentro de la MISMA empresa, a las personas activas cuyo cargo
    sea el que corresponde según la jerarquía de Cargos y Funciones (MOF).
    Es un cálculo en vivo (no se guarda), así que nunca queda desincronizado
    si alguien cambia de cargo o de empresa."""
    ficha = employee.ficha_data or {}
    cargo_nombre = (ficha.get("cargo") or "").strip()
    if not cargo_nombre or not employee.empresa_id:
        return {"jefe": None, "jefe_cargo": None, "subordinados": []}
    cargo = db.query(Cargo).filter(Cargo.nombre == cargo_nombre).first()
    if not cargo:
        return {"jefe": None, "jefe_cargo": None, "subordinados": []}

    companeros_activos = db.query(Employee).filter(
        Employee.empresa_id == employee.empresa_id,
        Employee.estado == "activo",
        Employee.id != employee.id,
    ).all()

    jefe = None
    jefe_cargo = cargo.reporta_a.nombre if cargo.reporta_a else None
    if jefe_cargo:
        jefe = next((c for c in companeros_activos if (c.ficha_data or {}).get("cargo") == jefe_cargo), None)

    nombres_subordinados_cargo = {c.nombre for c in cargo.subordinados}
    subordinados = [c for c in companeros_activos if (c.ficha_data or {}).get("cargo") in nombres_subordinados_cargo]

    return {"jefe": jefe, "jefe_cargo": jefe_cargo, "subordinados": subordinados}


def _contratos_no_indefinidos(db: Session, dias_max: int = None):
    """Puntos 12 y 13 del pedido: trabajadores activos con contrato distinto
    de 'Plazo Indeterminado' y con fecha de vencimiento cargada, ordenados
    del más próximo a vencer al más lejano. Si se pasa dias_max, solo
    devuelve los que vencen dentro de esa cantidad de días (puede incluir
    los ya vencidos, para que no se pierdan de vista)."""
    hoy = datetime.date.today()
    resultado = []
    for e in db.query(Employee).filter(Employee.estado == "activo").all():
        f = e.ficha_data or {}
        tipo_contrato = (f.get("tipo_contrato") or "").strip()
        fecha_fin_str = (f.get("fecha_fin_contrato") or "").strip()
        if not tipo_contrato or tipo_contrato == "Plazo Indeterminado" or not fecha_fin_str:
            continue
        try:
            fecha_fin = datetime.datetime.strptime(fecha_fin_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        dias_restantes = (fecha_fin - hoy).days
        if dias_max is not None and dias_restantes > dias_max:
            continue
        resultado.append({
            "employee": e, "tipo_contrato": tipo_contrato,
            "fecha_fin_contrato": fecha_fin, "dias_restantes": dias_restantes,
        })
    resultado.sort(key=lambda r: r["fecha_fin_contrato"])
    return resultado


def _catalogo_en_uso(db: Session, tipo: str, nombre: str) -> bool:
    """True si algún trabajador (activo o cesado) tiene este valor guardado
    en su ficha — no es una FK real (ficha_data es JSON de texto libre), pero
    igual bloqueamos el borrado para no perder de vista que sigue en uso."""
    campos = {
        "area": ["area"], "gerencia": ["gerencia"], "sede": ["sede"],
        "banco": ["banco_haberes", "banco_cts"], "centro_costo": ["centro_costos"],
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


@router.get("/rrhh/parametrizacion/holdings", response_class=HTMLResponse)
def holdings_list(request: Request, error: str = "", db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador"))):
    holdings = db.query(Holding).order_by(Holding.nombre).all()
    bloqueados = {h.id: _holding_tiene_unidades(db, h.id) for h in holdings}
    return templates.TemplateResponse(request, "rrhh_holdings.html", _ctx(
        request, user, holdings=holdings, bloqueados=bloqueados, error=error, active="holdings",
    ))


@router.post("/rrhh/parametrizacion/holding")
def crear_holding(nombre: str = Form(...), descripcion: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    db.add(Holding(nombre=nombre.strip(), descripcion=descripcion.strip() or None))
    db.commit()
    return RedirectResponse("/rrhh/parametrizacion/holdings", status_code=303)


@router.post("/rrhh/parametrizacion/holding/{holding_id}/editar")
def editar_holding(holding_id: int, nombre: str = Form(...), descripcion: str = Form(""),
                    db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    h = db.query(Holding).get(holding_id)
    if h:
        h.nombre = nombre.strip()
        h.descripcion = descripcion.strip() or None
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/holdings", status_code=303)


@router.post("/rrhh/parametrizacion/holding/{holding_id}/logo")
async def subir_logo_holding(holding_id: int, logo: UploadFile = File(...), db: Session = Depends(get_db),
                              user: User = Depends(require_role("administrador"))):
    h = db.query(Holding).get(holding_id)
    if not h:
        raise HTTPException(404)
    dest = os.path.join(FIRMAS_EMPRESA_DIR, f"holding_{holding_id}.png")
    content = await logo.read()
    with open(dest, "wb") as f:
        f.write(content)
    h.logo_path = dest
    db.commit()
    return RedirectResponse("/rrhh/parametrizacion/holdings", status_code=303)


@router.get("/rrhh/parametrizacion/holding/{holding_id}/logo")
def ver_logo_holding(holding_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)):
    h = db.query(Holding).get(holding_id)
    if not h or not h.logo_path or not os.path.exists(h.logo_path):
        raise HTTPException(404)
    return FileResponse(h.logo_path, media_type="image/png")


@router.post("/rrhh/parametrizacion/holding/{holding_id}/toggle")
def toggle_holding(holding_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_role("administrador"))):
    h = db.query(Holding).get(holding_id)
    if h:
        if h.activo and _holding_tiene_unidades(db, holding_id):
            return RedirectResponse(_con_error("/rrhh/parametrizacion/holdings",
                "No se puede desactivar: todavía tiene unidades de negocio asignadas."), status_code=303)
        h.activo = not h.activo
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/holdings", status_code=303)


@router.post("/rrhh/parametrizacion/holding/{holding_id}/eliminar")
def eliminar_holding(holding_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_role("administrador"))):
    h = db.query(Holding).get(holding_id)
    if h:
        if _holding_tiene_unidades(db, holding_id):
            return RedirectResponse(_con_error("/rrhh/parametrizacion/holdings",
                "No se puede eliminar: todavía tiene unidades de negocio asignadas."), status_code=303)
        db.delete(h)
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/holdings", status_code=303)


@router.get("/rrhh/parametrizacion/unidades", response_class=HTMLResponse)
def unidades_list(request: Request, error: str = "", db: Session = Depends(get_db),
                   user: User = Depends(require_role("administrador"))):
    unidades = db.query(UnidadNegocio).order_by(UnidadNegocio.nombre).all()
    holdings = db.query(Holding).filter(Holding.activo == True).order_by(Holding.nombre).all()  # noqa: E712
    bloqueadas = {u.id: _unidad_tiene_empresas(db, u.id) for u in unidades}
    return templates.TemplateResponse(request, "rrhh_unidades.html", _ctx(
        request, user, unidades=unidades, holdings=holdings, bloqueadas=bloqueadas, error=error, active="unidades",
    ))


@router.post("/rrhh/parametrizacion/unidad")
def crear_unidad(nombre: str = Form(...), descripcion: str = Form(""), holding_id: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    db.add(UnidadNegocio(nombre=nombre.strip(), descripcion=descripcion.strip() or None,
                          holding_id=int(holding_id) if holding_id else None))
    db.commit()
    return RedirectResponse("/rrhh/parametrizacion/unidades", status_code=303)


@router.post("/rrhh/parametrizacion/unidad/{unidad_id}/editar")
def editar_unidad(unidad_id: int, nombre: str = Form(...), descripcion: str = Form(""), holding_id: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    u = db.query(UnidadNegocio).get(unidad_id)
    if u:
        u.nombre = nombre.strip()
        u.descripcion = descripcion.strip() or None
        u.holding_id = int(holding_id) if holding_id else None
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
    holdings = db.query(Holding).filter(Holding.activo == True).order_by(Holding.nombre).all()  # noqa: E712
    bloqueadas = {e.id: _empresa_tiene_activos(db, e.id) for e in empresas}
    return templates.TemplateResponse(request, "rrhh_empresas.html", _ctx(
        request, user, empresas=empresas, unidades=unidades, holdings=holdings, regimenes=REGIMENES_LABORALES,
        bloqueadas=bloqueadas, error=error, active="empresas",
    ))


@router.post("/rrhh/parametrizacion/empresa")
def crear_empresa(nombre: str = Form(...), razon_social: str = Form(""), ruc: str = Form(""),
                   unidad_negocio_id: int = Form(...), regimen_laboral: str = Form(""),
                   representante_legal: str = Form(""),
                   gerente_nombre: str = Form(""), gerente_email: str = Form(""),
                   jefe_rrhh_nombre: str = Form(""), jefe_rrhh_email: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    db.add(Empresa(
        nombre=nombre.strip(), razon_social=razon_social.strip() or None, ruc=ruc.strip() or None,
        unidad_negocio_id=unidad_negocio_id, regimen_laboral=regimen_laboral or None,
        representante_legal=representante_legal.strip() or None,
        gerente_nombre=gerente_nombre.strip() or None, gerente_email=gerente_email.strip() or None,
        jefe_rrhh_nombre=jefe_rrhh_nombre.strip() or None, jefe_rrhh_email=jefe_rrhh_email.strip() or None,
    ))
    db.commit()
    return RedirectResponse("/rrhh/parametrizacion/empresas", status_code=303)


@router.get("/rrhh/parametrizacion/empresa/{empresa_id}/editar", response_class=HTMLResponse)
def editar_empresa_form(request: Request, empresa_id: int, db: Session = Depends(get_db),
                         user: User = Depends(require_role("administrador"))):
    e = db.query(Empresa).get(empresa_id)
    if not e:
        raise HTTPException(404)
    holdings = db.query(Holding).filter(Holding.activo == True).order_by(Holding.nombre).all()  # noqa: E712
    unidades = db.query(UnidadNegocio).order_by(UnidadNegocio.nombre).all()
    return templates.TemplateResponse(request, "rrhh_empresa_editar.html", _ctx(
        request, user, e=e, holdings=holdings, unidades=unidades, regimenes=REGIMENES_LABORALES,
        active="empresas",
    ))


@router.post("/rrhh/parametrizacion/empresa/{empresa_id}/editar")
def editar_empresa(empresa_id: int, nombre: str = Form(...), razon_social: str = Form(""), ruc: str = Form(""),
                    unidad_negocio_id: int = Form(...), regimen_laboral: str = Form(""),
                    representante_legal: str = Form(""),
                    gerente_nombre: str = Form(""), gerente_email: str = Form(""),
                    jefe_rrhh_nombre: str = Form(""), jefe_rrhh_email: str = Form(""),
                    db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    e = db.query(Empresa).get(empresa_id)
    if e:
        e.nombre = nombre.strip()
        e.razon_social = razon_social.strip() or None
        e.ruc = ruc.strip() or None
        e.unidad_negocio_id = unidad_negocio_id
        e.regimen_laboral = regimen_laboral or None
        e.representante_legal = representante_legal.strip() or None
        e.gerente_nombre = gerente_nombre.strip() or None
        e.gerente_email = gerente_email.strip() or None
        e.jefe_rrhh_nombre = jefe_rrhh_nombre.strip() or None
        e.jefe_rrhh_email = jefe_rrhh_email.strip() or None
        db.commit()
    return RedirectResponse(f"/rrhh/parametrizacion/empresa/{empresa_id}/editar?ok=1", status_code=303)


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
    return RedirectResponse(f"/rrhh/parametrizacion/empresa/{empresa_id}/editar?ok=1", status_code=303)


@router.post("/rrhh/parametrizacion/empresa/{empresa_id}/logo")
async def subir_logo_empresa(empresa_id: int, logo: UploadFile = File(...), db: Session = Depends(get_db),
                              user: User = Depends(require_role("administrador"))):
    e = db.query(Empresa).get(empresa_id)
    if not e:
        raise HTTPException(404)
    dest = os.path.join(FIRMAS_EMPRESA_DIR, f"logo_empresa_{empresa_id}.png")
    content = await logo.read()
    with open(dest, "wb") as f:
        f.write(content)
    e.logo_path = dest
    db.commit()
    return RedirectResponse(f"/rrhh/parametrizacion/empresa/{empresa_id}/editar?ok=1", status_code=303)


@router.get("/rrhh/parametrizacion/empresa/{empresa_id}/logo")
def ver_logo_empresa(empresa_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)):
    e = db.query(Empresa).get(empresa_id)
    if not e or not e.logo_path or not os.path.exists(e.logo_path):
        raise HTTPException(404)
    return FileResponse(e.logo_path, media_type="image/png")


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


@router.get("/rrhh/parametrizacion/lineas-producto", response_class=HTMLResponse)
def lineas_producto_list(request: Request, error: str = "", db: Session = Depends(get_db),
                          user: User = Depends(require_role("administrador"))):
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
    lineas = db.query(LineaProducto).join(Empresa).order_by(Empresa.nombre, LineaProducto.nombre).all()
    return templates.TemplateResponse(request, "rrhh_lineas_producto.html", _ctx(
        request, user, empresas=empresas, lineas=lineas, error=error, active="lineas_producto",
    ))


@router.post("/rrhh/parametrizacion/linea-producto")
def crear_linea_producto(nombre: str = Form(...), descripcion: str = Form(""), empresa_id: int = Form(...),
                          db: Session = Depends(get_db), user: User = Depends(require_role("administrador"))):
    db.add(LineaProducto(nombre=nombre.strip(), descripcion=descripcion.strip() or None, empresa_id=empresa_id))
    db.commit()
    return RedirectResponse("/rrhh/parametrizacion/lineas-producto", status_code=303)


@router.post("/rrhh/parametrizacion/linea-producto/{linea_id}/editar")
def editar_linea_producto(linea_id: int, nombre: str = Form(...), descripcion: str = Form(""),
                           empresa_id: int = Form(...), db: Session = Depends(get_db),
                           user: User = Depends(require_role("administrador"))):
    lp = db.query(LineaProducto).get(linea_id)
    if lp:
        lp.nombre = nombre.strip()
        lp.descripcion = descripcion.strip() or None
        lp.empresa_id = empresa_id
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/lineas-producto", status_code=303)


@router.post("/rrhh/parametrizacion/linea-producto/{linea_id}/toggle")
def toggle_linea_producto(linea_id: int, db: Session = Depends(get_db),
                           user: User = Depends(require_role("administrador"))):
    lp = db.query(LineaProducto).get(linea_id)
    if lp:
        lp.activo = not lp.activo
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/lineas-producto", status_code=303)


@router.post("/rrhh/parametrizacion/linea-producto/{linea_id}/eliminar")
def eliminar_linea_producto(linea_id: int, db: Session = Depends(get_db),
                             user: User = Depends(require_role("administrador"))):
    lp = db.query(LineaProducto).get(linea_id)
    if lp:
        db.delete(lp)
        db.commit()
    return RedirectResponse("/rrhh/parametrizacion/lineas-producto", status_code=303)


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


@router.post("/rrhh/parametrizacion/catalogo/{item_id}/logo")
async def subir_logo_catalogo(item_id: int, logo: UploadFile = File(...), db: Session = Depends(get_db),
                               user: User = Depends(require_role("administrador"))):
    """Punto 5 del pedido: por ahora solo se usa desde Áreas, pero queda
    disponible para cualquier catálogo por si más adelante hace falta."""
    item = db.query(Catalogo).get(item_id)
    if not item:
        raise HTTPException(404)
    dest = os.path.join(FIRMAS_EMPRESA_DIR, f"catalogo_{item_id}.png")
    content = await logo.read()
    with open(dest, "wb") as f:
        f.write(content)
    item.logo_path = dest
    db.commit()
    return RedirectResponse(f"/rrhh/parametrizacion/catalogo/{item.tipo}", status_code=303)


@router.get("/rrhh/parametrizacion/catalogo/{item_id}/logo")
def ver_logo_catalogo(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)):
    item = db.query(Catalogo).get(item_id)
    if not item or not item.logo_path or not os.path.exists(item.logo_path):
        raise HTTPException(404)
    return FileResponse(item.logo_path, media_type="image/png")


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
                       nivel_4: str = Form(""), conductas_no_deseadas: str = Form(""),
                       db: Session = Depends(get_db),
                       user: User = Depends(require_role("administrador"))):
    if tipo not in TIPO_COMPETENCIA_KEYS:
        raise HTTPException(400, "Tipo inválido.")
    db.add(Competencia(
        tipo=tipo, nombre=nombre.strip(), descripcion=descripcion.strip() or None,
        nivel_1=nivel_1.strip() or None, nivel_2=nivel_2.strip() or None,
        nivel_3=nivel_3.strip() or None, nivel_4=nivel_4.strip() or None,
        conductas_no_deseadas=(conductas_no_deseadas.strip() or None) if tipo == "valor" else None,
    ))
    db.commit()
    return RedirectResponse("/rrhh/parametrizacion/competencias", status_code=303)


@router.post("/rrhh/parametrizacion/competencia/{item_id}/editar")
def editar_competencia(item_id: int, tipo: str = Form(...), nombre: str = Form(...), descripcion: str = Form(""),
                        nivel_1: str = Form(""), nivel_2: str = Form(""), nivel_3: str = Form(""),
                        nivel_4: str = Form(""), conductas_no_deseadas: str = Form(""),
                        db: Session = Depends(get_db),
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
        item.conductas_no_deseadas = (conductas_no_deseadas.strip() or None) if tipo == "valor" else None
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
    # Punto 1 del pedido: después de cambiar la contraseña, va a la pantalla
    # de entrada (menú + imagen de MICELIO), no se queda en Mi cuenta.
    return RedirectResponse("/rrhh", status_code=303)


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


@router.get("/rrhh/personal/export.xlsx")
def personal_export(empresa_id: str = "", unidad_id: str = "", q: str = "", estado: str = "",
                     db: Session = Depends(get_db),
                     user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    """Punto 2 del pedido: descargar a Excel según el filtro activo en
    Personal (mismos parámetros que personal_list), o todos si no hay filtro."""
    from .export_xlsx import build_export
    query = db.query(Employee)
    if empresa_id:
        query = query.filter(Employee.empresa_id == int(empresa_id))
    if unidad_id:
        query = query.join(Empresa, Employee.empresa_id == Empresa.id).filter(Empresa.unidad_negocio_id == int(unidad_id))
    if estado:
        query = query.filter(Employee.estado == estado)
    if q:
        query = query.filter(Employee.nombre_completo.ilike(f"%{q}%"))
    empleados = query.order_by(Employee.nombre_completo).all()
    path = build_export(db, employees=empleados)
    return FileResponse(
        path, filename="Base de Datos Maestra (Portal RRHH).xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/rrhh/personal/nuevo")
def personal_nuevo_crear(db: Session = Depends(get_db),
                          user: User = Depends(require_role("administrador", "opeoka"))):
    """Punto 9.2 del pedido: "Agregar trabajador" ya no pasa por un mini
    formulario de nombre/correo/empresa — crea el registro en blanco y va
    directo a la ficha completa, donde se llena todo desde cero (el nombre
    se termina de definir ahí, en la Sección I). Es POST (no GET) para que
    un simple link/prefetch del navegador no cree trabajadores fantasma."""
    emp = Employee(nombre_completo="(Nuevo trabajador)", estado="activo", status="completo")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return RedirectResponse(f"/rrhh/personal/{emp.id}/ficha", status_code=303)


@router.post("/rrhh/personal/nueva-seleccion")
def personal_nueva_seleccion(nombre_completo: str = Form(...), email: str = Form(""), empresa_id: str = Form(""),
                              db: Session = Depends(get_db),
                              user: User = Depends(require_role("administrador", "opeoka"))):
    """Punto 2 del pedido: segunda forma de dar de alta a un trabajador —
    genera el enlace de Selección (/f/{token}) para que la propia persona
    llene su ficha (con menos secciones que la ficha completa; ver
    formulario.html), en vez de que RR.HH. la cargue directo."""
    empresa = db.query(Empresa).get(int(empresa_id)) if empresa_id else None
    emp = Employee(
        nombre_completo=nombre_completo.strip(), email=email.strip() or None,
        empresa_id=empresa.id if empresa else None, empresa=empresa.nombre if empresa else None,
        estado="activo", status=STATUS_PENDIENTE,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    _ensure_documents(db, emp)
    return RedirectResponse(f"/rrhh/personal?enlace={emp.token}", status_code=303)


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

    # Punto 2 del pedido: cuando la ficha se llenó por Selección (el propio
    # trabajador), avisar qué datos de completar RR.HH./Administrador todavía
    # faltan (Sección IV completa, fecha de afiliación/seguro, cuenta CTS).
    f = emp.ficha_data or {}
    faltan_datos = []
    if emp.ficha_data:
        if not (f.get("cargo") or "").strip():
            faltan_datos.append("Cargo / Datos Laborales (Sección IV)")
        if not (f.get("fecha_afiliacion") or "").strip():
            faltan_datos.append("Fecha de Afiliación (Sección VI)")
        if not (f.get("seguro") or "").strip():
            faltan_datos.append("Seguro (Sección VI)")
        if not (f.get("cuenta_cts") or "").strip():
            faltan_datos.append("Cuenta CTS (Sección V)")

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
        faltan_datos=faltan_datos, renovaciones=emp.renovaciones_contrato,
        organigrama=_organigrama_de(db, emp),
        active="personal",
    ))


@router.post("/rrhh/personal/{employee_id}/renovar-contrato")
def renovar_contrato(employee_id: int, nueva_fecha_contrato: str = Form(...),
                      nueva_fecha_fin_contrato: str = Form(""), tipo_contrato: str = Form(""),
                      notas: str = Form(""), db: Session = Depends(get_db),
                      user: User = Depends(require_role("administrador", "opeoka"))):
    """Punto 2.5 del pedido: al renovar el contrato, los campos fecha_contrato
    y fecha_fin_contrato de la ficha se actualizan, pero queda un registro
    permanente de cada renovación (fechas anteriores y nuevas, tipo, quién la
    registró)."""
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    ficha = dict(emp.ficha_data or {})
    anterior = ficha.get("fecha_contrato") or ""
    fin_anterior = ficha.get("fecha_fin_contrato") or ""
    db.add(ContratoRenovacion(
        employee_id=emp.id, fecha_contrato_anterior=anterior or None,
        fecha_contrato_nueva=nueva_fecha_contrato,
        fecha_fin_contrato_anterior=fin_anterior or None,
        fecha_fin_contrato_nueva=nueva_fecha_fin_contrato.strip() or None,
        tipo_contrato=tipo_contrato.strip() or None,
        notas=notas.strip() or None, registrado_por=user.nombre_completo,
    ))
    ficha["fecha_contrato"] = nueva_fecha_contrato
    if nueva_fecha_fin_contrato.strip():
        ficha["fecha_fin_contrato"] = nueva_fecha_fin_contrato.strip()
    if tipo_contrato.strip():
        ficha["tipo_contrato"] = tipo_contrato.strip()
    emp.ficha_data = ficha
    db.commit()
    return RedirectResponse(f"/rrhh/personal/{employee_id}#laboral", status_code=303)


# ---------------------------------------------------------------------------
# Punto 14 del pedido: solicitud de aprobación de renovación de contrato por
# correo, con enlace de un solo uso para que el gerente de la empresa
# apruebe o rechace sin necesitar usuario en MICELIO. Si rechaza, se genera
# la carta de aviso de no renovación (modelo inicial, ver pdf_signed.py).
# ---------------------------------------------------------------------------
@router.get("/rrhh/personal/{employee_id}/solicitar-renovacion", response_class=HTMLResponse)
def solicitar_renovacion_form(request: Request, employee_id: int, db: Session = Depends(get_db),
                               user: User = Depends(require_role("administrador", "opeoka"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    empresa = emp.empresa_rel
    solicitudes = db.query(SolicitudRenovacion).filter(
        SolicitudRenovacion.employee_id == employee_id
    ).order_by(SolicitudRenovacion.created_at.desc()).all()
    return templates.TemplateResponse(request, "rrhh_solicitar_renovacion.html", _ctx(
        request, user, e=emp, empresa=empresa, solicitudes=solicitudes, active="contratos",
    ))


@router.post("/rrhh/personal/{employee_id}/solicitar-renovacion")
def solicitar_renovacion_crear(employee_id: int, meses_renovacion: str = Form(""),
                                nueva_fecha_fin_contrato: str = Form(...),
                                aumento_sueldo: str = Form(""), monto_aumento: str = Form(""),
                                movilidad: str = Form(""), otra_comision: str = Form(""),
                                notas: str = Form(""), db: Session = Depends(get_db),
                                user: User = Depends(require_role("administrador", "opeoka"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    empresa = emp.empresa_rel
    if not empresa or not empresa.gerente_email:
        return RedirectResponse(_con_error(f"/rrhh/personal/{employee_id}/solicitar-renovacion",
            "Esta empresa no tiene cargado el correo del gerente (Parámetros > Empresas > Editar)."), status_code=303)

    solicitud = SolicitudRenovacion(
        employee_id=employee_id,
        meses_renovacion=int(meses_renovacion) if meses_renovacion.strip().isdigit() else None,
        nueva_fecha_fin_contrato=nueva_fecha_fin_contrato,
        aumento_sueldo=bool(aumento_sueldo), monto_aumento=monto_aumento.strip() or None,
        movilidad=movilidad.strip() or None, otra_comision=otra_comision.strip() or None,
        notas=notas.strip() or None, solicitado_por=user.nombre_completo,
    )
    db.add(solicitud)
    db.commit()
    db.refresh(solicitud)

    ficha = emp.ficha_data or {}
    detalle = [
        f"Trabajador: {emp.nombre_completo}",
        f"Empresa: {empresa.nombre}",
        f"Cargo: {ficha.get('cargo', '—')}",
        f"Contrato actual vence: {ficha.get('fecha_fin_contrato', '—')}",
        f"Nueva fecha de vencimiento propuesta: {nueva_fecha_fin_contrato}",
    ]
    if solicitud.meses_renovacion:
        detalle.append(f"Meses de renovación: {solicitud.meses_renovacion}")
    detalle.append(f"¿Aumento de sueldo base?: {'Sí' + (f' — {monto_aumento}' if monto_aumento.strip() else '') if aumento_sueldo else 'No'}")
    if movilidad.strip():
        detalle.append(f"Movilidad: {movilidad.strip()}")
    if otra_comision.strip():
        detalle.append(f"Otra comisión: {otra_comision.strip()}")
    if notas.strip():
        detalle.append(f"Notas de RR.HH.: {notas.strip()}")

    link = _public_base_url() + f"renovacion/{solicitud.token}"
    cuerpo = (
        f"Hola{(' ' + empresa.gerente_nombre) if empresa.gerente_nombre else ''},\n\n"
        f"Recursos Humanos solicita tu aprobación para renovar el contrato de {emp.nombre_completo}.\n\n"
        + "\n".join(detalle) +
        f"\n\nPara aprobar o rechazar esta renovación, entra a este enlace:\n{link}\n\n"
        "Saludos,\nRecursos Humanos — DIGETEL GROUP"
    )
    enviado = _enviar_correo(
        [empresa.gerente_email], f"Aprobación de renovación de contrato — {emp.nombre_completo}",
        cuerpo, cc=[empresa.jefe_rrhh_email] if empresa.jefe_rrhh_email else None,
    )
    mensaje = "correo_enviado" if enviado else "correo_no_configurado"
    return RedirectResponse(f"/rrhh/personal/{employee_id}/solicitar-renovacion?ok={mensaje}", status_code=303)


def _public_base_url() -> str:
    """Base pública del sitio para armar el enlace del correo. Usa
    PUBLIC_BASE_URL si está definida (recomendado en producción, p.ej.
    https://micelio.digetelperu.com); si no, localhost, para que al menos
    funcione probando en la computadora."""
    base = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    return base.rstrip("/") + "/"


@router.get("/renovacion/{token}", response_class=HTMLResponse)
def renovacion_confirmar(request: Request, token: str, db: Session = Depends(get_db)):
    """Página pública (sin login) a la que llega el gerente desde el correo."""
    solicitud = db.query(SolicitudRenovacion).filter(SolicitudRenovacion.token == token).first()
    if not solicitud:
        raise HTTPException(404, "Enlace no válido.")
    return templates.TemplateResponse(request, "renovacion_confirmar.html", {
        "s": solicitud, "e": solicitud.employee, "empresa": solicitud.employee.empresa_rel,
    })


@router.post("/renovacion/{token}/aprobar")
def renovacion_aprobar(request: Request, token: str, db: Session = Depends(get_db)):
    solicitud = db.query(SolicitudRenovacion).filter(SolicitudRenovacion.token == token).first()
    if not solicitud:
        raise HTTPException(404, "Enlace no válido.")
    if solicitud.estado != "pendiente":
        return RedirectResponse(f"/renovacion/{token}", status_code=303)
    emp = solicitud.employee
    ficha = dict(emp.ficha_data or {})
    fin_anterior = ficha.get("fecha_fin_contrato") or ""
    fecha_anterior = ficha.get("fecha_contrato") or ""
    hoy = datetime.date.today().isoformat()
    db.add(ContratoRenovacion(
        employee_id=emp.id, fecha_contrato_anterior=fecha_anterior or None, fecha_contrato_nueva=hoy,
        fecha_fin_contrato_anterior=fin_anterior or None,
        fecha_fin_contrato_nueva=solicitud.nueva_fecha_fin_contrato,
        tipo_contrato=ficha.get("tipo_contrato"),
        notas=f"Renovación aprobada por el gerente vía correo. {solicitud.notas or ''}".strip(),
        registrado_por="Gerente (aprobación por correo)",
    ))
    ficha["fecha_contrato"] = hoy
    ficha["fecha_fin_contrato"] = solicitud.nueva_fecha_fin_contrato
    emp.ficha_data = ficha
    solicitud.estado = "aprobado"
    solicitud.respondido_at = datetime.datetime.utcnow()
    solicitud.respondido_ip = request.client.host if request.client else None
    db.commit()
    return RedirectResponse(f"/renovacion/{token}", status_code=303)


@router.post("/renovacion/{token}/rechazar")
def renovacion_rechazar(request: Request, token: str, db: Session = Depends(get_db)):
    solicitud = db.query(SolicitudRenovacion).filter(SolicitudRenovacion.token == token).first()
    if not solicitud:
        raise HTTPException(404, "Enlace no válido.")
    if solicitud.estado != "pendiente":
        return RedirectResponse(f"/renovacion/{token}", status_code=303)
    from .pdf_signed import build_no_renovacion_pdf
    emp = solicitud.employee
    ficha = emp.ficha_data or {}
    empresa = emp.empresa_rel
    out_path = os.path.join(GENERATED_DIR, f"no_renovacion_{emp.token}_{solicitud.id}.pdf")
    build_no_renovacion_pdf(
        nombre_completo=emp.nombre_completo, tipo_documento=ficha.get("tipo_documento"),
        numero_documento=ficha.get("numero_documento"), cargo=ficha.get("cargo"),
        tipo_contrato=ficha.get("tipo_contrato"), fecha_fin_contrato=ficha.get("fecha_fin_contrato") or "—",
        fecha_emision=datetime.date.today().strftime("%d/%m/%Y"),
        empresa_nombre=empresa.nombre if empresa else "",
        representante_legal=empresa.representante_legal if empresa else None,
        firma_empresa_path=empresa.firma_representante_path if empresa else None,
        out_path=out_path,
    )
    solicitud.estado = "rechazado"
    solicitud.respondido_at = datetime.datetime.utcnow()
    solicitud.respondido_ip = request.client.host if request.client else None
    solicitud.carta_no_renovacion_path = out_path
    db.commit()
    return RedirectResponse(f"/renovacion/{token}", status_code=303)


@router.get("/rrhh/solicitudes-renovacion/{solicitud_id}/carta")
def descargar_carta_no_renovacion(solicitud_id: int, db: Session = Depends(get_db),
                                   user: User = Depends(require_role("administrador", "opeoka"))):
    solicitud = db.query(SolicitudRenovacion).get(solicitud_id)
    if not solicitud or not solicitud.carta_no_renovacion_path or not os.path.exists(solicitud.carta_no_renovacion_path):
        raise HTTPException(404, "Carta no disponible.")
    fname = f"Aviso de No Renovación - {solicitud.employee.nombre_completo}.pdf"
    return FileResponse(solicitud.carta_no_renovacion_path, filename=fname, media_type="application/pdf")


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
    ficha_nueva = payload.get("ficha", {})

    # Punto 2 del pedido: no puede haber dos trabajadores con el mismo tipo
    # y número de documento de identidad.
    if _documento_duplicado(db, ficha_nueva.get("tipo_documento"), ficha_nueva.get("numero_documento"),
                             excluir_employee_id=employee_id):
        return JSONResponse({
            "ok": False,
            "error": "Ya existe otro trabajador registrado con ese mismo tipo y número de documento de identidad.",
        }, status_code=400)

    emp.ficha_data = ficha_nueva

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
              user: User = Depends(require_role("administrador"))):
    data = kpis_module.resumen_dashboard(db, dias=dias)
    max_empresa = max([c for _, c in data["headcount_empresa"]], default=0) or 1
    max_unidad = max([c for _, c in data["headcount_unidad"]], default=0) or 1
    return templates.TemplateResponse(request, "rrhh_dashboard.html", _ctx(
        request, user, data=data, max_empresa=max_empresa, max_unidad=max_unidad,
        contratos_por_vencer=_contratos_no_indefinidos(db, dias_max=30), active="dashboard",
    ))


# ---------------------------------------------------------------------------
# Contratos / Renovaciones (puntos 12 y 13 del pedido)
# ---------------------------------------------------------------------------
@router.get("/rrhh/contratos", response_class=HTMLResponse)
def contratos_list(request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_role("administrador", "conta", "opeoka"))):
    return templates.TemplateResponse(request, "rrhh_contratos.html", _ctx(
        request, user, contratos=_contratos_no_indefinidos(db), active="contratos",
    ))
