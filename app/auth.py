# -*- coding: utf-8 -*-
"""Autenticación y control de accesos por rol para el Sistema RR.HH. DIGETEL GROUP.

Login simple usuario/contraseña (sin dependencias externas de OAuth), con
sesión firmada en una cookie (Starlette SessionMiddleware). Las contraseñas
se guardan con PBKDF2-SHA256 (librería estándar de Python, sin necesitar
compilar bcrypt en la computadora del usuario).

Niveles de acceso (Employee.rol / User.rol):
  administrador  -> acceso total (bypassa cualquier require_role).
  conta          -> planillas (datos bancarios/previsionales/remuneración).
  opeoka         -> parte operativa (datos laborales, sin ver banco/sueldo).
  usuario        -> solo su propia información (autoservicio).
"""
import hashlib
import hmac
import os
import secrets

from fastapi import Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

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


def es_jefe_o_gerente(user: User, db: Session) -> bool:
    """Registro de Pedidos de Personal: además de RR.HH. (administrador/
    conta/opeoka en general para gestionar el pipeline), puede GENERAR un
    pedido nuevo un usuario con rol Operaciones cuyo Cargo (en su ficha)
    contenga "Jefe" o "Gerente" — así los jefes/gerentes de área piden
    personal ellos mismos, sin depender de que RR.HH. lo cargue por ellos."""
    if user.rol == "administrador":
        return True
    if user.rol != "opeoka" or not user.employee_id:
        return False
    emp = db.query(Employee).get(user.employee_id)
    cargo = ((emp.ficha_data or {}).get("cargo") or "") if emp else ""
    cargo = cargo.lower()
    return "jefe" in cargo or "gerente" in cargo


def require_jefe_o_gerente(request: Request, db: Session = Depends(get_db)) -> User:
    """Dependencia para el POST que crea un Pedido de Personal: administrador
    o un Jefe/Gerente (ver es_jefe_o_gerente)."""
    user = require_login(request, db)
    if es_jefe_o_gerente(user, db):
        return user
    raise Forbidden()


def can_see_planilla(user: User) -> bool:
    """Secciones bancarias/previsionales/remuneración: administrador y conta."""
    return user.rol in ("administrador", "conta")


def can_see_operativo(user: User) -> bool:
    """Secciones operativas: administrador, conta y opeoka (todo el staff de RR.HH.)."""
    return user.rol in ("administrador", "conta", "opeoka")


def is_staff(user: User) -> bool:
    """Cualquier rol de RR.HH. (no 'usuario')."""
    return user.rol in ("administrador", "conta", "opeoka")
