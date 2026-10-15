# -*- coding: utf-8 -*-
"""Preguntas por nivel de cada Competencia / Valor / Principio (15/10/2026).

El postulante responde, por cada competencia que exige su cargo, una
AFIRMACIÓN por nivel (1 Básico, 2 Consolidado, 3 Avanzado, 4 Referente) en una
escala de 1 a 5 ("qué tanto describe cómo actúo"), más una pregunta abierta
con un ejemplo real. Con eso MICELIO estima el nivel (ver nivel_estimado).

Las afirmaciones de abajo son el texto inicial: se siembran una sola vez para
cada competencia que todavía no tiene preguntas, y RR.HH. las edita después en
Parámetros › Competencias (lo editado nunca se pisa)."""

NIVELES = {1: "Básico", 2: "Consolidado", 3: "Avanzado", 4: "Referente"}

PREGUNTAS_BASE = {
    # ---- Competencias
    "ADAPTABILIDAD": [
        "Cuando cambia una instrucción o la forma de trabajar, la acepto y la aplico siguiendo las indicaciones que me dan.",
        "Me ajusto con rapidez a cambios en mis tareas o prioridades sin que baje mi desempeño.",
        "Ante cambios importantes propongo cómo organizarme y ayudo a mi equipo a adaptarse.",
        "Anticipo los cambios del entorno y lidero a otros para aprovecharlos como mejoras para la organización.",
    ],
    "COMUNICACION": [
        "Transmito mis mensajes de forma clara y respetuosa cuando me lo piden.",
        "Adapto lo que digo y cómo lo digo según la persona, y compruebo que me hayan entendido.",
        "Escucho con atención, expongo ideas con argumentos y logro que otros las comprendan y se comprometan.",
        "Manejo conversaciones difíciles o con muchas personas, y mi comunicación alinea a toda un área o a la organización.",
    ],
    "DISPONIBILIDAD": [
        "Estoy disponible en el horario acordado y respondo cuando se me requiere.",
        "Me ajusto con flexibilidad a horarios o turnos distintos cuando el trabajo lo necesita.",
        "Me ofrezco por iniciativa a cubrir necesidades urgentes y respondo rápido aun fuera de lo habitual.",
        "Soy un referente de compromiso: mi ejemplo hace que otros se involucren en las urgencias del negocio.",
    ],
    "ETICA": [
        "Cumplo las normas y políticas de la empresa aunque nadie me esté supervisando.",
        "Actúo con honestidad y reconozco mis errores sin ocultarlos.",
        "Cuando veo una conducta inadecuada la señalo o la reporto por el canal correcto.",
        "Tomo decisiones difíciles guiado por la ética aunque tengan un costo, y promuevo una cultura de integridad.",
    ],
    "GESTION DE CONFLICTOS": [
        "Evito responder con agresividad cuando hay un desacuerdo y procuro calmarme.",
        "Escucho la posición del otro y busco un punto de acuerdo cuando surge un desacuerdo.",
        "Intervengo en conflictos entre otras personas y ayudo a que lleguen a una solución justa.",
        "Prevengo conflictos antes de que escalen y establezco formas de trabajo que los reducen en toda el área.",
    ],
    "GESTION DE PROCESOS": [
        "Sigo los procedimientos establecidos tal como están definidos.",
        "Entiendo cómo mi tarea encaja en el proceso completo y cuido los pasos anteriores y siguientes.",
        "Identifico fallas o demoras en un proceso y propongo mejoras concretas.",
        "Rediseño procesos y doy seguimiento a indicadores para mejorar de forma sostenida el desempeño del área.",
    ],
    "LIDERAZGO": [
        "Cumplo mi parte y apoyo a mis compañeros cuando me lo piden.",
        "Oriento a otros en tareas concretas y doy el ejemplo en mi trabajo diario.",
        "Motivo al equipo, delego con claridad y doy retroalimentación para que mejoren.",
        "Desarrollo a otras personas y líderes, genero confianza y conduzco a un grupo grande hacia resultados exigentes.",
    ],
    "ORGANIZACION": [
        "Mantengo mi puesto y mis materiales ordenados y cumplo mis tareas del día.",
        "Priorizo mis tareas y cumplo mis plazos de forma consistente.",
        "Organizo mi trabajo y el de otros para que los plazos se cumplan aun con imprevistos.",
        "Diseño sistemas de trabajo ordenados que mejoran la eficiencia de todo un equipo o área.",
    ],
    "ORIENTACION A RESULTADOS": [
        "Cumplo las metas que me asignan siguiendo las instrucciones.",
        "Doy seguimiento a mi avance y cumplo mis metas con constancia.",
        "Supero las metas con frecuencia y busco cómo lograr más con los mismos recursos.",
        "Establezco metas retadoras para mi área y consigo que otros las alcancen de manera sostenida.",
    ],
    "ORIENTADO AL CLIENTE": [
        "Atiendo consultas básicas de clientes con amabilidad, sin necesidad de anticipar lo que necesitan.",
        "Identifico lo que el cliente pide de forma explícita y le respondo adecuadamente.",
        "Anticipo necesidades que el cliente no expresó y le propongo soluciones.",
        "Diseño estrategias de servicio que elevan la experiencia del cliente en toda la organización.",
    ],
    "PLANIFICACION": [
        "Realizo mis actividades siguiendo el plan que me dan.",
        "Planifico mi semana definiendo tareas, plazos y recursos.",
        "Preveo riesgos y preparo alternativas para que el plan se cumpla.",
        "Elaboro planes de largo plazo para varias áreas y los ajusto según los resultados.",
    ],
    "RESOLUCION DE PROBLEMAS": [
        "Cuando algo falla, aviso a quien corresponde y aplico la solución que me indican.",
        "Analizo qué causó el problema y aplico una solución por mi cuenta en situaciones habituales.",
        "Resuelvo problemas complejos o nuevos con calma, evaluando varias alternativas.",
        "Resuelvo problemas críticos bajo mucha presión y establezco soluciones que evitan que vuelvan a ocurrir.",
    ],
    "TOMA DE DECISIONES": [
        "Decido en asuntos simples de mi tarea y consulto cuando tengo dudas.",
        "Decido con autonomía en mi ámbito, usando la información disponible.",
        "Decido en situaciones con incertidumbre, evaluando riesgos y asumiendo la responsabilidad.",
        "Tomo decisiones estratégicas de alto impacto, considerando a toda la organización y sus consecuencias.",
    ],
    "TRABAJO EN EQUIPO": [
        "Colaboro con mi equipo cumpliendo la parte que me corresponde.",
        "Comparto información y ayudo a mis compañeros sin que me lo pidan.",
        "Coordino con otras áreas y busco que el equipo logre el resultado en conjunto.",
        "Construyo equipos que colaboran entre áreas y resuelvo diferencias en beneficio de la organización.",
    ],
    # ---- Valores
    "APRENDIZAJE CONTINUO": [
        "Recibo con buena disposición las indicaciones y la retroalimentación, y aprendo lo necesario para mi función.",
        "Busco activamente nuevos conocimientos, pido retroalimentación y aprendo de mis errores.",
        "Me mantengo actualizado, comparto lo que sé y propongo nuevas formas de hacer las cosas.",
        "Soy fuente de aprendizaje para otros y promuevo una cultura de aprendizaje que se convierte en mejoras para la organización.",
    ],
    "COLABORACION": [
        "Mantengo una actitud respetuosa, cumplo mi parte en el equipo y apoyo cuando me lo piden.",
        "Coopero activamente con mis compañeros y comparto la información que el equipo necesita.",
        "Facilito la integración entre personas y áreas y priorizo los objetivos colectivos.",
        "Construyo equipos cohesionados que trabajan juntos hacia objetivos comunes, incluso bajo presión o conflicto.",
    ],
    "CONFIANZA": [
        "Cumplo lo que prometo y comunico la información relevante con respeto.",
        "Mantengo mis compromisos, actúo con coherencia y manejo con discreción la información que recibo.",
        "Genero confianza incluso en situaciones difíciles, comunicando con claridad y cumpliendo los acuerdos.",
        "Soy reconocido como una persona altamente confiable y ayudo a construir una cultura de confianza.",
    ],
    "EXCELENCIA": [
        "Respeto los procedimientos y entrego un trabajo correcto, sin errores evitables.",
        "Busco mejorar la calidad y la eficiencia de mi trabajo y corrijo los errores que detecto.",
        "Identifico oportunidades de mejora y propongo soluciones; no me conformo con resultados solo aceptables.",
        "Elevo los estándares de la organización e inspiro a otros a buscar mejores resultados.",
    ],
    "INTEGRIDAD": [
        "Respeto las normas, los procedimientos y las políticas, y evito conductas que perjudiquen a otros.",
        "Mantengo una conducta ética aun bajo presión y reconozco mis errores.",
        "Defiendo los principios de la organización en situaciones complejas y prevengo conductas inadecuadas.",
        "Soy un referente ético y promuevo comportamientos íntegros en la organización.",
    ],
    "RESPONSABILIDAD": [
        "Cumplo horarios, funciones y compromisos, y aviso a tiempo cuando no podré cumplir algo.",
        "Organizo y priorizo mi trabajo de forma autónoma y anticipo dificultades.",
        "Asumo la responsabilidad por los resultados, no solo por las tareas, y ayudo a otros a cumplir las suyas.",
        "Me anticipo a los riesgos, asumo desafíos adicionales y movilizo a otros hacia una cultura de cumplimiento.",
    ],
    "TRANSPARENCIA": [
        "Doy información verdadera por los canales establecidos y no oculto errores.",
        "Comunico a tiempo avances, dificultades y resultados, y reconozco mis errores.",
        "Anticipo información relevante y comunico situaciones difíciles con objetividad, proponiendo alternativas.",
        "Genero un entorno donde las personas pueden decir problemas y errores sin temor, orientado a la solución.",
    ],
    # ---- Principios
    "APRENDIZAJE Y MEJORA CONTINUA": [
        "Aprendo de las correcciones que me hacen y las aplico.",
        "Reconozco mis errores y los uso para mejorar mi trabajo.",
        "Comparto lo que aprendo con mi equipo para que no se repitan los errores.",
        "Promuevo prácticas para convertir errores y experiencias en mejoras permanentes en toda el área.",
    ],
    "COLABORACION SISTEMICA": [
        "Respeto las decisiones del equipo aunque no coincidan con mi preferencia.",
        "Considero cómo mi trabajo afecta a otras áreas antes de actuar.",
        "Evito mejorar mi resultado a costa del de otras áreas y busco soluciones convenientes para todos.",
        "Tomo decisiones pensando en el resultado global de la empresa, aunque me perjudiquen a mí o a mi área.",
    ],
    "DECISIONES BASADAS EN LA EXPERIENCIA": [
        "Decido usando la información y las instrucciones que tengo a mano.",
        "Reviso hechos y datos relevantes antes de decidir.",
        "Combino datos con mi criterio y la experiencia previa para decidir mejor.",
        "Establezco criterios y registros para que las decisiones del área se apoyen en datos y aprendizaje.",
    ],
    "EXCELENCIA DESDE EL ORIGEN": [
        "Hago mi tarea con cuidado para evitar errores desde el inicio.",
        "Verifico mi trabajo antes de entregarlo para no depender de que otros lo corrijan.",
        "Preveo en qué puntos del proceso pueden aparecer fallas y las controlo desde el principio.",
        "Diseño controles y estándares que previenen errores en toda un área o proceso.",
    ],
    "INNOVACION": [
        "Aplico las nuevas formas de trabajo que me presentan.",
        "Propongo ideas para hacer mejor mis tareas.",
        "Pruebo ideas nuevas y las llevo a la práctica con mi equipo.",
        "Impulso una cultura de innovación en la que se prueban ideas y se amplían las que funcionan.",
    ],
    "ORIENTACION AL CLIENTE": [
        "Trato al cliente interno y externo con respeto y cumplo lo que se le promete.",
        "Considero cómo mi tarea afecta al cliente antes de ejecutarla.",
        "Priorizo lo que le genera valor al cliente aun cuando requiere más esfuerzo.",
        "Oriento las decisiones de toda un área al valor que reciben los clientes.",
    ],
    "RESPONSABILIDAD POR EL RESULTADO": [
        "Cumplo lo que me corresponde y respondo si algo sale mal.",
        "Asumo mis compromisos y rindo cuentas de mis resultados, no solo de mis tareas.",
        "Asumo también los resultados de mis decisiones y corrijo el rumbo cuando hace falta.",
        "Respondo por el resultado de todo mi equipo y promuevo que cada persona responda por el suyo.",
    ],
    "SER INTEGROS": [
        "Digo la verdad y cumplo las reglas en mi trabajo diario.",
        "Actúo de manera coherente: lo que digo y lo que hago coinciden.",
        "Elijo lo correcto aunque sea incómodo o no me beneficie.",
        "Soy ejemplo de integridad y pongo límites claros frente a prácticas inadecuadas.",
    ],
}


