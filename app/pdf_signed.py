# -*- coding: utf-8 -*-
"""
Genera el PDF final de cada documento firmado del legajo directamente en
Python (reportlab), con el mismo diseño corporativo que antes armaban
render/render_signed.js + render/components_signed.js (docx) convertido a
PDF con Word/LibreOffice.

Se reemplazó ese pipeline (Node.js -> .docx -> conversión con Word o
LibreOffice) por este generador porque el hosting de destino (cPanel
compartido) no tiene Word ni LibreOffice instalables, y no siempre tiene
Node.js disponible como proceso de larga duración. reportlab + Pillow se
instalan solo con pip, sin depender de ningún programa externo, así que esto
corre igual en tu computadora, en el hosting, o en cualquier lado con Python.
"""
import json
import os
import re
from xml.sax.saxutils import escape as _xml_escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, "static", "dg_logo.png")
LEGAL_TEXTS_PATH = os.path.join(BASE_DIR, "legal_texts.json")

with open(LEGAL_TEXTS_PATH, encoding="utf-8") as _f:
    LEGAL_TEXTS = json.load(_f)

# --- Paleta (igual a render/components_signed.js) --------------------------
NAVY_HEX = "#1246AB"
NAVY_DARK_HEX = "#0E3585"
GREEN_DARK_HEX = "#1E7B34"

NAVY = colors.HexColor(NAVY_HEX)
NAVY_DARK = colors.HexColor(NAVY_DARK_HEX)
LIGHT2 = colors.HexColor("#EEF2FA")
GRAY = colors.HexColor("#595959")
GREEN_DARK = colors.HexColor(GREEN_DARK_HEX)
LINE_GRAY = colors.HexColor("#B7C3D9")
INK = colors.HexColor("#1F1F1F")

PAGE_SIZE = A4
MARGIN = 18 * mm
FOOTER_RESERVE = 12 * mm
CONTENT_W = PAGE_SIZE[0] - 2 * MARGIN
FOOTER_TEXT = "DIGETEL GROUP · Documento firmado electrónicamente – Portal RR.HH."

_style_title = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=13.5,
                               textColor=NAVY_DARK, leading=16)
_style_subtitle = ParagraphStyle("subtitle", fontName="Helvetica-Oblique", fontSize=8.5,
                                  textColor=GRAY, leading=11, spaceBefore=2)
_style_body = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14,
                              alignment=TA_JUSTIFY, textColor=INK, spaceAfter=8)
_style_italic = ParagraphStyle("italic", parent=_style_body, fontName="Helvetica-Oblique", textColor=GRAY)
_style_numbered = ParagraphStyle("numbered", parent=_style_body, leftIndent=14, firstLineIndent=-14)
_style_caption = ParagraphStyle("caption", fontName="Helvetica-Bold", fontSize=9,
                                 textColor=colors.white, leading=11)
_style_label = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=7,
                               textColor=GRAY, leading=9)
_style_value = ParagraphStyle("value", fontName="Helvetica", fontSize=9.5,
                               textColor=INK, leading=12)
_style_sig_name = ParagraphStyle("sig_name", fontName="Helvetica-Bold", fontSize=9,
                                  textColor=INK, leading=12)
_style_sig_meta = ParagraphStyle("sig_meta", fontName="Helvetica", fontSize=8,
                                  textColor=GRAY, leading=11)
_style_sig_ok = ParagraphStyle("sig_ok", fontName="Helvetica-Bold", fontSize=9,
                                textColor=GREEN_DARK, leading=12)


def _esc(v) -> str:
    """Escapa texto de datos del usuario antes de meterlo en el mini-XML de
    reportlab (Paragraph interpreta <, >, & como marcado; sin esto un nombre
    o dirección con "&" o "<" rompería la generación del PDF)."""
    if v is None:
        return ""
    return _xml_escape(str(v)).replace("\n", "<br/>")


def _fill(template: str, fields: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(fields.get(m.group(1), "") or ""), template or "")


def _value_or_dash(v) -> str:
    s = str(v).strip() if v not in (None, "") else ""
    return _esc(s) if s else "—"


# ---------------------------------------------------------------------------
# Piezas reutilizables (equivalentes a render/components_signed.js)
# ---------------------------------------------------------------------------
def _hrule(width_pt, color=LINE_GRAY, thickness=0.8):
    t = Table([[""]], colWidths=[width_pt], rowHeights=[1])
    t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), thickness, color)]))
    return t


def _header_flowables(title: str, subtitle: str):
    logo = Paragraph("", _style_body)
    if os.path.exists(LOGO_PATH):
        try:
            with PILImage.open(LOGO_PATH) as im:
                im.load()  # fuerza la decodificación completa (open() es perezoso)
                iw, ih = im.size
            logo_w = 28 * mm
            logo_h = logo_w * ih / iw
            logo = Image(LOGO_PATH, width=logo_w, height=logo_h)
        except Exception:
            pass
    title_block = [
        Paragraph(_esc(title).upper(), _style_title),
        Paragraph(_esc(subtitle), _style_subtitle),
    ]
    t = Table([[logo, title_block]], colWidths=[32 * mm, CONTENT_W - 32 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [t, Spacer(1, 4 * mm), _hrule(CONTENT_W, NAVY, 1.6), Spacer(1, 5 * mm)]


def _body_text(text, italic=False):
    return Paragraph(_esc(text), _style_italic if italic else _style_body)


def _numbered_item(n, text):
    return Paragraph(
        f'<b><font color="{NAVY_DARK_HEX}">{n}. </font></b>{_esc(text)}', _style_numbered,
    )


def _section_caption(text):
    t = Table([[Paragraph(("  " + text).upper(), _style_caption)]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return [Spacer(1, 4 * mm), t, Spacer(1, 2 * mm)]


def _field_table(rows2col):
    lw = 0.15 * CONTENT_W
    vw = (CONTENT_W - 2 * lw) / 2
    data = []
    for l1, v1, l2, v2 in rows2col:
        data.append([
            Paragraph(_esc(l1).upper(), _style_label) if l1 else "",
            Paragraph(_value_or_dash(v1), _style_value),
            Paragraph(_esc(l2).upper(), _style_label) if l2 else "",
            Paragraph(_value_or_dash(v2), _style_value),
        ])
    t = Table(data, colWidths=[lw, vw, lw, vw])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT2),
        ("BACKGROUND", (2, 0), (2, -1), LIGHT2),
        ("LINEBELOW", (1, 0), (1, -1), 0.6, LINE_GRAY),
        ("LINEBELOW", (3, 0), (3, -1), 0.6, LINE_GRAY),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _grid_table(headers, ratios, rows):
    total = sum(ratios) or 1
    widths = [CONTENT_W * r / total for r in ratios]
    data = [[Paragraph(_esc(h).upper(), _style_label) for h in headers]]
    for row in rows:
        data.append([Paragraph(_value_or_dash(v), _style_value) for v in row])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT2),
        ("LINEBELOW", (0, 1), (-1, -1), 0.6, LINE_GRAY),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _signed_block(nombre, dni, fecha_hora, ip, hash_, signature_image_path):
    half = CONTENT_W / 2
    if signature_image_path and os.path.exists(signature_image_path):
        try:
            with PILImage.open(signature_image_path) as im:
                im.load()  # fuerza la decodificación completa (open() es perezoso)
                iw, ih = im.size
            sig_w = 42 * mm
            sig_h = min(sig_w * ih / iw, 20 * mm)
            sig = Image(signature_image_path, width=sig_w, height=sig_h)
        except Exception:
            sig = Paragraph("[firma no disponible]", _style_italic)
    else:
        sig = Paragraph("[firma no disponible]", _style_italic)

    left = [
        sig, Spacer(1, 2 * mm), _hrule(half - 6 * mm, colors.HexColor("#808080")),
        Spacer(1, 1.5 * mm),
        Paragraph(_esc(nombre), _style_sig_name),
        Paragraph("DNI/CE: " + _esc(dni), _style_sig_meta),
    ]
    right = [
        Paragraph("✔ FIRMADO ELECTRÓNICAMENTE", _style_sig_ok),
        Spacer(1, 1.5 * mm),
        Paragraph("Fecha y hora: " + _esc(fecha_hora), _style_sig_meta),
        Paragraph("Dirección IP: " + _esc(ip), _style_sig_meta),
        Paragraph("Huella digital (hash): " + _esc(hash_), _style_sig_meta),
    ]
    t = Table([[left, right]], colWidths=[half, half])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (1, 0), (1, 0), 6 * mm),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6 * mm),
    ]))
    return t


def _company_signature_block(empresa_nombre, representante_legal, firma_path):
    """Segunda firma, del representante legal de la empresa (Parametrización >
    Empresas). Solo se llama cuando la empresa tiene la firma cargada."""
    half = CONTENT_W / 2
    if firma_path and os.path.exists(firma_path):
        try:
            with PILImage.open(firma_path) as im:
                im.load()
                iw, ih = im.size
            sig_w = 42 * mm
            sig_h = min(sig_w * ih / iw, 20 * mm)
            sig = Image(firma_path, width=sig_w, height=sig_h)
        except Exception:
            sig = Paragraph("[firma no disponible]", _style_italic)
    else:
        sig = Paragraph("[firma no disponible]", _style_italic)

    left = [
        sig, Spacer(1, 2 * mm), _hrule(half - 6 * mm, colors.HexColor("#808080")),
        Spacer(1, 1.5 * mm),
        Paragraph(_esc(representante_legal or ""), _style_sig_name),
        Paragraph("Por " + _esc(empresa_nombre or ""), _style_sig_meta),
    ]
    right = [Paragraph("REPRESENTANTE LEGAL", _style_sig_ok)]
    t = Table([[left, right]], colWidths=[half, half])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (1, 0), (1, 0), 6 * mm),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6 * mm),
    ]))
    return t


# ---------------------------------------------------------------------------
# Estructura de cada documento (equivalente a render/render_signed.js)
# ---------------------------------------------------------------------------
def _legal_body(doc_type, fields):
    spec = LEGAL_TEXTS[doc_type]
    story = [_body_text(_fill(spec["intro"], fields))]
    for i, item in enumerate(spec.get("items") or [], start=1):
        story.append(_numbered_item(i, _fill(item, fields)))
    if spec.get("nota_legal"):
        story.append(_body_text(spec["nota_legal"], italic=True))
    return story, spec.get("cierre"), spec["titulo"], spec["subtitulo"]


