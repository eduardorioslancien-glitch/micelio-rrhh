# -*- coding: utf-8 -*-
"""Análisis de CV de postulantes ("Trabaja con Nosotros") contra los
requisitos de un Cargo, usando Claude (Anthropic). Punto 6.3 del pedido:
calificación preliminar de compatibilidad en estrellas (1-5) con una breve
explicación, para que RR.HH. pueda priorizar a quién revisar primero.

Degrada con gracia si falta ANTHROPIC_API_KEY o si algo falla: no bloquea
el registro de la postulación, solo deja sin calificar al candidato."""
import json
import os

from .models import Cargo

MODEL = "claude-sonnet-5"


def extraer_texto_cv(ruta_archivo: str, content_type: str) -> str:
    """Extrae texto de un CV en PDF o Word. Devuelve "" si no se pudo leer."""
    try:
        nombre = (ruta_archivo or "").lower()
        if nombre.endswith(".pdf") or "pdf" in (content_type or ""):
            from pypdf import PdfReader
            reader = PdfReader(ruta_archivo)
            return "\n".join((p.extract_text() or "") for p in reader.pages).strip()
        if nombre.endswith(".docx") or "wordprocessingml" in (content_type or ""):
            import docx
            doc = docx.Document(ruta_archivo)
            return "\n".join(p.text for p in doc.paragraphs).strip()
    except Exception:
        return ""
    return ""


def _requisitos_cargo_texto(cargo: Cargo) -> str:
    partes = [f"Cargo: {cargo.nombre}"]
    if cargo.descripcion:
        partes.append(f"Descripción del puesto: {cargo.descripcion}")
    if cargo.funciones:
        partes.append("Funciones:\n" + "\n".join(f"- {f}" for f in cargo.funciones))
    if cargo.requisito_academico:
        partes.append(f"Formación académica requerida: {cargo.requisito_academico}")
    if cargo.requisito_experiencia:
        partes.append(f"Experiencia requerida: {cargo.requisito_experiencia}")
    if cargo.requisito_conocimientos:
        partes.append(f"Conocimientos requeridos: {cargo.requisito_conocimientos}")
    if cargo.requisitos_competencias:
        comp_txt = ", ".join(
            f"{r.competencia.nombre} (nivel {r.nivel_requerido}/4)"
            for r in cargo.requisitos_competencias if r.competencia
        )
        if comp_txt:
            partes.append(f"Competencias/valores requeridos: {comp_txt}")
    return "\n\n".join(partes)


def analizar_cv(texto_cv: str, cargo: Cargo):
    """Devuelve (estrellas: int|None, analisis: str). Si falta la API key o
    algo falla, devuelve (None, mensaje explicando por qué no hay análisis)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, "Análisis automático no disponible: falta configurar ANTHROPIC_API_KEY en el servidor."
    if not texto_cv or not texto_cv.strip():
        return None, "No se pudo extraer texto del CV adjunto (¿es una imagen escaneada?) — revísalo manualmente."

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            "Eres un asistente de Recursos Humanos. Compara el siguiente CV contra los "
            "requisitos de un cargo y califica qué tan compatible es el candidato, del 1 al 5 "
            "(1 = muy poco compatible, 5 = muy compatible), considerando estudios, experiencia, "
            "habilidades/conocimientos y cualquier indicio de valores/competencias relevantes. "
            "Responde ÚNICAMENTE con un JSON válido de la forma "
            '{"estrellas": <entero 1-5>, "analisis": "<2-4 líneas explicando el motivo, en español, tono profesional>"}.'
            f"\n\n--- REQUISITOS DEL CARGO ---\n{_requisitos_cargo_texto(cargo)}"
            f"\n\n--- CV DEL POSTULANTE ---\n{texto_cv[:12000]}"
        )
        respuesta = client.messages.create(
            model=MODEL, max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        texto = "".join(b.text for b in respuesta.content if hasattr(b, "text"))
        inicio, fin = texto.find("{"), texto.rfind("}")
        data = json.loads(texto[inicio:fin + 1])
        estrellas = int(data.get("estrellas", 0))
        estrellas = max(1, min(5, estrellas))
        return estrellas, data.get("analisis", "").strip()
    except Exception as exc:
        return None, f"El análisis automático falló ({exc.__class__.__name__}) — revisa el CV manualmente."
