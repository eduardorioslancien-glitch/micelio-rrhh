# -*- coding: utf-8 -*-
"""
Portal RR.HH. DIGETEL GROUP — prototipo funcional.

Flujo:
  RR.HH. (admin, sin login en este prototipo) genera un enlace único por
  trabajador -> el trabajador abre el enlace, llena su ficha completa (11
  secciones), adjunta documentos (CV, CUL, antecedentes, otros) y firma
  electrónicamente cada uno de los documentos legales -> el sistema genera
  los PDF finales (con la firma incrustada y un pie de auditoría), actualiza
  la base de datos, deja un registro de envío/apertura/firma para cada
  documento, y envía un correo de confirmación al completar el legajo.

Ejecutar:
    uvicorn app.main:app --reload --port 8000
"""
import base64
import datetime
import hashlib
import json
import mimetypes
import os
import smtplib
import uuid
from email.message import EmailMessage

from fastapi import FastAPI, Request, Depends, Form, HTTPException, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from .database import init_db, get_db, SessionLocal
from .models import (
    Employee, Document, Signature, AuditLog, Attachment, Catalogo, Cargo,
    DOC_TYPES, DOC_TYPE_KEYS, STATUS_PENDIENTE, STATUS_ABIERTO, STATUS_FIRMADO,
    ATTACHMENT_TYPES, ATTACHMENT_TYPE_KEYS, CATALOGO_TIPO_KEYS, TIPOS_LICENCIA, NIVELES_EDUCATIVOS,
)
from .auth import NotAuthenticated, Forbidden, MustChangePassword, require_role
from . import rrhh as rrhh_module
from . import reclutamiento as reclutamiento_module
from . import clima as clima_module
from . import public_landing as public_landing_module
from .seed import seed_initial_data
from .pdf_signed import build_pdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
GENERATED_DIR = os.path.join(BASE_DIR, "generated")
SIGNATURES_DIR = os.path.join(BASE_DIR, "signatures")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
LEGAL_TEXTS_PATH = os.path.join(BASE_DIR, "legal_texts.json")

