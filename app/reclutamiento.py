# -*- coding: utf-8 -*-
"""Reclutamiento y Selección — Registro de Pedidos de Personal, Control de
Leads (con postulaciones desde "Trabaja con Nosotros", calificación de CV
por IA, coordinación de entrevista, Entrevista por Competencias + DISC, y
paso a Selección) y Onboarding (Fase 3 + mejoras posteriores).
"""
import datetime
import os
import uuid
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Request, Depends, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import get_db
from .telefono import normalizar_o_dejar
from .models import (
    PedidoPersonal, LeadCandidato, Empresa, Employee, User, Cargo, Catalogo, EsquemaPago, BaseOperativa,
    ESTADOS_PEDIDO, ESTADO_PEDIDO_KEYS, MOTIVOS_PEDIDO, URGENCIAS_PEDIDO,
    MODALIDADES_PEDIDO, MODALIDAD_PEDIDO_KEYS, TIPOS_CONTRATO_PEDIDO, TIPOS_CONCEPTO_PEDIDO, Horario,
    MODALIDAD_A_REGIMEN_FICHA, ConceptoRemunerativo,
    ETAPAS_LEAD, ETAPA_LEAD_KEYS, ORIGENES_LEAD, ETAPAS_ONBOARDING, STATUS_PENDIENTE,
    CLASIFICACIONES_LEAD, HistorialDescarte, ETAPAS_DESCARTE, LEAD_NOMBRE_PENDIENTE, lead_incompleto,
    lead_semaforo,
)
from .auth import require_role, require_perm, require_jefe_o_gerente, es_jefe_o_gerente
from .rrhh import (
    _ctx, _enviar_correo, _public_base_url, _ensure_documents, _pedido_recibio_lead,
    _pedido_cubre_vacante, _a_lima, _eliminar_employee_completo, _notificar_lead_incompleto_n8n,
    _payload_analizar_cv, _notificar_analizar_cv_n8n,
    _payload_lead_para_n8n, UPLOADS_DIR,
)
from .public_landing import CV_DIR, EXTENSIONES_CV_VALIDAS, TAMANO_MAXIMO_CV

# Cargos de mayor rotación que necesitan referencia obligatoria a una Base
# (zona geográfica) — ver punto 2 del pedido sobre Registro de Pedidos.
CARGOS_REQUIEREN_BASE = ["tecnico", "técnico", "guardian", "guardián", "lider de base", "líder de base", "almacenero", "supervisor"]
# Área cuyos pedidos siempre llevan Base (16/10): OKA.
AREAS_REQUIEREN_BASE = ["oka"]


def requiere_base(cargo_nombre: str, area: str) -> bool:
    c, a = (cargo_nombre or "").lower(), (area or "").strip().lower()
    return any(k in c for k in CARGOS_REQUIEREN_BASE) or a in AREAS_REQUIEREN_BASE

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.filters["lima"] = _a_lima
from . import disc as _disc
templates.env.globals["interpretar_disc"] = _disc.interpretar
router = APIRouter()

ESTADO_LABELS = dict(ESTADOS_PEDIDO)
ETAPA_LABELS = dict(ETAPAS_LEAD)

# Evaluación DISC — versión simplificada (20 afirmaciones, 5 por dimensión,
# escala 1-5 "qué tanto te describe"). No pretende ser el instrumento DISC
# comercial completo, es una aproximación funcional dentro del prototipo.
DISC_PREGUNTAS = [
    ("d1", "D", "Me gusta tomar decisiones rápidas y asumir el control de la situación."),
    ("d2", "D", "Prefiero enfocarme en resultados más que en el proceso para llegar a ellos."),
    ("d3", "D", "No tengo problema en confrontar directamente un desacuerdo."),
    ("d4", "D", "Disfruto los desafíos y la competencia."),
    ("d5", "D", "Suelo ser impaciente cuando las cosas avanzan lento."),
    ("i1", "I", "Me resulta fácil generar entusiasmo en un grupo."),
    ("i2", "I", "Disfruto conocer gente nueva y socializar."),
    ("i3", "I", "Prefiero persuadir con optimismo antes que con datos fríos."),
    ("i4", "I", "Me energiza trabajar en equipo y hablar en público."),
    ("i5", "I", "Expreso mis emociones con facilidad."),
    ("s1", "S", "Prefiero la estabilidad y la rutina antes que el cambio constante."),
    ("s2", "S", "Soy paciente y buen escucha con los demás."),
    ("s3", "S", "Me cuesta decir que no cuando alguien me pide ayuda."),
    ("s4", "S", "Prefiero trabajar en equipo de forma armoniosa antes que competir."),
    ("s5", "S", "Mantengo la calma incluso bajo presión."),
    ("c1", "C", "Reviso los detalles cuidadosamente antes de decidir."),
    ("c2", "C", "Prefiero seguir procedimientos y estándares establecidos."),
    ("c3", "C", "Analizo los datos a fondo antes de dar una opinión."),
    ("c4", "C", "Soy exigente con la calidad de mi propio trabajo."),
    ("c5", "C", "Prefiero pensar bien las cosas antes de actuar."),
]
DISC_DIMENSIONES = {
    "D": "Dominancia", "I": "Influencia", "S": "Estabilidad", "C": "Conciencia (Cautela)",
}


def _calcular_disc(respuestas: dict):
    """respuestas: {pregunta_id: 1-5}. Devuelve {"D":pct,"I":pct,"S":pct,"C":pct,"perfil_dominante":"D"}."""
    sumas = {"D": 0, "I": 0, "S": 0, "C": 0}
    for pid, dim, _texto in DISC_PREGUNTAS:
        try:
            sumas[dim] += int(respuestas.get(pid, 0))
        except (TypeError, ValueError):
            pass
    total = sum(sumas.values()) or 1
    porcentajes = {dim: round(v / total * 100, 1) for dim, v in sumas.items()}
    dominante = max(porcentajes, key=porcentajes.get)
    return {**porcentajes, "perfil_dominante": dominante}


def _ficha_desde_pedido(pedido: PedidoPersonal) -> dict:
    """Datos de la ficha que ya se conocen por el pedido (07/10): de la
    MODALIDAD depende el régimen laboral, el tipo de contrato y por tanto el
    documento que se firma (contrato / locación de servicios / convenio APE),
    y también la remuneración y el horario. RR.HH. los puede corregir en la
    ficha; esto solo evita volver a digitarlos. Nada se pisa si el pedido no
    trae el dato (pedidos anteriores al 07/10)."""
    # (el cargo y el área NO se siembran: un candidato en Selección no debe contar
    # como ocupante del cargo en Procesos y Funciones hasta que RR.HH. lo confirme)
    datos = {}
    regimen = MODALIDAD_A_REGIMEN_FICHA.get(pedido.modalidad or "")
    if regimen:
        datos["regimen_laboral_persona"] = regimen
    if pedido.modalidad == "RHE":
        datos["tipo_contrato"] = "Locación de Servicios"
    elif pedido.tipo_contrato:
        datos["tipo_contrato"] = pedido.tipo_contrato
    if pedido.sueldo_base_ofrecido:
        datos["remuneracion"] = f"{pedido.sueldo_base_ofrecido:.2f}"
    movilidad = sum(c["importe"] for c in pedido.conceptos_lista if "movil" in (c.get("concepto") or "").lower())
    if movilidad:
        datos["movilidad"] = f"{movilidad:.2f}"
    if pedido.horario:
        datos["horario"] = f"{pedido.horario.nombre}: {pedido.horario.resumen}"
    return datos


def _parse_fecha(s: str):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


def _monto_o_none(texto: str):
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        return None


def _generar_codigo_pedido(db: Session) -> str:
    anio = datetime.datetime.utcnow().year
    prefijo = f"PED-{anio}-"
    existentes = (
        db.query(PedidoPersonal)
        .filter(PedidoPersonal.codigo.like(f"{prefijo}%"))
        .count()
    )
    return f"{prefijo}{existentes + 1:04d}"


# ---------------------------------------------------------------------------
# Registro de Pedidos de Personal
# ---------------------------------------------------------------------------
def _hoy_utc():
    return datetime.datetime.utcnow()


PEDIDOS_PDF_DIR = os.path.join(UPLOADS_DIR, "pedidos_comision")
os.makedirs(PEDIDOS_PDF_DIR, exist_ok=True)
TAMANO_MAXIMO_PDF_COMISION = 10 * 1024 * 1024


@router.get("/rrhh/reclutamiento/pedidos", response_class=HTMLResponse)
def pedidos_list(request: Request, estado: str = "", error: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require_perm("pedidos", "ver"))):
    # Punto pedido (28/09): al entrar sin filtro elegido, se ven solo los
    # pedidos ABIERTO (lo que RR.HH. necesita mirar primero). "Todos" y cada
    # estado puntual son opciones explícitas del selector, no el default.
    f_estado = estado if estado else "abierto"
    query = db.query(PedidoPersonal)
    if f_estado != "todos":
        query = query.filter(PedidoPersonal.estado == f_estado)
    pedidos = query.order_by(PedidoPersonal.created_at.desc()).all()
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
    cargos = db.query(Cargo).filter(Cargo.activo == True).order_by(Cargo.nombre).all()  # noqa: E712
    areas = db.query(Catalogo).filter(Catalogo.tipo == "area", Catalogo.activo == True).order_by(Catalogo.nombre).all()  # noqa: E712
    empleados = db.query(Employee).filter(Employee.estado == "activo").order_by(Employee.nombre_completo).all()
    bases = db.query(BaseOperativa).filter(BaseOperativa.activo == True).order_by(  # noqa: E712
        BaseOperativa.empresa_id, BaseOperativa.nombre).all()
    horarios = db.query(Horario).filter(Horario.activo == True).order_by(Horario.nombre).all()  # noqa: E712
    conceptos_catalogo = [
        {"id": c.id, "nombre": c.nombre, "tipo": c.tipo, "codigo": c.codigo_plame, "tipo_dato": c.tipo_dato}
        for c in db.query(ConceptoRemunerativo).filter(ConceptoRemunerativo.activo == True)  # noqa: E712
        .order_by(ConceptoRemunerativo.nombre).all()
    ]
    puede_generar = es_jefe_o_gerente(user, db)
    compensacion_por_cargo = {
        c.nombre: {
            "sueldo_base": c.esquema_pago.sueldo_base if c.esquema_pago else None,
            "comision": c.esquema_pago.comision_variable if c.esquema_pago else None,
            "movilidad": c.esquema_pago.movilidad if c.esquema_pago else None,
            "combustible": c.esquema_pago.combustible if c.esquema_pago else None,
            "otros": c.esquema_pago.otros_ingresos if c.esquema_pago else None,
        } for c in cargos
    }
    return templates.TemplateResponse(request, "rrhh_pedidos.html", _ctx(
        request, user, pedidos=pedidos, empresas=empresas, estados=ESTADOS_PEDIDO,
        estado_labels=ESTADO_LABELS, motivos=MOTIVOS_PEDIDO,
        cargos=cargos, areas=areas, empleados=empleados, bases=bases, horarios=horarios,
        modalidades=MODALIDADES_PEDIDO, tipos_contrato=TIPOS_CONTRATO_PEDIDO, conceptos_catalogo=conceptos_catalogo,
        compensacion_por_cargo=compensacion_por_cargo, puede_generar=puede_generar,
        cargos_requieren_base=CARGOS_REQUIEREN_BASE, areas_requieren_base=AREAS_REQUIEREN_BASE, error=error,
        f_estado=f_estado, active="pedidos",
    ))


