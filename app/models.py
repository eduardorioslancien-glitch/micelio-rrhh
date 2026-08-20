# -*- coding: utf-8 -*-
"""
Modelo de datos del Sistema de RR.HH. DIGETEL GROUP.

El sistema tiene dos grandes frentes que comparten la misma base de datos:

1. SELECCIÓN DE PERSONAL (módulo original, "Legajo Digital"): el postulante
   llena su ficha y firma sus documentos vía un enlace único (Employee.token).
   Sigue funcionando igual que antes (ver Document, Signature, Attachment).

2. ADMINISTRACIÓN DE PERSONAL (nuevo): una vez que la persona ingresa a
   trabajar, su mismo registro (Employee) pasa a vivir en la base de datos
   maestra del grupo, organizada por Empresa/Unidad de Negocio, con foto,
   bitácora de acciones, documentos adicionales del legajo (certificados,
   memos, salud, altas/bajas SUNAT) y estado (activo/cesado).

Estructura organizacional (parametrizable desde /rrhh/parametrizacion):
  Grupo DIGETEL GROUP
    └── Unidad de Negocio (UnidadNegocio) — p.ej. "UM Servicios"
          └── Empresa (Empresa) — p.ej. "Digetel", con su propio régimen laboral

Control de accesos (User.rol):
  - administrador: acceso total al sistema.
  - conta: acceso a la parte de planillas (datos bancarios/previsionales/
    remuneración) de todo el personal.
  - opeoka: acceso a la parte operativa (datos laborales, tallas, etc.), sin
    ver información bancaria/salarial.
  - usuario: acceso solo a su propia información (autoservicio), vía
    User.employee_id.

NOTA SOBRE EL CONTRATO (ver README, sección "Firma de Contrato"):
El legajo está pensado para TERMINAR con la firma del contrato de trabajo,
pero el formato/plantilla del contrato todavía no fue entregado por RR.HH.
Por eso dejamos previstas aquí las columnas `contrato_tipo`, `contrato_pdf_path`
y `contrato_signed_at` en Employee (hoy sin usar). Cuando se entregue el
formato, el contrato se agrega como una entrada más de DOC_TYPES (la última,
después de "autorizacion_deposito") y el flujo de firma existente lo soporta
sin cambios estructurales adicionales.
"""
import datetime
import uuid

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Text, JSON, Boolean
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

DOC_TYPES = [
    ("ficha", "Ficha de Datos del Personal"),
    ("declaracion_jurada", "Declaración Jurada"),
    ("autorizacion_datos", "Autorización de Tratamiento de Datos Personales"),
    ("derechohabientes", "Formato de Derechohabientes EsSalud"),
    ("autorizacion_deposito", "Autorización de Depósito de Haberes y CTS"),
    # <-- Cuando RR.HH. entregue el formato del contrato, agregar aquí:
    #     ("contrato", "Contrato de Trabajo"),
    # y su texto correspondiente en legal_texts.json. El resto del flujo
    # (firma, PDF, auditoría, email) ya está preparado para soportarlo.
]
DOC_TYPE_KEYS = [d[0] for d in DOC_TYPES]

STATUS_PENDIENTE = "pendiente"
STATUS_ABIERTO = "abierto"
STATUS_FIRMADO = "firmado"

# Tipos de documento que puede adjuntar un postulante/trabajador, o que RR.HH.
# puede subir directamente al legajo de un trabajador ya administrado.
ATTACHMENT_TYPES = [
    ("cv", "Curriculum Vitae (CV)"),
    ("cul", "Certificado Único Laboral (CUL)"),
    ("antecedentes_policiales", "Antecedentes Policiales"),
    ("certificado_curso", "Certificado de Curso / Capacitación"),
    ("memo", "Memorándum"),
    ("documento_salud", "Documento de Salud"),
    ("alta_sunat", "Constancia de Alta en SUNAT"),
    ("baja_sunat", "Constancia de Baja en SUNAT"),
    ("dni_anverso", "Documento de Identidad — Anverso (PDF)"),
    ("dni_reverso", "Documento de Identidad — Reverso (PDF)"),
    ("otros", "Otros documentos"),
]
ATTACHMENT_TYPE_KEYS = [a[0] for a in ATTACHMENT_TYPES]

