# -*- coding: utf-8 -*-
"""Interpretación legible del resultado DISC (14/10/2026).

El cálculo (porcentajes D/I/S/C) vive en reclutamiento._calcular_disc. Acá solo
se traduce ese resultado a lenguaje de RR.HH.: qué significa cada dimensión en
su nivel (alto/medio/bajo), fortalezas, puntos a vigilar, cómo conversar con
esa persona y en qué entornos suele rendir mejor. Con cuatro dimensiones que
suman 100 %, el promedio es 25 %: ≥30 % es alto, <20 % es bajo."""

UMBRAL_ALTO = 30.0
UMBRAL_BAJO = 20.0
DIFERENCIA_COMBINADO = 5.0

DIMENSIONES = {
    "D": {
        "nombre": "Dominancia",
        "lema": "Cómo enfrenta los retos y decide",
        "alto": "Decide rápido, asume el control y va directo al resultado. Le motivan los retos y la competencia.",
        "medio": "Combina iniciativa con cautela: decide cuando hace falta, sin imponerse ni esperar siempre indicaciones.",
        "bajo": "Prefiere colaborar y evitar la confrontación; suele esperar consenso o indicaciones antes de decidir.",
        "fortalezas": "Decisión, iniciativa, orientación a metas, resolución bajo presión.",
        "vigilar": "Impaciencia, poca escucha, choques con quienes avanzan más lento.",
        "comunicacion": "Ser breve y directo: resultados esperados, plazos y qué decisión le corresponde.",
        "entornos": "Metas exigentes, ventas con objetivos, roles que piden decidir rápido.",
    },
    "I": {
        "nombre": "Influencia",
        "lema": "Cómo se relaciona y persuade",
        "alto": "Sociable y entusiasta: contagia energía al grupo y persuade con facilidad.",
        "medio": "Se relaciona bien sin necesitar ser el centro; equilibra la conversación con la tarea.",
        "bajo": "Reservado en lo social: prefiere hechos y trabajo individual antes que persuadir o hablar en público.",
        "fortalezas": "Comunicación, optimismo, trato con clientes, capacidad de motivar.",
        "vigilar": "Dispersión, poca atención al seguimiento y a los detalles administrativos.",
        "comunicacion": "Dar espacio para conversar, reconocer sus ideas y mantener un trato cercano.",
        "entornos": "Atención al cliente, ventas, trabajo en equipo, roles con mucho contacto.",
    },
    "S": {
        "nombre": "Estabilidad",
        "lema": "Cómo enfrenta el ritmo y el cambio",
        "alto": "Paciente, constante y buen escucha: valora la estabilidad y la armonía del equipo.",
        "medio": "Se adapta al cambio con calma, sin dejar de valorar la rutina.",
        "bajo": "Cómodo con el cambio y el ritmo acelerado; la rutina y la repetición lo desmotivan.",
        "fortalezas": "Constancia, lealtad, calma bajo presión, trabajo en equipo.",
        "vigilar": "Resistencia a cambios bruscos, dificultad para decir que no o para expresar desacuerdos.",
        "comunicacion": "Ritmo tranquilo y explicar el porqué de los cambios con anticipación.",
        "entornos": "Procesos estables, soporte, atención sostenida, equipos consolidados.",
    },
    "C": {
        "nombre": "Conciencia (Cautela)",
        "lema": "Cómo cuida la calidad y las reglas",
        "alto": "Analítico y exigente: revisa los detalles, sigue procedimientos y cuida la calidad.",
        "medio": "Cuida el detalle cuando importa, sin frenarse por perfeccionismo.",
        "bajo": "Práctico y flexible con las normas; prefiere actuar antes que analizar en profundidad.",
        "fortalezas": "Precisión, análisis, cumplimiento de estándares, planificación.",
        "vigilar": "Lentitud para decidir, perfeccionismo, exceso de crítica o de verificación.",
        "comunicacion": "Datos claros, tiempo para analizar y expectativas por escrito.",
        "entornos": "Control de calidad, administración, finanzas, procesos regulados.",
    },
}

CAUTELA = ("Es una referencia simplificada de 20 afirmaciones, no un test psicométrico validado. "
           "Úsala para conversar en la entrevista y contrastarla con ejemplos concretos; no para decidir por sí sola. "
           "Ningún perfil es mejor que otro: cada cargo pide combinaciones distintas.")


def _nivel(pct: float) -> str:
    if pct >= UMBRAL_ALTO:
        return "alto"
    if pct < UMBRAL_BAJO:
        return "bajo"
    return "medio"