def _error_pedido(mensaje: str):
    return RedirectResponse("/rrhh/reclutamiento/pedidos?error=" + quote(mensaje), status_code=303)


@router.post("/rrhh/reclutamiento/pedidos/nuevo")
async def pedidos_crear(cargo_solicitado: str = Form(""), cantidad: str = Form("1"),
                   empresa_id: str = Form(""), area: str = Form(""), base_id: str = Form(""),
                   solicitante: str = Form(""), motivo: str = Form(""),
                   fecha_requerida: str = Form(""), observaciones: str = Form(""),
                   horario_id: str = Form(""), modalidad: str = Form(""), tipo_contrato: str = Form(""),
                   sueldo_base_ofrecido: str = Form(""), comision_ofrecida: str = Form(""),
                   concepto_id: list[str] = Form([]), concepto_importe: list[str] = Form([]),
                   comision_pdf: UploadFile = File(None),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_jefe_o_gerente)):
    """Registro de Pedidos (rehecho el 07/10): el cargo debe existir en Cargos -
    MOF, y de la MODALIDAD (APE / MYPE / GENERAL / RHE) depende cómo se llena la
    ficha y qué documento se firma: APE = convenio (con comisión mensual), RHE =
    locación de servicios (el sueldo base es el total del recibo por honorarios),
    MYPE y GENERAL = contrato de trabajo (cada importe va por separado para la
    planilla; hay que decir si es plazo fijo, indeterminado o por obra)."""
    cargo = db.query(Cargo).filter(Cargo.nombre == cargo_solicitado.strip(), Cargo.activo == True).first()  # noqa: E712
    if not cargo:
        return _error_pedido("Elige un cargo que exista en Parámetros › Cargos - MOF.")
    try:
        cant = int(cantidad)
    except ValueError:
        cant = 0
    if cant < 1:
        return _error_pedido("La cantidad de vacantes debe ser al menos 1.")
    empresa = db.query(Empresa).get(int(empresa_id)) if empresa_id.isdigit() else None
    if not empresa:
        return _error_pedido("Elige para qué empresa es el pedido.")
    if modalidad not in MODALIDAD_PEDIDO_KEYS:
        return _error_pedido("Elige la modalidad: APE, MYPE, GENERAL o RHE.")
    sueldo = _monto_o_none(sueldo_base_ofrecido)
    if sueldo is None or sueldo <= 0:
        return _error_pedido("El Sueldo Base es obligatorio en todas las modalidades (y debe ser mayor a cero).")
    if not area.strip():
        return _error_pedido("Elige el área.")
    if not motivo.strip():
        return _error_pedido("Elige el motivo del pedido.")
    fecha_tope = _parse_fecha(fecha_requerida)
    if not fecha_tope:
        return _error_pedido("Indica la fecha tope para cubrir la vacante.")
    horario = db.query(Horario).get(int(horario_id)) if horario_id.isdigit() else None
    if not horario or not horario.activo:
        return _error_pedido("Elige el horario (si no existe, créalo en Control de Asistencia › Horarios).")
    cargo_requiere_base = requiere_base(cargo.nombre, area)
    if cargo_requiere_base and not base_id.isdigit():
        return _error_pedido("Este cargo requiere indicar la Base (zona) donde va a trabajar.")

    tipo_contrato_final = None
    if modalidad in ("MYPE", "GENERAL"):
        if tipo_contrato not in TIPOS_CONTRATO_PEDIDO:
            return _error_pedido("Para MYPE y GENERAL indica si el contrato es a Plazo Fijo, Plazo Indeterminado o Por Obra o Servicio.")
        tipo_contrato_final = tipo_contrato

    # Conceptos remunerativos / no remunerativos (no aplica a RHE: ahí el sueldo
    # base ya es el total del recibo por honorarios)
    conceptos = []
    comision_importe = None
    pdf_path = pdf_nombre = None
    if modalidad != "RHE":
        # Cada concepto se ELIGE del catálogo (Remuneraciones › Conceptos) y solo se
        # le pone el importe: el tipo (remunerativo / no) lo trae el propio concepto.
        usados = set()
        for i, cid in enumerate(concepto_id):
            importe_txt = concepto_importe[i] if i < len(concepto_importe) else ""
            if not (cid or "").strip() and not (importe_txt or "").strip():
                continue
            concepto = db.query(ConceptoRemunerativo).get(int(cid)) if (cid or "").isdigit() else None
            if not concepto or not concepto.activo:
                return _error_pedido("Elige el concepto de la lista (se crean en Remuneraciones › Conceptos), o quita la fila.")
            importe = _monto_o_none(importe_txt)
            if importe is None or importe < 0:
                return _error_pedido(f"Indica el importe de «{concepto.nombre}» (o quita la fila).")
            if concepto.id in usados:
                return _error_pedido(f"El concepto «{concepto.nombre}» está repetido: súmalo en un solo importe.")
            usados.add(concepto.id)
            conceptos.append({
                "concepto_id": concepto.id, "concepto": concepto.nombre, "importe": importe,
                "tipo": concepto.tipo, "codigo_plame": concepto.codigo_plame, "tipo_dato": concepto.tipo_dato,
            })

        comision_importe = _monto_o_none(comision_ofrecida)
        if comision_pdf is not None and (comision_pdf.filename or ""):
            contenido = await comision_pdf.read()
            if not comision_pdf.filename.lower().endswith(".pdf") or not contenido.startswith(b"%PDF"):
                return _error_pedido("El documento de comisiones debe ser un PDF.")
            if len(contenido) > TAMANO_MAXIMO_PDF_COMISION:
                return _error_pedido("El PDF de comisiones supera el tamaño máximo (10 MB).")
            pdf_nombre = comision_pdf.filename
            pdf_path = os.path.join(PEDIDOS_PDF_DIR, f"{uuid.uuid4().hex[:10]}_{os.path.basename(pdf_nombre)}")
            with open(pdf_path, "wb") as f:
                f.write(contenido)
        if modalidad == "APE" and comision_importe is None and not pdf_path:
            return _error_pedido("En modalidad APE la comisión es obligatoria: indica el importe mensual o adjunta el PDF que explica cómo se calcula.")

    # Punto 3 del pedido: si quien registra NO es administrador (es un Jefe/
    # Gerente generando su propio pedido), el Solicitante es él mismo — se
    # ignora cualquier otro valor que llegue del formulario.
    if user.rol == "administrador":
        solicitante_final = solicitante.strip() or None
    else:
        solicitante_final = user.nombre_completo
    db.add(PedidoPersonal(
        codigo=_generar_codigo_pedido(db),
        cargo_solicitado=cargo.nombre, area=area.strip(),
        empresa_id=empresa.id, base_id=int(base_id) if base_id.isdigit() else None,
        cantidad=cant, motivo=motivo, solicitante=solicitante_final,
        fecha_requerida=fecha_tope, observaciones=observaciones.strip() or None,
        horario_id=horario.id, modalidad=modalidad, tipo_contrato=tipo_contrato_final,
        sueldo_base_ofrecido=sueldo, comision_ofrecida=comision_importe, conceptos=conceptos or None,
        comision_pdf_path=pdf_path, comision_pdf_filename=pdf_nombre,
        registrado_por=user.nombre_completo,
        creado_por_user_id=user.id,
    ))
    db.commit()
    return RedirectResponse("/rrhh/reclutamiento/pedidos", status_code=303)


@router.get("/rrhh/reclutamiento/pedidos/{pedido_id}/comision.pdf")
def pedido_comision_pdf(pedido_id: int, db: Session = Depends(get_db),
                        user: User = Depends(require_perm("pedidos", "ver"))):
    from fastapi.responses import FileResponse
    pedido = db.query(PedidoPersonal).get(pedido_id)
    if not pedido or not pedido.comision_pdf_path or not os.path.exists(pedido.comision_pdf_path):
        raise HTTPException(404, "Este pedido no tiene PDF de comisiones.")
    return FileResponse(pedido.comision_pdf_path, filename=pedido.comision_pdf_filename or "comisiones.pdf",
                        media_type="application/pdf")


@router.get("/rrhh/reclutamiento/pedidos/{pedido_id}", response_class=HTMLResponse)
def pedido_detalle(request: Request, pedido_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_perm("pedidos", "ver"))):
    """Punto 2 del pedido (16/09): aunque un pedido ya se haya cerrado (por
    cubierto o cancelado), tiene que poder revisarse quiénes postularon y
    todo lo que pasó — Leads activos, personal que llegó a Selección/
    Personal por este pedido, y el historial de quienes se descartaron en
    el camino."""
    pedido = db.query(PedidoPersonal).get(pedido_id)
    if not pedido:
        raise HTTPException(404)
    leads = (
        db.query(LeadCandidato).filter(LeadCandidato.pedido_id == pedido_id)
        .order_by(LeadCandidato.created_at).all()
    )
    empleados = [
        e for e in db.query(Employee).filter(Employee.estado == "activo").order_by(Employee.nombre_completo).all()
        if (e.ficha_data or {}).get("codigo_pedido_seleccion") == pedido.codigo
    ] if pedido.codigo else []
    descartados = (
        db.query(HistorialDescarte).filter(HistorialDescarte.codigo_pedido == pedido.codigo)
        .order_by(HistorialDescarte.descartado_at).all()
    ) if pedido.codigo else []
    return templates.TemplateResponse(request, "rrhh_pedido_detalle.html", _ctx(
        request, user, pedido=pedido, leads=leads, empleados=empleados, descartados=descartados,
        etapa_labels=ETAPA_LABELS, active="pedidos",
    ))


