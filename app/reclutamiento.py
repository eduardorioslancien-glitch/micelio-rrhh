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

from fastapi import APIRouter, Request, Depends, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    PedidoPersonal, LeadCandidato, Empresa, Employee, User, Cargo, Catalogo, EsquemaPago, BaseOperativa,
    ESTADOS_PEDIDO, ESTADO_PEDIDO_KEYS, MOTIVOS_PEDIDO, URGENCIAS_PEDIDO,
    ETAPAS_LEAD, ETAPA_LEAD_KEYS, ORIGENES_LEAD, ETAPAS_ONBOARDING, STATUS_PENDIENTE,
    CLASIFICACIONES_LEAD,
)
from .auth import require_role, require_jefe_o_gerente, es_jefe_o_gerente
from .rrhh import _ctx, _enviar_correo, _public_base_url, _ensure_documents
from .cv_analysis import extraer_texto_cv, analizar_cv
from .public_landing import CV_DIR, EXTENSIONES_CV_VALIDAS, TAMANO_MAXIMO_CV

# Cargos de mayor rotación que necesitan referencia obligatoria a una Base
# (zona geográfica) — ver punto 2 del pedido sobre Registro de Pedidos.
CARGOS_REQUIEREN_BASE = ["tecnico", "técnico", "guardian", "guardián", "lider de base", "líder de base", "almacenero"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
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
@router.get("/rrhh/reclutamiento/pedidos", response_class=HTMLResponse)
def pedidos_list(request: Request, estado: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require_role("administrador", "opeoka"))):
    query = db.query(PedidoPersonal)
    if estado:
        query = query.filter(PedidoPersonal.estado == estado)
    pedidos = query.order_by(PedidoPersonal.created_at.desc()).all()
    empresas = db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
    cargos = db.query(Cargo).filter(Cargo.activo == True).order_by(Cargo.nombre).all()  # noqa: E712
    areas = db.query(Catalogo).filter(Catalogo.tipo == "area", Catalogo.activo == True).order_by(Catalogo.nombre).all()  # noqa: E712
    empleados = db.query(Employee).filter(Employee.estado == "activo").order_by(Employee.nombre_completo).all()
    bases = db.query(BaseOperativa).filter(BaseOperativa.activo == True).order_by(  # noqa: E712
        BaseOperativa.empresa_id, BaseOperativa.nombre).all()
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
        estado_labels=ESTADO_LABELS, motivos=MOTIVOS_PEDIDO, urgencias=URGENCIAS_PEDIDO,
        cargos=cargos, areas=areas, empleados=empleados, bases=bases,
        compensacion_por_cargo=compensacion_por_cargo, puede_generar=puede_generar,
        cargos_requieren_base=CARGOS_REQUIEREN_BASE,
        f_estado=estado, active="pedidos",
    ))


@router.post("/rrhh/reclutamiento/pedidos/nuevo")
def pedidos_crear(cargo_solicitado: str = Form(...), cantidad: int = Form(1),
                   empresa_id: str = Form(""), area: str = Form(""), base_id: str = Form(""),
                   solicitante: str = Form(""), motivo: str = Form(""), urgencia: str = Form(""),
                   fecha_requerida: str = Form(""), observaciones: str = Form(""),
                   sueldo_base_ofrecido: str = Form(""), comision_ofrecida: str = Form(""),
                   movilidad_ofrecida: str = Form(""), combustible_ofrecido: str = Form(""),
                   otros_ingresos_ofrecido: str = Form(""),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_jefe_o_gerente)):
    # Punto 3 del pedido: si quien registra NO es administrador (es un Jefe/
    # Gerente generando su propio pedido), el Solicitante es él mismo — se
    # ignora cualquier otro valor que llegue del formulario.
    if user.rol == "administrador":
        solicitante_final = solicitante.strip() or None
    else:
        solicitante_final = user.nombre_completo
    db.add(PedidoPersonal(
        codigo=_generar_codigo_pedido(db),
        cargo_solicitado=cargo_solicitado.strip(), area=area.strip() or None,
        empresa_id=int(empresa_id) if empresa_id else None, base_id=int(base_id) if base_id else None,
        cantidad=max(cantidad, 1),
        motivo=motivo or None, urgencia=urgencia or None, solicitante=solicitante_final,
        fecha_requerida=_parse_fecha(fecha_requerida), observaciones=observaciones.strip() or None,
        sueldo_base_ofrecido=_monto_o_none(sueldo_base_ofrecido), comision_ofrecida=_monto_o_none(comision_ofrecida),
        movilidad_ofrecida=_monto_o_none(movilidad_ofrecida), combustible_ofrecido=_monto_o_none(combustible_ofrecido),
        otros_ingresos_ofrecido=_monto_o_none(otros_ingresos_ofrecido),
        registrado_por=user.nombre_completo,
    ))
    db.commit()
    return RedirectResponse("/rrhh/reclutamiento/pedidos", status_code=303)


