# -*- coding: utf-8 -*-
"""Autenticación y control de accesos por rol para el Sistema RR.HH. DIGETEL GROUP.

Login simple usuario/contraseña (sin dependencias externas de OAuth), con
sesión firmada en una cookie (Starlette SessionMiddleware). Las contraseñas
se guardan con PBKDF2-SHA256 (librería estándar de Python, sin necesitar
compilar bcrypt en la computadora del usuario).

Tipos de usuario (User.rol): administrador / gerente / usuario — y qué opciones
del menú tiene cada uno (nivel ver / editar) lo define app/permisos.py. Las
rutas se protegen con `require_perm("<seccion>", "ver"|"editar")`,
`require_admin` o `require_empleado(...)`; `require_role` queda para los casos
fijos por tipo de usuario.
"""
import hashlib
import hmac
import os
import secrets

from fastapi import Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from . import permisos
from .database import get_db
from .models import User, Employee

PBKDF2_ITERATIONS = 260_000


def hash_password(password: str, salt: str = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _, salt, hexdigest = password_hash.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), hexdigest)


class NotAuthenticated(Exception):
    """Se lanza cuando una ruta protegida no tiene sesión válida; el handler
    en main.py la convierte en una redirección a /login."""
    def __init__(self, next_path: str = "/"):
        self.next_path = next_path


class Forbidden(Exception):
    """El usuario está logueado pero su rol no alcanza para esta sección."""
    pass


class MustChangePassword(Exception):
    """El usuario tiene pendiente cambiar su contraseña (primer ingreso o
    contraseña reseteada por un administrador) antes de usar el resto del
    sistema; el handler en main.py lo manda a /rrhh/mi-cuenta."""
    pass


# Rutas permitidas mientras el cambio de contraseña está pendiente (para no
# generar un bucle de redirecciones).
_RUTAS_PERMITIDAS_SIN_CAMBIAR_PASSWORD = {"/rrhh/mi-cuenta", "/rrhh/mi-cuenta/password", "/logout"}


def get_current_user(request: Request, db: Session) -> User | None:
    uid = request.session.get("user_id")
    if not uid:
        return None
    user = db.query(User).get(uid)
    if not user or not user.activo:
        return None
    return user


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user(request, db)
    if not user:
        raise NotAuthenticated(next_path=request.url.path)
    if user.must_change_password and request.url.path not in _RUTAS_PERMITIDAS_SIN_CAMBIAR_PASSWORD:
        raise MustChangePassword()
    return user


def require_role(*roles: str):
    """Dependencia que exige que el usuario tenga uno de los roles indicados.
    'administrador' siempre pasa, sin importar qué roles se pidan."""
    def dependency(user: User = Depends(require_login)) -> User:
        if user.rol == "administrador" or user.rol in roles:
            return user
        raise Forbidden()
    return dependency


def require_admin(user: User = Depends(require_login)) -> User:
    """Solo el administrador (Usuarios del Sistema, eliminar personal, dar de
    baja...): ninguna casilla de permisos lo puede dar."""
    if user.rol == "administrador":
        return user
    raise Forbidden()


def require_perm(seccion: str, minimo: str = "ver"):
    """Dependencia: exige acceso `minimo` ('ver' | 'editar') a una opción del
    menú (ver app/permisos.py). El administrador siempre pasa. Esto es lo que
    protege de verdad: ocultar el menú no alcanza si alguien pega la URL."""
    if seccion not in permisos.SECCION_POR_KEY:
        raise ValueError(f"Sección de permisos desconocida: {seccion}")

    def dependency(user: User = Depends(require_login)) -> User:
        if permisos.puede(user, seccion, minimo):
            return user
        raise Forbidden()
    return dependency


def require_alguna(*secciones: str):
    """Dependencia: basta con tener 'ver' en alguna de estas opciones (p. ej.
    la pantalla índice de Parámetros)."""
    def dependency(user: User = Depends(require_login)) -> User:
        if any(permisos.puede(user, s, "ver") for s in secciones):
            return user
        raise Forbidden()
    return dependency