@router.post("/rrhh/reclutamiento/pedidos/{pedido_id}/estado")
def pedidos_cambiar_estado(pedido_id: int, estado: str = Form(...), db: Session = Depends(get_db),
                            user: User = Depends(require_perm("leads", "editar"))):
    # Cerrar/cancelar/reabrir un pedido es de quien gestiona la captación (Leads), no de
    # quien solo genera pedidos (el antiguo "Gerente o Jefe" nunca pudo cambiar el estado).
    if estado not in ESTADO_PEDIDO_KEYS:
        raise HTTPException(400, "Estado inválido.")
    pedido = db.query(PedidoPersonal).get(pedido_id)
    if not pedido:
        raise HTTPException(404)
    pedido.estado = estado
    pedido.cerrado_at = datetime.datetime.utcnow() if estado in ("cubierto", "cancelado") else None
    db.commit()
    return RedirectResponse("/rrhh/reclutamiento/pedidos", status_code=303)


# ---------------------------------------------------------------------------
# Control de Leads (candidatos)
# ---------------------------------------------------------------------------
def _orden_leads(lead: LeadCandidato):
    """El más antiguo primero, para que RR.HH. no deje esperando a quien
    postuló hace más tiempo. Punto del pedido (16/09): se quitó la
    calificación por IA, que ordenaba esto antes."""
    return lead.created_at


@router.get("/rrhh/reclutamiento/leads", response_class=HTMLResponse)
def leads_list(request: Request, etapa: str = "", pedido_id: str = "", db: Session = Depends(get_db),
                user: User = Depends(require_perm("leads", "ver"))):
    # Punto pedido (28/09): "últimos 10 Pedidos con scroll" — se traen todos
    # los abiertos (más reciente primero, igual que antes) y el límite a 10
    # visibles es en la plantilla (contenedor con scroll), no acá, para no
    # tener que paginar si hay más de 10.
    pedidos_abiertos = (
        db.query(PedidoPersonal)
        .filter(PedidoPersonal.estado == "abierto")
        .order_by(PedidoPersonal.created_at.desc()).all()
    )

    # Punto pedido por el usuario: la pantalla principal de Gestión de Leads
    # muestra primero los Pedidos abiertos (cargo + cantidad); al entrar a
    # uno se filtran/registran los candidatos de ESE pedido. "Sin pedido"
    # (pedido_id=none) muestra los pocos candidatos sueltos que no quedaron
    # ligados a ninguno (siempre fue posible dejarlo así al registrar
    # manualmente).
    pedido_actual = None
    if pedido_id and pedido_id != "none":
        pedido_actual = db.query(PedidoPersonal).get(int(pedido_id))

    leads = []
    if pedido_id:
        query = db.query(LeadCandidato)
        if pedido_id == "none":
            query = query.filter(LeadCandidato.pedido_id.is_(None))
        else:
            query = query.filter(LeadCandidato.pedido_id == int(pedido_id))
        if etapa:
            query = query.filter(LeadCandidato.etapa == etapa)
        leads = sorted(query.all(), key=_orden_leads)

    leads_por_pedido = {
        pid: cnt for pid, cnt in
        db.query(LeadCandidato.pedido_id, func.count(LeadCandidato.id))
        .group_by(LeadCandidato.pedido_id).all()
    }

    return templates.TemplateResponse(request, "rrhh_leads.html", _ctx(
        request, user, leads=leads, pedidos_abiertos=pedidos_abiertos, etapas=ETAPAS_LEAD,
        etapa_labels=ETAPA_LABELS, estado_labels=ESTADO_LABELS, origenes=ORIGENES_LEAD,
        f_etapa=etapa, f_pedido=pedido_id, lead_incompleto=lead_incompleto, lead_semaforo=lead_semaforo,
        pedido_actual=pedido_actual, leads_por_pedido=leads_por_pedido, active="leads",
    ))


@router.post("/rrhh/reclutamiento/leads/nuevo")
async def leads_crear(background_tasks: BackgroundTasks,
                       nombre_completo: str = Form(...), email: str = Form(""), celular: str = Form(""),
                       origen: str = Form(""), pedido_id: str = Form(""), notas: str = Form(""),
                       cv: UploadFile = File(None),
                       db: Session = Depends(get_db),
                       user: User = Depends(require_perm("leads", "editar"))):
    lead = LeadCandidato(
        nombre_completo=nombre_completo.strip(), email=email.strip() or None, celular=normalizar_o_dejar(celular),
        origen=origen or None, pedido_id=int(pedido_id) if pedido_id else None, notas=notas.strip() or None,
        registrado_por=user.nombre_completo,
    )

    # CV opcional al registrar manualmente (candidatos que llegan por correo,
    # WhatsApp, etc.) — mismo criterio de validación que "Trabaja con
    # Nosotros" (tipo y tamaño de archivo).
    nombre_archivo = (cv.filename or "") if cv else ""
    if nombre_archivo:
        if nombre_archivo.lower().endswith(EXTENSIONES_CV_VALIDAS):
            contenido = await cv.read()
            if len(contenido) <= TAMANO_MAXIMO_CV:
                nombre_seguro = f"{uuid.uuid4().hex[:10]}_{nombre_archivo}"
                ruta = os.path.join(CV_DIR, nombre_seguro)
                with open(ruta, "wb") as f:
                    f.write(contenido)
                lead.cv_path = ruta
                lead.cv_filename = nombre_archivo

    db.add(lead)
    db.commit()
    db.refresh(lead)

    pedido = db.query(PedidoPersonal).get(lead.pedido_id) if lead.pedido_id else None
    if pedido:
        _pedido_recibio_lead(pedido)
        db.commit()
    payload_n8n = _payload_lead_para_n8n(lead, pedido)
    background_tasks.add_task(_notificar_lead_incompleto_n8n, payload_n8n, lead_incompleto(lead) or not lead.distrito)
    if lead.cv_path and pedido:
        background_tasks.add_task(_notificar_analizar_cv_n8n, _payload_analizar_cv(lead, pedido))

    destino = f"/rrhh/reclutamiento/leads?pedido_id={pedido_id}" if pedido_id else "/rrhh/reclutamiento/leads?pedido_id=none"
    return RedirectResponse(destino, status_code=303)


@router.post("/rrhh/reclutamiento/leads/{lead_id}/analizar-cv")
def lead_analizar_cv(lead_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db),
                     user: User = Depends(require_perm("leads", "editar"))):
    """Botón «Analizar CV» / «Volver a analizar»: pide a n8n el análisis con IA.
    El resultado aparece solo en la ficha del lead (unos segundos después)."""
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    if not lead.cv_path or not os.path.exists(lead.cv_path):
        return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok=analisis_sin_cv", status_code=303)
    if not lead.pedido_id:
        return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok=analisis_sin_pedido", status_code=303)
    from . import n8n as n8n_mod
    if not n8n_mod.webhook_configurado("N8N_WEBHOOK_ANALIZAR_CV_URL"):
        return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok=analisis_no_configurado", status_code=303)
    # Un aviso de falla anterior («⚠ No se pudo analizar…») se quita para que no
    # parezca el resultado del nuevo intento; un análisis bueno se conserva.
    if (lead.analisis_ia or "").startswith("⚠"):
        lead.analisis_ia = None
        lead.estrellas = None
        db.commit()
    background_tasks.add_task(_notificar_analizar_cv_n8n, _payload_analizar_cv(lead, lead.pedido))
    return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok=analisis_solicitado&n=0", status_code=303)


@router.post("/rrhh/reclutamiento/leads/{lead_id}/etapa")
def leads_cambiar_etapa(lead_id: int, etapa: str = Form(...), db: Session = Depends(get_db),
                         user: User = Depends(require_perm("leads", "editar"))):
    if etapa not in ETAPA_LEAD_KEYS:
        raise HTTPException(400, "Etapa inválida.")
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    lead.etapa = etapa
    db.commit()
    return RedirectResponse("/rrhh/reclutamiento/leads", status_code=303)


@router.get("/rrhh/reclutamiento/leads/{lead_id}/cv")
def lead_cv(lead_id: int, db: Session = Depends(get_db),
            user: User = Depends(require_perm("leads", "ver"))):
    from fastapi.responses import FileResponse
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead or not lead.cv_path or not os.path.exists(lead.cv_path):
        raise HTTPException(404)
    return FileResponse(lead.cv_path, filename=lead.cv_filename or "cv.pdf")


def _entrevistador_de(db, pedido):
    from .entrevistas import entrevistador_de
    return entrevistador_de(db, pedido)


@router.get("/rrhh/reclutamiento/leads/{lead_id}", response_class=HTMLResponse)
def lead_detalle(request: Request, lead_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_perm("leads", "ver"))):
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    cargo = None
    if lead.pedido and lead.pedido.cargo_solicitado:
        cargo = db.query(Cargo).filter(Cargo.nombre == lead.pedido.cargo_solicitado).first()
    disc_resultado = None
    if lead.entrevista_data and lead.entrevista_data.get("disc"):
        disc_resultado = lead.entrevista_data["disc"]
    disc_respuestas_guardadas = (lead.entrevista_data or {}).get("disc_respuestas") or {}
    entrevistadores = db.query(Employee).filter(Employee.estado == "activo").order_by(Employee.nombre_completo).all()
    pedido_base_distritos = (lead.pedido.base.distritos or []) if lead.pedido and lead.pedido.base else []
    from .models import LlamadaN8n
    ultimo_analisis = (db.query(LlamadaN8n)
                       .filter(LlamadaN8n.evento == "analizar_cv", LlamadaN8n.lead_id == lead.id)
                       .order_by(LlamadaN8n.id.desc()).first())
    return templates.TemplateResponse(request, "rrhh_lead_detalle.html", _ctx(
        request, user, lead=lead, cargo=cargo, disc_preguntas=DISC_PREGUNTAS,
        disc_dimensiones=DISC_DIMENSIONES, disc_resultado=disc_resultado,
        disc_respuestas_guardadas=disc_respuestas_guardadas, entrevistadores=entrevistadores,
        clasificaciones=CLASIFICACIONES_LEAD, lead_incompleto=lead_incompleto(lead),
        pedido_base_distritos=pedido_base_distritos, ultimo_analisis=ultimo_analisis,
        cuestionario=lead.cuestionario_data or {},
        entrevistador=_entrevistador_de(db, lead.pedido),
        seleccion_id=request.query_params.get('seleccion'),
        cuestionario_enlace=(_public_base_url() + f"cuestionario/{lead.cuestionario_token}") if lead.cuestionario_token else "",
        active="leads",
    ))