os.makedirs(GENERATED_DIR, exist_ok=True)
os.makedirs(SIGNATURES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

with open(LEGAL_TEXTS_PATH, encoding="utf-8") as f:
    LEGAL_TEXTS = json.load(f)

def _get_or_create_secret_key() -> str:
    """La clave que firma la cookie de sesión. Si se define RRHH_SECRET_KEY
    (recomendado en un servidor real) se usa esa; si no, se genera una vez y
    se guarda en disco para que no cambie en cada reinicio (lo que forzaría a
    todos a volver a loguearse) ni quede un valor fijo igual en todas las
    instalaciones de este prototipo."""
    env_key = os.environ.get("RRHH_SECRET_KEY")
    if env_key:
        return env_key
    key_path = os.path.join(PROJECT_DIR, "data", "secret_key.txt")
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    if os.path.exists(key_path):
        with open(key_path, "r", encoding="utf-8") as f:
            existing = f.read().strip()
            if existing:
                return existing
    import secrets as _secrets
    new_key = _secrets.token_hex(32)
    with open(key_path, "w", encoding="utf-8") as f:
        f.write(new_key)
    return new_key


app = FastAPI(title="Sistema RR.HH. DIGETEL GROUP")
app.add_middleware(SessionMiddleware, secret_key=_get_or_create_secret_key())
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

init_db()
with SessionLocal() as _db:
    seed_initial_data(_db)

app.include_router(rrhh_module.router)
app.include_router(reclutamiento_module.router)
app.include_router(clima_module.router)
app.include_router(public_landing_module.router)


@app.exception_handler(NotAuthenticated)
def _handle_not_authenticated(request: Request, exc: NotAuthenticated):
    return RedirectResponse(f"/login?next={exc.next_path}", status_code=303)


@app.exception_handler(Forbidden)
def _handle_forbidden(request: Request, exc: Forbidden):
    return HTMLResponse(
        "<h2 style='font-family:sans-serif; color:#9C0006; text-align:center; margin-top:80px;'>"
        "No tienes permiso para acceder a esta sección.</h2>"
        "<p style='text-align:center;'><a href='/rrhh'>Volver</a></p>",
        status_code=403,
    )


@app.exception_handler(MustChangePassword)
def _handle_must_change_password(request: Request, exc: MustChangePassword):
    return RedirectResponse("/rrhh/mi-cuenta?forzado=1", status_code=303)


DOC_LABELS = dict(DOC_TYPES)
ATTACHMENT_LABELS = dict(ATTACHMENT_TYPES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ensure_documents(db: Session, employee: Employee):
    existing = {d.doc_type for d in employee.documents}
    for key, _label in DOC_TYPES:
        if key not in existing:
            db.add(Document(employee_id=employee.id, doc_type=key, status=STATUS_PENDIENTE))
    db.commit()


def log_event(db: Session, employee: Employee, event: str, request: Request = None,
              document: Document = None, meta: dict = None):
    ip = request.client.host if request and request.client else None
    ua = request.headers.get("user-agent") if request else None
    db.add(AuditLog(
        employee_id=employee.id,
        document_id=document.id if document else None,
        event=event, ip_address=ip, user_agent=ua, meta=meta or {},
    ))
    db.commit()


def get_employee_or_404(db: Session, token: str) -> Employee:
    emp = db.query(Employee).filter(Employee.token == token).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Enlace no válido o expirado.")
    return emp


def doc_status_map(employee: Employee):
    m = {d.doc_type: d for d in employee.documents}
    return m


def documento_duplicado(db: Session, tipo_documento: str, numero_documento: str, excluir_employee_id: int = None) -> bool:
    """Punto 2 del pedido (rrhh.py tiene la misma función — se duplica acá
    para no crear un import circular con el router de rrhh)."""
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


def compute_overall_status(employee: Employee):
    docs = doc_status_map(employee)
    signed = sum(1 for d in docs.values() if d.status == STATUS_FIRMADO)
    if signed == 0 and not employee.ficha_data:
        return "pendiente"
    if signed == len(DOC_TYPE_KEYS):
        return "completo"
    return "en_proceso"


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/rrhh")


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db),
                     user=Depends(require_role("administrador", "conta", "opeoka"))):
    employees = db.query(Employee).order_by(Employee.created_at.desc()).all()
    rows = []
    for e in employees:
        docs = doc_status_map(e)
        rows.append({
            "e": e,
            "overall": compute_overall_status(e),
            "docs": [{"key": k, "label": lbl, "status": docs.get(k).status if docs.get(k) else "pendiente",
                      "doc_id": docs.get(k).id if docs.get(k) else None}
                     for k, lbl in DOC_TYPES],
        })
    from .rrhh import ROLE_LABELS
    return templates.TemplateResponse(request, "admin_dashboard.html", {
        "rows": rows, "doc_types": DOC_TYPES, "user": user, "role_labels": ROLE_LABELS, "active": "seleccion",
    })


@app.post("/admin/nuevo")
def admin_nuevo(request: Request, nombre_completo: str = Form(...), email: str = Form(""),
                 empresa: str = Form("Digetel"), db: Session = Depends(get_db),
                 user=Depends(require_role("administrador", "opeoka"))):
    emp = Employee(nombre_completo=nombre_completo.strip(), email=email.strip() or None, empresa=empresa)
    db.add(emp)
    db.commit()
    db.refresh(emp)
    ensure_documents(db, emp)
    log_event(db, emp, "enlace_generado", request)
    return RedirectResponse(f"/admin?nuevo={emp.token}", status_code=303)