# Regímenes laborales peruanos habituales, parametrizables por empresa.
REGIMENES_LABORALES = [
    "Microempresa (MYPE)",
    "Pequeña Empresa (MYPE)",
    "Régimen General",
    "Régimen Agrario",
    "Régimen CAS (sector público)",
]

ROLES = [
    ("administrador", "Administrador — acceso total"),
    ("conta", "Contabilidad / Planillas — acceso a datos de planilla"),
    ("opeoka", "Operaciones — acceso a la parte operativa"),
    ("usuario", "Usuario — acceso solo a su información"),
]
ROLE_KEYS = [r[0] for r in ROLES]

TIPOS_BITACORA = ["Observación", "Memorándum", "Reconocimiento", "Incidencia", "Otro"]

# Categorías oficiales de licencia de conducir en Perú (Reglamento Nacional de
# Licencias de Conducir, D.S. N.° 007-2016-MTC).
TIPOS_LICENCIA = [
    "A-I", "A-IIa", "A-IIb", "A-IIIa", "A-IIIb", "A-IIIc",
    "B-I", "B-IIa", "B-IIb", "B-IIc",
]

NIVELES_EDUCATIVOS = ["Primaria", "Secundaria", "Técnica", "Universitaria", "Postgrado"]

# Catálogos parametrizables por el Administrador desde /rrhh/parametrizacion
# (área/gerencia/sede/banco como listas desplegables, en vez de texto libre).
# Se guardan todos en la misma tabla Catalogo con un campo `tipo` porque
# estructuralmente son idénticos (id + nombre + activo); cada uno tiene su
# propia página en Parametrización. "Cargo" NO vive acá — es su propio modelo
# (Cargo, ver más abajo) porque necesita el MOF completo (funciones,
# responsabilidades, requisitos, competencias), no solo un nombre.
CATALOGO_TIPOS = [
    ("area", "Área"),
    ("gerencia", "Gerencia"),
    ("sede", "Sede"),
    ("banco", "Banco"),
    ("centro_costo", "Centro de Costos"),
]
CATALOGO_TIPO_KEYS = [c[0] for c in CATALOGO_TIPOS]


# Registro de Pedidos de Personal (Reclutamiento y Selección — Fase 3).
ESTADOS_PEDIDO = [
    ("abierto", "Abierto"),
    ("en_proceso", "En proceso"),
    ("cubierto", "Cubierto"),
    ("cancelado", "Cancelado"),
]
ESTADO_PEDIDO_KEYS = [e[0] for e in ESTADOS_PEDIDO]
MOTIVOS_PEDIDO = ["Nueva posición", "Reemplazo", "Incremento de dotación", "Otro"]
URGENCIAS_PEDIDO = ["Baja", "Media", "Alta"]

# Control de Leads (candidatos) — Reclutamiento y Selección, Fase 3.
ETAPAS_LEAD = [
    ("nuevo", "Nuevo"),
    ("contactado", "Contactado"),
    ("entrevista", "En entrevista"),
    ("oferta", "Oferta enviada"),
    ("contratado", "Contratado"),
    ("descartado", "Descartado"),
]
ETAPA_LEAD_KEYS = [e[0] for e in ETAPAS_LEAD]
ORIGENES_LEAD = ["LinkedIn", "Referido", "Bolsa de Trabajo", "Feria Laboral", "Otro"]

# Onboarding (seguimiento por trabajador) — Reclutamiento y Selección, Fase 3.
ETAPAS_ONBOARDING = [
    ("induccion_general", "Inducción General"),
    ("acompanamiento", "Acompañamiento"),
    ("evaluacion", "Evaluación"),
    ("feedback", "Feedback"),
]
ETAPA_ONBOARDING_KEYS = [e[0] for e in ETAPAS_ONBOARDING]
ESTADOS_ONBOARDING = [("pendiente", "Pendiente"), ("completado", "Completado")]

