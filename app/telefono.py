# -*- coding: utf-8 -*-
"""Celulares en formato internacional (08/10/2026).

WhatsApp (Meta Cloud API) identifica a cada persona por su número completo,
con código de país y sin "+" ni espacios: 51987654321. Para que n8n pueda
escribirle a un candidato sin adivinar, el celular se guarda siempre
normalizado: "+51 987 654 321"."""
import re

ERROR_CELULAR = ("Escribe un celular válido: 9 dígitos que empiecen con 9 (por ejemplo 987 654 321) "
                 "o el número completo con código de país (por ejemplo +51 987 654 321).")


def solo_digitos_whatsapp(texto: str) -> str | None:
    """Número como lo pide la API de WhatsApp (código de país + número, solo
    dígitos), o None si el texto no parece un celular válido.

    - 9 dígitos que empiezan con 9 -> Perú (51 + número).
    - 51 + 9 dígitos que empiezan con 9 -> ya trae el código de Perú.
    - Con "+" o "00" delante -> cualquier país (8 a 15 dígitos, como máximo
      permite el estándar E.164).
    Nada más se acepta: un número sin código que no sea de 9 dígitos es
    ambiguo y se pide corregirlo."""
    if not texto:
        return None
    crudo = texto.strip()
    digitos = re.sub(r"\D", "", crudo)
    if not digitos:
        return None
    internacional = crudo.startswith("+") or digitos.startswith("00")
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if internacional:
        return digitos if 8 <= len(digitos) <= 15 and digitos[0] != "0" else None
    if len(digitos) == 9 and digitos[0] == "9":
        return "51" + digitos
    if len(digitos) == 11 and digitos.startswith("51") and digitos[2] == "9":
        return digitos
    return None


def formato_celular(texto: str) -> str | None:
    """'+51 987 654 321' (legible y sin ambigüedad), o None si no es válido."""
    d = solo_digitos_whatsapp(texto)
    if not d:
        return None
    if d.startswith("51") and len(d) == 11:
        return f"+51 {d[2:5]} {d[5:8]} {d[8:]}"
    return "+" + d


def normalizar_o_dejar(texto: str) -> str | None:
    """Para entradas donde el celular es opcional o viene de un sistema externo:
    si es válido se guarda normalizado; si no, se conserva tal cual lo escribieron
    (no se pierde el dato). Vacío -> None."""
    texto = (texto or "").strip()
    if not texto:
        return None
    return formato_celular(texto) or texto