def _agendar_entrevista_n8n(lead: LeadCandidato) -> dict | None:
    """Punto 3.3 del pedido de automatización RyS (21/09): dispara el
    webhook de n8n que agenda una entrevista de 25 minutos en el Google
    Calendar de trabajaconnosotros@digetelgroup.com (n8n tiene esa
    credencial, MICELIO no) y devuelve el link de Meet real. Como el
    candidato queda como invitado del evento, Calendar ya le manda la
    invitación — n8n no necesita mandar un correo aparte para esto.
    Devuelve None si el webhook no está configurado o falla, para que la
    llamada haga el fallback de siempre (link genérico + correo manual)."""
    from . import n8n
    resultado = n8n.llamar_webhook("N8N_WEBHOOK_ENTREVISTAR_URL", {
        "lead_id": lead.id,
        "nombre_completo": lead.nombre_completo,
        "email": lead.email,
        "celular": _celular_lead_completo(lead),
        "cargo": lead.pedido.cargo_solicitado if lead.pedido else None,
        "area": lead.pedido.area if lead.pedido else None,
        "empresa": lead.pedido.empresa.nombre if lead.pedido and lead.pedido.empresa else None,
        "pedido_codigo": lead.pedido.codigo if lead.pedido else None,
    })
    if resultado and resultado.get("ok") and resultado.get("meet_link"):
        return resultado
    return None


def _correo_coordinar_meet_generico(lead: LeadCandidato) -> bool:
    """Respaldo de siempre cuando el webhook de n8n (N8N_WEBHOOK_ENTREVISTAR_URL)
    todavía no está configurado o falló: un link genérico de Meet y RR.HH.
    coordina el horario a mano por correo."""
    meet_link = "https://meet.google.com/new"
    cuerpo = (
        f"Hola {lead.nombre_completo.split()[0] if lead.nombre_completo else ''},\n\n"
        "Gracias por tu interés en postular a DIGETEL GROUP. Nos gustaría coordinar una "
        "breve entrevista por videollamada.\n\n"
        f"Aquí tienes un enlace de Google Meet para la reunión: {meet_link}\n\n"
        "Por favor respóndenos a este correo proponiendo 2-3 horarios en los que puedas "
        "conectarte en los próximos días y te confirmamos el que mejor calce.\n\n"
        "Saludos cordiales,\nRecursos Humanos — DIGETEL GROUP"
    )
    return _enviar_correo([lead.email], "Coordinemos tu entrevista — DIGETEL GROUP", cuerpo)


def _correo_descarte(lead: LeadCandidato) -> bool:
    cuerpo = (
        f"Hola {lead.nombre_completo.split()[0] if lead.nombre_completo else ''},\n\n"
        "Gracias por tu tiempo e interés en postular a DIGETEL GROUP y por habernos contado "
        "sobre tu experiencia y trayectoria.\n\n"
        "Luego de revisar con cuidado tu postulación, en esta oportunidad hemos decidido "
        "continuar el proceso con otros candidatos cuyo perfil se ajusta un poco más a lo que "
        "necesitamos para esta posición en particular. Esto no es en absoluto un reflejo de tu "
        "valor profesional, y nos encantaría que sigas atento a futuras vacantes que calcen "
        "mejor con tu perfil.\n\n"
        "Te deseamos mucho éxito en tu búsqueda y en tus próximos pasos profesionales.\n\n"
        "Saludos cordiales,\nRecursos Humanos — DIGETEL GROUP"
    )
    return _enviar_correo([lead.email], "Sobre tu postulación a DIGETEL GROUP", cuerpo)


@router.post("/rrhh/reclutamiento/leads/{lead_id}/coordinar-meet")
def lead_coordinar_meet(lead_id: int, db: Session = Depends(get_db),
                         user: User = Depends(require_perm("leads", "editar"))):
    """"Entrevistar" en Gestión de Leads — punto 3.3 del pedido de
    automatización RyS (21/09). Intenta agendar de verdad vía n8n (25 min,
    Google Calendar de trabajaconnosotros@digetelgroup.com, invitado =
    candidato); si el webhook todavía no está configurado o falla, cae al
    correo con link genérico de Meet de siempre — nunca bloquea la acción."""
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)

    resultado = _agendar_entrevista_n8n(lead) if lead.email else None
    if resultado:
        f = dict(lead.entrevista_data or {})
        f["coordinacion"] = {
            "fecha_hora": resultado.get("fecha_hora"),
            "meet_link": resultado.get("meet_link"),
            "calendar_event_id": resultado.get("calendar_event_id"),
            "agendado_at": (datetime.datetime.utcnow() - datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M"),
        }
        lead.entrevista_data = f
        lead.etapa = "contactado"
        db.commit()
        mensaje = "entrevista_agendada"
    else:
        enviado = _correo_coordinar_meet_generico(lead) if lead.email else False
        lead.etapa = "contactado"
        db.commit()
        mensaje = "correo_enviado" if enviado else "correo_no_configurado"
    return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok={mensaje}", status_code=303)


def _celular_lead_completo(lead: LeadCandidato) -> str:
    """Igual criterio que _celular_completo pero para LeadCandidato, que
    guarda el celular como un solo texto libre (sin código de país aparte)
    — asume Perú (+51) si no parece traer ya un código."""
    numero = "".join(ch for ch in (lead.celular or "") if ch.isdigit())
    if not numero:
        return ""
    if not numero.startswith("51") or len(numero) <= 9:
        numero = "51" + numero
    return numero


def _archivar_descarte(db: Session, *, nombre_completo: str, email: str, celular: str, empresa: str,
                        etapa_descarte: str, codigo_pedido: str, vacante_cargo: str, motivo: str,
                        datos_proceso: dict, descartado_por: str) -> None:
    """Punto 1 del pedido (16/09): a quien se descarta no le queda un
    registro en Personal (ni, para los de Gestión de Leads, en la lista de
    Leads activos) — pero sí un historial de qué pasó. No hace commit,
    quien llama decide cuándo."""
    db.add(HistorialDescarte(
        nombre_completo=nombre_completo, email=email or None, celular=celular or None, empresa=empresa or None,
        etapa_descarte=etapa_descarte, codigo_pedido=codigo_pedido or None, vacante_cargo=vacante_cargo or None,
        motivo=motivo or None, datos_proceso=datos_proceso or None, descartado_por=descartado_por,
    ))


@router.post("/rrhh/reclutamiento/leads/{lead_id}/descartar")
def lead_descartar(lead_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_perm("leads", "editar"))):
    """Descartar desde Gestión de Leads (el CV no calza, o no se aprobó en
    la Entrevista 1) — punto 1 del pedido (16/09): se archiva su historial
    completo y se borra de la lista de Leads activos, en vez de quedar ahí
    para siempre con etapa "descartado"."""
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)

    # Punto 3.4 del pedido de automatización RyS (21/09): el correo de
    # descarte ahora sale de trabajaconnosotros@digetelgroup.com (cuenta
    # nueva, cuyas credenciales solo tiene n8n) — se intenta primero por el
    # webhook de n8n; si todavía no está configurado o falla, cae al envío
    # directo de siempre desde el SMTP de MICELIO (digetelperu.com).
    from . import n8n
    resultado_n8n = n8n.llamar_webhook("N8N_WEBHOOK_DESCARTAR_URL", {
        "lead_id": lead.id,
        "nombre_completo": lead.nombre_completo,
        "email": lead.email,
        "celular": _celular_lead_completo(lead),
        "cargo": lead.pedido.cargo_solicitado if lead.pedido else None,
        "empresa": lead.pedido.empresa.nombre if lead.pedido and lead.pedido.empresa else None,
    }) if lead.email else None
    if resultado_n8n and resultado_n8n.get("ok"):
        enviado = True
    else:
        enviado = _correo_descarte(lead) if lead.email else False
    wa_link = None
    wa_numero = _celular_lead_completo(lead)
    if wa_numero:
        # Punto 2 del pedido (16/09): WhatsApp es solo un aviso rápido de
        # que se mandó un correo — nunca repite el contenido completo.
        texto_wa = (
            f"Hola {lead.nombre_completo.split()[0] if lead.nombre_completo else ''}, te escribimos de "
            "Recursos Humanos de DIGETEL GROUP. Te acabamos de enviar un correo sobre tu postulación — "
            "por favor revísalo. ¡Gracias por tu interés!"
        )
        wa_link = f"https://wa.me/{wa_numero}?text={quote(texto_wa)}"

    _archivar_descarte(
        db, nombre_completo=lead.nombre_completo, email=lead.email, celular=lead.celular,
        empresa=lead.pedido.empresa.nombre if lead.pedido and lead.pedido.empresa else None,
        etapa_descarte="leads", codigo_pedido=lead.pedido.codigo if lead.pedido else None,
        vacante_cargo=lead.pedido.cargo_solicitado if lead.pedido else None,
        motivo=(lead.entrevista_data or {}).get("conclusion") if lead.entrevista_data else None,
        datos_proceso={
            "origen": lead.origen, "documento_tipo": lead.documento_tipo, "documento_numero": lead.documento_numero,
            "clasificacion": lead.clasificacion, "cv_filename": lead.cv_filename,
            "entrevista": lead.entrevista_data,
            # Punto de cierre del pedido de automatización RyS (21/09): "toda
            # la información de los leads debe quedar registrada como
            # sustento, incluida conversaciones" — el análisis de IA y la
            # transcripción de WhatsApp no se pierden al descartar.
            "estrellas": lead.estrellas, "analisis_ia": lead.analisis_ia,
            "conversacion_whatsapp": lead.conversacion_whatsapp,
        },
        descartado_por=user.nombre_completo,
    )
    pedido_id = lead.pedido_id
    db.delete(lead)
    db.commit()

    destino = f"/rrhh/reclutamiento/leads?pedido_id={pedido_id}" if pedido_id else "/rrhh/reclutamiento/leads?pedido_id=none"
    mensaje = "correo_enviado" if enviado else "correo_no_configurado"
    destino += f"&ok={mensaje}"
    if wa_link:
        destino += f"&wa={quote(wa_link)}"
    return RedirectResponse(destino, status_code=303)