# Clima y Cultura — Encuesta 360 (Fase 3). Las preguntas las define RR.HH. al
# crear cada campaña (no hay un set fijo de competencias todavía — eso vive en
# "Principios, Valores y Competencias" de Parámetros, pendiente).
ESTADOS_ENCUESTA = [("abierta", "Abierta"), ("cerrada", "Cerrada")]
RELACIONES_ENCUESTA = ["Autoevaluación", "Jefe", "Par", "Subordinado", "Otro"]

# Principios, Valores y Competencias (Parámetros). Un mismo modelo para los
# tres, distinguidos por `tipo` — los tres se documentan igual (nombre,
# descripción, desarrollo de niveles 1 a 4); en Cargos y Funciones (MOF) solo
# las de tipo "competencia" se usan como requisito del puesto con un nivel
# exigido.
TIPOS_COMPETENCIA = [
    ("principio", "Principio"),
    ("valor", "Valor"),
    ("competencia", "Competencia"),
]
TIPO_COMPETENCIA_KEYS = [t[0] for t in TIPOS_COMPETENCIA]


def gen_token():
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Estructura organizacional: Holding -> Unidad de Negocio -> Empresa -> Línea
# de Producto. Personal (Employee) solo referencia directamente a Empresa;
# el Holding y la Unidad de Negocio se resuelven solos subiendo la cadena
# (Empresa.unidad_negocio -> UnidadNegocio.holding), así que basta con
# asignarle una empresa a alguien para ya saber a qué unidad y holding
# pertenece (punto 10 del pedido).
# ---------------------------------------------------------------------------
class Holding(Base):
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(120), unique=True, nullable=False)
    descripcion = Column(String(300), nullable=True)
    logo_path = Column(String(500), nullable=True)  # PNG del logo del holding
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    unidades_negocio = relationship("UnidadNegocio", back_populates="holding")


class UnidadNegocio(Base):
    __tablename__ = "unidades_negocio"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(120), unique=True, nullable=False)
    descripcion = Column(String(300), nullable=True)
    holding_id = Column(Integer, ForeignKey("holdings.id"), nullable=True)
    activo = Column(Boolean, default=True)

    holding = relationship("Holding", back_populates="unidades_negocio")
    empresas = relationship("Empresa", back_populates="unidad_negocio")


class Empresa(Base):
    __tablename__ = "empresas"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(120), unique=True, nullable=False)
    razon_social = Column(String(200), nullable=True)
    ruc = Column(String(20), nullable=True)
    regimen_laboral = Column(String(60), nullable=True)
    unidad_negocio_id = Column(Integer, ForeignKey("unidades_negocio.id"), nullable=True)
    representante_legal = Column(String(200), nullable=True)
    firma_representante_path = Column(String(500), nullable=True)  # PNG de la firma, sin fondo
    logo_path = Column(String(500), nullable=True)  # PNG del logo de la empresa
    # Punto 14 del pedido (todavía sin flujo de correo armado): datos de
    # contacto para pedir la aprobación de una renovación de contrato.
    gerente_nombre = Column(String(200), nullable=True)
    gerente_email = Column(String(200), nullable=True)
    jefe_rrhh_nombre = Column(String(200), nullable=True)
    jefe_rrhh_email = Column(String(200), nullable=True)
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    unidad_negocio = relationship("UnidadNegocio", back_populates="empresas")
    empleados = relationship("Employee", back_populates="empresa_rel")
    lineas_producto = relationship("LineaProducto", back_populates="empresa", cascade="all, delete-orphan")


class LineaProducto(Base):
    """Línea de Producto — nivel más bajo de la estructura organizacional,
    dentro de una Empresa (una empresa puede tener una o varias)."""
    __tablename__ = "lineas_producto"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), nullable=False)
    descripcion = Column(String(300), nullable=True)
    empresa_id = Column(Integer, ForeignKey("empresas.id"), nullable=False)
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    empresa = relationship("Empresa", back_populates="lineas_producto")


