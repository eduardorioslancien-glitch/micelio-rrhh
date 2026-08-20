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
        story.append(_numbered_item(i, item))
    if spec.get("nota_legal"):
        story.append(_body_text(spec["nota_legal"], italic=True))
    return story, spec.get("cierre"), spec["titulo"], spec["subtitulo"]


def _doc_ficha(fields):
    spec = LEGAL_TEXTS["ficha"]
    story = [_body_text(_fill(spec["intro"], fields))]
    g = fields.get

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
        ["Teléfono Fijo", g("telefono_fijo"), "Celular", g("celular")],
        ["Correo Personal", g("correo_personal"), "Correo Corporativo", g("correo_corporativo")],
        ["N.° de Licencia", g("licencia_numero"), "Tipo / Vencimiento",
         " — ".join([x for x in [g("licencia_tipo"), g("licencia_vencimiento")] if x])],
    ]))

    familia = fields.get("familia") or []
    story += _section_caption("II. Información Familiar")
    if familia:
        story.append(_grid_table(
            ["Parentesco", "Nombre", "DNI", "Fecha Nac.", "Depende Econ.", "EsSalud"],
            [1700, 3600, 1600, 1600, 1400, 1300],
            [[f.get("parentesco"), f.get("nombre"), f.get("dni"), f.get("fecha_nacimiento"),
              "Sí" if f.get("depende_economicamente") else "No",
              "Sí" if f.get("derechohabiente_essalud") else "No"] for f in familia],
        ))
    else:
        story.append(_body_text("El trabajador declara no tener familiares que registrar a la fecha.", italic=True))

    story += _section_caption("III. Contactos de Emergencia")
    story.append(_field_table([
        ["Contacto 1 — Nombre", g("emerg1_nombre"), "Parentesco", g("emerg1_parentesco")],
        ["Celular", g("emerg1_celular"), "Dirección", g("emerg1_direccion")],
        ["Contacto 2 — Nombre", g("emerg2_nombre"), "Parentesco", g("emerg2_parentesco")],
        ["Celular", g("emerg2_celular"), "Dirección", g("emerg2_direccion")],
    ]))

    story += _section_caption("IV. Datos Laborales")
    story.append(_field_table([
        ["Código", g("lab_codigo"), "Área", g("area")],
        ["Gerencia", g("gerencia"), "Cargo", g("cargo")],
        ["Sede", g("sede"), "Centro de Costos", g("centro_costos")],
        ["Jefe Inmediato", g("jefe_inmediato"), "Fecha de Ingreso", g("fecha_ingreso")],
        ["Fecha de Contrato", g("fecha_contrato"), "Fecha de Vencimiento", g("fecha_fin_contrato")],
        ["Tipo de Contrato", g("tipo_contrato"), "Modalidad", g("modalidad")],
        ["Horario", g("horario"), "Jornada", g("jornada")],
        ["Turno", g("turno"), "Grupo Ocupacional", g("grupo_ocupacional")],
        ["Asignación Familiar", g("asignacion_familiar"), "", ""],
        ["Remuneración (S/)", g("remuneracion"), "Bonificaciones (S/)", g("bonificaciones")],
    ]))

    story += _section_caption("V. Información Bancaria")
    story.append(_field_table([
        ["Banco (Haberes)", g("banco_haberes"), "Cuenta (Haberes)", g("cuenta_haberes")],
        ["CCI (Haberes)", g("cci_haberes"), "Banco CTS", g("banco_cts")],
        ["Cuenta CTS", g("cuenta_cts"), "CCI CTS", g("cci_cts")],
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
        story += _section_caption("Derechohabientes declarados para EsSalud")
        story.append(_grid_table(
            ["Parentesco", "Nombre", "DNI", "Fecha de Nacimiento"],
            [2200, 4200, 2200, 2300],
            [[d.get("parentesco"), d.get("nombre"), d.get("dni"), d.get("fecha_nacimiento")]
             for d in dependientes],
        ))
    else:
        story.append(_body_text(
            "El trabajador declara no tener derechohabientes que registrar a la fecha.", italic=True,
        ))
    return story, cierre, titulo, subtitulo


def _doc_autorizacion_deposito(fields):
    story, cierre, titulo, subtitulo = _legal_body("autorizacion_deposito", fields)
    story += _section_caption("I. Depósito de Haberes (Remuneración Mensual)")
    story.append(_field_table([
        ["Banco", fields.get("banco"), "N.° de Cuenta", fields.get("num_cuenta")],
        ["CCI", fields.get("cci"), "", ""],
    ]))
    story += _section_caption("II. Depósito de CTS")
    story.append(_field_table([
        ["Entidad Depositaria (Banco)", fields.get("banco_cts"), "N.° de Cuenta CTS", fields.get("cuenta_cts")],
    ]))
    return story, cierre, titulo, subtitulo


def _build_doc(doc_type, fields):
    if doc_type == "ficha":
        return _doc_ficha(fields)
    if doc_type == "derechohabientes":
        return _doc_derechohabientes(fields)
    if doc_type == "autorizacion_deposito":
        return _doc_autorizacion_deposito(fields)
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
