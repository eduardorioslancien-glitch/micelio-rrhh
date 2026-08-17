# -*- coding: utf-8 -*-
"""Exportador selectivo del legajo para una revisión de SUNAFIL (punto 4 del
pedido del usuario): RR.HH. elige qué documentos del legajo de un trabajador
quiere presentar y el sistema arma un único PDF consolidado, con una carátula
que deja constancia de qué se incluyó, para quién y cuándo se generó."""
import datetime
import os
import tempfile

from pypdf import PdfWriter, PdfReader
from PIL import Image, ImageDraw, ImageFont

from .models import DOC_TYPES

DOC_LABELS = dict(DOC_TYPES)

NAVY = (18, 70, 171)
GRAY = (89, 89, 89)


def _cover_page_pdf(emp, incluidos_labels: list[str]) -> str:
    """Genera una carátula simple (A4, 96dpi aprox vía Pillow) como PDF."""
    W, H = 1240, 1754  # ~A4 a 150dpi
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    try:
        font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
        font_h = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
        font_b = ImageFont.truetype("DejaVuSans.ttf", 20)
    except Exception:
        font_title = font_h = font_b = ImageFont.load_default()

    draw.rectangle([0, 0, W, 12], fill=NAVY)
    y = 90
    draw.text((80, y), "LEGAJO DE PERSONAL — PRESENTACIÓN SELECTIVA", font=font_title, fill=NAVY)
    y += 70
    draw.text((80, y), "DIGETEL GROUP", font=font_h, fill=GRAY)
    y += 60
    draw.line([80, y, W - 80, y], fill=(200, 200, 200), width=2)
    y += 40

    campos = [
        ("Trabajador", emp.nombre_completo or "—"),
        ("Empresa", emp.empresa or "—"),
        ("Correo", emp.email or "—"),
        ("Generado el", datetime.datetime.now().strftime("%d/%m/%Y %H:%M")),
    ]
    for label, val in campos:
        draw.text((80, y), f"{label}:", font=font_h, fill=NAVY)
        draw.text((320, y), str(val), font=font_b, fill=(30, 30, 30))
        y += 42

    y += 30
    draw.text((80, y), "Documentos incluidos en esta presentación:", font=font_h, fill=NAVY)
    y += 44
    for lbl in incluidos_labels:
        draw.text((100, y), f"•  {lbl}", font=font_b, fill=(30, 30, 30))
        y += 36

    y += 40
    draw.line([80, y, W - 80, y], fill=(200, 200, 200), width=2)
    y += 30
    nota = ("Documento generado automáticamente por el Portal RR.HH. DIGETEL GROUP a partir del "
            "legajo digital del trabajador. Cada documento incluido conserva su propia firma "
            "electrónica y pie de auditoría (fecha/hora, IP y huella digital del contenido).")
    # wrap simple
    import textwrap
    for line in textwrap.wrap(nota, width=95):
        draw.text((80, y), line, font=font_b, fill=GRAY)
        y += 28

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    img.save(tmp.name, "PDF")
    return tmp.name


def _image_to_pdf(path: str) -> str:
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    img.save(tmp.name, "PDF")
    return tmp.name


def build_sunafil_pdf(emp, docs_seleccionados: list, incluir_adjuntos: bool, generated_dir: str):
    """Arma el PDF consolidado. Retorna la ruta del archivo o None si no había
    nada disponible para incluir con la selección pedida."""
    docs_by_type = {d.doc_type: d for d in emp.documents}
    incluidos_labels = []
    piezas = []  # rutas de PDF a fusionar, en orden

    for key in docs_seleccionados:
        d = docs_by_type.get(key)
        if d and d.status == "firmado" and d.pdf_path and os.path.exists(d.pdf_path):
            piezas.append(d.pdf_path)
            incluidos_labels.append(DOC_LABELS.get(key, key))

    temp_files = []
    if incluir_adjuntos and emp.attachments:
        incluidos_labels.append("Documentos adjuntos (CV, CUL, antecedentes, otros)")
        for att in emp.attachments:
            if not os.path.exists(att.file_path):
                continue
            ext = os.path.splitext(att.file_path)[1].lower()
            if ext == ".pdf":
                piezas.append(att.file_path)
            elif ext in (".jpg", ".jpeg", ".png"):
                conv = _image_to_pdf(att.file_path)
                piezas.append(conv)
                temp_files.append(conv)

    if not piezas:
        return None

    cover = _cover_page_pdf(emp, incluidos_labels)
    temp_files.append(cover)

    writer = PdfWriter()
    writer.append(cover)
    for p in piezas:
        writer.append(p)

    os.makedirs(generated_dir, exist_ok=True)
    out_path = os.path.join(generated_dir, f"sunafil_{emp.token}_{int(datetime.datetime.now().timestamp())}.pdf")
    with open(out_path, "wb") as f:
        writer.write(f)

    for t in temp_files:
        try:
            os.remove(t)
        except OSError:
            pass

    return out_path