def afirmaciones_por_defecto(competencia) -> list[str]:
    """4 textos (niveles 1-4). Si el nombre no está en PREGUNTAS_BASE se arman
    a partir de lo que describe cada nivel, o de una frase genérica."""
    base = PREGUNTAS_BASE.get((competencia.nombre or "").strip().upper())
    if base:
        return list(base)
    nombre = (competencia.nombre or "esta competencia").strip().lower()
    generico = {
        1: f"En «{nombre}» cumplo con lo que se espera de mí siguiendo las indicaciones que recibo.",
        2: f"En «{nombre}» actúo con autonomía y de forma consistente en mi trabajo diario.",
        3: f"En «{nombre}» propongo mejoras y oriento a otros con mi ejemplo.",
        4: f"En «{nombre}» soy un referente: lidero cambios y genero resultados en toda la organización.",
    }
    out = []
    for n in (1, 2, 3, 4):
        descriptor = (getattr(competencia, f"nivel_{n}", None) or "").strip()
        out.append(descriptor or generico[n])
    return out


def pregunta_ejemplo(competencia) -> str:
    return (f"Cuéntanos una situación real y reciente en la que pusiste en práctica «{(competencia.nombre or '').title()}»: "
            "¿qué ocurrió, qué hiciste tú concretamente y qué resultado obtuviste?")