@router.post("/rrhh/reclutamiento/pedidos/{pedido_id}/estado")
def pedidos_cambiar_estado(pedido_id: int, estado: str = Form(...), db: Session = Depends(get_db),
                            user: User = Depends(require_role("administrador"))):
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
    """Mayor calificación primero; entre iguales, el más antiguo primero
    (para que RR.HH. no deje esperando a quien postuló hace más tiempo)."""
    return (-(lead.estrellas or 0), lead.created_at)


@router.get("/rrhh/reclutamiento/leads", response_class=HTMLResponse)
def leads_list(request: Request, etapa: str = "", pedido_id: str = "", db: Session = Depends(get_db),
                user: User = Depends(require_role("administrador"))):
    pedidos_abiertos = (
        db.query(PedidoPersonal)
        .filter(PedidoPersonal.estado.in_(["abierto", "en_proceso"]))
        .order_by(PedidoPersonal.created_at.desc()).all()
    )

    # Punto pedido por el usuario: la pantalla principal de Gestión de Leads
    # muestra primero los Pedidos abiertos/en proceso (cargo + cantidad); al
    # entrar a uno se filtran/registran los candidatos de ESE pedido. "Sin
    # pedido" (pedido_id=none) muestra los pocos candidatos sueltos que no
    # quedaron ligados a ninguno (siempre fue posible dejarlo así al
    # registrar manualmente).
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
        f_etapa=etapa, f_pedido=pedido_id,
        pedido_actual=pedido_actual, leads_por_pedido=leads_por_pedido, active="leads",
    ))


@router.post("/rrhh/reclutamiento/leads/nuevo")
async def leads_crear(nombre_completo: str = Form(...), email: str = Form(""), celular: str = Form(""),
                       origen: str = Form(""), pedido_id: str = Form(""), notas: str = Form(""),
                       cv: UploadFile = File(None),
                       db: Session = Depends(get_db),
                       user: User = Depends(require_role("administrador"))):
    lead = LeadCandidato(
        nombre_completo=nombre_completo.strip(), email=email.strip() or None, celular=celular.strip() or None,
        origen=origen or None, pedido_id=int(pedido_id) if pedido_id else None, notas=notas.strip() or None,
        registrado_por=user.nombre_completo,
    )

    # CV opcional al registrar manualmente (candidatos que llegan por correo,
    # WhatsApp, etc.) — mismo criterio de validación que "Trabaja con
    # Nosotros" para que después se pueda calificar con IA igual que ahí.
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

    if lead.cv_path and lead.pedido_id:
        pedido = db.query(PedidoPersonal).get(lead.pedido_id)
        cargo = db.query(Cargo).filter(Cargo.nombre == pedido.cargo_solicitado).first() if pedido else None
        if cargo:
            texto_cv = extraer_texto_cv(lead.cv_path, cv.content_type if cv else None)
            estrellas, analisis = analizar_cv(texto_cv, cargo)
            lead.estrellas = estrellas
            lead.analisis_ia = analisis
            db.commit()

    destino = f"/rrhh/reclutamiento/leads?pedido_id={pedido_id}" if pedido_id else "/rrhh/reclutamiento/leads?pedido_id=none"
    return RedirectResponse(destino, status_code=303)