@router.post("/rrhh/reclutamiento/leads/{lead_id}/entrevista")
async def lead_guardar_entrevista(request: Request, lead_id: int, db: Session = Depends(get_db),
                                   user: User = Depends(require_perm("leads", "editar"))):
    """Guarda la Entrevista por Competencias (una fila por competencia del
    cargo, campos dinámicos comp_{id}_nivel / comp_{id}_notas) y la
    Evaluación DISC (una fila por DISC_PREGUNTAS, disc_{id})."""
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    form = await request.form()

    competencias = {}
    for clave in form.keys():
        if clave.startswith("comp_") and clave.endswith("_nivel"):
            competencia_id = clave[len("comp_"):-len("_nivel")]
            competencias[competencia_id] = {
                "nivel_observado": form.get(clave) or None,
                "notas": form.get(f"comp_{competencia_id}_notas") or None,
            }

    disc_respuestas = {pid: form.get(f"disc_{pid}") for pid, _dim, _texto in DISC_PREGUNTAS if form.get(f"disc_{pid}")}
    disc_resultado = _calcular_disc(disc_respuestas) if disc_respuestas else (lead.entrevista_data or {}).get("disc")

    entrevista_data = dict(lead.entrevista_data or {})
    historial = list(entrevista_data.get("historial") or [])
    # Punto 5 del pedido (15/09): si ya había una entrevista guardada antes,
    # se archiva tal cual (competencias, DISC, conclusión, quién y cuándo)
    # en el historial antes de sobrescribirla, para poder comparar qué
    # cambió en una re-evaluación.
    if entrevista_data.get("fecha"):
        historial.append({k: v for k, v in entrevista_data.items() if k != "historial"})

    entrevista_data["competencias"] = competencias or entrevista_data.get("competencias")
    entrevista_data["disc"] = disc_resultado
    # Punto del pedido (16/09): que las respuestas del DISC no se pierdan al
    # volver a entrar — antes solo se guardaba el resultado calculado
    # (porcentajes), nunca las respuestas 1-5 de cada afirmación, así que el
    # formulario siempre se veía vacío al recargar aunque ya estuviera
    # guardado.
    entrevista_data["disc_respuestas"] = disc_respuestas or entrevista_data.get("disc_respuestas")
    entrevista_data["conclusion"] = form.get("conclusion") or entrevista_data.get("conclusion")
    entrevista_data["entrevistador"] = user.nombre_completo
    # Hora de Lima (UTC-5), no la del servidor (UTC) — mismo bug corregido
    # en Asistencia / cumpleaños (15-16/09).
    entrevista_data["fecha"] = (datetime.datetime.utcnow() - datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
    entrevista_data["historial"] = historial
    lead.entrevista_data = entrevista_data
    if form.get("clasificacion"):
        lead.clasificacion = form.get("clasificacion")
    if lead.etapa in ("nuevo", "contactado"):
        lead.etapa = "entrevista"
    db.commit()
    return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}", status_code=303)


def _competencias_resueltas(lead: LeadCandidato, db: Session):
    """Convierte entrevista_data['competencias'] (guardado por id de
    competencia, {id: {nivel_observado, notas}}) en una lista con nombres y
    nivel requerido, para poder mostrarla/mandarla una vez que el Lead ya no
    existe como tal (pasó a ser un Employee en Selección)."""
    comp_data = (lead.entrevista_data or {}).get("competencias") or {}
    if not comp_data or not lead.pedido or not lead.pedido.cargo_solicitado:
        return []
    cargo_obj = db.query(Cargo).filter(Cargo.nombre == lead.pedido.cargo_solicitado).first()
    if not cargo_obj:
        return []
    resultado = []
    for req in cargo_obj.requisitos_competencias:
        info = comp_data.get(str(req.competencia_id)) or {}
        if info.get("nivel_observado"):
            resultado.append({
                "nombre": req.competencia.nombre if req.competencia else "—",
                "nivel_requerido": req.nivel_requerido,
                "nivel_observado": info.get("nivel_observado"),
                "notas": info.get("notas"),
            })
    return resultado


def _employee_por_segunda_entrevista_token(db: Session, token: str):
    for e in db.query(Employee).filter(Employee.estado == "activo").all():
        if (e.ficha_data or {}).get("segunda_entrevista_token") == token:
            return e
    return None


def _notificar_entrevistador(emp: Employee, entrevistador: Employee):
    """Punto del pedido (16/09): al elegir a quién le toca la segunda
    entrevista, se le avisa por correo y (link de) WhatsApp — con los datos
    del candidato, DISC y competencias — y el enlace para que registre su
    propio veredicto (APROBADO/DESCARTADO/REVISAR) una vez la haga, sin
    necesitar usuario ni clave en MICELIO. Devuelve (correo_enviado, wa_link)."""
    f = emp.ficha_data or {}
    ef = entrevistador.ficha_data or {}
    correo = ef.get("correo_corporativo") or entrevistador.email or ef.get("correo_personal")
    enlace = _public_base_url() + f"entrevista2/{f.get('segunda_entrevista_token')}"

    disc = f.get("disc_resultado") or {}
    disc_txt = ""
    if disc:
        disc_txt = (
            f"D: {disc.get('D', 0)}%  I: {disc.get('I', 0)}%  S: {disc.get('S', 0)}%  C: {disc.get('C', 0)}%\n"
            f"Perfil dominante: {DISC_DIMENSIONES.get(disc.get('perfil_dominante'), '—')}"
        )
    comp_txt = ""
    comp_list = f.get("competencias_entrevista") or []
    if comp_list:
        comp_txt = "\n".join(
            f"- {c['nombre']}: nivel {c.get('nivel_observado', '—')}/4 (requerido {c.get('nivel_requerido', '—')}/4)"
            + (f" — {c['notas']}" if c.get("notas") else "")
            for c in comp_list
        )

    enviado = False
    if correo:
        cuerpo = (
            f"Hola {entrevistador.nombre_completo.split()[0]},\n\n"
            f"Le escribimos de Recursos Humanos de DIGETEL GROUP para informarle que "
            f"{emp.nombre_completo} ha sido seleccionado/a para continuar el proceso de selección "
            f"al puesto de {f.get('vacante_cargo') or 'la vacante'}, y usted ha sido designado/a "
            "para llevar a cabo la segunda entrevista correspondiente a su área.\n\n"
            "Datos del candidato/a:\n"
            f"Nombre: {emp.nombre_completo}\nCorreo: {emp.email or '—'}\n\n"
            + (f"Resultado de la Evaluación DISC:\n{disc_txt}\n\n" if disc_txt else "")
            + (f"Resultado de la Entrevista por Competencias:\n{comp_txt}\n\n" if comp_txt else "")
            + f"Clasificación de la primera entrevista: {f.get('clasificacion_entrevista') or '—'}\n\n"
            "Le pedimos coordinar y realizar la entrevista a la brevedad. Una vez concluida, por "
            f"favor ingrese al siguiente enlace para registrar su evaluación:\n{enlace}\n\n"
            "Quedamos atentos a cualquier consulta.\n\nSaludos cordiales,\nRecursos Humanos — DIGETEL GROUP"
        )
        enviado = _enviar_correo([correo], f"Segunda entrevista — {emp.nombre_completo}", cuerpo)

    # Punto 2 del pedido (16/09): WhatsApp es solo un aviso rápido de que se
    # mandó un correo — nunca repite el contenido completo (candidato, DISC,
    # competencias, enlace).
    wa_link = None
    wa_numero = _celular_completo(ef)
    if wa_numero:
        texto_wa = (
            f"Hola {entrevistador.nombre_completo.split()[0]}, le escribimos de Recursos Humanos de "
            "DIGETEL GROUP. Le acabamos de enviar un correo sobre una segunda entrevista a su cargo "
            "— por favor revíselo. ¡Gracias!"
        )
        wa_link = f"https://wa.me/{wa_numero}?text={quote(texto_wa)}"

    return enviado, wa_link


