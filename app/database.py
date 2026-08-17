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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