class Catalogo(Base):
    """Listas parametrizables por el Administrador (áreas, gerencias, cargos,
    sedes, bancos) que alimentan los selects del formulario de ficha, en vez
    de que cada trabajador escriba el nombre a mano."""
    __tablename__ = "catalogos"

    id = Column(Integer, primary_key=True)
    tipo = Column(String(20), nullable=False)  # uno de CATALOGO_TIPO_KEYS
    nombre = Column(String(150), nullable=False)
    logo_path = Column(String(500), nullable=True)  # PNG del logo (por ahora solo se usa en tipo="area")
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Competencia(Base):
    """Un Principio, Valor o Competencia del grupo (Parametrización). Los
    niveles 1-4 describen el comportamiento observable en cada grado de
    desarrollo (solo tienen sentido llenarlos para tipo="competencia", pero
    el modelo no lo obliga por si se quiere documentar igual para valores)."""
    __tablename__ = "competencias"

    id = Column(Integer, primary_key=True)
    tipo = Column(String(20), nullable=False)  # uno de TIPO_COMPETENCIA_KEYS
    nombre = Column(String(150), nullable=False)
    descripcion = Column(Text, nullable=True)
    nivel_1 = Column(Text, nullable=True)
    nivel_2 = Column(Text, nullable=True)
    nivel_3 = Column(Text, nullable=True)
    nivel_4 = Column(Text, nullable=True)
    # Solo aplica a tipo="valor": qué comportamientos NO se toleran en relación
    # a ese valor (además de los niveles, que describen el desarrollo deseado).
    conductas_no_deseadas = Column(Text, nullable=True)
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Cargo(Base):
    """Cargos y Funciones — Manual de Organización y Funciones (MOF) de cada
    puesto: descripción, funciones, responsabilidades, a quién reporta,
    requisitos (académicos/experiencia/conocimientos) y las competencias que
    exige con su nivel requerido (1-4, vía CargoRequisitoCompetencia).

    "Lidera a" NO es un campo propio: se calcula solo (relación inversa
    `subordinados`) a partir de qué otros cargos tienen a este como
    `reporta_a` — así nunca queda desincronizado con la jerarquía real."""
    __tablename__ = "cargos"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(150), unique=True, nullable=False)
    descripcion = Column(Text, nullable=True)
    funciones = Column(JSON, default=list)  # lista de strings
    responsabilidades = Column(JSON, default=list)  # lista de strings
    reporta_a_id = Column(Integer, ForeignKey("cargos.id"), nullable=True)
    requisito_academico = Column(Text, nullable=True)
    requisito_experiencia = Column(Text, nullable=True)
    requisito_conocimientos = Column(Text, nullable=True)
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    reporta_a = relationship("Cargo", remote_side=[id], backref="subordinados")
    requisitos_competencias = relationship("CargoRequisitoCompetencia", back_populates="cargo",
                                            cascade="all, delete-orphan")


class CargoRequisitoCompetencia(Base):
    """Una competencia exigida por un Cargo, con el nivel (1-4) requerido."""
    __tablename__ = "cargo_requisitos_competencia"

    id = Column(Integer, primary_key=True)
    cargo_id = Column(Integer, ForeignKey("cargos.id"), nullable=False)
    competencia_id = Column(Integer, ForeignKey("competencias.id"), nullable=False)
    nivel_requerido = Column(Integer, nullable=False)  # 1-4

    cargo = relationship("Cargo", back_populates="requisitos_competencias")
    competencia = relationship("Competencia")


# ---------------------------------------------------------------------------
# Usuarios del sistema (control de accesos)
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(300), nullable=False)
    nombre_completo = Column(String(200), nullable=False)
    rol = Column(String(20), nullable=False, default="usuario")  # uno de ROLE_KEYS
    empresa_id = Column(Integer, ForeignKey("empresas.id"), nullable=True)  # alcance opcional
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True, unique=True)  # para rol "usuario"
    activo = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=True)  # obliga a cambiar la clave en el primer ingreso
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

    empresa = relationship("Empresa")
    employee = relationship("Employee", foreign_keys=[employee_id])