def interpretar(resultado) -> dict | None:
    """resultado: {"D":pct,"I":pct,"S":pct,"C":pct,"perfil_dominante":"D"} → dict para la plantilla."""
    if not resultado:
        return None
    try:
        pcts = {k: float(resultado.get(k) or 0) for k in "DISC"}
    except (TypeError, ValueError):
        return None
    if not any(pcts.values()):
        return None
    orden = sorted(pcts, key=lambda k: pcts[k], reverse=True)
    primario, segundo = orden[0], orden[1]
    combinado = (pcts[primario] - pcts[segundo]) <= DIFERENCIA_COMBINADO
    dimensiones = []
    for k in "DISC":
        info = DIMENSIONES[k]
        nivel = _nivel(pcts[k])
        dimensiones.append({
            "clave": k, "nombre": info["nombre"], "lema": info["lema"], "pct": round(pcts[k], 1),
            "nivel": nivel.capitalize(), "nivel_clave": nivel, "ancho": max(2, min(100, round(pcts[k] * 2))),
            "texto": info[nivel], "fortalezas": info["fortalezas"], "vigilar": info["vigilar"],
            "comunicacion": info["comunicacion"], "entornos": info["entornos"],
            "es_primario": k == primario,
        })
    nombre_p, nombre_s = DIMENSIONES[primario]["nombre"], DIMENSIONES[segundo]["nombre"]
    if combinado:
        resumen = (f"Perfil combinado {primario}/{segundo}: {nombre_p} ({pcts[primario]:g} %) y {nombre_s} "
                   f"({pcts[segundo]:g} %) casi empatados. {DIMENSIONES[primario]['alto']} "
                   f"A la vez: {DIMENSIONES[segundo]['alto'][0].lower()}{DIMENSIONES[segundo]['alto'][1:]}")
    else:
        resumen = (f"Predomina {nombre_p} ({pcts[primario]:g} %), seguido de {nombre_s} ({pcts[segundo]:g} %). "
                   f"{DIMENSIONES[primario]['alto']}")
    return {"dimensiones": dimensiones, "primario": primario, "segundo": segundo, "combinado": combinado,
            "resumen": resumen, "cautela": CAUTELA}


# ---------------------------------------------------------------------------
# DISC por ELECCIÓN FORZADA (16/10/2026)
#
# 20 grupos de 4 palabras (una por dimensión). En cada grupo el postulante
# elige la que MÁS lo describe y la que MENOS. Es el formato clásico de los
# tests DISC: como obliga a renunciar a algo, es mucho más difícil de «adornar»
# que puntuar frases del 1 al 5. Cada «más» suma 1 a su dimensión y cada
# «menos» resta 1; con 20 grupos el rango es -20..+20 y se convierte a
# porcentajes que suman 100 (promedio 25 %).
# ---------------------------------------------------------------------------
_PALABRAS = {
    "D": ["Decidido", "Directo", "Competitivo", "Audaz", "Exigente", "Firme", "Resuelto", "Ambicioso", "Independiente",
          "Valiente", "Enérgico", "Emprendedor", "Determinado", "Retador", "Autónomo", "Líder", "Dominante",
          "Impaciente", "Arriesgado", "Tenaz"],
    "I": ["Entusiasta", "Sociable", "Persuasivo", "Optimista", "Expresivo", "Carismático", "Animado", "Comunicativo",
          "Simpático", "Inspirador", "Espontáneo", "Amistoso", "Divertido", "Motivador", "Conversador", "Alegre",
          "Extrovertido", "Cálido", "Convincente", "Popular"],
    "S": ["Paciente", "Leal", "Tranquilo", "Constante", "Amable", "Colaborador", "Estable", "Sereno", "Comprensivo",
          "Confiable", "Calmado", "Cooperador", "Atento", "Considerado", "Servicial", "Armonioso", "Pacífico",
          "Moderado", "Perseverante", "Receptivo"],
    "C": ["Preciso", "Analítico", "Ordenado", "Cuidadoso", "Metódico", "Perfeccionista", "Detallista", "Lógico",
          "Cauteloso", "Disciplinado", "Reservado", "Prudente", "Sistemático", "Organizado", "Exacto", "Riguroso",
          "Objetivo", "Formal", "Observador", "Planificador"],
}
# Se desplazan las columnas para que las palabras de un mismo grupo no sean «hermanas» de la lista.
DISC_GRUPOS = []
for _i in range(20):
    DISC_GRUPOS.append((_i + 1, {
        "D": _PALABRAS["D"][_i],
        "I": _PALABRAS["I"][(_i * 3 + 1) % 20],
        "S": _PALABRAS["S"][(_i * 7 + 2) % 20],
        "C": _PALABRAS["C"][(_i * 9 + 3) % 20],
    }))


def calcular_grupos(respuestas: dict) -> dict | None:
    """respuestas: {"1": {"mas": "D", "menos": "C"}, ...} → {"D": pct, ..., "perfil_dominante": "D"}."""
    puntos = {"D": 0, "I": 0, "S": 0, "C": 0}
    n = 0
    for _gid, _pal in DISC_GRUPOS:
        r = respuestas.get(str(_gid)) or {}
        mas, menos = r.get("mas"), r.get("menos")
        if mas in puntos and menos in puntos and mas != menos:
            puntos[mas] += 1
            puntos[menos] -= 1
            n += 1
    if not n:
        return None
    total = len(DISC_GRUPOS)
    porcentajes = {k: round((v + total) / (4 * total) * 100, 1) for k, v in puntos.items()}
    dominante = max(porcentajes, key=porcentajes.get)
    return {**porcentajes, "perfil_dominante": dominante, "metodo": "eleccion_forzada"}