@router.post("/rrhh/reclutamiento/leads/{lead_id}/etapa")
def leads_cambiar_etapa(lead_id: int, etapa: str = Form(...), db: Session = Depends(get_db),
                         user: User = Depends(require_role("administrador"))):
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
            user: User = Depends(require_role("administrador"))):
    from fastapi.responses import FileResponse
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead or not lead.cv_path or not os.path.exists(lead.cv_path):
        raise HTTPException(404)
    return FileResponse(lead.cv_path, filename=lead.cv_filename or "cv.pdf")


@router.post("/rrhh/reclutamiento/leads/{lead_id}/analizar-cv")
def lead_analizar_cv(lead_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_role("administrador"))):
    """Corre (o vuelve a correr) la calificación de IA sobre el CV de un
    candidato — a pedido, no solo automático al postular, para poder
    calificar leads registrados manualmente o volver a intentar después de
    configurar ANTHROPIC_API_KEY o de actualizar el MOF del cargo."""
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    destino = f"/rrhh/reclutamiento/leads/{lead_id}"
    if not lead.cv_path or not os.path.exists(lead.cv_path):
        return RedirectResponse(f"{destino}?cv_error=Este+candidato+no+tiene+un+CV+adjunto+valido.", status_code=303)

    cargo = None
    if lead.pedido and lead.pedido.cargo_solicitado:
        cargo = db.query(Cargo).filter(Cargo.nombre == lead.pedido.cargo_solicitado).first()
    if not cargo:
        return RedirectResponse(
            f"{destino}?cv_error=No+se+encontro+el+Cargo+del+pedido+asignado+(o+el+lead+no+tiene+pedido)+para+comparar+el+CV.",
            status_code=303,
        )

    texto_cv = extraer_texto_cv(lead.cv_path, None)
    estrellas, analisis = analizar_cv(texto_cv, cargo)
    lead.estrellas = estrellas
    lead.analisis_ia = analisis
    db.commit()
    if estrellas is None:
        return RedirectResponse(f"{destino}?cv_error={quote(analisis)}", status_code=303)
    return RedirectResponse(f"{destino}?ok=cv_analizado", status_code=303)


@router.get("/rrhh/reclutamiento/leads/{lead_id}", response_class=HTMLResponse)
def lead_detalle(request: Request, lead_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_role("administrador"))):
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    cargo = None
    if lead.pedido and lead.pedido.cargo_solicitado:
        cargo = db.query(Cargo).filter(Cargo.nombre == lead.pedido.cargo_solicitado).first()
    disc_resultado = None
    if lead.entrevista_data and lead.entrevista_data.get("disc"):
        disc_resultado = lead.entrevista_data["disc"]
    return templates.TemplateResponse(request, "rrhh_lead_detalle.html", _ctx(
        request, user, lead=lead, cargo=cargo, disc_preguntas=DISC_PREGUNTAS,
        disc_dimensiones=DISC_DIMENSIONES, disc_resultado=disc_resultado,
        clasificaciones=CLASIFICACIONES_LEAD, active="leads",
    ))


def _correo_coordinar_meet(lead: LeadCandidato) -> bool:
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
                         user: User = Depends(require_role("administrador"))):
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    enviado = _correo_coordinar_meet(lead) if lead.email else False
    lead.etapa = "contactado"
    db.commit()
    mensaje = "correo_enviado" if enviado else "correo_no_configurado"
    return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok={mensaje}", status_code=303)


@router.post("/rrhh/reclutamiento/leads/{lead_id}/descartar")
def lead_descartar(lead_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_role("administrador"))):
    lead = db.query(LeadCandidato).get(lead_id)
    if not lead:
        raise HTTPException(404)
    enviado = _correo_descarte(lead) if lead.email else False
    lead.etapa = "descartado"
    db.commit()
    mensaje = "correo_enviado" if enviado else "correo_no_configurado"
    return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok={mensaje}", status_code=303)


@router.post("/rrhh/reclutamiento/leads/{lead_id}/entrevista")
async def lead_guardar_entrevista(request: Request, lead_id: int, db: Session = Depends(get_db),
                                   user: User = Depends(require_role("administrador"))):
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
    entrevista_data["competencias"] = competencias or entrevista_data.get("competencias")
    entrevista_data["disc"] = disc_resultado
    entrevista_data["conclusion"] = form.get("conclusion") or entrevista_data.get("conclusion")
    entrevista_data["entrevistador"] = user.nombre_completo
    entrevista_data["fecha"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    lead.entrevista_data = entrevista_data
    if form.get("clasificacion"):
        lead.clasificacion = form.get("clasificacion")
    if lead.etapa in ("nuevo", "contactado"):
        lead.etapa = "entrevista"
    db.commit()
    return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}", status_code=303)