def _doc_ficha(fields):
    spec = LEGAL_TEXTS["ficha"]
    story = [_body_text(_fill(spec["intro"], fields))]
    g = fields.get
    tel = lambda base: " ".join(x for x in [g(base + "_codigo"), g(base)] if x)  # noqa: E731

    story += _section_caption("I. Datos Personales")
    story.append(_field_table([
        ["Código de Trabajador", g("codigo_trabajador"), "Empresa", g("empresa")],
        ["Apellido Paterno", g("apellido_paterno"), "Apellido Materno", g("apellido_materno")],
        ["Nombres", g("nombres"), "Nacionalidad", g("nacionalidad")],
        ["Tipo de Documento", g("tipo_documento"), "N.° de Documento", g("numero_documento")],
        ["RUC", g("ruc"), "Sexo", g("sexo")],
        ["Estado Civil", g("estado_civil"), "Fecha de Nacimiento", g("fecha_nacimiento")],
        ["Lugar de Nacimiento", g("lugar_nacimiento"), "Edad", g("edad")],
    ]))
    story.append(_field_table([
        ["Dirección", g("direccion"), "Urbanización", g("urbanizacion")],
        ["Distrito", g("distrito"), "Provincia", g("provincia")],
        ["Departamento", g("departamento"), "Referencia", g("referencia")],
        ["Teléfono Fijo", tel("telefono_fijo"), "Celular", tel("celular")],
        ["Correo Personal", g("correo_personal"), "Correo Corporativo", g("correo_corporativo")],
        ["N.° de Licencia", g("licencia_numero"), "Tipo / Vencimiento",
         " — ".join([x for x in [g("licencia_tipo"), g("licencia_vencimiento")] if x])],
    ]))

    familia = fields.get("familia") or []
    story += _section_caption("II. Información Familiar")
    if familia:
        def _doc_familiar(f):
            # Compatibilidad: registros antiguos solo tenían "dni" (sin tipo).
            numero = f.get("documento_numero") or f.get("dni") or ""
            tipo = f.get("documento_tipo") or ("DNI" if numero else "")
            return " ".join(x for x in [tipo, numero] if x)
        story.append(_grid_table(
            ["Parentesco", "Nombre", "Documento", "Fecha Nac.", "Depende Econ.", "EsSalud"],
            [1700, 3300, 1900, 1600, 1400, 1300],
            [[f.get("parentesco"), f.get("nombre"), _doc_familiar(f), f.get("fecha_nacimiento"),
              "Sí" if f.get("depende_economicamente") else "No",
              "Sí" if f.get("derechohabiente_essalud") else "No"] for f in familia],
        ))
    else:
        story.append(_body_text("El trabajador declara no tener familiares que registrar a la fecha.", italic=True))

    story += _section_caption("III. Contactos de Emergencia")
    story.append(_field_table([
        ["Contacto 1 — Nombre", g("emerg1_nombre"), "Parentesco", g("emerg1_parentesco")],
        ["Celular", tel("emerg1_celular"), "Dirección", g("emerg1_direccion")],
        ["Contacto 2 — Nombre", g("emerg2_nombre"), "Parentesco", g("emerg2_parentesco")],
        ["Celular", tel("emerg2_celular"), "Dirección", g("emerg2_direccion")],
    ]))

    story += _section_caption("IV. Datos Laborales")
    story.append(_field_table([
        ["Código", g("lab_codigo"), "Área", g("area")],
        ["Gerencia", g("gerencia"), "Cargo", g("cargo")],
        ["Base", g("sede"), "Centro de Costos", g("centro_costos")],
        ["Jefe Inmediato", g("jefe_inmediato"), "Fecha de Ingreso", g("fecha_ingreso")],
        ["Fecha de Contrato", g("fecha_contrato"), "Fecha de Vencimiento", g("fecha_fin_contrato")],
        ["Periodo de Prueba (días)", g("periodo_prueba_dias"), "Tipo de Contrato", g("tipo_contrato")],
        ["Régimen Laboral", g("regimen_laboral_persona"), "Personal de Confianza", "Sí" if g("personal_confianza") else "No"],
        ["Modalidad", g("modalidad"), "", ""],
        ["Horario", g("horario"), "Jornada", g("jornada")],
        ["Turno", g("turno"), "Grupo Ocupacional", g("grupo_ocupacional")],
        ["Asignación Familiar", g("asignacion_familiar"), "", ""],
        ["Remuneración (S/)", g("remuneracion"), "Bonificaciones (S/)", g("bonificaciones")],
    ]))

    story += _section_caption("V. Información Bancaria")
    story.append(_field_table([
        ["Banco (Haberes)", g("banco_haberes"), "Tipo de Cuenta (Haberes)", g("tipo_cuenta_haberes")],
        ["Cuenta (Haberes)", g("cuenta_haberes"), "CCI (Haberes)", g("cci_haberes")],
        ["Banco CTS", g("banco_cts"), "Cuenta CTS", g("cuenta_cts")],
        ["CCI CTS", g("cci_cts"), "", ""],
    ]))

    story += _section_caption("VI. Información Previsional")
    story.append(_field_table([
        ["Sistema", g("sistema_pension"), "AFP", g("afp")],
        ["CUSPP", g("cuspp"), "Comisión", g("comision")],
        ["Seguro", g("seguro"), "Fecha de Afiliación", g("fecha_afiliacion")],
    ]))

    educacion = fields.get("educacion") or []
    story += _section_caption("VII. Educación")
    if educacion:
        story.append(_grid_table(
            ["Institución", "Carrera", "Nivel", "Grado", "Año", "Estado"],
            [2900, 2900, 1700, 1700, 1200, 1200],
            [[e.get("institucion"), e.get("carrera"), e.get("nivel"), e.get("grado"),
              e.get("anio"), e.get("estado")] for e in educacion],
        ))
    else:
        story.append(_body_text("Sin registros de educación a la fecha.", italic=True))

    experiencia = fields.get("experiencia") or []
    story += _section_caption("VIII. Experiencia Laboral")
    if experiencia:
        story.append(_grid_table(
            ["Empresa", "Cargo", "Periodo", "Funciones"],
            [2600, 2600, 2200, 4200],
            [[e.get("empresa"), e.get("cargo"), e.get("periodo"), e.get("funciones")] for e in experiencia],
        ))
    else:
        story.append(_body_text("Sin registros de experiencia laboral previa a la fecha.", italic=True))

    capacitaciones = fields.get("capacitaciones") or []
    story += _section_caption("IX. Capacitaciones")
    if capacitaciones:
        story.append(_grid_table(
            ["Curso", "Institución", "Horas", "Año"],
            [4400, 4400, 1500, 1300],
            [[c.get("curso"), c.get("institucion"), c.get("horas"), c.get("anio")] for c in capacitaciones],
        ))
    else:
        story.append(_body_text("Sin capacitaciones registradas a la fecha.", italic=True))

    story += _section_caption("X. Tallas")
    story.append(_field_table([
        ["Camisa", g("talla_camisa"), "Polo", g("talla_polo")],
        ["Pantalón", g("talla_pantalon"), "Zapato", g("talla_zapato")],
        ["Chaleco", g("talla_chaleco"), "Casco", g("talla_casco")],
        ["Guantes", g("talla_guantes"), "", ""],
    ]))

    story += _section_caption("XI. Salud")
    story.append(_field_table([
        ["Grupo Sanguíneo", g("grupo_sanguineo"), "EPS", g("eps")],
        ["EsSalud", g("essalud"), "Alergias", g("alergias")],
        ["Restricciones", g("restricciones"), "Medicamentos", g("medicamentos")],
        ["Examen Médico — Fecha", g("examen_medico_fecha"),
         "Examen Médico — Vencimiento", g("examen_medico_vencimiento")],
        ["Vacunas", g("vacunas"), "", ""],
    ]))

    return story, spec.get("cierre"), spec["titulo"], spec["subtitulo"]


def _doc_derechohabientes(fields):
    story, cierre, titulo, subtitulo = _legal_body("derechohabientes", fields)
    story += _section_caption("Datos del Titular")
    story.append(_field_table([
        ["Empresa", fields.get("empresa"), "Cargo", fields.get("cargo")],
        ["Nombre Completo", fields.get("nombre_completo"), "DNI", fields.get("dni_titular")],
    ]))
    dependientes = fields.get("dependientes") or []
    if dependientes:
        def _doc_dependiente(d):
            numero = d.get("documento_numero") or d.get("dni") or ""
            tipo = d.get("documento_tipo") or ("DNI" if numero else "")
            return " ".join(x for x in [tipo, numero] if x)
        story += _section_caption("Derechohabientes declarados para EsSalud")
        story.append(_grid_table(
            ["Parentesco", "Nombre", "Documento", "Fecha de Nacimiento"],
            [2200, 4000, 2400, 2300],
            [[d.get("parentesco"), d.get("nombre"), _doc_dependiente(d), d.get("fecha_nacimiento")]
             for d in dependientes],
        ))
    else:
        story.append(_body_text(
            "El trabajador declara no tener derechohabientes que registrar a la fecha.", italic=True,
        ))
    return story, cierre, titulo, subtitulo


def _doc_autorizacion_deposito(fields):
    story, cierre, titulo, subtitulo = _legal_body("autorizacion_deposito", fields)
    story.append(_field_table([
        ["Banco", fields.get("banco"), "Tipo de Cuenta", fields.get("tipo_cuenta")],
        ["N.° de Cuenta", fields.get("num_cuenta"), "CCI", fields.get("cci")],
    ]))
    return story, cierre, titulo, subtitulo


MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "setiembre", "octubre", "noviembre", "diciembre"]


def _fecha_larga(iso: str) -> str:
    """'2026-10-01' -> '01 de octubre de 2026'. Si no es una fecha ISO válida
    (campo vacío, o texto libre ya escrito por RR.HH.), se devuelve tal cual."""
    if not iso:
        return ""
    try:
        import datetime as _dt
        d = _dt.datetime.strptime(iso[:10], "%Y-%m-%d")
        return f"{d.day:02d} de {MESES_ES[d.month - 1]} de {d.year}"
    except (ValueError, IndexError):
        return iso


def _fecha_mas_dias(iso: str, dias) -> str:
    """Suma `dias` a una fecha ISO ('2026-10-01') y devuelve la fecha larga
    resultante. Se usa para calcular el fin del periodo de prueba a partir de
    su inicio. Devuelve "" si no hay fecha de inicio o `dias` no es un número."""
    if not iso:
        return ""
    try:
        import datetime as _dt
        d = _dt.datetime.strptime(iso[:10], "%Y-%m-%d") + _dt.timedelta(days=int(dias))
        return f"{d.day:02d} de {MESES_ES[d.month - 1]} de {d.year}"
    except (ValueError, IndexError, TypeError):
        return ""