def alcance_empresas(user: User, db: Session):
    """None = sin restricción; set de ids de Empresa = solo esas."""
    return permisos.alcance_empresas(user, db)


def puede_empresa(user: User, db: Session, empresa_id) -> bool:
    return permisos.puede_empresa(user, db, empresa_id)


def exigir_empresa(user: User, db: Session, empresa_id) -> None:
    """Corta con 403 si la empresa está fuera del alcance del usuario (un
    gerente de Intecno no toca nada de Digetel aunque pegue la URL)."""
    if not permisos.puede_empresa(user, db, empresa_id):
        raise Forbidden()


def filtrar_por_empresa(query, columna, user: User, db: Session):
    """Aplica el alcance de empresa del usuario a una consulta SQLAlchemy."""
    alcance = permisos.alcance_empresas(user, db)
    if alcance is None:
        return query
    if not alcance:
        return query.filter(columna == -1)
    return query.filter(columna.in_(alcance))


def require_recurso(seccion: str, minimo: str, modelo, parametro: str, campo_empresa: str = "empresa_id"):
    """Como require_perm, pero además el registro que viene en la URL
    (p. ej. {linea_id}) tiene que pertenecer a una empresa del alcance del
    usuario. `campo_empresa="id"` cuando el recurso ES la empresa."""
    base = require_perm(seccion, minimo)

    def dependency(request: Request, db: Session = Depends(get_db), user: User = Depends(base)) -> User:
        valor = request.path_params.get(parametro)
        obj = db.query(modelo).get(int(valor)) if valor is not None else None
        if obj is None:
            raise HTTPException(404)
        if not permisos.puede_empresa(user, db, getattr(obj, campo_empresa)):
            raise Forbidden()
        return user
    return dependency


def acceso_ficha(user: User, db: Session, employee, minimo: str = "ver") -> bool:
    """¿Puede este usuario ver ('ver') o modificar ('editar') la ficha de
    `employee`? Cualquiera ve la PROPIA (solo lectura, nunca editar); para la
    de otra persona hace falta el permiso de Personal Y que la persona
    pertenezca a una empresa de su alcance."""
    if user.rol == "administrador":
        return True
    if user.employee_id and employee.id == user.employee_id and minimo == "ver":
        return True
    return permisos.puede(user, "personal", minimo) and permisos.puede_empresa(user, db, employee.empresa_id)


def exigir_ficha(user: User, db: Session, employee_id: int, minimo: str = "ver"):
    """Carga al trabajador y corta con 404/403. Devuelve el Employee."""
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(404)
    if not acceso_ficha(user, db, emp, minimo):
        raise Forbidden()
    return emp


def require_empleado(minimo: str = "ver"):
    """Dependencia para rutas con `{employee_id}` en el path: permiso de
    Personal + empresa dentro del alcance (+ ficha propia en solo lectura)."""
    def dependency(employee_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)) -> User:
        exigir_ficha(user, db, employee_id, minimo)
        return user
    return dependency


def puede_generar_pedidos(user: User) -> bool:
    """Registro de Pedidos de Personal: quien tenga 'editar' en esa opción."""
    return permisos.puede(user, "pedidos", "editar")


# Compatibilidad con código anterior al 07/10 -------------------------------
def es_jefe_o_gerente(user: User, db: Session = None) -> bool:
    return puede_generar_pedidos(user)


def require_jefe_o_gerente(user: User = Depends(require_perm("pedidos", "editar"))) -> User:
    return user


def can_see_planilla(user: User) -> bool:
    """Datos bancarios/previsionales/remuneración de OTRAS personas: quien
    tenga acceso a Personal (el filtro de empresa lo aplica la ruta)."""
    return permisos.puede(user, "personal", "ver")


def can_see_operativo(user: User) -> bool:
    return permisos.puede(user, "personal", "ver")


def is_staff(user: User) -> bool:
    """¿Gestiona Personal (ve fichas ajenas)? Antes: solo administrador."""
    return permisos.puede(user, "personal", "ver")