@app.get("/admin/empleado/{employee_id}", response_class=HTMLResponse)
def admin_detalle(request: Request, employee_id: int, db: Session = Depends(get_db),
                   user=Depends(require_role("administrador", "conta", "opeoka"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    docs = doc_status_map(emp)
    logs = sorted(emp.audit_logs, key=lambda l: l.created_at, reverse=True)
    from .rrhh import ROLE_LABELS
    return templates.TemplateResponse(request, "admin_detalle.html", {
        "e": emp, "docs": docs, "doc_types": DOC_TYPES, "logs": logs,
        "attachments": emp.attachments, "attachment_types": ATTACHMENT_TYPES,
        "attachment_labels": ATTACHMENT_LABELS, "user": user, "role_labels": ROLE_LABELS, "active": "seleccion",
        "familia": emp.familia_data or [], "educacion": emp.educacion_data or [],
        "experiencia": emp.experiencia_data or [], "capacitaciones": emp.capacitaciones_data or [],
    })


@app.get("/admin/export.xlsx")
def admin_export(db: Session = Depends(get_db), user=Depends(require_role("administrador", "conta"))):
    from .export_xlsx import build_export
    path = build_export(db)
    return FileResponse(
        path,
        filename="Base de Datos Maestra (Portal RRHH).xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/descargas/{document_id}")
def descargar_pdf(document_id: int, db: Session = Depends(get_db),
                   user=Depends(require_role("administrador", "conta", "opeoka"))):
    doc = db.query(Document).get(document_id)
    if not doc or not doc.pdf_path or not os.path.exists(doc.pdf_path):
        raise HTTPException(404, "Documento no disponible todavía.")
    fname = f"{DOC_LABELS.get(doc.doc_type, doc.doc_type)} - {doc.employee.nombre_completo}.pdf"
    return FileResponse(doc.pdf_path, filename=fname, media_type="application/pdf")


@app.get("/adjuntos/{attachment_id}")
def descargar_adjunto(attachment_id: int, db: Session = Depends(get_db),
                       user=Depends(require_role("administrador", "conta", "opeoka"))):
    att = db.query(Attachment).get(attachment_id)
    if not att or not os.path.exists(att.file_path):
        raise HTTPException(404, "Archivo no disponible.")
    return FileResponse(att.file_path, filename=att.filename, media_type=att.content_type or "application/octet-stream")


# ---------------------------------------------------------------------------
# Exportador selectivo para SUNAFIL (punto 4 del pedido: presentar solo las
# secciones del legajo que se necesiten ante una revisión).
# ---------------------------------------------------------------------------
@app.get("/admin/empleado/{employee_id}/exportar-sunafil")
def exportar_sunafil(employee_id: int, docs: list[str] = Query(default=[]),
                      adjuntos: bool = Query(default=False), db: Session = Depends(get_db),
                      user=Depends(require_role("administrador", "conta", "opeoka"))):
    from .sunafil_export import build_sunafil_pdf
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    out_path = build_sunafil_pdf(emp, docs_seleccionados=docs, incluir_adjuntos=adjuntos,
                                  generated_dir=GENERATED_DIR)
    if not out_path:
        raise HTTPException(400, "No se seleccionó ninguna sección disponible para exportar.")
    fname = f"Legajo SUNAFIL - {emp.nombre_completo}.pdf"
    return FileResponse(out_path, filename=fname, media_type="application/pdf")


# ---------------------------------------------------------------------------
# Formulario del trabajador
# ---------------------------------------------------------------------------
@app.get("/f/{token}", response_class=HTMLResponse)
def formulario(request: Request, token: str, db: Session = Depends(get_db)):
    emp = get_employee_or_404(db, token)
    ensure_documents(db, emp)
    if emp.link_opened_at is None:
        emp.link_opened_at = datetime.datetime.utcnow()
        db.commit()
        log_event(db, emp, "formulario_abierto", request)

    catalogos = {
        tipo: [c.nombre for c in db.query(Catalogo).filter(Catalogo.tipo == tipo, Catalogo.activo == True)  # noqa: E712
               .order_by(Catalogo.nombre).all()]
        for tipo in CATALOGO_TIPO_KEYS
    }
    # Cargo/Puesto ya no es un Catalogo simple: viene del MOF completo (Cargo).
    cargos_activos = [c.nombre for c in db.query(Cargo).filter(Cargo.activo == True)  # noqa: E712
                       .order_by(Cargo.nombre).all()]
    # "activos" para este selector = personal que ya completó su legajo, no
    # cualquier postulante recién invitado (Employee.estado por defecto es
    # "activo" incluso mientras todavía está llenando su ficha).
    empleados_activos = [
        nombre for (nombre,) in db.query(Employee.nombre_completo)
        .filter(Employee.estado == "activo", Employee.status == "completo")
        .filter(Employee.token != token)
        .order_by(Employee.nombre_completo).all()
    ]
    return templates.TemplateResponse(request, "formulario.html", {
        "e": emp, "doc_types": DOC_TYPES, "legal": LEGAL_TEXTS,
        "catalogos": catalogos, "cargos_activos": cargos_activos, "empleados_activos": empleados_activos,
        "tipos_licencia": TIPOS_LICENCIA, "niveles_educativos": NIVELES_EDUCATIVOS,
    })


@app.get("/f/{token}/estado")
def formulario_estado(token: str, db: Session = Depends(get_db)):
    emp = get_employee_or_404(db, token)
    docs = doc_status_map(emp)
    return {
        "nombre_completo": emp.nombre_completo,
        "empresa": emp.empresa,
        "ficha_data": emp.ficha_data or {},
        "documentos": {k: (docs.get(k).status if docs.get(k) else "pendiente") for k, _ in DOC_TYPES},
        "overall": compute_overall_status(emp),
    }


@app.post("/f/{token}/ficha")
async def guardar_ficha(token: str, request: Request, db: Session = Depends(get_db)):
    emp = get_employee_or_404(db, token)
    payload = await request.json()
    ficha_nueva = payload.get("ficha", {})

    if documento_duplicado(db, ficha_nueva.get("tipo_documento"), ficha_nueva.get("numero_documento"),
                            excluir_employee_id=emp.id):
        return JSONResponse({
            "ok": False,
            "error": "Ya existe otra persona registrada con ese mismo tipo y número de documento de identidad. "
                     "Si crees que es un error, comunícate con Recursos Humanos.",
        }, status_code=400)

    emp.ficha_data = ficha_nueva

    # Código de trabajador: lo genera el sistema (2 primeras letras de la
    # empresa + N.° de documento), no lo escribe el trabajador. Se calcula
    # aquí (server-side) para que sea la fuente de verdad, aunque el
    # navegador ya muestre una vista previa igual mientras se llena la ficha.
    numero_doc = (emp.ficha_data.get("numero_documento") or "").strip()
    empresa_nombre = emp.empresa or emp.ficha_data.get("empresa") or ""
    prefijo = "".join(ch for ch in empresa_nombre if ch.isalpha())[:2].upper()
    if numero_doc and prefijo:
        emp.ficha_data["codigo_trabajador"] = f"{prefijo}{numero_doc}"

    emp.familia_data = payload.get("familia", [])
    emp.educacion_data = payload.get("educacion", [])
    emp.experiencia_data = payload.get("experiencia", [])
    emp.capacitaciones_data = payload.get("capacitaciones", [])
    # Compatibilidad: seguimos alimentando derechohabientes_data (cónyuge/hijos)
    # a partir de la tabla de familia, por si algo externo todavía lo usa.
    familia = emp.familia_data or []
    conyuge = next((f for f in familia if f.get("parentesco") in ("Cónyuge", "Conviviente")), None)
    hijos = [f for f in familia if f.get("parentesco") == "Hijo(a)"]
    emp.derechohabientes_data = {
        "conyuge": {"nombre": conyuge.get("nombre", ""), "dni": conyuge.get("dni", "")} if conyuge else {},
        "hijos": [{"nombre": h.get("nombre", ""), "documento": h.get("dni", ""),
                   "fecha_nacimiento": h.get("fecha_nacimiento", "")} for h in hijos],
    }
    if payload.get("nombre_completo"):
        emp.nombre_completo = payload["nombre_completo"]
    db.commit()
    ensure_documents(db, emp)
    for d in emp.documents:
        if d.status == STATUS_PENDIENTE:
            d.status = STATUS_ABIERTO
    db.commit()
    log_event(db, emp, "ficha_guardada", request)
    return {"ok": True}


@app.post("/f/{token}/documentos")
async def subir_documento(token: str, request: Request, tipo: str = Form(...),
                            archivo: UploadFile = File(...), db: Session = Depends(get_db)):
    emp = get_employee_or_404(db, token)
    if tipo not in ATTACHMENT_TYPE_KEYS:
        raise HTTPException(400, "Tipo de documento inválido.")
    emp_dir = os.path.join(UPLOADS_DIR, emp.token)
    os.makedirs(emp_dir, exist_ok=True)
    safe_name = f"{tipo}_{uuid.uuid4().hex[:8]}_{archivo.filename}"
    dest_path = os.path.join(emp_dir, safe_name)
    content = await archivo.read()
    with open(dest_path, "wb") as f:
        f.write(content)
    att = Attachment(
        employee_id=emp.id, tipo=tipo, filename=archivo.filename,
        file_path=dest_path, content_type=archivo.content_type,
    )
    db.add(att)
    db.commit()
    log_event(db, emp, "documento_adjuntado", request, meta={"tipo": tipo, "nombre_archivo": archivo.filename})
    return {"ok": True}


@app.post("/f/{token}/firmar/{doc_type}")
async def firmar_documento(token: str, doc_type: str, request: Request, db: Session = Depends(get_db)):
    if doc_type not in DOC_TYPE_KEYS:
        raise HTTPException(400, "Tipo de documento inválido.")
    emp = get_employee_or_404(db, token)
    ensure_documents(db, emp)
    doc = next(d for d in emp.documents if d.doc_type == doc_type)

    payload = await request.json()
    sig_b64 = payload.get("signature_image", "")
    if not sig_b64 or "," not in sig_b64:
        raise HTTPException(400, "Falta la firma.")

    # --- Guardar imagen de la firma ---
    img_bytes = base64.b64decode(sig_b64.split(",", 1)[1])
    sig_filename = f"{emp.token}_{doc_type}_{uuid.uuid4().hex[:8]}.png"
    sig_path = os.path.join(SIGNATURES_DIR, sig_filename)
    with open(sig_path, "wb") as f:
        f.write(img_bytes)

    signed_at = datetime.datetime.utcnow()
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")

    # --- Construir el payload de datos que va impreso en el documento firmado ---
    doc_fields = build_doc_fields(emp, doc_type)
    consent_text = LEGAL_TEXTS[doc_type]["cierre"]

    hash_source = json.dumps({"doc_type": doc_type, "fields": doc_fields, "consent": consent_text},
                              sort_keys=True, ensure_ascii=False, default=str)
    content_hash = hashlib.sha256(hash_source.encode("utf-8")).hexdigest()

    # --- Generar el PDF final firmado (Python puro, reusa el diseño DG) ---
    out_pdf = os.path.join(GENERATED_DIR, f"{doc.doc_type}_{emp.token}.pdf")
    empresa_obj = emp.empresa_rel
    try:
        pdf_path = build_pdf(
            doc_type=doc_type,
            fields=doc_fields,
            signature_image_path=sig_path,
            signed_at=signed_at.strftime("%d/%m/%Y %H:%M:%S"),
            ip=ip,
            hash_=content_hash[:16],
            out_path=out_pdf,
            empresa_nombre=empresa_obj.nombre if empresa_obj else emp.empresa,
            representante_legal=empresa_obj.representante_legal if empresa_obj else None,
            firma_empresa_path=empresa_obj.firma_representante_path if empresa_obj else None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al generar el documento firmado: {e}")

    # --- Persistir en base de datos ---
    doc.status = STATUS_FIRMADO
    doc.pdf_path = pdf_path
    doc.content_hash = content_hash
    doc.signed_at = signed_at
    db.add(Signature(
        document_id=doc.id, image_path=sig_path, consent_text_snapshot=consent_text,
        ip_address=ip, user_agent=ua, signed_at=signed_at,
    ))
    db.commit()
    log_event(db, emp, "documento_firmado", request, document=doc,
              meta={"doc_type": doc_type, "hash": content_hash[:16]})

    if all(d.status == STATUS_FIRMADO for d in emp.documents):
        emp.completed_at = datetime.datetime.utcnow()
        db.commit()
        log_event(db, emp, "legajo_completo", request)
        # Punto 5 del pedido: correo automático al postulante con copia de sus
        # documentos. No debe bloquear el flujo si el correo no está configurado.
        try:
            enviado = send_completion_email(emp)
            if enviado:
                emp.completion_email_sent_at = datetime.datetime.utcnow()
                db.commit()
                log_event(db, emp, "correo_enviado", request)
        except Exception as e:
            log_event(db, emp, "correo_error", request, meta={"error": str(e)})

    return {"ok": True, "status": doc.status, "hash": content_hash[:16]}


def build_doc_fields(emp: Employee, doc_type: str) -> dict:
    """Arma el diccionario de campos que se imprime en cada documento.

    Para la ficha se pasa el diccionario COMPLETO que llenó el trabajador
    (todas las secciones), más la tabla de familia; para los demás documentos
    se arma un subconjunto relevante a partir de la misma ficha."""
    ficha = emp.ficha_data or {}
    familia = emp.familia_data or []

    base = {
        "nombre_completo": emp.nombre_completo,
        "num_doc": ficha.get("numero_documento", ficha.get("num_doc", "")),
        "direccion": ficha.get("direccion", ""),
        "empresa": emp.empresa or ficha.get("empresa", ""),
        "cargo": ficha.get("cargo", ""),
        "area": ficha.get("area", ""),
    }
    if doc_type == "ficha":
        num_doc = base["num_doc"]  # dict(ficha) pisaría esta clave más abajo si no la guardamos antes
        base = dict(ficha)  # todos los campos de las 11 secciones
        base["nombre_completo"] = emp.nombre_completo
        base["num_doc"] = num_doc
        base["empresa"] = emp.empresa or ficha.get("empresa", "")
        base["familia"] = familia
        base["educacion"] = emp.educacion_data or []
        base["experiencia"] = emp.experiencia_data or []
        base["capacitaciones"] = emp.capacitaciones_data or []
    if doc_type == "derechohabientes":
        dependientes = [f for f in familia if f.get("derechohabiente_essalud")]
        base["dependientes"] = dependientes
        base["dni_titular"] = ficha.get("numero_documento", "")
    if doc_type == "autorizacion_deposito":
        base["banco"] = ficha.get("banco_haberes", "")
        base["num_cuenta"] = ficha.get("cuenta_haberes", "")
        base["cci"] = ficha.get("cci_haberes", "")
        base["banco_cts"] = ficha.get("banco_cts", "")
        base["cuenta_cts"] = ficha.get("cuenta_cts", "")
    return base


# ---------------------------------------------------------------------------
# Correo automático al completar el legajo (punto 5 del pedido)
# ---------------------------------------------------------------------------
def send_completion_email(emp: Employee) -> bool:
    """Envía al postulante una copia de todos sus documentos firmados por correo.

    Se configura con variables de entorno (no bloquea el flujo si faltan):
      SMTP_HOST, SMTP_PORT (por defecto 587), SMTP_USER, SMTP_PASS,
      SMTP_FROM (por defecto SMTP_USER).
    Si SMTP_HOST no está definido, no se envía nada y se retorna False
    (queda registrado en la bitácora igual, sin PDFs adjuntos)."""
    host = os.environ.get("SMTP_HOST")
    if not host or not emp.email:
        return False
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    sender = os.environ.get("SMTP_FROM", user)

    msg = EmailMessage()
    msg["Subject"] = f"Tu legajo de personal — DIGETEL GROUP"
    msg["From"] = sender
    msg["To"] = emp.email
    doc_labels = ", ".join(DOC_LABELS.get(d.doc_type, d.doc_type) for d in emp.documents if d.pdf_path)
    cuerpo = (
        f"Hola {emp.nombre_completo},\n\n"
        f"Gracias por completar tu legajo de personal en {emp.empresa or 'DIGETEL GROUP'}.\n"
        f"Adjuntamos copia de los documentos que firmaste electrónicamente: {doc_labels}.\n"
    )
    if emp.contrato_pdf_path:
        cuerpo += "Se incluye también tu contrato de trabajo firmado.\n"
    cuerpo += "\nSaludos,\nRecursos Humanos — DIGETEL GROUP"
    msg.set_content(cuerpo)

    for d in emp.documents:
        if d.pdf_path and os.path.exists(d.pdf_path):
            with open(d.pdf_path, "rb") as f:
                msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                                    filename=f"{DOC_LABELS.get(d.doc_type, d.doc_type)}.pdf")
    if emp.contrato_pdf_path and os.path.exists(emp.contrato_pdf_path):
        with open(emp.contrato_pdf_path, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename="Contrato de Trabajo.pdf")

    with smtplib.SMTP(host, port, timeout=20) as server:
        server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)
    return True
