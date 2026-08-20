# -*- coding: utf-8 -*-
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Permite sobreescribir la ubicación de la base de datos con la variable de entorno
# HRAPP_DB_PATH. Esto es necesario en algunos entornos con sistemas de archivos de
# red/FUSE (como este sandbox) donde SQLite no puede tomar locks sobre el archivo;
# en un despliegue normal (disco local en el servidor) no hace falta configurarla.
DB_PATH = os.environ.get("HRAPP_DB_PATH") or os.path.join(BASE_DIR, "data", "hrapp.db")

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    from .models import Base
    Base.metadata.create_all(engine)
    _run_migraciones_livianas()


# Columnas agregadas a tablas que ya existían en instalaciones previas.
# Base.metadata.create_all() solo CREA tablas nuevas — no altera una tabla
# que ya existe, así que una columna nueva en un modelo no aparece sola en
# una base de datos que ya tenía esa tabla. Se agregan acá con ALTER TABLE
# (una sola vez, no rompe si ya existe) para no forzar a nadie a borrar su
# base de datos real cada vez que el modelo gana un campo.
_MIGRACIONES_COLUMNAS = [
    ("competencias", "conductas_no_deseadas", "TEXT"),
    ("unidades_negocio", "holding_id", "INTEGER"),
    ("empresas", "logo_path", "VARCHAR(500)"),
    ("empresas", "gerente_nombre", "VARCHAR(200)"),
    ("empresas", "gerente_email", "VARCHAR(200)"),
    ("empresas", "jefe_rrhh_nombre", "VARCHAR(200)"),
    ("empresas", "jefe_rrhh_email", "VARCHAR(200)"),
    ("catalogos", "logo_path", "VARCHAR(500)"),
    ("contrato_renovaciones", "fecha_fin_contrato_anterior", "VARCHAR(20)"),
    ("contrato_renovaciones", "fecha_fin_contrato_nueva", "VARCHAR(20)"),
]


def _run_migraciones_livianas():
    with engine.connect() as conn:
        for tabla, columna, tipo_sql in _MIGRACIONES_COLUMNAS:
            existentes = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({tabla})")}
            if columna not in existentes:
                conn.exec_driver_sql(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo_sql}")
                conn.commit()
        _backfill_holding_por_defecto(conn)


def _backfill_holding_por_defecto(conn):
    """Estructura organizacional nueva (Holding arriba de Unidad de Negocio):
    las instalaciones que ya tenían Unidades de Negocio quedarían sin
    Holding — se les crea uno por defecto ("DIGETEL GROUP") y se las asigna
    ahí, para no dejar huérfana la estructura existente."""
    huerfanas = conn.exec_driver_sql(
        "SELECT COUNT(*) FROM unidades_negocio WHERE holding_id IS NULL"
    ).scalar()
    if not huerfanas:
        return
    holding_id = conn.exec_driver_sql(
        "SELECT id FROM holdings WHERE nombre = 'DIGETEL GROUP'"
    ).scalar()
    if not holding_id:
        conn.exec_driver_sql(
            "INSERT INTO holdings (nombre, descripcion, activo, created_at) "
            "VALUES ('DIGETEL GROUP', 'Holding por defecto (creado automáticamente).', 1, CURRENT_TIMESTAMP)"
        )
        conn.commit()
        holding_id = conn.exec_driver_sql(
            "SELECT id FROM holdings WHERE nombre = 'DIGETEL GROUP'"
        ).scalar()
    conn.exec_driver_sql(
        f"UPDATE unidades_negocio SET holding_id = {holding_id} WHERE holding_id IS NULL"
    )
    conn.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