# ---------------------------------------------------------------------------
# Trabajador / postulante
# ---------------------------------------------------------------------------
class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, index=True, default=gen_token)

    # Datos sembrados por RR.HH. al crear el enlace / registro
    nombre_completo = Column(String(200), nullable=False)
    email = Column(String(200), nullable=True)
    empresa = Column(String(50), nullable=True)  # legado: nombre libre (compatibilidad)
    empresa_id = Column(Integer, ForeignKey("empresas.id"), nullable=True)  # BD maestra

    # Ficha completa del trabajador (11 secciones, ver formulario.html) como JSON.
    ficha_data = Column(JSON, default=dict)
    familia_data = Column(JSON, default=list)
    educacion_data = Column(JSON, default=list)
    experiencia_data = Column(JSON, default=list)
    capacitaciones_data = Column(JSON, default=list)
    derechohabientes_data = Column(JSON, default=dict)  # compatibilidad

    # Administración de Personal
    foto_path = Column(String(500), nullable=True)
    estado = Column(String(20), default="activo")  # activo/cesado
    fecha_baja = Column(DateTime, nullable=True)
    motivo_baja = Column(String(300), nullable=True)

    # Previsto para cuando se agregue el contrato (ver nota arriba). Sin usar todavía.
    contrato_tipo = Column(String(60), nullable=True)
    contrato_pdf_path = Column(String(500), nullable=True)
    contrato_signed_at = Column(DateTime, nullable=True)

    status = Column(String(20), default=STATUS_PENDIENTE)  # pendiente/en_proceso/completo (legajo de selección)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    link_opened_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    completion_email_sent_at = Column(DateTime, nullable=True)

    empresa_rel = relationship("Empresa", back_populates="empleados", foreign_keys=[empresa_id])
    documents = relationship("Document", back_populates="employee", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="employee", cascade="all, delete-orphan")
    attachments = relationship("Attachment", back_populates="employee", cascade="all, delete-orphan")
    bitacora = relationship("BitacoraEntry", back_populates="employee", cascade="all, delete-orphan",
                             order_by="desc(BitacoraEntry.created_at)")
    asistencia = relationship("AsistenciaRegistro", back_populates="employee", cascade="all, delete-orphan",
                               order_by="desc(AsistenciaRegistro.timestamp)")
    onboarding = relationship("OnboardingRegistro", back_populates="employee", cascade="all, delete-orphan",
                               order_by="desc(OnboardingRegistro.created_at)")
    renovaciones_contrato = relationship("ContratoRenovacion", back_populates="employee", cascade="all, delete-orphan",
                                          order_by="desc(ContratoRenovacion.created_at)")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    doc_type = Column(String(40), nullable=False)  # uno de DOC_TYPE_KEYS
    status = Column(String(20), default=STATUS_PENDIENTE)  # pendiente/abierto/firmado

    pdf_path = Column(String(500), nullable=True)
    content_hash = Column(String(128), nullable=True)  # sha256 del contenido firmado

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    signed_at = Column(DateTime, nullable=True)

    employee = relationship("Employee", back_populates="documents")
    signature = relationship("Signature", back_populates="document", uselist=False, cascade="all, delete-orphan")


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)

    tipo = Column(String(40), nullable=False)  # uno de ATTACHMENT_TYPE_KEYS
    filename = Column(String(300), nullable=False)  # nombre original del archivo
    file_path = Column(String(500), nullable=False)
    content_type = Column(String(120), nullable=True)
    subido_por = Column(String(200), nullable=True)  # nombre de quien lo subió (RR.HH. o el propio trabajador)

    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)

    employee = relationship("Employee", back_populates="attachments")


class BitacoraEntry(Base):
    __tablename__ = "bitacora_entries"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)

    tipo = Column(String(40), nullable=False)  # uno de TIPOS_BITACORA
    texto = Column(Text, nullable=False)
    autor = Column(String(200), nullable=True)  # nombre del usuario de RR.HH. que la registró

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    employee = relationship("Employee", back_populates="bitacora")


class AsistenciaRegistro(Base):
    """Marcado de entrada/salida. Punto 6 del pedido del usuario: control de
    asistencia con hora de ingreso y salida. El propio trabajador (rol
    'usuario') puede marcar su asistencia desde su perfil; RR.HH. también
    puede registrar o corregir marcaciones a nombre de alguien (p.ej. si
    olvidó marcar o no tiene cuenta propia todavía)."""
    __tablename__ = "asistencia_registros"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)

    tipo = Column(String(20), nullable=False)  # "entrada" o "salida"
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    ip_address = Column(String(64), nullable=True)
    registrado_por = Column(String(200), nullable=True)  # nombre de quien lo marcó (el propio o RR.HH.)
    nota = Column(String(300), nullable=True)  # p.ej. motivo de una corrección manual

    employee = relationship("Employee", back_populates="asistencia")