@router.post("/rrhh/reclutamiento/leads/{lead_id}/aprobar")
def lead_aprobar(lead_id: int, entrevistador_id: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(require_perm("leads", "editar"))):
    """Aprobado -> pasa a Selección: crea el legajo (Employee pendiente con
    token) y notifica a quien se eligió para la segunda entrevista. El
    enlace de autoservicio de la ficha ya NO se manda acá al candidato —
    ver punto del pedido (16/09): solo se manda cuando RR.HH. confirma un
    veredicto APROBADO en Selección (seleccion_confirmar_aprobado)."""
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    empresa = lead.pedido.empresa if lead.pedido else None
    # Punto 6 del pedido (Trabaja con Nosotros, 2026-09-08): toda persona que
    # llega por un proceso de selección debe quedar con el código del pedido
    # que la originó. Punto 1 de Gestión de Leads: la Evaluación DISC (con
    # sus porcentajes) y la clasificación de la entrevista también quedan en
    # la ficha permanente, no solo en el Lead (que es más difícil de ubicar
    # una vez contratada la persona).
    ficha_inicial = {}
    if lead.pedido and lead.pedido.codigo:
        ficha_inicial["codigo_pedido_seleccion"] = lead.pedido.codigo
        ficha_inicial["vacante_cargo"] = lead.pedido.cargo_solicitado
        ficha_inicial["vacante_area"] = lead.pedido.area
        ficha_inicial.update(_ficha_desde_pedido(lead.pedido))
    if lead.clasificacion:
        ficha_inicial["clasificacion_entrevista"] = lead.clasificacion
    disc = (lead.entrevista_data or {}).get("disc")
    if disc:
        ficha_inicial["disc_resultado"] = disc
    competencias_resueltas = _competencias_resueltas(lead, db)
    if competencias_resueltas:
        ficha_inicial["competencias_entrevista"] = competencias_resueltas

    entrevistador = db.query(Employee).get(int(entrevistador_id)) if entrevistador_id else None
    if entrevistador:
        ficha_inicial["segunda_entrevista_entrevistador_id"] = entrevistador.id
        ficha_inicial["segunda_entrevista_entrevistador_nombre"] = entrevistador.nombre_completo
        ficha_inicial["segunda_entrevista_token"] = uuid.uuid4().hex

    emp = Employee(
        nombre_completo=lead.nombre_completo.strip(), email=lead.email or None,
        empresa_id=empresa.id if empresa else None, empresa=empresa.nombre if empresa else None,
        estado="activo", status=STATUS_PENDIENTE, ficha_data=ficha_inicial,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    _ensure_documents(db, emp)
    lead.etapa = "oferta"
    db.commit()

    destino = f"/rrhh/reclutamiento/leads/{lead_id}"
    if not entrevistador:
        return RedirectResponse(f"{destino}?ok=sin_entrevistador", status_code=303)

    enviado, wa_link = _notificar_entrevistador(emp, entrevistador)
    mensaje = "entrevistador_notificado" if enviado else "entrevistador_no_configurado"
    if wa_link:
        return RedirectResponse(f"{destino}?ok={mensaje}&wa={quote(wa_link)}", status_code=303)
    return RedirectResponse(f"{destino}?ok={mensaje}", status_code=303)


# ---------------------------------------------------------------------------
# Selección (punto de Eduardo, 2026-09-08): segunda etapa, después de
# Gestión de Leads. Solo llegan acá los candidatos ya aprobados (Employee
# creado por lead_aprobar) con clasificación EXCELENTE/MUY BUENO/BUENO que
# todavía no tienen el visto bueno de una segunda entrevista. Es una
# pantalla nueva y separada — el legajo de documentos firmados de Personal
# no se toca (ver nota en _rrhh_topbar.html).
#
# El envío por WhatsApp queda como un enlace "wa.me" con el mensaje ya
# armado (Eduardo lo confirma y lo manda él mismo). Excepción (12/10):
# Confirmar APROBADO avisa a n8n, que manda la plantilla «seleccion» por
# WhatsApp Business — MICELIO nunca tiene las credenciales de Meta.
# ---------------------------------------------------------------------------

def _celular_completo(ficha: dict, campo: str = "celular") -> str:
    """Código de país + número, todo junto y solo dígitos (formato que
    necesita un link wa.me — sin '+' ni espacios)."""
    codigo = (ficha.get(f"{campo}_codigo") or "+51").lstrip("+")
    numero = "".join(ch for ch in (ficha.get(campo) or "") if ch.isdigit())
    return f"{codigo}{numero}" if numero else ""


def _ya_lleno_ficha(emp: Employee) -> bool:
    f = emp.ficha_data or {}
    return bool((f.get("apellido_paterno") or "").strip() and (f.get("nombres") or "").strip())


def _es_flujo_nuevo(f: dict) -> bool:
    """Candidato que llegó por el flujo del 15/10 (formulario + entrevista del
    creador del pedido). Los anteriores siguen con segunda entrevista por enlace."""
    return bool(f.get("lead_origen_id") or f.get("entrevista_cita"))


def _contrato_firmado(emp: Employee) -> bool:
    from .models import STATUS_FIRMADO
    return any(d.doc_type == "contrato" and d.status == STATUS_FIRMADO for d in emp.documents)


def _etapa_seleccion(emp: Employee, f: dict) -> tuple[str, str]:
    """(clave, etiqueta) del punto del proceso en el que está el candidato."""
    if f.get("seleccion_rrhh_confirmado") == "aprobado":
        if _contrato_firmado(emp):
            return "firmado", "Contrato firmado"
        if f.get("contrato_enviado_at"):
            return "contrato_enviado", "Contrato enviado, esperando firma"
        if emp.completed_at or _ya_lleno_ficha(emp):
            return "ficha_completa", "Ficha completa: falta enviar el contrato"
        return "ficha_enviada", "Contratar: esperando que llene su ficha"
    if _es_flujo_nuevo(f):
        return "decidir", "Entrevista agendada: falta decidir"
    return "decidir", "Falta decidir"


def registrar_contrato_firmado(db: Session, emp: Employee) -> None:
    """Se llama cuando el postulante firma su contrato. Descuenta UNA vacante
    del pedido (si llega a cero queda CUBIERTO) y deja constancia. A partir de
    acá la persona aparece en Onboarding."""
    f = dict(emp.ficha_data or {})
    f["contrato_firmado_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    # Solo si se contrató por el flujo nuevo (`contratar_at`): las aprobaciones
    # anteriores ya descontaron su vacante al aprobar.
    if not f.get("vacante_descontada") and f.get("codigo_pedido_seleccion") and f.get("contratar_at"):
        pedido = db.query(PedidoPersonal).filter(PedidoPersonal.codigo == f["codigo_pedido_seleccion"]).first()
        if pedido:
            _pedido_cubre_vacante(pedido)
        f["vacante_descontada"] = True
    emp.ficha_data = f
    db.commit()


@router.get("/rrhh/reclutamiento/seleccion", response_class=HTMLResponse)
def seleccion_list(request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_perm("seleccion", "ver"))):
    # Punto 7 del pedido (15/09): antes solo entraban acá los que ya tenían
    # clasificacion_entrevista en EXCELENTE/MUY BUENO/BUENO — pero esa
    # clasificación no siempre queda cargada al momento de aprobar el lead
    # (se llena en la Entrevista por Competencias, un paso previo y
    # opcional). El criterio correcto es: toda persona que llegó acá por el
    # pipeline de reclutamiento (tiene codigo_pedido_seleccion) y todavía no
    # tiene una decisión final confirmada por RR.HH. (aprobado o descartado)
    # — la clasificación, si existe, se sigue mostrando para priorizar.
    candidatos = []
    for e in db.query(Employee).filter(Employee.estado == "activo").order_by(Employee.nombre_completo).all():
        f = e.ficha_data or {}
        if not f.get("codigo_pedido_seleccion"):
            continue
        if _es_flujo_nuevo(f):
            # permanece en Selección hasta que firma el contrato (luego pasa a Onboarding)
            if f.get("seleccion_rrhh_confirmado") == "descartado" or _contrato_firmado(e):
                continue
        elif f.get("seleccion_rrhh_confirmado") in ("aprobado", "descartado"):
            continue
        candidatos.append(e)
    estados = {e.id: _etapa_seleccion(e, e.ficha_data or {}) for e in candidatos}
    return templates.TemplateResponse(request, "rrhh_seleccion.html", _ctx(
        request, user, candidatos=candidatos, estados=estados, active="seleccion",
    ))


@router.get("/rrhh/reclutamiento/seleccion/{employee_id}", response_class=HTMLResponse)
def seleccion_detalle(request: Request, employee_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_perm("seleccion", "ver"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    f = emp.ficha_data or {}
    entrevistadores = db.query(Employee).filter(
        Employee.estado == "activo", Employee.id != employee_id).order_by(Employee.nombre_completo).all()
    if f.get("vacante_area"):
        del_area = [e for e in entrevistadores if (e.ficha_data or {}).get("area") == f.get("vacante_area")]
        if del_area:
            entrevistadores = del_area
    entrevistador_actual = None
    if f.get("segunda_entrevista_entrevistador_id"):
        entrevistador_actual = db.query(Employee).get(f["segunda_entrevista_entrevistador_id"])
    pedido = (db.query(PedidoPersonal).filter(PedidoPersonal.codigo == f["codigo_pedido_seleccion"]).first()
              if f.get("codigo_pedido_seleccion") else None)
    lead = db.query(LeadCandidato).get(f["lead_origen_id"]) if f.get("lead_origen_id") else None
    etapa_clave, etapa_texto = _etapa_seleccion(emp, f)
    return templates.TemplateResponse(request, "rrhh_seleccion_detalle.html", _ctx(
        request, user, e=emp, ficha=f, entrevistadores=entrevistadores,
        entrevistador_actual=entrevistador_actual, ya_lleno_ficha=_ya_lleno_ficha(emp),
        disc_dimensiones=DISC_DIMENSIONES, nuevo=True, pedido=pedido, lead=lead,
        etapa_clave=etapa_clave, etapa_texto=etapa_texto, legajo_completo=bool(emp.completed_at),
        enlace_ficha=_public_base_url() + f"f/{emp.token}",
        enlace_contrato=_public_base_url() + f"f/{emp.token}/contrato",
        active="seleccion",
    ))


@router.post("/rrhh/reclutamiento/seleccion/{employee_id}/asignar")
def seleccion_asignar(employee_id: int, entrevistador_id: int = Form(...),
                       db: Session = Depends(get_db),
                       user: User = Depends(require_perm("seleccion", "editar"))):
    """Asigna (o reasigna) a quién le toca la segunda entrevista — mismo
    aviso por correo/WhatsApp que al aprobar el lead con un entrevistador
    elegido (ver _notificar_entrevistador), por si hay que cambiarlo o
    todavía no se había elegido a nadie."""
    emp = db.query(Employee).get(employee_id)
    entrevistador = db.query(Employee).get(entrevistador_id)
    if not emp or not entrevistador:
        raise HTTPException(404)
    f = dict(emp.ficha_data or {})
    f["segunda_entrevista_entrevistador_id"] = entrevistador.id
    f["segunda_entrevista_entrevistador_nombre"] = entrevistador.nombre_completo
    if not f.get("segunda_entrevista_token"):
        f["segunda_entrevista_token"] = uuid.uuid4().hex
    # Al reasignar, se limpia el veredicto anterior (si lo hubiera) — es una
    # entrevista nueva, con otra persona.
    f["segunda_entrevista_veredicto"] = None
    f["segunda_entrevista_comentario"] = None
    emp.ficha_data = f
    db.commit()

    enviado, wa_link = _notificar_entrevistador(emp, entrevistador)
    mensaje = "correo_enviado" if enviado else "correo_no_configurado"
    destino = f"/rrhh/reclutamiento/seleccion/{employee_id}?ok={mensaje}"
    if wa_link:
        destino += f"&wa={quote(wa_link)}"
    return RedirectResponse(destino, status_code=303)


@router.post("/rrhh/reclutamiento/seleccion/{employee_id}/confirmar-aprobado")
def seleccion_confirmar_aprobado(employee_id: int, observaciones: str = Form(""), db: Session = Depends(get_db),
                                  user: User = Depends(require_perm("seleccion", "editar"))):
    """RR.HH. confirma el veredicto APROBADO del entrevistador — punto 1 del
    pedido (16/09): recién acá se le manda al candidato el enlace de
    autoservicio para llenar su ficha (antes se mandaba al aprobar el lead,
    demasiado pronto)."""
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    f = dict(emp.ficha_data or {})
    f["seleccion_aprobado"] = True
    f["seleccion_rrhh_confirmado"] = "aprobado"
    f["contratar_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    if observaciones.strip():
        f["seleccion_observaciones"] = observaciones.strip()[:2000]
    emp.ficha_data = f
    db.commit()

    # Punto 1 del pedido (15/09): aprobar en Selección resta una vacante del
    # pedido de origen. Desde el 15/10, en el flujo nuevo la vacante se descuenta
    # recién cuando la persona FIRMA el contrato (registrar_contrato_firmado);
    # el flujo anterior conserva el descuento al aprobar.

    enlace = _public_base_url() + f"f/{emp.token}"
    enviado = False
    if emp.email:
        cuerpo = (
            f"Hola {emp.nombre_completo.split()[0] if emp.nombre_completo else ''},\n\n"
            "¡Buenas noticias! Has sido seleccionado/a para continuar el proceso de selección de "
            "DIGETEL GROUP.\n\n"
            f"El siguiente paso es completar tu ficha de datos y documentos aquí:\n{enlace}\n\n"
            "Cualquier duda que tengas, escríbenos respondiendo este correo.\n\n"
            "Saludos cordiales,\nRecursos Humanos — DIGETEL GROUP"
        )
        enviado = _enviar_correo([emp.email], "¡Felicitaciones! Siguiente paso — DIGETEL GROUP", cuerpo)

    wa_link = None
    wa_auto = False
    wa_numero = _celular_completo(f)
    if wa_numero:
        # 12/10: si el flujo de n8n está configurado, él manda la plantilla
        # «seleccion» por WhatsApp Business (Meta Cloud API) y RR.HH. ya no
        # tiene que hacer clic en el wa.me. Si no está o falla, queda el
        # enlace manual de siempre.
        from . import n8n
        primer_nombre = emp.nombre_completo.split()[0] if emp.nombre_completo else ""
        if n8n.webhook_configurado("N8N_WEBHOOK_SELECCION_APROBADO_URL"):
            pedido_origen = (db.query(PedidoPersonal)
                             .filter(PedidoPersonal.codigo == f["codigo_pedido_seleccion"]).first()
                             if f.get("codigo_pedido_seleccion") else None)
            cargo = f.get("vacante_cargo") or (pedido_origen.cargo_solicitado if pedido_origen else None)
            resultado_n8n = n8n.llamar_webhook("N8N_WEBHOOK_SELECCION_APROBADO_URL", {
                "employee_id": emp.id,
                "nombre_completo": emp.nombre_completo,
                "primer_nombre": primer_nombre,
                "email": emp.email,
                "celular": wa_numero,
                "cargo": cargo,
                # Los cargos se guardan en MAYÚSCULAS; para el texto del
                # WhatsApp ("...para el puesto de Asesor Comercial").
                "cargo_legible": cargo.title() if cargo else "",
                "area": f.get("vacante_area"),
                "empresa": pedido_origen.empresa.nombre if pedido_origen and pedido_origen.empresa else None,
                "pedido_codigo": f.get("codigo_pedido_seleccion"),
                "enlace_ficha": enlace,
            })
            # n8n puede contestar {"ok": true} o solo recibir el aviso (sin
            # JSON): en ambos casos la llamada quedó registrada como OK.
            from .models import LlamadaN8n
            ultima = db.query(LlamadaN8n).filter(LlamadaN8n.evento == "seleccion_aprobado")                 .order_by(LlamadaN8n.id.desc()).first()
            wa_auto = bool((resultado_n8n and resultado_n8n.get("ok") is not False) or
                           (resultado_n8n is None and ultima and ultima.ok))
        if not wa_auto:
            texto_wa = (
                f"Hola {primer_nombre}, te escribimos de "
                "Recursos Humanos de DIGETEL GROUP. ¡Felicitaciones! Te acabamos de enviar un correo con el "
                "siguiente paso de tu proceso — por favor revísalo."
            )
            wa_link = f"https://wa.me/{wa_numero}?text={quote(texto_wa)}"

    mensaje = "correo_enviado" if enviado else "correo_no_configurado"
    destino = f"/rrhh/reclutamiento/seleccion/{employee_id}?ok={mensaje}"
    if wa_auto:
        destino += "&wa_auto=1"
    if wa_link:
        destino += f"&wa={quote(wa_link)}"
    return RedirectResponse(destino, status_code=303)


@router.post("/rrhh/reclutamiento/seleccion/{employee_id}/confirmar-descartado")
def seleccion_confirmar_descartado(employee_id: int, observaciones: str = Form(""), db: Session = Depends(get_db),
                                    user: User = Depends(require_perm("seleccion", "editar"))):
    """RR.HH. confirma el veredicto DESCARTADO del entrevistador (o descarta
    directamente un REVISAR) — punto 1 del pedido (16/09): a quien se
    descarta NO le queda un registro en Personal, pero sí un historial
    completo de su proceso en HistorialDescarte. Se le manda antes el
    correo/WhatsApp de agradecimiento (con los datos que todavía tiene),
    y recién después se archiva y se borra el legajo."""
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    f = dict(emp.ficha_data or {})

    enviado = False
    primer = emp.nombre_completo.split()[0] if emp.nombre_completo else ""
    cuerpo = (
        f"Hola {primer},\n\n"
        "Queremos agradecerte de corazón el tiempo que dedicaste a nuestro proceso de selección y la "
        "conversación que tuvimos contigo. Fue un gusto conocerte.\n\n"
        "Después de evaluar con mucho cuidado a todas las personas que llegaron hasta esta etapa, hemos "
        "decidido continuar con otro perfil para esta posición. Con esto damos por concluido tu proceso. "
        "Sabemos que no es la noticia que esperabas, pero lo que mostraste nos dejó una muy buena impresión "
        "y nos encantaría que sigas atento a futuras oportunidades en DIGETEL GROUP.\n\n"
        "Te deseamos muchos éxitos en lo que viene.\n\n"
        "Un abrazo,\nRecursos Humanos — DIGETEL GROUP"
    )
    if emp.email:
        # Primero por n8n (Gmail de trabajaconnosotros); si no está o falla, el SMTP de MICELIO.
        from . import n8n as _n8n
        resultado_n8n = _n8n.llamar_webhook("N8N_WEBHOOK_DESCARTAR_URL", {
            "tipo": "seleccion", "employee_id": emp.id,
            "nombre_completo": emp.nombre_completo, "email": emp.email,
            "celular": _celular_completo(f), "cargo": f.get("vacante_cargo"), "empresa": emp.empresa,
        }, evento="descartar")
        if resultado_n8n and resultado_n8n.get("ok"):
            enviado = True
        else:
            enviado = _enviar_correo([emp.email], "Gracias por tu tiempo — DIGETEL GROUP", cuerpo)

    wa_link = None
    wa_numero = _celular_completo(f)
    if wa_numero:
        # Punto 2 del pedido (16/09): aviso corto, el correo lleva el detalle.
        texto_wa = (
            f"Hola {emp.nombre_completo.split()[0] if emp.nombre_completo else ''}, te escribimos de "
            "Recursos Humanos de DIGETEL GROUP. Te acabamos de enviar un correo sobre tu proceso de "
            "selección — por favor revísalo. ¡Gracias por tu tiempo!"
        )
        wa_link = f"https://wa.me/{wa_numero}?text={quote(texto_wa)}"

    # El Lead que originó este legajo (si se puede ubicar por el código de
    # pedido + nombre) también se archiva y se borra — ya no debe quedar
    # colgado en Gestión de Leads con la etapa "oferta".
    lead = None
    if f.get("codigo_pedido_seleccion"):
        lead = (
            db.query(LeadCandidato).join(PedidoPersonal)
            .filter(PedidoPersonal.codigo == f["codigo_pedido_seleccion"],
                    LeadCandidato.nombre_completo == emp.nombre_completo)
            .first()
        )

    _archivar_descarte(
        db, nombre_completo=emp.nombre_completo, email=emp.email, celular=_celular_completo(f),
        empresa=emp.empresa, etapa_descarte="seleccion", codigo_pedido=f.get("codigo_pedido_seleccion"),
        vacante_cargo=f.get("vacante_cargo"),
        motivo=(observaciones.strip() or f.get("seleccion_observaciones") or f.get("segunda_entrevista_comentario")),
        datos_proceso={
            "conclusion_ia": f.get("conclusion_ia"), "calificacion_final": f.get("calificacion_final"),
            "entrevista_cita": f.get("entrevista_cita"),
            "clasificacion_entrevista": f.get("clasificacion_entrevista"),
            "disc_resultado": f.get("disc_resultado"),
            "competencias_entrevista": f.get("competencias_entrevista"),
            "segunda_entrevista_entrevistador_nombre": f.get("segunda_entrevista_entrevistador_nombre"),
            "segunda_entrevista_fecha_hora": f.get("segunda_entrevista_fecha_hora"),
            "segunda_entrevista_veredicto": f.get("segunda_entrevista_veredicto"),
            "segunda_entrevista_comentario": f.get("segunda_entrevista_comentario"),
            "segunda_entrevista_veredicto_at": f.get("segunda_entrevista_veredicto_at"),
            "lead_origen": lead.entrevista_data if lead else None,
        },
        descartado_por=user.nombre_completo,
    )
    if lead:
        db.delete(lead)
    _eliminar_employee_completo(db, emp)
    db.commit()

    mensaje = "correo_enviado" if enviado else "correo_no_configurado"
    destino = f"/rrhh/reclutamiento/seleccion?ok={mensaje}"
    if wa_link:
        destino += f"&wa={quote(wa_link)}"
    return RedirectResponse(destino, status_code=303)


# ---------------------------------------------------------------------------
# ENVIAR CONTRATO (15/10): aparece cuando la persona terminó su ficha. Primero
# se revisa en pantalla (borrador) y, si está bien, se envía a firma digital.
# ---------------------------------------------------------------------------
CAMPOS_REVISION_CONTRATO = [
    ("empresa_razon_social", "Empresa"), ("empresa_ruc", "RUC"), ("nombre_completo", "Trabajador/a"),
    ("tipo_documento", "Tipo de documento"), ("numero_documento", "N.º de documento"),
    ("cargo", "Cargo"), ("area", "Área"), ("regimen_laboral_persona", "Régimen laboral"),
    ("remuneracion", "Remuneración"), ("horario", "Horario"), ("jornada", "Jornada"),
    ("fecha_ingreso", "Fecha de ingreso"), ("fecha_fin_contrato", "Fin del contrato"),
    ("periodo_prueba_dias", "Periodo de prueba (días)"), ("direccion", "Dirección"),
]


def _campos_contrato(db: Session, emp: Employee) -> list[dict]:
    from . import main as _main
    campos = _main.build_doc_fields(emp, "contrato", db)
    return [{"clave": k, "etiqueta": et, "valor": campos.get(k), "falta": not campos.get(k)} for k, et in CAMPOS_REVISION_CONTRATO]


@router.get("/rrhh/reclutamiento/seleccion/{employee_id}/contrato", response_class=HTMLResponse)
def seleccion_contrato_revision(request: Request, employee_id: int, db: Session = Depends(get_db),
                                 user: User = Depends(require_perm("seleccion", "ver"))):
    from .models import CONTRATO_LABELS_POR_REGIMEN, CONTRATO_LABEL_DEFAULT
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    f = emp.ficha_data or {}
    regimen = (f.get("regimen_laboral_persona") or "").strip()
    campos = _campos_contrato(db, emp)
    return templates.TemplateResponse(request, "rrhh_contrato_revision.html", _ctx(
        request, user, e=emp, ficha=f, campos=campos, faltan=[c for c in campos if c["falta"]],
        titulo_doc=CONTRATO_LABELS_POR_REGIMEN.get(regimen, CONTRATO_LABEL_DEFAULT),
        enlace_contrato=_public_base_url() + f"f/{emp.token}/contrato",
        legajo_completo=bool(emp.completed_at), active="seleccion",
    ))


@router.get("/rrhh/reclutamiento/seleccion/{employee_id}/contrato/borrador.pdf")
def seleccion_contrato_borrador(employee_id: int, db: Session = Depends(get_db),
                                 user: User = Depends(require_perm("seleccion", "ver"))):
    """Borrador del contrato para revisarlo dentro de la pantalla (inline)."""
    from fastapi.responses import FileResponse
    from . import main as _main
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    doc_fields = _main.build_doc_fields(emp, "contrato", db)
    out_path = os.path.join(_main.GENERATED_DIR, f"contrato_revision_{emp.token}.pdf")
    empresa_obj = emp.empresa_rel
    _main.build_pdf(
        doc_type="contrato", fields=doc_fields, signature_image_path="",
        signed_at="(borrador — pendiente de firma)", ip="", hash_="(pendiente)", out_path=out_path,
        empresa_nombre=empresa_obj.nombre if empresa_obj else emp.empresa,
        representante_legal=empresa_obj.representante_legal if empresa_obj else None,
        firma_empresa_path=empresa_obj.firma_representante_path if empresa_obj else None,
    )
    return FileResponse(out_path, media_type="application/pdf", content_disposition_type="inline")


@router.post("/rrhh/reclutamiento/seleccion/{employee_id}/enviar-contrato")
def seleccion_enviar_contrato(employee_id: int, db: Session = Depends(get_db),
                               user: User = Depends(require_perm("seleccion", "editar"))):
    """Confirmado en pantalla: manda el enlace de firma al postulante por
    WhatsApp y correo (n8n) y deja constancia."""
    from .models import CONTRATO_LABELS_POR_REGIMEN, CONTRATO_LABEL_DEFAULT
    from . import n8n as _n8n
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    f = dict(emp.ficha_data or {})
    if f.get("seleccion_rrhh_confirmado") != "aprobado":
        raise HTTPException(400, "Primero hay que contratar al candidato.")
    if _contrato_firmado(emp):
        return RedirectResponse(f"/rrhh/reclutamiento/seleccion/{employee_id}?ok=contrato_ya_firmado", status_code=303)
    regimen = (f.get("regimen_laboral_persona") or "").strip()
    titulo = CONTRATO_LABELS_POR_REGIMEN.get(regimen, CONTRATO_LABEL_DEFAULT)
    enlace = _public_base_url() + f"f/{emp.token}/contrato"
    primer = emp.nombre_completo.split()[0] if emp.nombre_completo else ""
    celular = _celular_completo(f)
    resultado = None
    if _n8n.webhook_configurado("N8N_WEBHOOK_CONTRATO_URL"):
        resultado = _n8n.llamar_webhook("N8N_WEBHOOK_CONTRATO_URL", {
            "employee_id": emp.id, "nombre_completo": emp.nombre_completo, "primer_nombre": primer,
            "email": emp.email, "celular": celular, "cargo": f.get("vacante_cargo"),
            "cargo_legible": (f.get("vacante_cargo") or "").title(), "empresa": emp.empresa,
            "tipo_documento": titulo, "enlace_contrato": enlace,
        }, evento="contrato")
    enviado_correo = False
    if not (resultado and resultado.get("ok")) and emp.email:
        cuerpo = (f"Hola {primer},\n\nTu {titulo.lower()} está listo para tu revisión y firma digital. "
                  f"Ingresa aquí para leerlo y firmarlo:\n{enlace}\n\nSaludos,\nRecursos Humanos — DIGETEL GROUP")
        enviado_correo = _enviar_correo([emp.email], f"Tu {titulo} — DIGETEL GROUP", cuerpo)
    f["contrato_enviado_at"] = datetime.datetime.utcnow().isoformat(timespec="seconds")
    f["contrato_enviado_por"] = user.nombre_completo
    emp.ficha_data = f
    db.commit()
    via = "n8n" if (resultado and resultado.get("ok")) else ("correo" if enviado_correo else "enlace")
    return RedirectResponse(f"/rrhh/reclutamiento/seleccion/{employee_id}?ok=contrato_enviado&via={via}", status_code=303)


# ---------------------------------------------------------------------------
# Enlace público de la segunda entrevista: quien fue elegido para hacerla
# entra sin usuario ni clave (mismo criterio que /f/{token} en main.py), ve
# los datos del candidato/a (correo, DISC, competencias) y marca su
# veredicto — APROBADO, DESCARTADO o REVISAR — con un comentario.
# ---------------------------------------------------------------------------
@router.get("/entrevista2/{token}", response_class=HTMLResponse)
def entrevista2_form(request: Request, token: str, db: Session = Depends(get_db)):
    emp = _employee_por_segunda_entrevista_token(db, token)
    if not emp:
        raise HTTPException(404)
    f = emp.ficha_data or {}
    # Punto 4 del pedido (16/09): una vez que el entrevistador envía su
    # veredicto, el enlace se deshabilita para él — queda solo en modo
    # lectura, todo pasa a potestad de RR.HH.
    bloqueado = bool(f.get("segunda_entrevista_veredicto"))
    return templates.TemplateResponse(request, "entrevista2_publico.html", {
        "request": request, "e": emp, "ficha": f, "token": token, "disc_dimensiones": DISC_DIMENSIONES,
        "bloqueado": bloqueado, "ok": request.query_params.get("ok"),
    })


@router.post("/entrevista2/{token}/fecha")
def entrevista2_guardar_fecha(token: str, fecha_hora: str = Form(""), db: Session = Depends(get_db)):
    """Punto 4 del pedido (16/09): el entrevistador coloca (y puede
    modificar) la fecha/hora de la entrevista mientras el enlace siga
    abierto — para que RR.HH. tenga conocimiento de cuándo se coordinó."""
    emp = _employee_por_segunda_entrevista_token(db, token)
    if not emp:
        raise HTTPException(404)
    f = dict(emp.ficha_data or {})
    if f.get("segunda_entrevista_veredicto"):
        # Ya se bloqueó el enlace — no se puede seguir editando.
        return RedirectResponse(f"/entrevista2/{token}", status_code=303)
    f["segunda_entrevista_fecha_hora"] = fecha_hora.strip() or None
    emp.ficha_data = f
    db.commit()
    return RedirectResponse(f"/entrevista2/{token}?ok=fecha_guardada", status_code=303)


@router.post("/entrevista2/{token}")
def entrevista2_guardar(token: str, veredicto: str = Form(...), comentario: str = Form(""),
                         db: Session = Depends(get_db)):
    if veredicto not in ("aprobado", "descartado", "revisar"):
        raise HTTPException(400, "Veredicto inválido.")
    emp = _employee_por_segunda_entrevista_token(db, token)
    if not emp:
        raise HTTPException(404)
    f = dict(emp.ficha_data or {})
    if f.get("segunda_entrevista_veredicto"):
        # Defensa en profundidad — el enlace ya está bloqueado, la plantilla
        # ya no debería mostrar el formulario, pero por si acaso.
        return RedirectResponse(f"/entrevista2/{token}", status_code=303)
    f["segunda_entrevista_veredicto"] = veredicto
    f["segunda_entrevista_comentario"] = comentario.strip() or None
    # Hora de Lima (UTC-5), no la del servidor (UTC).
    f["segunda_entrevista_veredicto_at"] = (datetime.datetime.utcnow() - datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
    emp.ficha_data = f
    db.commit()
    return RedirectResponse(f"/entrevista2/{token}?ok=1", status_code=303)


# ---------------------------------------------------------------------------
# Onboarding — vista general de avance por trabajador (el detalle y la carga
# de cada registro vive en la ficha del trabajador, /rrhh/personal/{id}).
# ---------------------------------------------------------------------------
@router.get("/rrhh/reclutamiento/onboarding", response_class=HTMLResponse)
def onboarding_overview(request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require_perm("onboarding", "ver"))):
    empleados = (
        db.query(Employee)
        .filter(Employee.estado == "activo")
        .order_by(Employee.created_at.desc())
        .limit(100).all()
    )
    total_etapas = len(ETAPAS_ONBOARDING)
    resumen = []
    for e in empleados:
        fe = e.ficha_data or {}
        if _es_flujo_nuevo(fe) and not _contrato_firmado(e):
            continue  # sigue en Selección: entra a Onboarding al firmar su contrato
        completadas = {r.etapa for r in e.onboarding if r.estado == "completado"}
        resumen.append({"e": e, "completadas": len(completadas), "total": total_etapas})
    return templates.TemplateResponse(request, "rrhh_onboarding.html", _ctx(
        request, user, resumen=resumen, active="onboarding",
    ))


# ---------------------------------------------------------------------------
# Historial de Descartados — punto 1 del pedido (16/09): quienes se
# descartan (en Gestión de Leads o en Selección) no quedan en Personal ni
# en los Leads activos, pero sí queda acá un registro histórico completo.
# ---------------------------------------------------------------------------
@router.get("/rrhh/reclutamiento/descartados", response_class=HTMLResponse)
def descartados_list(request: Request, etapa: str = "", db: Session = Depends(get_db),
                      user: User = Depends(require_perm("descartados", "ver"))):
    query = db.query(HistorialDescarte)
    if etapa:
        query = query.filter(HistorialDescarte.etapa_descarte == etapa)
    descartados = query.order_by(HistorialDescarte.descartado_at.desc()).all()
    return templates.TemplateResponse(request, "rrhh_descartados.html", _ctx(
        request, user, descartados=descartados, etapas=ETAPAS_DESCARTE, f_etapa=etapa, active="descartados",
    ))
