"""
Punto de entrada para Passenger (cPanel > Software > "Setup Python App").

Passenger, en la configuración típica de cPanel, sirve aplicaciones WSGI.
FastAPI es ASGI, así que este archivo envuelve la app con un adaptador
(a2wsgi) para que Passenger pueda servirla sin tocar el código de la app.

cPanel genera un entorno virtual y espera encontrar en este archivo una
variable llamada `application` — no la renombres.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from a2wsgi import ASGIMiddleware  # noqa: E402
from app.main import app as _asgi_app  # noqa: E402

application = ASGIMiddleware(_asgi_app)
