# -*- coding: utf-8 -*-
"""Simula el flujo completo del portal (lo que haría el navegador del
trabajador) golpeando los mismos endpoints que usa el formulario JS,
incluyendo la ficha ampliada de 11 secciones, la carga de documentos,
el exportador selectivo para SUNAFIL y la exportación masiva a Excel."""
import base64
import io
import sys

import requests
from PIL import Image, ImageDraw

BASE = "http://127.0.0.1:8000"


def fake_signature_dataurl():
    img = Image.new("RGBA", (400, 150), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    d.line([(20, 100), (80, 40), (140, 110), (200, 30), (260, 100), (320, 50), (380, 90)],
           fill=(18, 70, 171, 255), width=6)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def fake_pdf_bytes():
    # PDF mínimo válido (para simular el CV adjunto).
    return (b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
            b"xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF")


def check(label, cond, extra=""):
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {label} {extra}")
    if not cond:
        sys.exit(1)


def login_admin(s: requests.Session):
    """Inicia sesión como admin y resuelve el cambio de contraseña obligatorio
    del primer ingreso (o de una recién creada), usando una clave de prueba
    fija para que el resto del script pueda seguir logueado."""
    r = s.post(f"{BASE}/login", data={"username": "admin", "password": "digetel2026", "next": "/rrhh"},
               allow_redirects=False)
    if r.status_code not in (302, 303):
        check("login admin", False, f"status={r.status_code}")
    # Tras el primer login, cualquier ruta protegida redirige a esta URL
    # exacta si la contraseña está pendiente de cambio (ver MustChangePassword
    # en app/auth.py); la pedimos directo para no depender de ese redirect.
    r = s.get(f"{BASE}/rrhh/mi-cuenta?forzado=1", allow_redirects=True)
    if "contraseña nueva antes de continuar" in r.text:
        r = s.post(f"{BASE}/rrhh/mi-cuenta/password",
                    data={"actual": "digetel2026", "nueva": "PruebaE2E-Segura2026"})
        check("cambio de contraseña obligatorio del admin de prueba", "actualizada correctamente" in r.text)
    check("sesión admin activa", True)


def main():
    s = requests.Session()
    login_admin(s)

    r = s.get(f"{BASE}/admin")
    check("GET /admin", r.status_code == 200, f"status={r.status_code}")

    r = s.post(f"{BASE}/admin/nuevo", data={
        "nombre_completo": "Prueba E2E Gómez Ramírez",
        "email": "prueba.e2e@digetel.pe",
        "empresa": "Digetel",
    }, allow_redirects=False)
    check("POST /admin/nuevo", r.status_code in (302, 303), f"status={r.status_code}")
    location = r.headers.get("location", "")
    token = location.split("nuevo=")[-1]
    check("token generado", len(token) == 32, f"token={token}")

    r = s.get(f"{BASE}/admin")
    check("GET /admin (listado)", "Prueba E2E" in r.text)

    r = s.get(f"{BASE}/f/{token}")
    check("GET /f/{token} (formulario)", r.status_code == 200 and "Legajo Digital" in r.text)

    ficha_payload = {
        "nombre_completo": "Prueba E2E Gómez Ramírez",
        "ficha": {
            "codigo_trabajador": "E-0099", "apellido_paterno": "Gómez", "apellido_materno": "Ramírez",
            "nombres": "Prueba E2E", "tipo_documento": "DNI", "numero_documento": "45678912",
            "ruc": "", "nacionalidad": "Peruana", "sexo": "Masculino", "estado_civil": "Soltero(a)",
            "fecha_nacimiento": "1994-03-12", "lugar_nacimiento": "Lima", "edad": "32",
            "direccion": "Av. Los Álamos 245", "urbanizacion": "Los Álamos", "distrito": "San Borja",
            "provincia": "Lima", "departamento": "Lima", "referencia": "Cerca al parque",
            "telefono_fijo": "014567890", "celular": "987654321", "correo_personal": "prueba@example.com",
            "correo_corporativo": "prueba.gomez@digetel.pe",
            "licencia_numero": "Q12345678", "licencia_tipo": "A-I", "licencia_vencimiento": "2027-05-01",
            "emerg1_nombre": "María Ramírez", "emerg1_parentesco": "Madre", "emerg1_celular": "988112233",
            "emerg1_direccion": "Av. Los Álamos 245",
            "emerg2_nombre": "Juan Gómez", "emerg2_parentesco": "Padre", "emerg2_celular": "988445566",
            "emerg2_direccion": "Av. Los Álamos 245",
            "lab_codigo": "P-045", "area": "Recursos Humanos", "gerencia": "Gerencia de Administración",
            "cargo": "Analista de RRHH", "puesto": "Analista Senior", "sede": "Sede Central",
            "centro_costos": "CC-100", "jefe_inmediato": "Laura Torres", "fecha_ingreso": "2023-06-01",
            "fecha_contrato": "2023-05-25", "tipo_contrato": "Plazo Indeterminado", "modalidad": "Híbrido",
            "horario": "L-V 9:00-18:00", "jornada": "Completa", "turno": "Día", "categoria": "Empleado",
            "grupo_ocupacional": "Empleado", "remuneracion": "3500", "bonificaciones": "200",
            "asignacion_familiar": "No",
            "banco_haberes": "BCP", "cuenta_haberes": "1941234567", "cci_haberes": "00219411234567801245",
            "banco_cts": "Scotiabank", "cuenta_cts": "003123987654", "cci_cts": "00932100312398765401",
            "sistema_pension": "AFP", "afp": "Integra", "cuspp": "1234567890", "comision": "Mixta",
            "seguro": "Seguro de Invalidez", "fecha_afiliacion": "2015-01-01",
            "talla_camisa": "M", "talla_polo": "M", "talla_pantalon": "32", "talla_zapato": "41",
            "talla_chaleco": "M", "talla_casco": "M", "talla_guantes": "M",
            "grupo_sanguineo": "O+", "eps": "Pacífico EPS", "essalud": "Sí", "alergias": "Ninguna",
            "restricciones": "Ninguna", "medicamentos": "Ninguno",
            "examen_medico_fecha": "2024-01-15", "examen_medico_vencimiento": "2025-01-15",
            "vacunas": "COVID-19 completo",
        },
        "familia": [
            {"parentesco": "Madre", "nombre": "María Ramírez", "dni": "10234567", "fecha_nacimiento": "1965-02-20",
             "depende_economicamente": False, "derechohabiente_essalud": False},
            {"parentesco": "Hijo(a)", "nombre": "Ana Gómez", "dni": "Partida 12345", "fecha_nacimiento": "2020-01-10",
             "depende_economicamente": True, "derechohabiente_essalud": True},
        ],
        "educacion": [
            {"institucion": "UNMSM", "carrera": "Administración", "nivel": "Universitaria", "grado": "Bachiller",
             "anio": "2018", "estado": "Concluido"},
        ],
        "experiencia": [
            {"empresa": "Empresa Anterior SAC", "cargo": "Asistente de RRHH", "periodo": "03/2020 - 05/2023",
             "funciones": "Reclutamiento y selección, planillas"},
        ],
        "capacitaciones": [
            {"curso": "Legislación Laboral Peruana", "institucion": "ESAN", "horas": "20", "anio": "2023"},
        ],
    }
    r = s.post(f"{BASE}/f/{token}/ficha", json=ficha_payload)
    check("POST /f/{token}/ficha", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")

    # --- Carga de documentos (CV, CUL, antecedentes, otros) ---
    r = s.post(f"{BASE}/f/{token}/documentos", data={"tipo": "cv"},
               files={"archivo": ("cv_prueba.pdf", fake_pdf_bytes(), "application/pdf")})
    check("POST /f/{token}/documentos (CV)", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")

    r = s.post(f"{BASE}/f/{token}/documentos", data={"tipo": "cul"},
               files={"archivo": ("cul_prueba.pdf", fake_pdf_bytes(), "application/pdf")})
    check("POST /f/{token}/documentos (CUL)", r.status_code == 200, f"status={r.status_code}")

    for doc_type in ["ficha", "declaracion_jurada", "autorizacion_datos", "derechohabientes", "autorizacion_deposito"]:
        r = s.post(f"{BASE}/f/{token}/firmar/{doc_type}", json={"signature_image": fake_signature_dataurl()})
        check(f"POST /f/{token}/firmar/{doc_type}", r.status_code == 200, f"status={r.status_code} body={r.text[:300]}")

    r = s.get(f"{BASE}/f/{token}/estado")
    data = r.json()
    all_signed = all(v == "firmado" for v in data["documentos"].values())
    check("todos los documentos firmados", all_signed, str(data["documentos"]))
    check("overall == completo", data["overall"] == "completo", data["overall"])

    r = s.get(f"{BASE}/admin")
    check("panel admin muestra 'completo'", 'class="badge completo"' in r.text or "badge completo" in r.text)

    # localizar employee_id probando ids pequeños (prototipo de un solo usuario)
    employee_id = None
    for eid in range(1, 30):
        rr = s.get(f"{BASE}/admin/empleado/{eid}")
        if rr.status_code == 200 and "Prueba E2E" in rr.text:
            employee_id = eid
            break
    check("localizar admin/empleado/{id}", employee_id is not None, f"employee_id={employee_id}")

    r = s.get(f"{BASE}/admin/empleado/{employee_id}")
    check("detalle admin incluye Familia", "Información familiar" in r.text)
    check("detalle admin incluye Educación", "Educación" in r.text)
    check("detalle admin incluye adjuntos", "cv_prueba.pdf" in r.text)

    # --- Exportador selectivo SUNAFIL ---
    r = s.get(f"{BASE}/admin/empleado/{employee_id}/exportar-sunafil",
              params=[("docs", "ficha"), ("docs", "declaracion_jurada"), ("adjuntos", "true")])
    check("GET exportar-sunafil", r.status_code == 200 and r.headers.get("content-type", "").startswith("application/pdf"),
          f"status={r.status_code} size={len(r.content)}")

    # --- Exportación masiva a Excel ---
    r = s.get(f"{BASE}/admin/export.xlsx")
    check("GET /admin/export.xlsx", r.status_code == 200 and len(r.content) > 1000, f"size={len(r.content)}")

    # descarga de un PDF firmado
    downloaded = False
    for doc_id in range(1, 30):
        rr = s.get(f"{BASE}/descargas/{doc_id}")
        if rr.status_code == 200 and rr.headers.get("content-type", "").startswith("application/pdf"):
            downloaded = True
            check(f"GET /descargas/{doc_id} (PDF)", True, f"size={len(rr.content)}")
            break
    check("al menos un PDF descargable", downloaded)

    print("\n=== TODOS LOS CHEQUEOS PASARON ===")


if __name__ == "__main__":
    main()
