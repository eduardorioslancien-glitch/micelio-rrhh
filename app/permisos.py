# -*- coding: utf-8 -*-
"""Permisos de MICELIO (pedido de Eduardo, 07/10/2026).

Tres tipos de usuario (User.rol):
  administrador -> acceso total a todo MICELIO, sin restricción de empresa.
  gerente       -> por defecto, todo MICELIO pero SOLO de su(s) empresa(s):
                   las empresas de las que es Representante Legal en
                   Parámetros > Empresas (ver `empresas_de_gerente`). Las
                   secciones comunes a todo el holding (Reclutamiento y
                   Selección) no se filtran por empresa.
  usuario       -> solo VE su propia ficha (sin editarla, darse de baja ni
                   cambiar de empresa) + lo que el administrador le marque,
                   opción por opción del menú, en Usuarios del Sistema.

Cada opción del menú lateral es una "sección" con un nivel por usuario:
ninguno (0) / ver (1) / editar (2). `editar` incluye `ver`.

Reglas fijas (no se pueden dar con una casilla):
  - "Usuarios del Sistema" es SOLO del administrador (nadie se da permisos a
    sí mismo ni crea cuentas).
  - Las secciones "globales" (compartidas por todas las empresas: Holdings,
    Competencias, Áreas, Bancos...) solo se pueden dar en nivel "ver" a quien
    no es administrador: editarlas afectaría a empresas que esa persona no ve.
  - Eliminar personal o darlo de baja es solo del administrador (ver las
    rutas que usan `require_admin`).

Este módulo no importa modelos (los recibe por parámetro) para que
`models.User` pueda usarlo sin ciclos de importación.
"""
import re
import unicodedata

NINGUNO, VER, EDITAR = 0, 1, 2
NIVELES = {"ninguno": NINGUNO, "ver": VER, "editar": EDITAR}
NIVEL_LABEL = {NINGUNO: "Sin acceso", VER: "Solo ver", EDITAR: "Ver y editar"}

# alcance:
#   "empresa" -> sus datos se filtran por empresa para el gerente (y para quien
#                tenga una empresa asignada en su usuario)
#   "comun"   -> abastece a todas las empresas (Reclutamiento, Clima...), no se filtra
#   "global"  -> catálogos compartidos del holding; a un no-administrador solo se
#                le puede dar "ver"
# tope: nivel máximo que se le puede dar a alguien que NO es administrador.
#   (key, etiqueta, grupo, alcance, tope, ayuda)
SECCIONES = [
    ("dashboard", "Dashboard", "Administración", "empresa", VER,
     "Indicadores (headcount, rotación, ausentismo...)."),
    ("personal", "Personal", "Administración", "empresa", EDITAR,
     "Listado y fichas de los trabajadores, con sus documentos, bitácora y vacaciones."),
    ("asistencia", "Control de Asistencia", "Administración", "empresa", EDITAR,
     "Marcaciones del día y registro manual."),
    ("contratos", "Contratos / Renovaciones", "Administración", "empresa", EDITAR,
     "Contratos por vencer y renovaciones."),
    ("p_holdings", "Parámetros › Holdings", "Parámetros", "global", VER, ""),
    ("p_unidades", "Parámetros › Unidades de Negocio", "Parámetros", "global", VER, ""),
    ("p_empresas", "Parámetros › Empresas", "Parámetros", "empresa", VER, ""),
    ("p_procesos", "Parámetros › Procesos y Funciones", "Parámetros", "empresa", EDITAR, ""),
    ("p_lineas", "Parámetros › Líneas de Producto", "Parámetros", "empresa", EDITAR, ""),
    ("p_competencias", "Parámetros › Principios, Valores y Competencias", "Parámetros", "global", VER, ""),
    ("p_catalogos", "Parámetros › Áreas, Gerencias, Bancos y Centros de Costo", "Parámetros", "global", VER, ""),
    ("p_cargos", "Parámetros › Cargos - MOF", "Parámetros", "empresa", VER,
     "Un gerente solo ve los cargos que usa su empresa."),
    ("p_esquemas", "Parámetros › Esquemas de Pago", "Parámetros", "empresa", VER,
     "Un gerente solo ve los esquemas de los cargos que usa su empresa."),
    ("p_bases", "Parámetros › Bases", "Parámetros", "empresa", EDITAR, ""),
    ("p_sedes", "Parámetros › Sedes y Geocercas (GPS)", "Parámetros", "global", VER, ""),
    ("pedidos", "Reclutamiento › Registro de Pedidos", "Reclutamiento y Selección", "comun", EDITAR,
     "Quien no es administrador ni gerente queda fijo como solicitante de sus propios pedidos."),
    ("leads", "Reclutamiento › Gestión de Leads", "Reclutamiento y Selección", "comun", EDITAR, ""),
    ("seleccion", "Reclutamiento › Selección", "Reclutamiento y Selección", "comun", EDITAR, ""),
    ("descartados", "Reclutamiento › Historial de Descartados", "Reclutamiento y Selección", "comun", VER, ""),
    ("onboarding", "Reclutamiento › Onboarding (ver avance)", "Reclutamiento y Selección", "comun", VER, ""),
    ("remuneraciones", "Remuneraciones", "Remuneraciones", "empresa", VER,
     "Planilla, RHE, APE, vacaciones, gratificaciones, CTS y liquidaciones (próximamente)."),
    ("anuncios", "Clima y Cultura › Anuncios", "Clima y Cultura", "comun", EDITAR, ""),
    ("encuestas", "Clima y Cultura › Encuesta 360 (administrar campañas)", "Clima y Cultura", "comun", EDITAR,
     "Responder una campaña abierta lo puede hacer cualquier usuario sin esta casilla."),
    ("indicadores", "Clima y Cultura › Indicadores de Gestión", "Clima y Cultura", "comun", VER, ""),
]
SECCION_POR_KEY = {s[0]: s for s in SECCIONES}
GRUPOS = []
for _s in SECCIONES:
    if _s[2] not in GRUPOS:
        GRUPOS.append(_s[2])

