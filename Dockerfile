# MICELIO — Sistema de Recursos Humanos DIGETEL GROUP
# Imagen mínima: FastAPI + SQLite + generación de PDF en Python puro
# (sin Word/LibreOffice/Node — ver app/pdf_signed.py).
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY sync_parametrizacion.py parametros_export.json ./

# Carpetas de datos en tiempo de ejecución (la app también las crea sola al
# arrancar, esto solo asegura que existan desde el primer build).
RUN mkdir -p data app/generated app/uploads app/fotos app/signatures app/firmas_empresa

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