class OnboardingRegistro(Base):
    """Seguimiento de onboarding de un trabajador ya contratado: un registro
    por cada acción de Inducción General / Acompañamiento / Evaluación /
    Feedback (se puede repetir la misma etapa varias veces, p.ej. varias
    sesiones de acompañamiento)."""
    __tablename__ = "onboarding_registros"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)

    etapa = Column(String(30), nullable=False)  # uno de ETAPA_ONBOARDING_KEYS
    estado = Column(String(20), default="pendiente")  # uno de ESTADOS_ONBOARDING (keys)
    fecha = Column(DateTime, nullable=True)
    responsable = Column(String(200), nullable=True)
    notas = Column(Text, nullable=True)
    registrado_por = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    employee = relationship("Employee", back_populates="onboarding")


class ContratoRenovacion(Base):
    """Historial de renovaciones del contrato de un trabajador. Cada vez que
    se renueva, el campo `fecha_contrato` de la ficha (Employee.ficha_data)
    se actualiza al nuevo valor, pero queda acá un registro permanente de
    cada renovación (fecha anterior -> nueva, tipo de contrato, quién la
    registró) para no perder el historial."""
    __tablename__ = "contrato_renovaciones"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)

    fecha_contrato_anterior = Column(String(20), nullable=True)  # "YYYY-MM-DD" o vacío si no había
    fecha_contrato_nueva = Column(String(20), nullable=False)
    fecha_fin_contrato_anterior = Column(String(20), nullable=True)
    fecha_fin_contrato_nueva = Column(String(20), nullable=True)
    tipo_contrato = Column(String(60), nullable=True)
    notas = Column(Text, nullable=True)
    registrado_por = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    employee = relationship("Employee", back_populates="renovaciones_contrato")


class Signature(Base):
    __tablename__ = "signatures"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), unique=True, nullable=False)

    image_path = Column(String(500), nullable=True)  # PNG de la firma dibujada
    consent_text_snapshot = Column(Text, nullable=True)  # texto legal exacto que aceptó (auditable)

    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(300), nullable=True)
    signed_at = Column(DateTime, default=datetime.datetime.utcnow)

    document = relationship("Document", back_populates="signature")


class PedidoPersonal(Base):
    """Solicitud de personal que hace un área/gerencia a RR.HH. (Registro de
    Pedidos, primer paso de Reclutamiento y Selección). No implica todavía
    ningún candidato — eso lo cubre Control de Leads más adelante."""
    __tablename__ = "pedidos_personal"

    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresas.id"), nullable=True)
    cargo_solicitado = Column(String(150), nullable=False)
    area = Column(String(150), nullable=True)
    cantidad = Column(Integer, default=1)
    motivo = Column(String(60), nullable=True)  # uno de MOTIVOS_PEDIDO
    urgencia = Column(String(20), nullable=True)  # uno de URGENCIAS_PEDIDO
    solicitante = Column(String(200), nullable=True)  # quién pide el personal (jefe/gerencia)
    fecha_solicitud = Column(DateTime, default=datetime.datetime.utcnow)
    fecha_requerida = Column(DateTime, nullable=True)
    estado = Column(String(20), default="abierto")  # uno de ESTADO_PEDIDO_KEYS
    observaciones = Column(Text, nullable=True)
    registrado_por = Column(String(200), nullable=True)  # usuario de RR.HH. que lo cargó
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    cerrado_at = Column(DateTime, nullable=True)

    empresa = relationship("Empresa")
    leads = relationship("LeadCandidato", back_populates="pedido")