# Lo que recibe un gerente mientras el administrador no le personalice los
# accesos (el administrador puede cambiarlo opción por opción).
PERMISOS_GERENTE = {
    "dashboard": VER, "personal": EDITAR, "asistencia": EDITAR, "contratos": EDITAR,
    "p_empresas": VER, "p_procesos": EDITAR, "p_lineas": EDITAR, "p_cargos": VER, "p_esquemas": VER,
    "p_bases": EDITAR, "p_competencias": VER, "p_catalogos": VER, "p_sedes": VER,
    "pedidos": EDITAR, "leads": EDITAR, "seleccion": EDITAR, "descartados": VER, "onboarding": VER,
    "indicadores": VER,
}

# Cuentas creadas antes de este modelo: se interpretan sin tocar la fila.
#   opeoka = "Gerente o Jefe" de la matriz del 08/09 -> Usuario + Registro de Pedidos
#   conta  = Contabilidad (Planillas aún no existe)   -> Usuario sin extras
PERMISOS_LEGADO = {"opeoka": {"pedidos": EDITAR}, "conta": {}}


def _a_nivel(valor) -> int:
    if isinstance(valor, int):
        return max(NINGUNO, min(EDITAR, valor))
    return NIVELES.get(str(valor or "ninguno").strip().lower(), NINGUNO)


def permisos_base(rol: str) -> dict:
    """Permisos que corresponden a un tipo de usuario mientras no se le
    haya guardado una personalización."""
    if rol == "gerente":
        return dict(PERMISOS_GERENTE)
    return dict(PERMISOS_LEGADO.get(rol, {}))


def limitar(permisos: dict, es_admin: bool = False) -> dict:
    """Normaliza a {key: nivel(0..2)} para TODAS las secciones, aplicando los
    topes (un no-administrador nunca pasa del tope de la sección, y
    "Usuarios del Sistema" no existe como casilla)."""
    resultado = {}
    for key, _lbl, _grupo, _alcance, tope, _ayuda in SECCIONES:
        nivel = EDITAR if es_admin else _a_nivel((permisos or {}).get(key, NINGUNO))
        if not es_admin:
            nivel = min(nivel, tope)
        resultado[key] = nivel
    return resultado


def permisos_efectivos(user) -> dict:
    """{seccion: 0|1|2} de un usuario (objeto con .rol y .permisos)."""
    if user is None:
        return limitar({})
    es_admin = user.rol == "administrador"
    guardados = getattr(user, "permisos", None)
    crudo = guardados if isinstance(guardados, dict) else permisos_base(user.rol)
    return limitar(crudo, es_admin=es_admin)


def nivel_de(user, key: str) -> int:
    if key == "usuarios":
        return EDITAR if (user is not None and user.rol == "administrador") else NINGUNO
    return permisos_efectivos(user).get(key, NINGUNO)


def puede(user, key: str, minimo="ver") -> bool:
    return nivel_de(user, key) >= _a_nivel(minimo)