def asegurar_preguntas(db, competencia) -> bool:
    """Siembra las preguntas de una competencia si todavía no tiene ninguna.
    No hace commit. Devuelve True si creó algo."""
    from .models import CompetenciaPregunta
    if db.query(CompetenciaPregunta).filter(CompetenciaPregunta.competencia_id == competencia.id).first():
        return False
    for i, texto in enumerate(afirmaciones_por_defecto(competencia), start=1):
        db.add(CompetenciaPregunta(competencia_id=competencia.id, tipo="afirmacion", nivel=i, texto=texto))
    db.add(CompetenciaPregunta(competencia_id=competencia.id, tipo="ejemplo", nivel=None,
                               texto=pregunta_ejemplo(competencia)))
    return True


def asegurar_todas(db) -> int:
    from .models import Competencia
    n = 0
    for c in db.query(Competencia).all():
        if asegurar_preguntas(db, c):
            n += 1
    if n:
        db.commit()
    return n


def nivel_estimado(respuestas: dict[int, int]) -> int:
    """respuestas: {nivel(1-4): puntaje(1-5)} → nivel 0-4.

    El nivel estimado es el MÁS ALTO cuyo puntaje es 4 o 5. Si ninguno llega a
    4 pero el nivel 1 tiene 3, el postulante queda en 1; si no, en 0 (por
    debajo del nivel básico)."""
    altos = [n for n, p in respuestas.items() if p is not None and p >= 4]
    if altos:
        return max(altos)
    if (respuestas.get(1) or 0) >= 3:
        return 1
    return 0


def incoherente(respuestas: dict[int, int]) -> bool:
    """Marca respuestas poco creíbles: dice dominar un nivel alto pero puntúa
    bajo (≤2) un nivel inferior."""
    for n, p in respuestas.items():
        if p is not None and p >= 4:
            for m in range(1, n):
                if (respuestas.get(m) or 5) <= 2:
                    return True
    return False