class LeadCandidato(Base):
    """Candidato en el pipeline de reclutamiento (Control de Leads). Puede
    estar asociado a un pedido de personal puntual, o quedar suelto (p.ej.
    un candidato que llegó por su cuenta y todavía no tiene vacante asignada).
    Ver ETAPAS_LEAD para el flujo del pipeline; la Entrevista por Competencias
    y el paso a Selección (ficha/documentos/firma) quedan para más adelante."""
    __tablename__ = "leads_candidatos"

    id = Column(Integer, primary_key=True)
    pedido_id = Column(Integer, ForeignKey("pedidos_personal.id"), nullable=True)
    nombre_completo = Column(String(200), nullable=False)
    email = Column(String(200), nullable=True)
    celular = Column(String(30), nullable=True)
    origen = Column(String(60), nullable=True)  # uno de ORIGENES_LEAD
    etapa = Column(String(20), default="nuevo")  # uno de ETAPA_LEAD_KEYS
    notas = Column(Text, nullable=True)
    registrado_por = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    pedido = relationship("PedidoPersonal", back_populates="leads")


class EncuestaCampana(Base):
    """Una ronda de Encuesta 360 (Clima y Cultura). Las preguntas (escala 1-5)
    las define RR.HH. al crearla — no asumimos un set fijo de competencias."""
    __tablename__ = "encuesta_campanas"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(200), nullable=False)
    descripcion = Column(Text, nullable=True)
    preguntas = Column(JSON, default=list)  # lista de strings
    estado = Column(String(20), default="abierta")  # uno de ESTADOS_ENCUESTA (keys)
    fecha_inicio = Column(DateTime, default=datetime.datetime.utcnow)
    fecha_fin = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    creado_por = Column(String(200), nullable=True)

    respuestas = relationship("EncuestaRespuesta", back_populates="campana", cascade="all, delete-orphan")


class EncuestaRespuesta(Base):
    """Una respuesta de un evaluador sobre un evaluado, dentro de una campaña.
    `respuestas` es una lista de puntajes 1-5 en el mismo orden que
    `campana.preguntas`."""
    __tablename__ = "encuesta_respuestas"

    id = Column(Integer, primary_key=True)
    campana_id = Column(Integer, ForeignKey("encuesta_campanas.id"), nullable=False)
    evaluado_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    relacion = Column(String(30), nullable=True)  # uno de RELACIONES_ENCUESTA
    respuestas = Column(JSON, default=list)  # lista de int 1-5
    comentario = Column(Text, nullable=True)
    registrado_por = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    campana = relationship("EncuestaCampana", back_populates="respuestas")
    evaluado = relationship("Employee")


AMBITOS_ANUNCIO = [
    ("holding", "Todo el Holding"),
    ("unidad", "Una Unidad de Negocio"),
    ("empresa", "Una Empresa"),
]
AMBITO_ANUNCIO_KEYS = [a[0] for a in AMBITOS_ANUNCIO]


class Anuncio(Base):
    """Anuncio de Clima y Cultura (punto 4 del pedido): puede publicarse para
    todo el Holding, para una Unidad de Negocio o para una Empresa puntual —
    lo que determina a quién se le muestra en la pantalla de inicio."""
    __tablename__ = "anuncios"

    id = Column(Integer, primary_key=True)
    titulo = Column(String(200), nullable=False)
    cuerpo = Column(Text, nullable=False)
    ambito = Column(String(20), nullable=False, default="holding")  # uno de AMBITO_ANUNCIO_KEYS
    holding_id = Column(Integer, ForeignKey("holdings.id"), nullable=True)
    unidad_negocio_id = Column(Integer, ForeignKey("unidades_negocio.id"), nullable=True)
    empresa_id = Column(Integer, ForeignKey("empresas.id"), nullable=True)
    autor = Column(String(200), nullable=True)
    activo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    holding = relationship("Holding")
    unidad_negocio = relationship("UnidadNegocio")
    empresa = relationship("Empresa")


class SaludoCumpleanos(Base):
    """Saludo de cumpleaños que otro usuario deja en la tarjeta de alguien en
    la pantalla de inicio (punto 3 del pedido: "puedan otros usuarios agregar
    su propio saludo")."""
    __tablename__ = "saludos_cumpleanos"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)  # a quién se saluda
    autor = Column(String(200), nullable=False)  # nombre de quien saluda
    mensaje = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    employee = relationship("Employee")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)

    event = Column(String(50), nullable=False)  # enlace_generado/formulario_abierto/ficha_guardada/documento_firmado
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(300), nullable=True)
    meta = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    employee = relationship("Employee", back_populates="audit_logs")