def ve_algo_de(user, keys) -> bool:
    return any(puede(user, k) for k in keys)


# ---------------------------------------------------------------------------
# Gerente: de qué empresas es Representante Legal
# ---------------------------------------------------------------------------
def _tokens(texto: str) -> set:
    s = unicodedata.normalize("NFKD", texto or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return {t for t in re.split(r"[^a-z0-9ñ]+", s) if t}


def mismo_nombre(representante: str, nombre_completo: str) -> bool:
    """¿El Representante Legal escrito en Parámetros > Empresas es esta
    persona de Personal? Tolera mayúsculas, tildes, el orden (en Personal
    suele ser "APELLIDOS NOMBRES") y que Parámetros tenga solo un nombre y
    un apellido ("Guillermo Araya" == "ARAYA UGARTE GUILLERMO"): basta con que
    todas las palabras del representante estén en el nombre de la ficha."""
    rep, nom = _tokens(representante), _tokens(nombre_completo)
    if not rep or not nom:
        return False
    if len(rep) == 1:
        return rep == nom
    return rep <= nom


def empresas_de_gerente(user, db) -> list:
    """Empresas activas donde la persona vinculada al usuario figura como
    Representante Legal (se calcula en vivo: si cambia el representante en
    Parámetros, cambia el alcance del gerente de inmediato)."""
    from .models import Empresa
    emp = getattr(user, "employee", None)
    if emp is None:
        return []
    return [e for e in db.query(Empresa).filter(Empresa.activo == True).all()  # noqa: E712
            if e.representante_legal and mismo_nombre(e.representante_legal, emp.nombre_completo)]


def alcance_empresas(user, db):
    """None = sin restricción de empresa; set de ids = solo esas empresas
    (vacío = ninguna: p. ej. un gerente que dejó de ser Representante Legal)."""
    if user is None or user.rol == "administrador":
        return None
    if user.rol == "gerente":
        return {e.id for e in empresas_de_gerente(user, db)}
    if getattr(user, "empresa_id", None):
        return {user.empresa_id}
    return None


def puede_empresa(user, db, empresa_id) -> bool:
    alcance = alcance_empresas(user, db)
    if alcance is None:
        return True
    return empresa_id is not None and empresa_id in alcance


def cargos_visibles_ids(db, alcance):
    """Cargos (MOF) que "usa" el alcance de empresas: los que figuran en la
    ficha de su personal o están asignados en Procesos y Funciones de esas
    empresas. None = sin restricción (todos)."""
    if alcance is None:
        return None
    from .models import Cargo, Employee, ProcesoNodo
    ids_emp = list(alcance) or [-1]
    nombres = set()
    for (ficha,) in db.query(Employee.ficha_data).filter(Employee.empresa_id.in_(ids_emp)).all():
        c = ((ficha or {}).get("cargo") or "").strip()
        if c:
            nombres.add(c)
    ids = {c.id for c in db.query(Cargo).filter(Cargo.nombre.in_(nombres or [""])).all()}
    ids |= {cid for (cid,) in db.query(ProcesoNodo.cargo_id).filter(
        ProcesoNodo.empresa_id.in_(ids_emp), ProcesoNodo.cargo_id.isnot(None)).all()}
    return ids


def diagnostico_gerente(db, employee) -> dict:
    """Para la pantalla de Usuarios: ¿esta persona puede ser Gerente? Debe ser
    Representante Legal de al menos una empresa; además se avisa si en su
    ficha no figura como Gerente General o si ese cargo no reporta al
    Director General (no bloquea: es un aviso para que RR.HH. lo corrija)."""
    from .models import Empresa, Cargo
    out = {"empresas": [], "cargo": None, "es_gerente_general": False, "reporta_a_director": False}
    if employee is None:
        return out
    out["empresas"] = [e for e in db.query(Empresa).filter(Empresa.activo == True).all()  # noqa: E712
                       if e.representante_legal and mismo_nombre(e.representante_legal, employee.nombre_completo)]
    cargo_nombre = ((employee.ficha_data or {}).get("cargo") or "").strip()
    out["cargo"] = cargo_nombre or None
    out["es_gerente_general"] = cargo_nombre.upper().startswith("GERENTE GENERAL")
    cargo = db.query(Cargo).filter(Cargo.nombre == cargo_nombre).first() if cargo_nombre else None
    out["reporta_a_director"] = bool(cargo and cargo.reporta_a and cargo.reporta_a.nombre.upper().startswith("DIRECTOR GENERAL"))
    return out