@router.post("/rrhh/reclutamiento/leads/{lead_id}/aprobar")
def lead_aprobar(lead_id: int, db: Session = Depends(get_db),
                  user: User = Depends(require_role("administrador"))):
    """Aprobado -> pasa a Selección: crea el legajo (Employee pendiente con
    token) y le manda al candidato su enlace de autoservicio."""
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
    if lead.clasificacion:
        ficha_inicial["clasificacion_entrevista"] = lead.clasificacion
    disc = (lead.entrevista_data or {}).get("disc")
    if disc:
        ficha_inicial["disc_resultado"] = disc
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

    enlace = _public_base_url() + f"f/{emp.token}"
    enviado = False
    if lead.email:
        cuerpo = (
            f"Hola {lead.nombre_completo.split()[0] if lead.nombre_completo else ''},\n\n"
            "¡Buenas noticias! Nos gustaría avanzar contigo en el proceso de selección de "
            "DIGETEL GROUP.\n\n"
            f"El siguiente paso es completar tu ficha de datos y documentos aquí:\n{enlace}\n\n"
            "Cualquier duda que tengas, escríbenos respondiendo este correo.\n\n"
            "Saludos cordiales,\nRecursos Humanos — DIGETEL GROUP"
        )
        enviado = _enviar_correo([lead.email], "Siguiente paso en tu proceso — DIGETEL GROUP", cuerpo)
    mensaje = "correo_enviado" if enviado else "correo_no_configurado"
    return RedirectResponse(f"/rrhh/reclutamiento/leads/{lead_id}?ok={mensaje}&enlace={emp.token}", status_code=303)


# ---------------------------------------------------------------------------
# Selección (punto de Eduardo, 2026-09-08): segunda etapa, después de
# Gestión de Leads. Solo llegan acá los candidatos ya aprobados (Employee
# creado por lead_aprobar) con clasificación EXCELENTE/MUY BUENO/BUENO que
# todavía no tienen el visto bueno de una segunda entrevista. Es una
# pantalla nueva y separada — el legajo de documentos firmados de Personal
# no se toca (ver nota en _rrhh_topbar.html).
#
# El envío por WhatsApp queda como un enlace "wa.me" con el mensaje ya
# armado (Eduardo lo confirma y lo manda él mismo) — MICELIO no tiene hoy
# ninguna integración de WhatsApp Business API para mandarlo solo.
# ---------------------------------------------------------------------------
CLASIFICACIONES_ELEGIBLES = ["EXCELENTE", "MUY BUENO", "BUENO"]


def _celular_completo(ficha: dict, campo: str = "celular") -> str:
    """Código de país + número, todo junto y solo dígitos (formato que
    necesita un link wa.me — sin '+' ni espacios)."""
    codigo = (ficha.get(f"{campo}_codigo") or "+51").lstrip("+")
    numero = "".join(ch for ch in (ficha.get(campo) or "") if ch.isdigit())
    return f"{codigo}{numero}" if numero else ""


@router.get("/rrhh/reclutamiento/seleccion", response_class=HTMLResponse)
def seleccion_list(request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_role("administrador"))):
    candidatos = [
        e for e in db.query(Employee).filter(Employee.estado == "activo")
        .order_by(Employee.nombre_completo).all()
        if (e.ficha_data or {}).get("clasificacion_entrevista") in CLASIFICACIONES_ELEGIBLES
        and not (e.ficha_data or {}).get("seleccion_aprobado")
    ]
    return templates.TemplateResponse(request, "rrhh_seleccion.html", _ctx(
        request, user, candidatos=candidatos, active="seleccion",
    ))


