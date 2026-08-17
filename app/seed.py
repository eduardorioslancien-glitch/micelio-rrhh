# -*- coding: utf-8 -*-
"""Datos iniciales del Sistema RR.HH. DIGETEL GROUP: estructura organizacional
(Unidades de Negocio + Empresas) tal como la describió el usuario, y un
usuario Administrador por defecto para poder entrar la primera vez.

Se ejecuta una sola vez, al arrancar el servidor, y no hace nada si ya hay
datos (para no duplicar ni pisar lo que RR.HH. haya editado después)."""
import os

from sqlalchemy.orm import Session

from .models import UnidadNegocio, Empresa, User, Catalogo
from .auth import hash_password

# (nombre unidad, descripción, [(nombre empresa, régimen laboral)])
ESTRUCTURA_INICIAL = [
    ("UM Servicios", "Servicios de fibra óptica.", [
        ("Digetel", "Régimen General"),
        ("Intecno", "Régimen General"),
    ]),
    ("UM Infraestructura", "Venta e implementación de infraestructura para redes de fibra óptica.", [
        ("Empresa de Infraestructura (completar nombre)", "Régimen General"),
    ]),
    ("UM Digital", "Aplicaciones y desarrollo tecnológico del grupo.", [
        ("Empresa Digital (completar nombre)", "Régimen General"),
    ]),
    ("Evolution", "Consecución de técnicos y cuadrillas para UM Servicios.", [
        ("Evolution", "Régimen General"),
    ]),
    ("Enjambre", "Implementación de la operatividad, apoyo a UM Servicios.", [
        ("Enjambre", "Régimen General"),
    ]),
]

DEFAULT_ADMIN_USERNAME = os.environ.get("RRHH_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("RRHH_ADMIN_PASSWORD", "digetel2026")

# Bancos que operan en Perú (lista base; el Administrador puede agregar más
# desde Parametrización). No se sembraron Áreas/Gerencias/Cargos/Sedes porque
# esa es la estructura organizacional real de DIGETEL GROUP y le corresponde
# a RR.HH. definirla desde Parametrización, no inventarla aquí.
BANCOS_PERU = [
    "Banco de Crédito del Perú (BCP)",
    "BBVA Perú",
    "Interbank",
    "Scotiabank Perú",
    "Banco de la Nación",
    "BanBif",
    "Banco Pichincha",
    "Mibanco",
    "Banco Falabella",
    "Banco Ripley",
    "Banco GNB Perú",
    "Banco Santander Perú",
    "Citibank Perú",
]


def seed_initial_data(db: Session):
    if db.query(UnidadNegocio).count() == 0:
        for nombre_um, descripcion, empresas in ESTRUCTURA_INICIAL:
            um = UnidadNegocio(nombre=nombre_um, descripcion=descripcion)
            db.add(um)
            db.flush()  # para tener um.id disponible
            for nombre_emp, regimen in empresas:
                db.add(Empresa(nombre=nombre_emp, unidad_negocio_id=um.id, regimen_laboral=regimen))
        db.commit()

    if db.query(Catalogo).filter(Catalogo.tipo == "banco").count() == 0:
        for nombre in BANCOS_PERU:
            db.add(Catalogo(tipo="banco", nombre=nombre))
        db.commit()

    if db.query(User).count() == 0:
        db.add(User(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
            nombre_completo="Administrador del Sistema",
            rol="administrador",
            activo=True,
        ))
        db.commit()