_UNIDADES = ["", "UNO", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE"]
_ESPECIALES = {10: "DIEZ", 11: "ONCE", 12: "DOCE", 13: "TRECE", 14: "CATORCE", 15: "QUINCE",
               16: "DIECISÉIS", 17: "DIECISIETE", 18: "DIECIOCHO", 19: "DIECINUEVE",
               20: "VEINTE", 21: "VEINTIUNO", 22: "VEINTIDÓS", 23: "VEINTITRÉS",
               24: "VEINTICUATRO", 25: "VEINTICINCO", 26: "VEINTISÉIS", 27: "VEINTISIETE",
               28: "VEINTIOCHO", 29: "VEINTINUEVE"}
_DECENAS = {3: "TREINTA", 4: "CUARENTA", 5: "CINCUENTA", 6: "SESENTA", 7: "SETENTA",
            8: "OCHENTA", 9: "NOVENTA"}
_CENTENAS = ["", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS",
             "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS"]


def _num_a_letras_hasta_999(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "CIEN"
    if n < 10:
        return _UNIDADES[n]
    if n < 30:
        return _ESPECIALES[n]
    if n < 100:
        d, u = divmod(n, 10)
        return _DECENAS[d] + (f" Y {_UNIDADES[u]}" if u else "")
    c, resto = divmod(n, 100)
    return _CENTENAS[c] + (f" {_num_a_letras_hasta_999(resto)}" if resto else "")


def _num_a_letras(n: int) -> str:
    """Entero no negativo -> letras en español, hasta 999 999 999 (de sobra
    para un sueldo). Sin librerías externas."""
    if n == 0:
        return "CERO"
    millones, resto = divmod(n, 1_000_000)
    miles, cientos = divmod(resto, 1000)
    partes = []
    if millones:
        partes.append("UN MILLÓN" if millones == 1 else f"{_num_a_letras_hasta_999(millones)} MILLONES")
    if miles:
        partes.append("MIL" if miles == 1 else f"{_num_a_letras_hasta_999(miles)} MIL")
    if cientos:
        partes.append(_num_a_letras_hasta_999(cientos))
    return " ".join(partes)


def _monto_en_letras(monto) -> str:
    """1130.5 -> 'MIL CIENTO TREINTA CON 50/100 SOLES'. Devuelve "" si `monto`
    no es un número válido (RR.HH. no lo llenó todavía)."""
    try:
        valor = float(monto)
    except (TypeError, ValueError):
        return ""
    entero = int(valor)
    centavos = round((valor - entero) * 100)
    return f"{_num_a_letras(entero)} CON {centavos:02d}/100 SOLES"


def _clause(nombre, texto, fields):
    filled = _fill(texto, fields)
    return Paragraph(f'<b><font color="{NAVY_DARK_HEX}">{_esc(nombre)}.- </font></b>{_esc(filled)}',
                      _style_numbered)


# Cláusulas comunes a los 2 contratos de planilla a plazo fijo (Régimen
# General / MYPE) — son idénticas salvo 3 frases marcadas con {mype_*}, que
# quedan vacías si la persona no está en régimen MYPE. Transcritas de los
# formatos que RR.HH. entregó el 16/09.
_CLAUSULAS_PLAZO_FIJO = [
    ("PRIMERA: ANTECEDENTES", "EL EMPLEADOR es una persona jurídica de derecho privado que recientemente "
     "emprende sus actividades en el mercado, constituida en el 2023, bajo el régimen de la Microempresa, "
     "cuyo objeto social es {empresa_objeto_social}, entre otras previstas en el estatuto de su creación, "
     "según consta en la Partida Registral N.° {empresa_partida_registral} del Registro."),
    ("SEGUNDA: SUSTENTO", "Actualmente existe en el sector de las telecomunicaciones una coyuntura de mercado, "
     "que sumada a la inestabilidad política y social ha producido desequilibrio y oscilaciones económicas en "
     "el negocio, lo cual genera una incertidumbre en la continuidad del negocio; por lo que, a efecto de "
     "verificar la respuesta del mercado a los servicios que ofrece {empresa_razon_social}, ésta requiere "
     "cubrir una serie de puestos de trabajo que le permitan determinar la viabilidad de sus actividades, así "
     "como su permanencia en el mercado; bajo esas circunstancias, se encontraría sustentada la temporalidad "
     "del presente contrato modal por inicio de actividad."),
    ("TERCERA: CARGO", "Por el presente documento EL EMPLEADOR contrata a plazo fijo bajo {mype_regimen_texto} y "
     "la modalidad ya indicada, los servicios de EL TRABAJADOR quien desempeñará el cargo de {cargo}, en "
     "relación con las causas objetivas señaladas en la cláusula anterior."),
    ("CUARTA: FUNCIONES", "En atención al cargo que desempeñará EL TRABAJADOR para EL EMPLEADOR, éste tendrá el "
     "deber de cumplir, entre otras, con las actividades indicadas en la Descripción del Cargo anexa al "
     "presente contrato marcada con la letra \"A\", de modo tal que pueda cumplir a cabalidad con las "
     "actividades que le sean encomendadas. Asimismo, EL TRABAJADOR deberá cumplir con las normas propias del "
     "Centro de Trabajo y las demás normas laborales, y las que se impartan por necesidades del servicio en "
     "ejercicio de las facultades de administración de la empresa, de conformidad con el Art. 9.° de la Ley de "
     "Productividad y Competitividad Laboral aprobado por D.S. N.° 003-97-TR. EL EMPLEADOR se encuentra "
     "facultado a efectuar modificaciones razonables en función de la capacidad y actitud de EL TRABAJADOR y a "
     "las necesidades y requerimientos de la misma, sin que dichas variaciones signifiquen menoscabo de "
     "categoría y/o remuneración."),
    ("QUINTA: PLAZO", "El plazo de duración del presente contrato rige desde el {fecha_contrato_larga} hasta el "
     "{fecha_fin_contrato_larga}, fecha en que debe empezar y terminar sus labores EL TRABAJADOR, "
     "respectivamente."),
    ("SEXTA: PERIODO DE PRUEBA", "EL TRABAJADOR estará sujeto a un período de prueba de {periodo_prueba_dias} "
     "días, el mismo que inicia el {fecha_contrato_larga} y concluye el {periodo_prueba_fin_larga}, debiendo "
     "señalar en este extremo que las partes acuerdan libremente este plazo de período de prueba debido a la "
     "adaptación que, en el tiempo, deberá acceder EL TRABAJADOR en su nuevo puesto de trabajo."),
    ("SÉPTIMA: JORNADA", "EL TRABAJADOR cumplirá el siguiente horario de trabajo: {horario}, con un tiempo de "
     "refrigerio de sesenta (60) minutos."),
    ("OCTAVA: REMUNERACIÓN", "EL EMPLEADOR abonará a EL TRABAJADOR la cantidad de S/ {remuneracion} "
     "({remuneracion_letras}) como remuneración mensual, de la cual se deducirán las aportaciones y "
     "descuentos por tributos establecidos en la ley que le resulten de aplicación."),
    ("NOVENA: TERMINACIÓN", "Queda entendido que EL EMPLEADOR no está obligado a dar aviso alguno adicional "
     "referente al término del presente contrato, operando su extinción en la fecha de su vencimiento conforme "
     "la cláusula QUINTA, oportunidad en la cual se abonará a EL TRABAJADOR los beneficios sociales que le "
     "pudieran corresponder de acuerdo a ley."),
    ("DÉCIMA: CONFIDENCIALIDAD", "EL TRABAJADOR acuerda mantener la debida confidencialidad sobre la "
     "información que reciba de EL EMPLEADOR con motivo del presente Contrato. Será considerada como "
     "información confidencial aquella que haya sido plasmada en cualquier medio y que por cualquier "
     "mecanismo, sea suministrada por EL EMPLEADOR a EL TRABAJADOR, directamente o a través de dependientes, "
     "subcontratistas, asesores o auxiliares, aunque tal información no haya sido calificada como "
     "confidencial. La confidencialidad no se extiende a la información que, desde antes de su entrega por "
     "una parte a la otra, sea del dominio público. EL EMPLEADOR podrá solicitar a EL TRABAJADOR que le "
     "devuelva la información confidencial que le concierne y que la destruya o la borre de sus archivos. En "
     "caso de que cualquier autoridad, de índole administrativo o judicial, solicite que EL TRABAJADOR le "
     "suministre información confidencial perteneciente a EL EMPLEADOR, EL TRABAJADOR deberá notificarlo de "
     "inmediato a EL EMPLEADOR."),
    ("DÉCIMA PRIMERA: SECRETO DE LAS TELECOMUNICACIONES", "EL TRABAJADOR declara conocer que en ejecución de "
     "los servicios materia del presente Contrato tendrá acceso a determinada información que se encuentra "
     "protegida, entre otros, por el artículo 2.° numeral 10) de la Constitución Política del Perú; los "
     "artículos 161.° y siguientes del Código Penal; los artículos 4.°, 87.° inciso 5) y 90.° del Texto Único "
     "Ordenado de la Ley de Telecomunicaciones; los artículos 10.° y 15.° del Reglamento de la Ley de "
     "Telecomunicaciones y la Ley N.° 29733 - Ley de Protección de Datos Personales, al calificar la misma "
     "como \"secreto de las telecomunicaciones\" y/o \"datos personales\", respectivamente. En consecuencia, "
     "EL TRABAJADOR se obliga a no sustraer, interceptar, interferir, alterar, desviar, acceder, utilizar, "
     "publicar o facilitar tanto el contenido de cualquier comunicación como la información personal de los "
     "usuarios de alguno de los servicios prestados por EL EMPLEADOR."),
    ("DÉCIMA SEGUNDA: PROTECCIÓN DE DATOS PERSONALES", "A efectos de lo establecido en la Ley N.° 29733, Ley de "
     "Protección de Datos Personales, y en virtud del acceso que EL TRABAJADOR tiene a los datos personales "
     "contenidos en bancos de datos de titularidad de EL EMPLEADOR y el cliente, EL TRABAJADOR deberá utilizar "
     "dicha información exclusivamente para los fines del presente contrato, no comunicarla ni transferirla a "
     "terceros sin autorización expresa y por escrito de EL EMPLEADOR, y una vez finalizado el contrato, "
     "deberá destruirla, eliminarla o devolverla a EL EMPLEADOR. El incumplimiento de esta cláusula será "
     "causal de resolución del presente Contrato."),
    ("DÉCIMA TERCERA: ANTICORRUPCIÓN", "LAS PARTES declaran y se obligan a que ellas y todas las personas "
     "empleadas o que actúan a su nombre se abstendrán de dar, ofrecer, aceptar o recibir, directa o "
     "indirectamente, dinero o cualquier otra cosa de valor con la finalidad de obtener o retener una ventaja "
     "comercial indebida, incluyendo pagos a funcionarios públicos o privados. La Parte afectada podrá dar por "
     "terminado este contrato inmediatamente, sin responsabilidad alguna, si concluye que la otra ha "
     "incumplido esta cláusula."),
    ("DÉCIMA CUARTA: NORMATIVA", "Este contrato queda sujeto a las disposiciones que contiene el TUO del D. "
     "Leg. N.° 728 aprobado por D.S. N.° 003-97-TR, Ley de Productividad y Competitividad Laboral{mype_normativa_texto}, "
     "y demás normas legales que lo regulen o que sean dictadas durante la vigencia del contrato."),
]

# Cláusulas del Contrato por Servicio Específico para Personal de Confianza
# (Art. 43° LPCL). {mype_*} funcionan igual que en el set anterior — quedan
# vacíos para la variante en Régimen General (redactada por analogía, ya que
# RR.HH. solo entregó la versión MYPE — debe revisarla el asesor legal antes
# de usarla con alguien en Régimen General).
_CLAUSULAS_CONFIANZA = [
    ("PRIMERA: ANTECEDENTES", "EL EMPLEADOR es una persona jurídica de derecho privado que recientemente "
     "emprende sus actividades en el mercado, constituida en el 2023, bajo el régimen de la Microempresa, "
     "cuyo objeto social es {empresa_objeto_social}, entre otras previstas en el estatuto de su creación, "
     "según consta en la Partida Registral N.° {empresa_partida_registral} del Registro."),
    ("SEGUNDA: SUSTENTO", "Este contrato por servicio específico tiene por objeto la prestación de servicios "
     "sustentada en la confianza, dado que EL TRABAJADOR (i) estará en constante contacto personal y directo "
     "con el empleador y el personal de dirección, (ii) tendrá acceso a secretos industriales, comerciales o "
     "profesionales y a información de carácter reservado, y (iii) sus opiniones o informes serán presentados "
     "directamente al personal de dirección, impactando en las decisiones empresariales; bajo esas "
     "circunstancias, se encontraría sustentado el presente contrato modal de prestación de servicios basado "
     "en la confianza."),
    ("TERCERA: CARGO", "Por el presente documento EL EMPLEADOR contrata a plazo fijo bajo {mype_regimen_texto} y "
     "la modalidad ya indicada, los servicios de EL TRABAJADOR quien desempeñará el cargo de {cargo}, en "
     "relación con las causas objetivas señaladas en la cláusula anterior."),
    ("CUARTA: FUNCIONES", "En atención al cargo que desempeñará EL TRABAJADOR para EL EMPLEADOR, a través de "
     "una relación laboral de exclusiva confianza, éste tendrá el deber de: prestar servicios en contacto "
     "personal y directo con EL EMPLEADOR y/o personal de dirección; guardar reserva de los secretos "
     "industriales, comerciales o profesionales de los que tome conocimiento; emitir las opiniones o informes "
     "que le solicite EL EMPLEADOR; y desempeñar las actividades de confianza indicadas en la Descripción del "
     "Cargo anexa al presente contrato marcada con la letra \"A\". EL EMPLEADOR se encuentra facultado a "
     "efectuar modificaciones razonables en función de la capacidad y actitud de EL TRABAJADOR y a las "
     "necesidades y requerimientos de la misma, sin que dichas variaciones signifiquen menoscabo de categoría "
     "y/o remuneración."),
    ("QUINTA: PLAZO", "La duración del presente contrato rige desde el {fecha_contrato_larga} hasta el "
     "{fecha_fin_contrato_larga}, fecha en que termina el contrato, salvo que EL EMPLEADOR retire la "
     "confianza antes del plazo indicado. EL TRABAJADOR indica tener pleno conocimiento del término de su "
     "relación contractual por cumplimiento del plazo indicado o el retiro de confianza, manifestando su "
     "consentimiento libre y voluntario en ambas formas de terminación de la relación laboral."),
    ("SEXTA: PERIODO DE PRUEBA", "EL TRABAJADOR estará sujeto a un período de prueba de {periodo_prueba_dias} "
     "días, el mismo que inicia el {fecha_contrato_larga} y concluye el {periodo_prueba_fin_larga}, debiendo "
     "señalar en este extremo que las partes acuerdan libremente este plazo de período de prueba debido a la "
     "adaptación que, en el tiempo, deberá acceder EL TRABAJADOR en su nuevo puesto de trabajo."),
    ("SÉPTIMA: JORNADA", "EL TRABAJADOR, como trabajador de confianza, no se encuentra sujeto a la jornada "
     "máxima de trabajo de ocho (8) horas diarias o cuarenta y ocho (48) horas semanales conforme al TUO de la "
     "Ley de Jornada de Trabajo, Horario y Trabajo en Sobretiempo — D.S. N.° 007-2002-TR. En este sentido, EL "
     "TRABAJADOR se encuentra eximido de la obligación de llevar un registro de control de asistencia, sin "
     "perjuicio de lo cual deberá encontrarse en el lugar habitual de trabajo en los días laborales ({horario}). "
     "Asimismo, se deja establecido que EL TRABAJADOR no tiene derecho a exigir ni el descanso sustitutorio ni "
     "el pago del descanso semanal obligatorio, por cuanto no se encuentra sujeto a control efectivo del "
     "tiempo de trabajo."),
    ("OCTAVA: REMUNERACIÓN", "EL EMPLEADOR pagará a EL TRABAJADOR la cantidad de S/ {remuneracion} "
     "({remuneracion_letras}) como remuneración mensual, del cual se deducirán las aportaciones y descuentos "
     "por tributos establecidos en la ley que le resulten de aplicación. La remuneración no implicará el pago "
     "de horas extras o sobretiempo al no estar sujeto el trabajador a jornada de trabajo y registro de "
     "asistencia."),
    ("NOVENA: TERMINACIÓN", "Queda entendido que EL EMPLEADOR no está obligado a dar aviso alguno adicional "
     "referente al término del presente contrato, operando su extinción en la fecha de su vencimiento "
     "conforme la cláusula QUINTA, oportunidad en la cual se abonará a EL TRABAJADOR los beneficios sociales "
     "que le pudieran corresponder de acuerdo a ley, salvo la indemnización por despido arbitrario, que no le "
     "corresponderá al TRABAJADOR por haber ingresado directamente al cargo de confianza. En el caso del "
     "retiro de confianza, la sola comunicación escrita al trabajador da por terminado el contrato."),
    ("DÉCIMA: CONFIDENCIALIDAD", "Las partes acuerdan incorporar al presente una cláusula de confidencialidad "
     "y reserva que deberá observar EL TRABAJADOR respecto de la información de carácter reservada de "
     "propiedad de EL EMPLEADOR (planes, proyectos, software, estrategias comerciales, financieras, de "
     "clientes, proveedores y demás), guardando total reserva y absoluta confidencialidad frente a terceros. "
     "El plazo de esta reserva se encontrará vigente durante la vigencia del vínculo laboral e incluso "
     "abarcará dos (2) años luego de culminada la relación contractual. EL TRABAJADOR será responsable en "
     "forma directa de cualquier daño o perjuicio que se origine por el incumplimiento de esta cláusula, sin "
     "perjuicio de las acciones penales que correspondan conforme al artículo 165.° del Código Penal."),
    ("DÉCIMA PRIMERA: SECRETO DE LAS TELECOMUNICACIONES", "EL TRABAJADOR declara conocer que en ejecución de "
     "los servicios materia del presente Contrato tendrá acceso a determinada información protegida por el "
     "artículo 2.° numeral 10) de la Constitución Política del Perú, los artículos 161.° y siguientes del "
     "Código Penal, y la Ley N.° 29733 - Ley de Protección de Datos Personales, al calificar la misma como "
     "\"secreto de las telecomunicaciones\" y/o \"datos personales\". EL TRABAJADOR se obliga a no sustraer, "
     "interceptar, interferir, alterar, desviar, acceder, utilizar, publicar o facilitar dicha información."),
    ("DÉCIMA SEGUNDA: PROTECCIÓN DE DATOS PERSONALES", "EL TRABAJADOR deberá utilizar la información personal "
     "proporcionada por EL EMPLEADOR exclusivamente para los fines del presente contrato, no comunicarla a "
     "terceros sin autorización expresa y por escrito de EL EMPLEADOR, y destruirla, eliminarla o devolverla "
     "una vez finalizado el contrato. El incumplimiento será causal de resolución del presente Contrato."),
    ("DÉCIMA TERCERA: DERECHO DE AUTOR", "La titularidad y propiedad de los derechos sobre creaciones "
     "intelectuales que se generen con ocasión de la ejecución del presente contrato serán de EL EMPLEADOR, "
     "quien podrá registrarlas, reproducirlas, transformarlas, difundirlas y explotarlas por cualquier medio, "
     "sin que EL TRABAJADOR pueda comercializarlas en ningún momento ni reclamar contraprestación adicional "
     "por mejoras que EL EMPLEADOR obtenga en bienes o procedimientos."),
    ("DÉCIMA CUARTA: ANTICORRUPCIÓN", "EL TRABAJADOR se compromete a no participar en actos de corrupción o "
     "soborno que puedan involucrar a EL EMPLEADOR, a no influir en decisiones de funcionarios públicos o "
     "privados mediante beneficios personales en nombre de EL EMPLEADOR, y a informar cualquier conducta "
     "desleal de la que tenga conocimiento."),
    ("DÉCIMA QUINTA: EXCLUSIVIDAD", "EL TRABAJADOR se compromete a desarrollar su tarea de manera profesional "
     "y exclusiva para EL EMPLEADOR, quedando expresamente prohibido mantener relación laboral o por cuenta "
     "ajena con terceros que puedan ser competencia, directa o indirecta, de la actividad de EL EMPLEADOR. Su "
     "incumplimiento faculta a EL EMPLEADOR a proceder al despido de EL TRABAJADOR por incumplimiento grave de "
     "sus obligaciones contractuales."),
    ("DÉCIMA SEXTA: INDEMNIDAD", "EL TRABAJADOR cumplirá fielmente y con la diligencia de un buen padre de "
     "familia las obligaciones asumidas en el presente Contrato, e indemnizará a EL EMPLEADOR por cualesquiera "
     "daños causados en razón de su incumplimiento. Las acciones contrarias a lo estipulado en este documento "
     "serán causal de terminación del vínculo laboral por pérdida de la confianza."),
    ("DÉCIMA SÉPTIMA: NORMATIVA APLICABLE", "Este contrato queda sujeto a las disposiciones que contiene el "
     "TUO del D. Leg. N.° 728 aprobado por D.S. N.° 003-97-TR, Ley de Productividad y Competitividad "
     "Laboral{mype_normativa_texto}, y demás normas legales que lo regulen o que sean dictadas durante la "
     "vigencia del contrato."),
]


def _doc_contrato(fields):
    """Arma el contrato de trabajo correcto según el Régimen Laboral de la
    persona y si es Personal de Confianza — ver CONTRATOS_VARIANTES. El
    contenido de las cláusulas se arma en Python (no en legal_texts.json)
    porque cambia según esas dos variables y necesita fechas/montos
    calculados, algo que el motor genérico de {placeholders} no resuelve."""
    g = fields.get
    es_confianza = bool(g("personal_confianza"))
    es_mype = (g("regimen_laboral_persona") or "").strip().upper() == "MYPE"

    tipo_doc = g("tipo_documento") or "DNI"
    numero_doc = g("numero_documento") or ""
    direccion = ", ".join(x for x in [g("direccion"), g("distrito"), g("provincia"), g("departamento")] if x)
    correo = g("correo_corporativo") or g("correo_personal") or ""
    periodo_prueba_dias = g("periodo_prueba_dias") or "30"

    computed = {
        "num_doc": f"{tipo_doc} N.° {numero_doc}" if numero_doc else "________",
        "direccion": direccion or "________",
        "correo": correo or "________",
        "fecha_contrato_larga": _fecha_larga(g("fecha_contrato")) or "________",
        "fecha_fin_contrato_larga": _fecha_larga(g("fecha_fin_contrato")) or "________",
        "periodo_prueba_dias": str(periodo_prueba_dias),
        "periodo_prueba_fin_larga": _fecha_mas_dias(g("fecha_contrato"), periodo_prueba_dias) or "________",
        "remuneracion": g("remuneracion") or "________",
        "remuneracion_letras": _monto_en_letras(g("remuneracion")) or "monto a completar por RR.HH.",
        "horario": g("horario") or "________",
        "cargo": g("cargo") or "________",
        "empresa_razon_social": g("empresa_razon_social") or "________",
        "empresa_ruc": g("empresa_ruc") or "________",
        "empresa_domicilio_fiscal": g("empresa_domicilio_fiscal") or "________",
        "empresa_partida_registral": g("empresa_partida_registral") or "________",
        "empresa_objeto_social": g("empresa_objeto_social") or (
            "prestar servicios de ventas presenciales, call centers, capacitación de ventas, representación de "
            "marcas, ventas receptivas en tiendas, distribución e instalación de bienes de todo tipo, asesoría "
            "comercial y asesoría en desarrollo de fuerza de ventas"),
        "representante_legal": g("representante_legal") or "________",
        "representante_tipo_documento": g("representante_tipo_documento") or "DNI",
        "representante_numero_documento": g("representante_numero_documento") or "________",
        "representante_nacionalidad": g("representante_nacionalidad") or "________",
        "mype_regimen_texto": ("el Régimen Laboral aplicable a la Micro y Pequeña Empresa" if es_mype
                                else "el Régimen General"),
        "mype_normativa_texto": (", así como la Ley y Reglamento de la Micro y Pequeña Empresa" if es_mype else ""),
    }
    merged = {**fields, **computed}

    clausulas = _CLAUSULAS_CONFIANZA if es_confianza else _CLAUSULAS_PLAZO_FIJO
    variante_nombre = (
        ("Personal de Confianza — " + ("MYPE" if es_mype else "Régimen General"))
        if es_confianza else
        ("Régimen MYPE — Plazo Fijo" if es_mype else "Régimen General — Plazo Fijo")
    )

    intro = _fill(
        "Conste por el presente documento el Contrato de Trabajo que celebran, de una parte, "
        "{empresa_razon_social} {empresa_ruc}, domiciliada en {empresa_domicilio_fiscal}, representada por su "
        "representante legal {representante_legal}, {representante_nacionalidad}, mayor de edad e "
        "identificado(a) con {representante_tipo_documento} N.° {representante_numero_documento}, a quien en "
        "adelante se le denominará EL EMPLEADOR; y de la otra parte {nombre_completo}, identificado(a) con "
        "{num_doc}, domiciliado(a) en {direccion}, correo electrónico {correo}, a quien en adelante se le "
        "denominará EL TRABAJADOR; en los términos y condiciones siguientes:",
        merged,
    )
    story = [_body_text(intro), Spacer(1, 2 * mm)]
    for nombre, texto in clausulas:
        story.append(_clause(nombre, texto, merged))

    # Anexo A — Descripción del Cargo (si el Cargo tiene MOF cargado).
    if fields.get("cargo_descripcion") or fields.get("cargo_funciones") or fields.get("cargo_responsabilidades"):
        story.append(Spacer(1, 4 * mm))
        story += _section_caption(f'ANEXO "A" — DESCRIPCIÓN DEL CARGO: {merged["cargo"]}'.upper())
        if fields.get("cargo_descripcion"):
            story.append(_body_text(fields["cargo_descripcion"]))
        if fields.get("cargo_funciones"):
            story.append(_body_text("Funciones:"))
            for f in fields["cargo_funciones"]:
                story.append(_numbered_item(fields["cargo_funciones"].index(f) + 1, f))
        if fields.get("cargo_responsabilidades"):
            story.append(_body_text("Responsabilidades:"))
            for i, r in enumerate(fields["cargo_responsabilidades"], start=1):
                story.append(_numbered_item(i, r))

    spec = LEGAL_TEXTS["contrato"]
    subtitulo = f"{spec['subtitulo']} · {variante_nombre}"
    return story, spec["cierre"], spec["titulo"], subtitulo


# Contrato de Locación de Servicios (Régimen RHE) — transcrito del modelo
# entregado por RR.HH. el 28/09. A diferencia del contrato de planilla, acá
# NO hay periodo de prueba ni jornada (es de naturaleza civil, no laboral).
_CLAUSULAS_LOCACION_SERVICIOS = [
    ("Primera", "OBJETO DEL CONTRATO. El objeto del presente es contratar los servicios profesionales de EL "
     "LOCADOR como {cargo} a fin de que realice las actividades descritas en el Anexo 1 de este documento. En "
     "consecuencia, EL LOCADOR se obliga frente a DIGETEL a realizar y dar cumplimiento a los servicios "
     "descritos en dicho Anexo."),
    ("Segunda", "NATURALEZA DEL CONTRATO. Las partes dejan expresamente establecido que el presente Contrato "
     "tiene naturaleza civil y no implica relación de subordinación ni dependencia alguna de EL LOCADOR con "
     "DIGETEL. El presente documento es estrictamente de carácter comercial y profesional, y EL LOCADOR "
     "obrará con autonomía. EL LOCADOR declara que la ejecución del presente Contrato no es su mayor ni única "
     "fuente de ingreso y que sus actividades no son conexas ni inherentes a las actividades de DIGETEL."),
    ("Tercera", "PAGO DE RETRIBUCIÓN. DIGETEL se obliga a pagar a EL LOCADOR la cantidad de S/ {remuneracion} "
     "({remuneracion_letras}) mensuales, incluidos los impuestos de ley. A tales efectos, EL LOCADOR deberá "
     "presentar dentro de los cinco (5) primeros días útiles del mes el recibo por honorarios correspondiente "
     "al mes en curso, que será pagado por DIGETEL dentro de los cinco (5) días útiles siguientes a su "
     "presentación. Todos los pagos se realizarán por transferencia a la cuenta bancaria: Banco {banco}, "
     "Beneficiario {nombre_completo}, N.° de Cuenta {num_cuenta}, CCI {cci}. Cada una de LAS PARTES se hará "
     "cargo de declarar y pagar los tributos, impuestos, gastos y cualquier otra erogación que le corresponda "
     "de conformidad a la normativa aplicable. Cualesquiera montos que deba EL LOCADOR a DIGETEL por concepto "
     "de indemnizaciones, reembolsos o cualquier otra causa podrán ser compensados de lo que DIGETEL adeude a "
     "EL LOCADOR por el Precio o por cualquier otro concepto."),
    ("Cuarta", "PLAZO DEL CONTRATO. La duración del presente Contrato es de {dias_contrato} días, contados a "
     "partir del {fecha_contrato_larga} hasta el {fecha_fin_contrato_larga}. Salvo manifestación expresa en "
     "contrario, el presente instrumento no será objeto de renovación ni prorrogado de manera automática, "
     "estando sujeta cualquier renovación o prórroga al consentimiento expreso y escrito de LAS PARTES."),
    ("Quinta", "TERMINACIÓN ANTICIPADA. La terminación de este Contrato tendrá lugar al vencimiento del plazo "
     "señalado en la cláusula anterior, o su prórroga de haberla. No obstante, cualquiera de LAS PARTES podrá "
     "terminar este Contrato anticipadamente dando aviso previo y por escrito a la otra parte, con al menos "
     "quince (15) días continuos de antelación. LAS PARTES no tendrán derecho a indemnización alguna por tal "
     "terminación anticipada, salvo que ésta derive del incumplimiento de alguna obligación del presente "
     "Contrato, en cuyo caso la parte afectada podrá terminarlo unilateralmente mediante simple notificación."),
    ("Sexta", "CONFIDENCIALIDAD. EL LOCADOR acuerda mantener la debida confidencialidad sobre la información "
     "que reciba de DIGETEL con motivo del presente Contrato, en cualquier medio que le sea suministrada, "
     "directamente o a través de dependientes, subcontratistas, asesores o auxiliares, aunque no haya sido "
     "calificada como confidencial. Esta obligación no se extiende a información que, antes de su entrega, "
     "sea de dominio público. DIGETEL podrá solicitar a EL LOCADOR la devolución, destrucción o borrado de la "
     "información confidencial. Si una autoridad administrativa o judicial solicitara a EL LOCADOR dicha "
     "información, éste deberá notificarlo de inmediato a DIGETEL."),
    ("Séptima", "SECRETO DE LAS TELECOMUNICACIONES. EL LOCADOR declara conocer que en ejecución de los "
     "servicios materia del presente Contrato tendrá acceso a información protegida, entre otros, por el "
     "artículo 2.° numeral 10) de la Constitución Política del Perú, los artículos 161.° y siguientes del "
     "Código Penal, los artículos 4.°, 87.° inciso 5) y 90.° del Texto Único Ordenado de la Ley de "
     "Telecomunicaciones, los artículos 10.° y 15.° del Reglamento de la Ley de Telecomunicaciones y la Ley "
     "N.° 29733, al calificar la misma como \"secreto de las telecomunicaciones\" y/o \"datos personales\". EL "
     "LOCADOR se obliga a no sustraer, interceptar, interferir, alterar, desviar, acceder, utilizar, publicar "
     "o facilitar el contenido de comunicaciones ni la información personal de los usuarios de los servicios "
     "prestados por DIGETEL, observando en todo momento las instrucciones que ésta le imparta al respecto."),
    ("Octava", "PROTECCIÓN DE DATOS PERSONALES. A efectos de la Ley N.° 29733 y su Reglamento (D.S. N.° "
     "003-2013-JUS), y en virtud del acceso que EL LOCADOR tiene a datos personales de titularidad de DIGETEL "
     "y sus clientes: (i) EL LOCADOR utilizará dicha información exclusivamente para los fines del presente "
     "contrato y según las instrucciones de DIGETEL; (ii) no la comunicará, transferirá ni la proporcionará a "
     "terceros sin autorización previa y por escrito de DIGETEL, ni la duplicará o reproducirá; (iii) "
     "trasladará estas obligaciones a sus propios colaboradores; (iv) garantizará que solo la maneje personal "
     "estrictamente necesario; (v) contará con las medidas de seguridad legalmente exigibles; (vi) al "
     "finalizar el contrato, destruirá, eliminará o devolverá la información; (vii) responderá por cualquier "
     "reclamo derivado del incumplimiento de esta cláusula; y (viii) su incumplimiento será causal de "
     "resolución del presente Contrato."),
    ("Novena", "ANTICORRUPCIÓN. LAS PARTES declaran y se obligan a que ellas y todas las personas que actúan a "
     "su nombre se abstendrán de dar, ofrecer, aceptar o recibir, directa o indirectamente, dinero o cualquier "
     "otra cosa de valor con la finalidad de obtener o retener una ventaja comercial indebida, incluyendo "
     "pagos a funcionarios públicos, candidatos políticos o representantes de partidos políticos. La parte "
     "afectada podrá dar por terminado este contrato inmediatamente, sin responsabilidad alguna, si concluye "
     "que la otra ha incumplido esta cláusula, quedando además facultada a exigir la indemnización "
     "correspondiente."),
    ("Décima", "DERECHO DE AUTOR. La titularidad y propiedad de los derechos sobre creaciones intelectuales "
     "que se generen con ocasión de la ejecución del presente contrato serán de DIGETEL, quien podrá "
     "registrarlas ante Indecopi, reproducirlas, transformarlas, difundirlas, comercializarlas y explotarlas "
     "por cualquier medio, sin que EL LOCADOR pueda comercializarlas en ningún momento. Las mejoras que "
     "DIGETEL obtenga en bienes o procedimientos también le corresponderán, sin derecho a reclamo de EL "
     "LOCADOR por este concepto."),
    ("Décima Primera", "AUDITORÍAS/SUPERVISIONES. EL LOCADOR permitirá a DIGETEL el acceso a sus oficinas, "
     "equipos y sistemas, y entregará la información relacionada con los Servicios, a fin de que se practiquen "
     "auditorías con la frecuencia que DIGETEL requiera, para verificar la capacidad, diligencia y efectividad "
     "de EL LOCADOR. Estas auditorías se notificarán por escrito con al menos un (1) día de anticipación, y la "
     "información entregada en ellas queda sujeta a la cláusula de confidencialidad."),
    ("Décima Segunda", "CONTRATO INTUITU PERSONAE/SUBCONTRATACIONES. El presente Contrato ha sido celebrado "
     "por cada una de LAS PARTES debido al conocimiento que tiene de la otra; por ello, ninguna podrá ceder a "
     "terceros los derechos y obligaciones derivados del mismo sin aprobación por escrito de la otra parte. No "
     "obstante, DIGETEL podrá ceder el presente Contrato a cualquiera de sus filiales, subsidiarias, afiliadas "
     "o relacionadas."),
    ("Décima Tercera", "GARANTÍA Y DAÑOS. EL LOCADOR cumplirá fielmente y con la diligencia de un buen padre "
     "de familia las obligaciones asumidas en el presente Contrato, e indemnizará a DIGETEL cualesquiera daños "
     "causados debido a su incumplimiento, conforme a la normativa legal vigente."),
    ("Décima Cuarta", "NOTIFICACIONES. Las notificaciones entre LAS PARTES se harán por correo electrónico: "
     "(i) en el caso de DIGETEL, en {empresa_correo_notificaciones}; y (ii) en el caso de EL LOCADOR, en "
     "{correo}."),
    ("Décima Quinta", "MODIFICACIONES. Cualquier modificación pactada por LAS PARTES en relación con el monto, "
     "los términos y condiciones del presente contrato deberá constar por escrito mediante addendum suscrito "
     "por LAS PARTES."),
    ("Décima Sexta", "GASTOS. Queda expresamente convenido que cada una de LAS PARTES pagará los honorarios de "
     "sus propios abogados relativos a la negociación y celebración del presente Contrato."),
    ("Décima Séptima", "DECLARACIONES DE EL LOCADOR. EL LOCADOR declara y garantiza que: a) cumple con las "
     "leyes de la jurisdicción donde está domiciliado y tiene todos los requisitos, poder y autoridad para "
     "celebrar este Contrato; b) tiene todas las facultades requeridas para suscribirlo y cumplirlo, sin que "
     "ello viole disposición legal o contractual alguna con terceros; c) no requiere autorización, aprobación "
     "o notificación de autoridad gubernamental ni de terceros para celebrarlo; d) no tiene conocimiento de "
     "acción judicial, demanda o litigio que lo afecte y que pudiera tener un efecto adverso sobre este "
     "Contrato; y e) está sometido a las leyes civiles o comerciales aplicables, sin gozar de inmunidad alguna."),
    ("Décima Octava", "LEY APLICABLE. El presente contrato se regirá por las leyes de la República del Perú."),
    ("Décima Novena", "RESOLUCIÓN AMISTOSA DE CONFLICTOS. Todo conflicto referido a la ejecución del presente "
     "Contrato se resolverá agotando primero la negociación amistosa: la parte reclamante notificará por "
     "escrito a la otra describiendo el hecho que origina la reclamación, y dentro de los cinco (5) días "
     "hábiles siguientes se convocará a una reunión para solucionarlo de manera amistosa. Transcurridos "
     "treinta (30) días continuos desde esa primera sesión sin acuerdo, cualquiera de LAS PARTES podrá acudir "
     "a la instancia establecida en la cláusula siguiente."),
    ("Vigésima", "DOMICILIO Y JURISDICCIÓN. Para todos los efectos del presente contrato, sus derivados y "
     "consecuencias, LAS PARTES eligen la ciudad de Lima como domicilio especial, sometiéndose expresamente a "
     "la jurisdicción de sus tribunales, con exclusión de cualquier otro que pudiera establecerse por ley."),
]


def _doc_locacion_servicios(fields):
    """Contrato de Locación de Servicios — Régimen RHE (Recibo por Honorarios
    Electrónico). Naturaleza civil, sin periodo de prueba ni jornada, a
    diferencia del contrato de planilla. Modelo entregado por RR.HH. el
    28/09 (CONTRATO DE SERVICIOS PROFESIONALES)."""
    g = fields.get
    tipo_doc = g("tipo_documento") or "DNI"
    numero_doc = g("numero_documento") or ""
    direccion = ", ".join(x for x in [g("direccion"), g("distrito"), g("provincia"), g("departamento")] if x)
    correo = g("correo_corporativo") or g("correo_personal") or ""
    dias_contrato = g("dias_contrato") or ""

    computed = {
        "num_doc": f"{tipo_doc} N.° {numero_doc}" if numero_doc else "________",
        "ruc_locador": g("ruc") or "________",
        "direccion": direccion or "________",
        "correo": correo or "________",
        "dias_contrato": str(dias_contrato) if dias_contrato else "________",
        "fecha_contrato_larga": _fecha_larga(g("fecha_contrato")) or "________",
        "fecha_fin_contrato_larga": _fecha_larga(g("fecha_fin_contrato")) or "________",
        "remuneracion": g("remuneracion") or "________",
        "remuneracion_letras": _monto_en_letras(g("remuneracion")) or "monto a completar por RR.HH.",
        "banco": g("banco_haberes") or "________",
        "num_cuenta": g("cuenta_haberes") or "________",
        "cci": g("cci_haberes") or "________",
        "cargo": g("cargo") or "________",
        "empresa_razon_social": g("empresa_razon_social") or "________",
        "empresa_ruc": g("empresa_ruc") or "________",
        "empresa_domicilio_fiscal": g("empresa_domicilio_fiscal") or "________",
        "empresa_correo_notificaciones": "trabajaconnosotros@digetelgroup.com",
        "representante_legal": g("representante_legal") or "________",
        "representante_tipo_documento": g("representante_tipo_documento") or "DNI",
        "representante_numero_documento": g("representante_numero_documento") or "________",
        "representante_nacionalidad": g("representante_nacionalidad") or "________",
    }
    merged = {**fields, **computed}

    intro = _fill(
        "Entre {empresa_razon_social}, con RUC N.° {empresa_ruc} (en lo sucesivo DIGETEL), domiciliada en "
        "{empresa_domicilio_fiscal}, representada por {representante_legal}, {representante_nacionalidad}, "
        "mayor de edad e identificado(a) con {representante_tipo_documento} N.° {representante_numero_documento}, "
        "por una parte, y por la otra {nombre_completo} (en lo sucesivo EL LOCADOR), mayor de edad, con "
        "domicilio en {direccion}, titular del {num_doc} y RUC {ruc_locador}, quienes actuando conjuntamente "
        "podrán denominarse LAS PARTES, han acordado celebrar el presente Contrato de Locación de Servicios, "
        "el cual se regirá por las cláusulas que siguen:",
        merged,
    )
    story = [_body_text(intro), Spacer(1, 2 * mm)]
    for nombre, texto in _CLAUSULAS_LOCACION_SERVICIOS:
        story.append(_clause(nombre, texto, merged))

    if fields.get("cargo_descripcion") or fields.get("cargo_funciones") or fields.get("cargo_responsabilidades"):
        story.append(Spacer(1, 4 * mm))
        story += _section_caption(f'ANEXO 1 — DESCRIPCIÓN Y ACUERDO DE SERVICIO: {merged["cargo"]}'.upper())
        if fields.get("cargo_descripcion"):
            story.append(_body_text(fields["cargo_descripcion"]))
        if fields.get("cargo_funciones"):
            story.append(_body_text("Servicios a prestar:"))
            for i, f in enumerate(fields["cargo_funciones"], start=1):
                story.append(_numbered_item(i, f))
        if fields.get("cargo_responsabilidades"):
            story.append(_body_text("Responsabilidades:"))
            for i, r in enumerate(fields["cargo_responsabilidades"], start=1):
                story.append(_numbered_item(i, r))

    spec = LEGAL_TEXTS["locacion_servicios"]
    return story, spec["cierre"], spec["titulo"], spec["subtitulo"]


# Convenio de Aprendizaje con Predominio en la Empresa (Régimen APE) —
# transcrito del modelo entregado por RR.HH. el 28/09. El Plan de
# Capacitación (competencias, evaluación, mapa de recorrido) es el mismo
# para todo aprendiz de "Asistente en Venta al Detalle" — es contenido fijo
# de RR.HH., no algo que varíe por persona (solo varían nombre, documento,
# fechas, cargo/especialidad y subvención, ver `computed` abajo). La
# Política de Subvención Adicional (con las tablas de "torres habilitadas")
# se adjunta como PDF estático aparte — ver ANEXO_POLITICA_SUBVENCION_APE.
ANEXO_POLITICA_SUBVENCION_APE = os.path.join(BASE_DIR, "static", "legal", "politica_subvencion_ape.pdf")

_CLAUSULAS_CONVENIO_APE = [
    ("Primero", "EL (LA) APRENDIZ manifiesta su interés y necesidad de efectuar sus actividades de aprendizaje "
     "en LA EMPRESA para los fines de obtener la certificación respectiva. Por su parte, LA EMPRESA acepta "
     "colaborar, tanto con el CENTRO DE FORMACIÓN PROFESIONAL como con EL (LA) APRENDIZ en esta tarea "
     "formativa."),
    ("Segundo", "EL (LA) APRENDIZ desempeñará las actividades formativas de {cargo} en el área de "
     "{area_formacion} en el domicilio de la empresa ubicado en {empresa_domicilio_fiscal}, de acuerdo con las "
     "condiciones generales señaladas en el Plan de Capacitación anexo."),
    ("Tercero", "Para efectos del presente convenio, LA EMPRESA se obliga a: brindar orientación y "
     "capacitación técnica y profesional a EL (LA) APRENDIZ dentro de su área de formación académica, así "
     "como evaluar su aprendizaje; designar a un supervisor para impartir la orientación correspondiente y "
     "verificar el desarrollo y cumplimiento del Plan Específico de Aprendizaje; emitir los informes que "
     "requiera el CENTRO DE FORMACIÓN PROFESIONAL; no cobrar suma alguna a EL (LA) APRENDIZ por la formación "
     "brindada; pagar puntualmente a EL (LA) APRENDIZ la subvención mensual convenida; otorgar una subvención "
     "adicional equivalente a media subvención económica mensual cada seis meses de duración continua del "
     "aprendizaje; otorgar un descanso de quince (15) días debidamente subvencionados cuando la duración del "
     "aprendizaje sea superior a doce (12) meses; cubrir los riesgos de enfermedad y accidentes de EL (LA) "
     "APRENDIZ a través de ESSALUD o de un seguro privado con cobertura equivalente a catorce (14) "
     "subvenciones mensuales en caso de enfermedad y treinta (30) por accidente; y expedir la certificación "
     "de aprendizaje correspondiente."),
    ("Cuarto", "Para efectos del presente convenio, EL (LA) APRENDIZ se obliga a: suscribir un convenio de "
     "aprendizaje con LA EMPRESA acatando las disposiciones formativas que se le asignen; desarrollar sus "
     "actividades de aprendizaje con disciplina y responsabilidad; cumplir con el desarrollo del Plan "
     "Específico de Aprendizaje que aplique LA EMPRESA; y sujetarse a las disposiciones administrativas "
     "internas que le señale LA EMPRESA."),
    ("Quinto", "Para efectos del presente convenio, EL CENTRO DE FORMACIÓN PROFESIONAL se obliga a: "
     "planificar y desarrollar los programas formativos que respondan a las necesidades del mercado laboral "
     "con participación del sector productivo; dirigir y conducir las actividades de formación de EL (LA) "
     "APRENDIZ en coordinación con la empresa; supervisar, evaluar y certificar las actividades formativas; y "
     "coordinar con la empresa el mecanismo de monitoreo y supervisión de las actividades del APRENDIZ."),
    ("Sexto", "LA EMPRESA ha contratado el seguro de FOLA para cubrir los riesgos de enfermedad y accidentes "
     "de EL (LA) APRENDIZ."),
    ("Séptimo", "LA EMPRESA concederá a EL (LA) APRENDIZ una subvención económica mensual de S/ "
     "{subvencion_economica}. De conformidad con el artículo 47.° de la Ley N.° 28518, esta subvención "
     "económica mensual no tiene carácter remunerativo y no está afecta al pago del Impuesto a la Renta, "
     "otros impuestos, contribuciones ni aportaciones de ningún tipo a cargo de LA EMPRESA. La subvención "
     "económica mensual no está sujeta a ningún tipo de retención a cargo de EL (LA) APRENDIZ, salvo "
     "afiliación facultativa por parte de éste a un sistema pensionario. Adicionalmente, LA EMPRESA otorgará "
     "una Asignación por Movilidad mensual, conforme a la Política de Subvención Adicional para Aprendices "
     "que se adjunta como anexo."),
    ("Octavo", "Las partes acuerdan la aplicación de las causas de modificación, suspensión y terminación del "
     "convenio: es causa de modificación, el acuerdo entre EL (LA) APRENDIZ, LA EMPRESA y EL CENTRO DE "
     "FORMACIÓN PROFESIONAL. Son causas de suspensión: la enfermedad y el accidente comprobados; el descanso "
     "físico subvencionado en caso de prórroga a un plazo mayor de doce meses; el permiso concedido por la "
     "empresa; la sanción disciplinaria; y el caso fortuito o fuerza mayor. Son causas de terminación: el "
     "cumplimiento del plazo estipulado; el mutuo disenso entre EL (LA) APRENDIZ y LA EMPRESA; el "
     "fallecimiento de EL (LA) APRENDIZ; la invalidez absoluta permanente; no guardar reserva de la "
     "información conocida durante la práctica; el incumplimiento de las obligaciones de EL (LA) APRENDIZ o "
     "del CENTRO DE FORMACIÓN PROFESIONAL señaladas en las cláusulas Cuarto y Quinto; y la renuncia o retiro "
     "voluntario de EL (LA) APRENDIZ, mediante aviso a LA EMPRESA con antelación de diez (10) días hábiles."),
    ("Noveno", "EL (LA) APRENDIZ declara conocer la naturaleza del presente convenio, el cual no tiene "
     "carácter laboral, de tal modo que sólo genera para las partes los derechos y obligaciones "
     "específicamente previstos en el mismo y en el texto de la Ley N.° 28518 y el Decreto Supremo N.° "
     "007-2005-TR."),
    ("Décimo", "Para todos los efectos relacionados con el presente convenio, las partes señalan como su "
     "domicilio el consignado en la parte introductoria de éste, los cuales se tendrán por válidos en tanto la "
     "variación no haya sido comunicada por escrito a la otra parte. Las partes, después de leído el presente "
     "convenio, se ratifican en su contenido y lo suscriben en señal de conformidad, quedando el cuarto "
     "ejemplar puesto en conocimiento y registrado ante la Autoridad Administrativa de Trabajo dentro de los "
     "quince (15) días naturales de la suscripción."),
]


def _doc_convenio_ape(fields):
    """Convenio de Aprendizaje con Predominio en la Empresa — Régimen APE.
    Modelo entregado por RR.HH. el 28/09 (CONVENIO - PLAN FORMATIVO Y
    POLITICA ape.docx). El Centro de Formación Profesional (CETPRO WORLDNET)
    es fijo — es el único con el que Digetel tiene convenio institucional."""
    g = fields.get
    tipo_doc = g("tipo_documento") or "DNI"
    numero_doc = g("numero_documento") or ""
    direccion = ", ".join(x for x in [g("direccion"), g("distrito"), g("provincia"), g("departamento")] if x)
    dias_contrato = g("dias_contrato") or ""

    computed = {
        "num_doc": f"{tipo_doc} N.° {numero_doc}" if numero_doc else "________",
        "direccion": direccion or "________",
        "nacionalidad": g("nacionalidad") or "________",
        "fecha_nacimiento_larga": _fecha_larga(g("fecha_nacimiento")) or "________",
        "sexo": g("sexo") or "________",
        "cargo": g("cargo") or "________",
        "area_formacion": g("area") or g("cargo") or "________",
        "dias_contrato": str(dias_contrato) if dias_contrato else "________",
        "fecha_contrato_larga": _fecha_larga(g("fecha_contrato")) or "________",
        "fecha_fin_contrato_larga": _fecha_larga(g("fecha_fin_contrato")) or "________",
        "horario": g("horario") or "________",
        "subvencion_economica": g("remuneracion") or "________",
        "empresa_razon_social": g("empresa_razon_social") or "________",
        "empresa_ruc": g("empresa_ruc") or "________",
        "empresa_domicilio_fiscal": g("empresa_domicilio_fiscal") or "________",
    }
    merged = {**fields, **computed}

    intro = _fill(
        "Conste por el presente documento que se firma por triplicado, el Convenio de Aprendizaje con "
        "Predominio en la Empresa, celebrado de conformidad con el artículo 11.° y siguientes de la Ley N.° "
        "28518, Ley sobre Modalidades Formativas Laborales, y su Reglamento aprobado mediante Decreto Supremo "
        "N.° 007-2005-TR, que se celebra entre {empresa_razon_social}, con RUC N.° {empresa_ruc}, domiciliada "
        "en {empresa_domicilio_fiscal} (en adelante, LA EMPRESA), el Centro de Educación Técnico-Productiva "
        "WORLDNET (CETPRO \"WORLDNET\"), representado por INNOVACIÓN Y FORMACIÓN DE TALENTO S.A.C., RUC N.° "
        "20611357088, con domicilio en Jr. Cusco 204, Chupaca (en adelante, EL CENTRO DE FORMACIÓN "
        "PROFESIONAL), y {nombre_completo}, identificado(a) con {num_doc}, de nacionalidad {nacionalidad}, "
        "nacido(a) el {fecha_nacimiento_larga}, sexo {sexo}, con domicilio en {direccion} (en adelante, EL/LA "
        "APRENDIZ), de acuerdo a los términos y condiciones siguientes:",
        merged,
    )
    story = [_body_text(intro), Spacer(1, 2 * mm)]
    for nombre, texto in _CLAUSULAS_CONVENIO_APE:
        story.append(_clause(nombre, texto, merged))

    story.append(Spacer(1, 4 * mm))
    story += _section_caption("ANEXO — PLAN DE CAPACITACIÓN")
    story.append(_field_table([
        ("Ocupación / especialidad", merged["cargo"], "Duración", f'{merged["dias_contrato"]} días'),
        ("Inicio", merged["fecha_contrato_larga"], "Término", merged["fecha_fin_contrato_larga"]),
    ]))
    story.append(_body_text(
        "Objetivos que debe lograr el/la beneficiario(a) al término de su formación: complementar la "
        "formación específica adquirida en el Centro de Formación, y consolidar el desarrollo de habilidades "
        "sociales y personales relacionadas al ámbito laboral."
    ))
    story.append(_body_text("Función principal del puesto: orientación y asistencia en venta al detalle; "
                             "asesoría y consultoría de servicios y/o productos; satisfacción de necesidades "
                             "específicas de los clientes; conocimiento de las ofertas, promociones y demás "
                             "productos ofrecidos.", italic=True))
    story.append(_body_text("Competencias específicas: conocer y aplicar los sistemas de atención y servicio "
                             "al cliente; compromiso de aseguramiento de la calidad y mejora continua; "
                             "capacidad de manejo de los productos y servicios que se ofrecen; manejo de "
                             "técnicas de promoción.", italic=True))
    story.append(_body_text("Competencias genéricas/transversales: comunicación interpersonal fluida; "
                             "desarrollo integral humano y manejo de emociones; trabajo en equipo y respeto a "
                             "las normas; autoestima y respeto por sí mismo y por los demás.", italic=True))
    story.append(_body_text(
        "El detalle completo de criterios de evaluación, hitos de práctica mensual y la Política de "
        "Subvención Adicional para Aprendices (incluida la Asignación por Movilidad) se adjuntan como anexo "
        "aparte a este documento.",
    ))

    spec = LEGAL_TEXTS["convenio_ape"]
    return story, spec["cierre"], spec["titulo"], spec["subtitulo"]


def generar_contrato_por_regimen(fields):
    """Punto 2 de "Contratos y Renovaciones" (28/09): decide qué documento
    generar según el Régimen Laboral de la ficha — Régimen General/MYPE
    (contrato de trabajo), RHE (locación de servicios) o APE (convenio de
    aprendizaje). Devuelve (story, cierre, titulo, subtitulo, anexo_pdf) —
    `anexo_pdf` es la ruta de un PDF estático a adjuntar además del firmado
    (solo APE lo usa; None en los demás casos)."""
    regimen = (fields.get("regimen_laboral_persona") or "").strip().upper()
    if regimen.startswith("RHE"):
        story, cierre, titulo, subtitulo = _doc_locacion_servicios(fields)
        return story, cierre, titulo, subtitulo, None
    if regimen.startswith("APE"):
        story, cierre, titulo, subtitulo = _doc_convenio_ape(fields)
        return story, cierre, titulo, subtitulo, ANEXO_POLITICA_SUBVENCION_APE
    story, cierre, titulo, subtitulo = _doc_contrato(fields)
    return story, cierre, titulo, subtitulo, None


_SALUD_ANTECEDENTES_FAMILIARES = [
    ("antecedente_fam_cancer", "Cáncer"), ("antecedente_fam_diabetes", "Diabetes"),
    ("antecedente_fam_cardiaco", "Problemas Cardiacos"), ("antecedente_fam_hipertension", "Hipertensión"),
]
_SALUD_ANTECEDENTES_PERSONALES = [
    ("antecedente_personal_sarampion", "Sarampión"), ("antecedente_personal_paperas", "Paperas"),
    ("antecedente_personal_rubeola", "Rubéola"), ("antecedente_personal_neumonia", "Neumonía"),
    ("antecedente_personal_epilepsia", "Epilepsia"), ("antecedente_personal_tuberculosis", "Tuberculosis"),
    ("antecedente_personal_perdida_memoria", "Pérdida de Memoria"), ("antecedente_personal_tos_cronica", "Tos Crónica"),
    ("antecedente_personal_cefaleas", "Cefaleas Prolongadas"), ("antecedente_personal_hemorragias", "Hemorragias"),
    ("antecedente_personal_hepatitis", "Hepatitis"), ("antecedente_personal_gastritis", "Gastritis"),
    ("antecedente_personal_asma", "Asma"), ("antecedente_personal_ulceras", "Úlceras"),
]
_SALUD_SISTEMAS = [
    ("salud_sistema_nervioso", "Sistema Nervioso"), ("salud_sistema_respiratorio", "Sistema Respiratorio"),
    ("salud_sistema_circulatorio", "Corazón / Sangre / Sist. Circulatorio"), ("salud_sistema_digestivo", "Sistema Digestivo"),
    ("salud_sistema_endocrino", "Enfermedades Endocrinas"), ("salud_sistema_oseo_muscular", "Óseas o Musculares"),
    ("salud_sistema_piel", "Enfermedades de la Piel"),
]


def _doc_declaracion_salud(fields):
    story, cierre, titulo, subtitulo = _legal_body("declaracion_salud", fields)
    g = fields.get

    story += _section_caption("Antecedentes Familiares")
    story.append(_grid_table(
        ["Enfermedad", "¿Sí/No?"], [4, 1],
        [[label, g(campo) or "—"] for campo, label in _SALUD_ANTECEDENTES_FAMILIARES],
    ))
    if g("antecedente_fam_otras"):
        story.append(_body_text(f"Otras: {g('antecedente_fam_otras')}"))

    story += _section_caption("Antecedentes Personales (infancia)")
    story.append(_grid_table(
        ["Afección", "¿Sí/No?"], [4, 1],
        [[label, g(campo) or "—"] for campo, label in _SALUD_ANTECEDENTES_PERSONALES],
    ))

    story += _section_caption("Hábitos")
    story.append(_field_table([
        ["Fuma", g("salud_fuma"), "Bebe Alcohol", g("salud_bebe_alcohol")],
        ["Cantidad (si bebe)", g("salud_alcohol_cantidad"), "", ""],
    ]))

    story += _section_caption("¿Padece actualmente o ha sido tratado por…?")
    story.append(_grid_table(
        ["Sistema", "¿Sí/No?", "Especificar"], [2.2, 0.8, 3],
        [[label, g(campo) or "—", g(f"{campo}_detalle") or "—"] for campo, label in _SALUD_SISTEMAS],
    ))

    story += _section_caption("Otros antecedentes")
    story.append(_field_table([
        ["¿Bajo tratamiento actualmente?", g("salud_bajo_tratamiento"), "Especificar", g("salud_bajo_tratamiento_detalle")],
        ["¿Cambio significativo de peso?", g("salud_cambio_peso"), "", ""],
        ["¿Intervención quirúrgica?", g("salud_cirugia"), "Diagnóstico y fecha", g("salud_cirugia_detalle")],
        ["¿Otra enfermedad no mencionada?", g("salud_otra_enfermedad"), "Especificar", g("salud_otra_enfermedad_detalle")],
    ]))
    return story, cierre, titulo, subtitulo


# Boletín Informativo SPP/SNP — contenido sustantivo (Ley 29903 y normas
# conexas) condensado del boletín oficial de 8 páginas que RR.HH. entregó el
# 16/09, para que el documento firmado quede en un tamaño manejable sin
# perder ninguno de los puntos legalmente relevantes para la decisión del
# trabajador.
_BOLETIN_PENSIONARIO = [
    ("¿Entre qué sistemas debe elegir?", "El Sistema Privado de Pensiones (SPP), a cargo de una AFP, funciona "
     "con una Cuenta Individual de Capitalización (CIC): la pensión depende de los aportes y la rentabilidad "
     "acumulada. El Sistema Nacional de Pensiones (SNP), administrado por la ONP, funciona con un fondo común: "
     "la pensión depende de los años de aportación y del promedio de las remuneraciones de los últimos meses."),
    ("Plazo para decidir", "El trabajador tiene diez (10) días calendario desde la entrega del boletín "
     "informativo para elegir SPP o SNP, con diez (10) días adicionales para cambiar de decisión. Vencido el "
     "plazo sin elección, el empleador debe afiliarlo de oficio a la AFP que cobre la menor comisión."),
    ("Reversibilidad", "Si se afilia al SPP, ya no podrá regresar al SNP — es una decisión irreversible. Si se "
     "afilia al SNP, puede eventualmente migrar al SPP más adelante."),
    ("Aportes mensuales", "En el SPP, el trabajador aporta 10% de su remuneración asegurable a su cuenta "
     "individual, más un porcentaje para el seguro de invalidez/sobrevivencia/sepelio, más la comisión de la "
     "AFP. En el SNP, el trabajador aporta 13% de su remuneración mensual, monto que ya incluye los gastos "
     "administrativos del sistema."),
    ("Beneficios", "Ambos sistemas cubren pensión de jubilación, invalidez y sobrevivencia (viudez, orfandad y, "
     "en algunos casos, ascendientes), además de gastos de sepelio (SPP) o capital de defunción (SNP)."),
    ("Tope de pensión", "En el SPP no existe un tope — la pensión depende de lo acumulado en la cuenta "
     "individual. En el SNP, la pensión máxima está fijada por el Estado (S/. 857.36 a la fecha del boletín)."),
    ("Edad de jubilación", "En ambos sistemas la jubilación se alcanza normalmente a los 65 años, aunque "
     "existen regímenes de jubilación adelantada o anticipada bajo ciertas condiciones de años de aporte."),
    ("Pensión mínima", "El Estado garantiza una pensión mínima en ambos sistemas para quien cumpla los "
     "requisitos de cada uno (65 años de edad y, en el SNP, 20 años de aportación)."),
]


def _doc_sistema_pensionario(fields):
    story, cierre, titulo, subtitulo = _legal_body("sistema_pensionario", fields)
    g = fields.get

    story += _section_caption("Boletín Informativo — Sistema Privado (SPP) vs. Sistema Nacional (SNP) de Pensiones")
    for pregunta, respuesta in _BOLETIN_PENSIONARIO:
        story.append(_body_text(f"{pregunta} {respuesta}"))

    story += _section_caption("Formato de Elección del Sistema Pensionario")
    story.append(_field_table([
        ["Trabajador", g("nombre_completo"), "Documento", g("num_doc") or g("numero_documento")],
        ["Sexo", g("sexo"), "Fecha de Nacimiento", g("fecha_nacimiento")],
        ["Domicilio", g("direccion"), "Distrito / Provincia / Departamento",
         ", ".join(x for x in [g("distrito"), g("provincia"), g("departamento")] if x)],
        ["Empleador", g("empresa_razon_social") or g("empresa"), "RUC", g("empresa_ruc")],
        ["Fecha de Inicio del Vínculo Laboral", g("fecha_ingreso") or g("fecha_contrato"), "Remuneración (S/)", g("remuneracion")],
    ]))
    sistema = g("sistema_pension") or ""
    detalle_sistema = f"{sistema} — {g('afp')}" if sistema == "AFP" and g("afp") else sistema
    story.append(_body_text(f"Sistema elegido: {detalle_sistema or '(pendiente de elección)'}"))

    story += _section_caption("Constancia de Entrega del Boletín Informativo")
    story.append(_body_text(
        "El trabajador deja constancia de haber recibido de su empleador el Boletín Informativo acerca de las "
        "características del SPP y del SNP, así como el Formato de Elección del Sistema Pensionario, y de "
        "conocer que, de no manifestar su decisión dentro del plazo de diez (10) días calendario contados "
        "desde la entrega de este documento, será afiliado de oficio al Sistema Privado de Pensiones bajo las "
        "condiciones indicadas en el boletín."
    ))
    return story, cierre, titulo, subtitulo


def _build_doc(doc_type, fields):
    if doc_type == "ficha":
        return _doc_ficha(fields)
    if doc_type == "derechohabientes":
        return _doc_derechohabientes(fields)
    if doc_type == "autorizacion_deposito":
        return _doc_autorizacion_deposito(fields)
    if doc_type == "declaracion_salud":
        return _doc_declaracion_salud(fields)
    if doc_type == "sistema_pensionario":
        return _doc_sistema_pensionario(fields)
    if doc_type == "contrato":
        # El contrato/convenio ya no vive en DOC_TYPES (28/09) — este
        # doc_type ahora solo lo usa la ruta standalone /c/{token} (ver
        # main.py), que arma el documento correcto según Régimen Laboral.
        story, cierre, titulo, subtitulo, _anexo = generar_contrato_por_regimen(fields)
        return story, cierre, titulo, subtitulo
    return _legal_body(doc_type, fields)  # declaracion_jurada, autorizacion_datos


# ---------------------------------------------------------------------------
# Footer con "Página X de Y" (necesita dos pasadas: reportlab solo sabe el
# total de páginas al terminar de armar el documento).
# ---------------------------------------------------------------------------
class _NumberedCanvas(pdfcanvas.Canvas):
    def __init__(self, *args, **kwargs):
        pdfcanvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(total_pages)
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)

    def _draw_footer(self, total_pages):
        line_y = MARGIN + 6 * mm
        text_y = MARGIN + 1.5 * mm
        self.setStrokeColor(LINE_GRAY)
        self.setLineWidth(0.6)
        self.line(MARGIN, line_y, PAGE_SIZE[0] - MARGIN, line_y)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(GRAY)
        self.drawString(MARGIN, text_y, FOOTER_TEXT)
        self.drawRightString(PAGE_SIZE[0] - MARGIN, text_y,
                              f"Página {self._pageNumber} de {total_pages}")


def build_pdf(doc_type: str, fields: dict, signature_image_path: str,
              signed_at: str, ip: str, hash_: str, out_path: str,
              empresa_nombre: str = None, representante_legal: str = None,
              firma_empresa_path: str = None) -> str:
    """Genera el PDF final del documento firmado en `out_path`. No depende de
    Word, LibreOffice ni Node.js. Si la empresa tiene representante legal y
    firma cargados (Parametrización > Empresas), se agrega su firma debajo de
    la del trabajador."""
    story, cierre, titulo, subtitulo = _build_doc(doc_type, fields)
    full_story = _header_flowables(titulo, subtitulo) + story
    if cierre:
        full_story.append(Spacer(1, 4 * mm))
        full_story.append(_body_text(cierre, italic=True))
    full_story.append(Spacer(1, 6 * mm))
    full_story.append(_signed_block(
        nombre=fields.get("nombre_completo"), dni=fields.get("num_doc"),
        fecha_hora=signed_at, ip=ip, hash_=hash_,
        signature_image_path=signature_image_path,
    ))
    if representante_legal and firma_empresa_path:
        full_story.append(Spacer(1, 6 * mm))
        full_story.append(_company_signature_block(empresa_nombre, representante_legal, firma_empresa_path))

    doc = SimpleDocTemplate(
        out_path, pagesize=PAGE_SIZE,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
        bottomMargin=MARGIN + FOOTER_RESERVE, title=titulo,
    )
    doc.build(full_story, canvasmaker=_NumberedCanvas)
    return out_path


def build_no_renovacion_pdf(*, nombre_completo: str, tipo_documento: str, numero_documento: str,
                             cargo: str, tipo_contrato: str, fecha_fin_contrato: str,
                             fecha_emision: str, empresa_nombre: str, representante_legal: str = None,
                             firma_empresa_path: str = None, out_path: str = None) -> str:
    """Punto 14 del pedido: modelo inicial de carta de aviso de no renovación
    de contrato — se genera cuando el gerente rechaza una solicitud de
    renovación. Es un primer borrador (mismo estilo visual que el resto del
    legajo); ajustar el texto legal cuando RR.HH. confirme el formato
    definitivo que van a usar."""
    doc_doc = f"{tipo_documento or 'documento'} N.° {numero_documento or '—'}"
    cuerpo = (
        f"Por medio de la presente, {empresa_nombre or 'la empresa'} comunica a {nombre_completo}, "
        f"identificado(a) con {doc_doc}, que su contrato de trabajo"
        + (f" en el cargo de {cargo}" if cargo else "")
        + (f", bajo la modalidad de {tipo_contrato}," if tipo_contrato else ",")
        + f" con fecha de vencimiento el {fecha_fin_contrato}, no será renovado."
    )
    cuerpo2 = (
        "La relación laboral concluirá en la fecha antes indicada. Agradecemos los servicios prestados "
        "durante su permanencia en la empresa y quedamos atentos para coordinar el proceso de liquidación "
        "de beneficios sociales conforme a ley."
    )
    story = _header_flowables("Aviso de No Renovación de Contrato", empresa_nombre or "")
    story.append(Paragraph(_esc(f"Fecha de emisión: {fecha_emision}"), _style_subtitle))
    story.append(Spacer(1, 5 * mm))
    story.append(_body_text(cuerpo))
    story.append(_body_text(cuerpo2))
    story.append(Spacer(1, 10 * mm))
    if representante_legal and firma_empresa_path:
        story.append(_company_signature_block(empresa_nombre, representante_legal, firma_empresa_path))
    else:
        story.append(Paragraph("_______________________________", _style_sig_meta))
        story.append(Paragraph(f"Por {_esc(empresa_nombre or '')}", _style_sig_meta))

    doc = SimpleDocTemplate(
        out_path, pagesize=PAGE_SIZE,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN,
        bottomMargin=MARGIN + FOOTER_RESERVE, title="Aviso de No Renovación de Contrato",
    )
    doc.build(story, canvasmaker=_NumberedCanvas)
    return out_path