@router.get("/rrhh/reclutamiento/seleccion/{employee_id}", response_class=HTMLResponse)
def seleccion_detalle(request: Request, employee_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_role("administrador"))):
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
    wa_link = None
    if entrevistador_actual:
        wa_numero = _celular_completo(entrevistador_actual.ficha_data or {})
        if wa_numero:
            texto = (
                f"Hola {entrevistador_actual.nombre_completo.split()[0]}, te comparto los datos de "
                f"{emp.nombre_completo}, candidato/a a {f.get('vacante_cargo') or 'la vacante'} en tu área, "
                "para coordinar la segunda entrevista. "
                f"Correo: {emp.email or 'sin correo'}. Cuando tengas tu evaluación, avísame para "
                "registrarlo en MICELIO."
            )
            wa_link = f"https://wa.me/{wa_numero}?text={quote(texto)}"
    return templates.TemplateResponse(request, "rrhh_seleccion_detalle.html", _ctx(
        request, user, e=emp, ficha=f, entrevistadores=entrevistadores,
        entrevistador_actual=entrevistador_actual, wa_link=wa_link, active="seleccion",
    ))


@router.post("/rrhh/reclutamiento/seleccion/{employee_id}/asignar")
def seleccion_asignar(employee_id: int, entrevistador_id: int = Form(...),
                       db: Session = Depends(get_db),
                       user: User = Depends(require_role("administrador"))):
    emp = db.query(Employee).get(employee_id)
    entrevistador = db.query(Employee).get(entrevistador_id)
    if not emp or not entrevistador:
        raise HTTPException(404)
    f = dict(emp.ficha_data or {})
    f["segunda_entrevista_entrevistador_id"] = entrevistador.id
    f["segunda_entrevista_entrevistador_nombre"] = entrevistador.nombre_completo
    f["seleccion_aprobado"] = False
    emp.ficha_data = f
    db.commit()

    ef = entrevistador.ficha_data or {}
    correo = ef.get("correo_corporativo") or entrevistador.email
    enviado = False
    if correo:
        cuerpo = (
            f"Hola {entrevistador.nombre_completo.split()[0]},\n\n"
            f"Te compartimos los datos de {emp.nombre_completo}, candidato/a a "
            f"{f.get('vacante_cargo') or 'la vacante'} en tu área, para que coordines con él/ella la "
            "segunda entrevista.\n\n"
            f"Nombre: {emp.nombre_completo}\nCorreo: {emp.email or '—'}\n"
            f"Vacante: {f.get('vacante_cargo') or '—'}\n"
            f"Clasificación de la primera entrevista: {f.get('clasificacion_entrevista') or '—'}\n\n"
            "Cuando tengas tu evaluación, avísale a RR.HH. para dejarlo registrado en MICELIO.\n\n"
            "Saludos,\nRecursos Humanos — DIGETEL GROUP"
        )
        enviado = _enviar_correo([correo], f"Segunda entrevista — {emp.nombre_completo}", cuerpo)
    mensaje = "correo_enviado" if enviado else "correo_no_configurado"
    return RedirectResponse(f"/rrhh/reclutamiento/seleccion/{employee_id}?ok={mensaje}", status_code=303)


@router.post("/rrhh/reclutamiento/seleccion/{employee_id}/aprobar")
def seleccion_aprobar(employee_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_role("administrador"))):
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    f = dict(emp.ficha_data or {})
    f["seleccion_aprobado"] = True
    emp.ficha_data = f
    db.commit()
    return RedirectResponse(f"/rrhh/reclutamiento/seleccion/{employee_id}", status_code=303)


# ---------------------------------------------------------------------------
# Onboarding — vista general de avance por trabajador (el detalle y la carga
# de cada registro vive en la ficha del trabajador, /rrhh/personal/{id}).
# ---------------------------------------------------------------------------
@router.get("/rrhh/reclutamiento/onboarding", response_class=HTMLResponse)
def onboarding_overview(request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require_role("administrador"))):
    empleados = (
        db.query(Employee)
        .filter(Employee.estado == "activo")
        .order_by(Employee.created_at.desc())
        .limit(100).all()
    )
    total_etapas = len(ETAPAS_ONBOARDING)
    resumen = []
    for e in empleados:
        completadas = {r.etapa for r in e.onboarding if r.estado == "completado"}
        resumen.append({"e": e, "completadas": len(completadas), "total": total_etapas})
    return templates.TemplateResponse(request, "rrhh_onboarding.html", _ctx(
        request, user, resumen=resumen, active="onboarding",
    ))
