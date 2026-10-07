# -*- coding: utf-8 -*-
"""Parametrización > Procesos y Funciones (pedido del 06/10, precisado el 07/10).

Árbol de Procesos Macro -> Procesos -> Subprocesos -> Funciones de CADA
empresa, para saber qué ROLES (cargos) tienen que existir, qué funciones
tiene cada uno, y si lo cubre una PERSONA o un AGENTE IA (automatizar) — y
con eso armar el headcount de personas y de agentes.

Reglas del dueño:
1. Procesos y funciones se asignan a un ROL (un Cargo de "Cargos - MOF") o a
   un AGENTE IA — NUNCA a una persona: varias personas pueden tener el mismo
   cargo y por lo tanto las mismas funciones (p. ej. varios Supervisores
   Guardián de la Experiencia GX, uno por zona). Quiénes son las personas de
   un rol sale solo de Personal (su cargo en la ficha); además se puede sumar
   a alguien de otra empresa del holding (RolPersonaExtra).
2. Se asigna en cualquier nodo y lo de abajo lo hereda, salvo lo que tenga
   su propia asignación.
3. Los procesos se completan hacia arriba: si todas las funciones de un
   proceso son del mismo rol, el proceso es de ese rol; si todas son de
   agentes IA, es de un agente IA.
4. Si las funciones de un proceso son mezcladas (roles distintos, o roles y
   agentes), el proceso tiene que asignarse a un rol responsable — el sistema
   avisa mientras falte. Vale en cada nivel (subprocesos, procesos, macros).
5. Cada función asignada a un rol se agrega sola al MOF de ese cargo (ver
   funciones_de_cargo y Cargo.funciones_todas): si se modifica o elimina acá,
   cambia allá.
Solo el administrador edita esto.
"""
import datetime
import os
from collections import defaultdict
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from .models import ProcesoNodo, RolPersonaExtra, Empresa, Employee, Cargo, User
from .auth import require_role, require_perm, alcance_empresas, exigir_empresa
from .rrhh import _ctx, _a_lima

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.filters["lima"] = _a_lima
router = APIRouter()

RUTA = "/rrhh/parametrizacion/procesos"


def _tipo_label(depth: int, es_funcion: bool) -> str:
    if es_funcion:
        return "Función"
    if depth == 0:
        return "Proceso Macro"
    if depth == 1:
        return "Proceso"
    return "Subproceso"


def _label_hijo(depth: int) -> str:
    """Cómo se llama lo que se agrega DENTRO de un nodo de esta profundidad."""
    return "Proceso" if depth == 0 else "Subproceso"


# ---------------------------------------------------------------------------
# Personas de cada rol (salen de Personal)
# ---------------------------------------------------------------------------
def _personas_por_cargo(db: Session, empresa_id: int) -> dict:
    """{cargo_id: [{"emp": Employee, "origen": "personal"|"extra"}]} — las
    personas activas de la empresa cuyo cargo en la ficha es ese, más las de
    otras empresas que se sumaron a mano a ese rol."""
    cargos = {c.nombre.strip().lower(): c.id for c in db.query(Cargo).all()}
    out = defaultdict(list)
    vistos = defaultdict(set)
    activos = db.query(Employee).filter(Employee.estado == "activo", Employee.empresa_id == empresa_id)
    for e in activos.order_by(Employee.nombre_completo).all():
        nombre = ((e.ficha_data or {}).get("cargo") or "").strip().lower()
        cid = cargos.get(nombre)
        if cid:
            out[cid].append({"emp": e, "origen": "personal"})
            vistos[cid].add(e.id)
    for x in db.query(RolPersonaExtra).filter(RolPersonaExtra.empresa_id == empresa_id).all():
        e = x.employee
        if e and e.estado == "activo" and e.id not in vistos[x.cargo_id]:
            out[x.cargo_id].append({"emp": e, "origen": "extra"})
            vistos[x.cargo_id].add(e.id)
    return out


# ---------------------------------------------------------------------------
# Armado del árbol con todo calculado (herencia, completado hacia arriba,
# estados, indicadores)
# ---------------------------------------------------------------------------
def _asig_propia(n):
    """La asignación DIRECTA del nodo, o None. Un cargo borrado deja la
    asignación huérfana: se ignora."""
    if n.asignado_tipo == "rol" and n.cargo:
        return {"tipo": "rol", "cargo": n.cargo, "key": ("c", n.cargo.id), "desde": n.nombre}
    if n.asignado_tipo == "agente":
        return {"tipo": "agente", "nombre": n.agente_nombre, "nota": n.agente_nota,
                "key": ("a",), "desde": n.nombre}
    return None


def construir_estructura(db: Session, empresa_id: int, con_personas: bool = True) -> dict:
    nodos = db.query(ProcesoNodo).filter(ProcesoNodo.empresa_id == empresa_id).all()
    ids = {n.id for n in nodos}
    hijos_map = defaultdict(list)
    raices = []
    for n in sorted(nodos, key=lambda n: ((n.orden or 0), n.id)):
        if n.parent_id and n.parent_id in ids:
            hijos_map[n.parent_id].append(n)
        else:
            raices.append(n)

    acc = {"macros": 0, "procesos": 0, "funciones": 0, "asignadas": 0, "sin_asignar": 0, "vacios": 0,
           "falta_responsable": 0, "conflictos": 0, "roles": {}, "agentes": {}}
    arbol = [_armar(n, hijos_map, str(i), 0, None, [], acc) for i, n in enumerate(raices, 1)]
    for r in arbol:
        _contar_agentes(r, False, None, acc)

    personas = _personas_por_cargo(db, empresa_id) if con_personas else {}
    roles = []
    for cargo_id, info in acc["roles"].items():
        ps = personas.get(cargo_id, [])
        roles.append({
            "cargo": info["cargo"], "personas": ps, "n_personas": len(ps),
            "necesarias": max(1, len(ps)), "por_cubrir": len(ps) == 0,
            "funciones": info["funciones"], "procesos": info["procesos"],
        })
    roles.sort(key=lambda r: r["cargo"].nombre.lower())
    por_cargo = {r["cargo"].id: r for r in roles}
    for r in arbol:
        _anotar(r, por_cargo)

    agentes = sorted(acc["agentes"].values(), key=lambda a: a["codigo"])
    distintas = {p["emp"].id for r in roles for p in r["personas"]}
    funciones = acc["funciones"]
    ind = {
        "macros": acc["macros"], "procesos": acc["procesos"], "funciones": funciones,
        "asignadas": acc["asignadas"], "sin_asignar": acc["sin_asignar"], "vacios": acc["vacios"],
        "falta_responsable": acc["falta_responsable"], "conflictos": acc["conflictos"],
        "roles": len(roles), "humanos": sum(r["necesarias"] for r in roles),
        "personas": len(distintas), "roles_por_cubrir": sum(1 for r in roles if r["por_cubrir"]),
        "agentes": len(agentes), "agente_funciones": sum(len(a["funciones"]) for a in agentes),
        "pct": round(acc["asignadas"] * 100 / funciones) if funciones else 0,
        "pendientes": acc["sin_asignar"] + acc["vacios"] + acc["falta_responsable"] + acc["conflictos"],
    }
    return {"arbol": arbol, "ind": ind, "roles": roles, "agentes": agentes}


def _armar(n, hijos_map, codigo, depth, directa_padre, ruta, acc):
    propia = _asig_propia(n)
    heredable = propia or directa_padre  # lo que heredan los de abajo (solo asignaciones DIRECTAS)
    ruta_n = ruta + [n.nombre]
    hijos = [
        _armar(h, hijos_map, f"{codigo}.{i}", depth + 1, heredable, ruta_n, acc)
        for i, h in enumerate(hijos_map.get(n.id, []), 1)
    ]
    d = {
        "id": n.id, "codigo": codigo, "depth": depth, "es_funcion": bool(n.es_funcion),
        "tipo": _tipo_label(depth, bool(n.es_funcion)), "label_hijo": _label_hijo(depth),
        "nombre": n.nombre, "descripcion": n.descripcion, "ruta": " › ".join(ruta_n), "hijos": hijos,
        "asig_tipo": n.asignado_tipo if propia else None,  # para precargar el diálogo (asignación PROPIA)
        "asig_cargo_id": n.cargo_id if (propia and propia["tipo"] == "rol") else None,
        "asig_agente_nombre": n.agente_nombre if propia else None,
        "asig_agente_nota": n.agente_nota if propia else None,
        "n_desc": sum(1 + h["n_desc"] for h in hijos),  # cuántos elementos cuelgan de este nodo
        "efectivo": None, "estado": None, "n_func": 0, "n_sin_asignar": 0, "n_problemas": 0,
        "vacio": False, "falta_responsable": False, "conflicto": False, "tiene_roles": False,
    }

    if n.es_funcion:
        acc["funciones"] += 1
        d["n_func"] = 1
        if propia:
            ef = {**propia, "origen": "propia"}
        elif directa_padre:
            ef = {**directa_padre, "origen": "heredada"}
        else:
            ef = None
        d["efectivo"] = ef
        if ef:
            d["estado"] = "asignada"
            acc["asignadas"] += 1
            d["tiene_roles"] = ef["tipo"] == "rol"
            if ef["tipo"] == "rol":
                _registrar_rol(acc, ef)["funciones"].append({"codigo": codigo, "nombre": n.nombre})
        else:
            d["estado"] = "sin_asignar"
            d["n_sin_asignar"] = 1
            acc["sin_asignar"] += 1
    else:
        if depth == 0:
            acc["macros"] += 1
        else:
            acc["procesos"] += 1
        efs = [h["efectivo"] for h in hijos]
        todos = bool(hijos) and all(efs)
        claves = {e["key"] for e in efs if e}
        uniforme = todos and len(claves) == 1
        mixto = todos and len(claves) > 1
        d["n_func"] = sum(h["n_func"] for h in hijos)
        d["n_sin_asignar"] = sum(h["n_sin_asignar"] for h in hijos)
        d["tiene_roles"] = any(h["tiene_roles"] for h in hijos)
        d["vacio"] = not hijos

        if propia:
            d["efectivo"] = {**propia, "origen": "propia"}
            if propia["tipo"] == "rol":
                _registrar_rol(acc, propia)["procesos"].append({"codigo": codigo, "nombre": n.nombre})
            elif d["tiene_roles"]:
                # Un proceso con funciones de un rol no puede ser "de un agente IA"
                d["conflicto"] = True
        elif uniforme:
            base = efs[0]
            if base["tipo"] == "agente":
                nombres = {e.get("nombre") for e in efs}
                base = {**base, "nombre": nombres.pop() if len(nombres) == 1 else None, "nota": None}
            d["efectivo"] = {**base, "origen": "derivada"}
        elif directa_padre:
            d["efectivo"] = {**directa_padre, "origen": "heredada"}
        if mixto and not d["efectivo"]:
            d["falta_responsable"] = True  # mezcladas y sin responsable

        if d["vacio"]:
            acc["vacios"] += 1
        if d["falta_responsable"]:
            acc["falta_responsable"] += 1
        if d["conflicto"]:
            acc["conflictos"] += 1

    # Cuántas cosas pendientes hay en este nodo y debajo (para el aviso del árbol)
    propios = 1 if (d["estado"] == "sin_asignar" or d["vacio"] or d["falta_responsable"] or d["conflicto"]) else 0
    d["n_problemas"] = propios + sum(h["n_problemas"] for h in hijos)
    return d


def _registrar_rol(acc, ef):
    c = ef["cargo"]
    return acc["roles"].setdefault(c.id, {"cargo": c, "funciones": [], "procesos": []})


def _anotar(d, por_cargo):
    """Agrega a cada asignación a rol cuántas personas lo cubren (para mostrarlo en el árbol)."""
    a = d["efectivo"]
    if a and a["tipo"] == "rol":
        r = por_cargo.get(a["cargo"].id)
        a["n_personas"] = r["n_personas"] if r else 0
    for h in d["hijos"]:
        _anotar(h, por_cargo)


def _contar_agentes(d, padre_es_agente, unidad, acc):
    """Un "agente a desarrollar" = el nodo más alto que es de un agente IA
    (cubre todo lo que cuelga de él). Si el agente tiene nombre, el mismo
    nombre en distintos lugares es UN solo agente."""
    ef = d["efectivo"]
    es_ag = bool(ef and ef["tipo"] == "agente")
    if es_ag and not padre_es_agente:
        unidad = d
    elif not es_ag:
        unidad = None
    if es_ag and d["es_funcion"]:
        nombre = (ef.get("nombre") or "").strip()
        ident = ("n", nombre.lower()) if nombre else ("u", unidad["id"])
        a = acc["agentes"].setdefault(ident, {
            "nombre": nombre or f"Agente de «{unidad['nombre']}»", "codigo": unidad["codigo"],
            "ruta": unidad["ruta"], "nota": ef.get("nota"), "funciones": [],
        })
        if not a["nota"] and ef.get("nota"):
            a["nota"] = ef["nota"]
        a["funciones"].append({"codigo": d["codigo"], "nombre": d["nombre"]})
    for h in d["hijos"]:
        _contar_agentes(h, es_ag, unidad, acc)


def _podar_pendientes(d):
    """Deja solo las ramas con algo pendiente (función sin asignar, proceso
    sin funciones, sin responsable o en conflicto), conservando el camino."""
    hijos = [p for p in (_podar_pendientes(h) for h in d["hijos"]) if p]
    mantener = (d["es_funcion"] and d["estado"] == "sin_asignar") or d["vacio"] or d["falta_responsable"] or d["conflicto"]
    if mantener or hijos:
        return {**d, "hijos": hijos}
    return None


# ---------------------------------------------------------------------------
# Funciones -> MOF del cargo
# ---------------------------------------------------------------------------
def funciones_de_cargo(db: Session, cargo_id: int) -> list:
    """Todas las funciones que Procesos y Funciones asignó a este rol, en
    cualquier empresa (asignadas directo, heredadas o por completado del
    proceso). Se calcula al momento — no se guarda — así que si una función
    se modifica, se reasigna o se elimina, el MOF del cargo cambia solo."""
    if not db.query(ProcesoNodo.id).filter(ProcesoNodo.cargo_id == cargo_id).first():
        return []  # un rol efectivo siempre nace de una asignación directa
    out = []
    for emp in db.query(Empresa).order_by(Empresa.nombre).all():
        est = construir_estructura(db, emp.id, con_personas=False)
        pila = list(reversed(est["arbol"]))
        while pila:
            d = pila.pop()
            ef = d["efectivo"]
            if d["es_funcion"] and ef and ef["tipo"] == "rol" and ef["cargo"].id == cargo_id:
                out.append({"nombre": d["nombre"], "empresa": emp.nombre, "ruta": d["ruta"], "codigo": d["codigo"]})
            pila.extend(reversed(d["hijos"]))
    return out


# ---------------------------------------------------------------------------
# Datos para los selectores y "compartido entre empresas"
# ---------------------------------------------------------------------------
def _otras_empresas(db: Session, empresa_id: int, alcance=None) -> dict:
    """Dónde más se usa cada rol y dónde más cubre cada persona (extras).
    `alcance` (set de ids) acota a las empresas que el usuario puede ver."""
    roles = defaultdict(set)
    filas = (
        db.query(ProcesoNodo.cargo_id, Empresa.nombre)
        .join(Empresa, Empresa.id == ProcesoNodo.empresa_id)
        .filter(ProcesoNodo.empresa_id != empresa_id, ProcesoNodo.cargo_id.isnot(None))
    )
    if alcance is not None:
        filas = filas.filter(ProcesoNodo.empresa_id.in_(list(alcance) or [-1]))
    filas = filas.distinct().all()
    for cid, nombre in filas:
        roles[cid].add(nombre)
    personas = defaultdict(set)
    filas = (
        db.query(RolPersonaExtra.employee_id, Empresa.nombre)
        .join(Empresa, Empresa.id == RolPersonaExtra.empresa_id)
        .filter(RolPersonaExtra.empresa_id != empresa_id)
    )
    if alcance is not None:
        filas = filas.filter(RolPersonaExtra.empresa_id.in_(list(alcance) or [-1]))
    filas = filas.distinct().all()
    for eid, nombre in filas:
        personas[eid].add(nombre)
    return {"roles": {k: sorted(v) for k, v in roles.items()},
            "personas": {k: sorted(v) for k, v in personas.items()}}


def _selector_cargos(db: Session, empresa_id: int):
    """Cargos activos, con cuántas personas lo tienen hoy en esta empresa."""
    por_cargo = _personas_por_cargo(db, empresa_id)
    return [
        {"id": c.id, "nombre": c.nombre, "n": len(por_cargo.get(c.id, []))}
        for c in db.query(Cargo).filter(Cargo.activo == True).order_by(Cargo.nombre).all()  # noqa: E712
    ]


def _selector_personas(db: Session, empresa: Empresa, alcance=None):
    """Personas activas de TODAS las empresas (para sumar a un rol a alguien
    de otra empresa del holding), la propia empresa primero. Un gerente solo
    ve las de las empresas de su alcance."""
    activos = db.query(Employee).filter(Employee.estado == "activo").order_by(Employee.nombre_completo).all()
    if alcance is not None:
        activos = [e for e in activos if e.empresa_id in alcance]
    grupos = defaultdict(list)
    for e in activos:
        grupos[e.empresa_id].append({"id": e.id, "nombre": e.nombre_completo,
                                     "cargo": (e.ficha_data or {}).get("cargo") or ""})
    empresas = {e.id: e.nombre for e in db.query(Empresa).all()}
    orden = [empresa.id] + sorted((k for k in grupos if k != empresa.id and k is not None),
                                  key=lambda k: empresas.get(k, ""))
    if None in grupos:
        orden.append(None)
    return [
        {"empresa": (empresas.get(k, "Sin empresa") if k is not None else "Sin empresa"),
         "propia": k == empresa.id, "lista": grupos[k]}
        for k in orden if grupos.get(k)
    ]


def _volver(empresa_id, sp="", foco=None, error="", ancla=None):
    url = f"{RUTA}?empresa_id={empresa_id}"
    if sp:
        url += "&solo_pendientes=1"
    if error:
        url += "&error=" + quote(error)
    if ancla:
        url += f"#{ancla}"
    elif foco:
        url += f"#n{foco}"
    return RedirectResponse(url, status_code=303)


# --- limpieza cuando se borra una empresa / persona / cargo (la llama rrhh.py)
def limpiar_empresa(db: Session, empresa_id: int) -> None:
    db.query(ProcesoNodo).filter(ProcesoNodo.empresa_id == empresa_id).delete()
    db.query(RolPersonaExtra).filter(RolPersonaExtra.empresa_id == empresa_id).delete()


def limpiar_persona(db: Session, employee_id: int) -> None:
    db.query(RolPersonaExtra).filter(RolPersonaExtra.employee_id == employee_id).delete()


def limpiar_cargo(db: Session, cargo_id: int) -> None:
    db.query(RolPersonaExtra).filter(RolPersonaExtra.cargo_id == cargo_id).delete()


# ---------------------------------------------------------------------------
# Pantallas
# ---------------------------------------------------------------------------
@router.get(RUTA, response_class=HTMLResponse)
def procesos_pantalla(request: Request, empresa_id: str = "", solo_pendientes: str = "", error: str = "",
                       db: Session = Depends(get_db),
                       user: User = Depends(require_perm("p_procesos", "ver"))):
    alcance = alcance_empresas(user, db)
    empresas = [e for e in db.query(Empresa).filter(Empresa.activo == True).order_by(Empresa.nombre).all()  # noqa: E712
                if alcance is None or e.id in alcance]
    if not empresas:
        return templates.TemplateResponse(request, "rrhh_procesos.html", _ctx(
            request, user, empresas=[], empresa=None, error=error, active="procesos",
        ))
    empresa = next((e for e in empresas if str(e.id) == empresa_id), empresas[0])

    est = construir_estructura(db, empresa.id)
    resumen_empresas = {
        e.id: (est["ind"] if e.id == empresa.id else construir_estructura(db, e.id, con_personas=False)["ind"])
        for e in empresas
    }
    arbol = est["arbol"]
    if solo_pendientes:
        arbol = [p for p in (_podar_pendientes(r) for r in arbol) if p]

    return templates.TemplateResponse(request, "rrhh_procesos.html", _ctx(
        request, user, empresas=empresas, empresa=empresa, arbol=arbol, ind=est["ind"], roles=est["roles"],
        agentes=est["agentes"], otras=_otras_empresas(db, empresa.id, alcance), resumen_empresas=resumen_empresas,
        cargos=_selector_cargos(db, empresa.id), personas=_selector_personas(db, empresa, alcance),
        puede_editar=user.puede("p_procesos", "editar"),
        solo_pendientes=bool(solo_pendientes), error=error, active="procesos",
    ))


@router.get(RUTA + "/imprimir", response_class=HTMLResponse)
def procesos_imprimir(request: Request, empresa_id: int, db: Session = Depends(get_db),
                       user: User = Depends(require_perm("p_procesos", "ver"))):
    empresa = db.query(Empresa).get(empresa_id)
    if not empresa:
        return RedirectResponse(RUTA, status_code=303)
    exigir_empresa(user, db, empresa.id)
    est = construir_estructura(db, empresa.id)
    return templates.TemplateResponse(request, "rrhh_procesos_imprimir.html", {
        "empresa": empresa, "arbol": est["arbol"], "ind": est["ind"], "roles": est["roles"],
        "agentes": est["agentes"], "otras": _otras_empresas(db, empresa.id, alcance_empresas(user, db)),
        "ahora": datetime.datetime.utcnow(), "user": user,
    })


# ---------------------------------------------------------------------------
# Acciones (todas redirigen de vuelta a la misma pantalla, sobre el nodo
# tocado, para que la persona no pierda el lugar donde estaba trabajando)
# ---------------------------------------------------------------------------
@router.post(RUTA + "/nodo")
def nodo_crear(empresa_id: int = Form(...), parent_id: str = Form(""), es_funcion: str = Form("0"),
               nombre: str = Form(...), descripcion: str = Form(""), sp: str = Form(""),
               db: Session = Depends(get_db), user: User = Depends(require_perm("p_procesos", "editar"))):
    exigir_empresa(user, db, empresa_id)
    nombre = nombre.strip()
    if not nombre or not db.query(Empresa).get(empresa_id):
        return _volver(empresa_id, sp, error="Falta el nombre.")
    funcion = es_funcion == "1"
    padre = None
    if parent_id:
        padre = db.query(ProcesoNodo).get(int(parent_id))
        if not padre or padre.empresa_id != empresa_id or padre.es_funcion:
            return _volver(empresa_id, sp, error="No se puede agregar ahí: una función no puede tener nada adentro.")
    elif funcion:
        return _volver(empresa_id, sp, error="Una función tiene que ir dentro de un proceso.")

    q = db.query(ProcesoNodo).filter(ProcesoNodo.empresa_id == empresa_id)
    q = q.filter(ProcesoNodo.parent_id == padre.id) if padre else q.filter(ProcesoNodo.parent_id.is_(None))
    siguiente = max([(h.orden or 0) for h in q.all()], default=-1) + 1
    nodo = ProcesoNodo(
        empresa_id=empresa_id, parent_id=padre.id if padre else None, es_funcion=funcion,
        nombre=nombre, descripcion=descripcion.strip() or None, orden=siguiente,
    )
    db.add(nodo)
    db.commit()
    return _volver(empresa_id, sp, foco=nodo.id)


@router.post(RUTA + "/nodo/{nodo_id}/editar")
def nodo_editar(nodo_id: int, nombre: str = Form(...), descripcion: str = Form(""), sp: str = Form(""),
                db: Session = Depends(get_db), user: User = Depends(require_perm("p_procesos", "editar"))):
    nodo = db.query(ProcesoNodo).get(nodo_id)
    if not nodo:
        return RedirectResponse(RUTA, status_code=303)
    exigir_empresa(user, db, nodo.empresa_id)
    if not nombre.strip():
        return _volver(nodo.empresa_id, sp, foco=nodo.id, error="Falta el nombre.")
    nodo.nombre = nombre.strip()
    nodo.descripcion = descripcion.strip() or None
    db.commit()
    return _volver(nodo.empresa_id, sp, foco=nodo.id)


def _descendientes_con_rol(db: Session, nodo: ProcesoNodo) -> bool:
    """¿Hay algo asignado directamente a un rol dentro de este nodo?"""
    todos = db.query(ProcesoNodo).filter(ProcesoNodo.empresa_id == nodo.empresa_id).all()
    hijos = defaultdict(list)
    for n in todos:
        hijos[n.parent_id].append(n)
    pila = list(hijos.get(nodo.id, []))
    while pila:
        n = pila.pop()
        if n.asignado_tipo == "rol" and n.cargo:
            return True
        pila.extend(hijos.get(n.id, []))
    return False


@router.post(RUTA + "/nodo/{nodo_id}/asignar")
def nodo_asignar(nodo_id: int, tipo: str = Form("ninguno"), cargo_id: str = Form(""),
                 agente_nombre: str = Form(""), agente_nota: str = Form(""), sp: str = Form(""),
                 db: Session = Depends(get_db), user: User = Depends(require_perm("p_procesos", "editar"))):
    """Asigna el nodo a un ROL (cargo) o a un AGENTE IA — o quita la
    asignación (tipo = "ninguno")."""
    nodo = db.query(ProcesoNodo).get(nodo_id)
    if not nodo:
        return RedirectResponse(RUTA, status_code=303)
    exigir_empresa(user, db, nodo.empresa_id)
    if tipo == "rol":
        cargo = db.query(Cargo).get(int(cargo_id)) if cargo_id else None
        if not cargo:
            return _volver(nodo.empresa_id, sp, foco=nodo.id, error="Elige el rol (cargo).")
        nodo.asignado_tipo, nodo.cargo_id = "rol", cargo.id
        nodo.agente_nombre = nodo.agente_nota = None
    elif tipo == "agente":
        if not nodo.es_funcion and _descendientes_con_rol(db, nodo):
            return _volver(nodo.empresa_id, sp, foco=nodo.id, error=(
                "Este proceso tiene funciones asignadas a un rol, así que no puede ser de un agente IA: "
                "asígnalo al rol responsable (el agente IA queda en las funciones que corresponda)."))
        nodo.asignado_tipo, nodo.cargo_id = "agente", None
        nodo.agente_nombre = agente_nombre.strip() or None
        nodo.agente_nota = agente_nota.strip() or None
    else:
        nodo.asignado_tipo, nodo.cargo_id = None, None
        nodo.agente_nombre = nodo.agente_nota = None
    db.commit()
    return _volver(nodo.empresa_id, sp, foco=nodo.id)


@router.post(RUTA + "/rol/{cargo_id}/persona")
def rol_persona_agregar(cargo_id: int, empresa_id: int = Form(...), employee_id: int = Form(...), sp: str = Form(""),
                        db: Session = Depends(get_db), user: User = Depends(require_perm("p_procesos", "editar"))):
    """Suma a un rol, en esta empresa, a una persona que no lo tiene por
    cargo (p. ej. alguien de otra empresa del holding)."""
    exigir_empresa(user, db, empresa_id)
    _persona = db.query(Employee).get(employee_id)
    if _persona is not None and alcance_empresas(user, db) is not None:
        exigir_empresa(user, db, _persona.empresa_id)  # un gerente solo suma gente de sus empresas
    if db.query(Cargo).get(cargo_id) and db.query(Empresa).get(empresa_id) and db.query(Employee).get(employee_id):
        existe = db.query(RolPersonaExtra).filter_by(
            empresa_id=empresa_id, cargo_id=cargo_id, employee_id=employee_id).first()
        if not existe:
            db.add(RolPersonaExtra(empresa_id=empresa_id, cargo_id=cargo_id, employee_id=employee_id))
            db.commit()
    return _volver(empresa_id, sp, ancla="roles")


@router.post(RUTA + "/rol/{cargo_id}/persona/{employee_id}/quitar")
def rol_persona_quitar(cargo_id: int, employee_id: int, empresa_id: int = Form(...), sp: str = Form(""),
                       db: Session = Depends(get_db), user: User = Depends(require_perm("p_procesos", "editar"))):
    exigir_empresa(user, db, empresa_id)
    db.query(RolPersonaExtra).filter_by(
        empresa_id=empresa_id, cargo_id=cargo_id, employee_id=employee_id).delete()
    db.commit()
    return _volver(empresa_id, sp, ancla="roles")


@router.post(RUTA + "/nodo/{nodo_id}/mover")
def nodo_mover(nodo_id: int, direccion: str = Form(...), sp: str = Form(""),
               db: Session = Depends(get_db), user: User = Depends(require_perm("p_procesos", "editar"))):
    nodo = db.query(ProcesoNodo).get(nodo_id)
    if not nodo:
        return RedirectResponse(RUTA, status_code=303)
    exigir_empresa(user, db, nodo.empresa_id)
    q = db.query(ProcesoNodo).filter(ProcesoNodo.empresa_id == nodo.empresa_id)
    q = q.filter(ProcesoNodo.parent_id == nodo.parent_id) if nodo.parent_id else q.filter(ProcesoNodo.parent_id.is_(None))
    hermanos = sorted(q.all(), key=lambda h: ((h.orden or 0), h.id))
    for i, h in enumerate(hermanos):  # re-numera por si había huecos o repetidos
        h.orden = i
    pos = next(i for i, h in enumerate(hermanos) if h.id == nodo.id)
    vecino = pos - 1 if direccion == "arriba" else pos + 1
    if 0 <= vecino < len(hermanos):
        hermanos[pos].orden, hermanos[vecino].orden = hermanos[vecino].orden, hermanos[pos].orden
    db.commit()
    return _volver(nodo.empresa_id, sp, foco=nodo.id)


@router.post(RUTA + "/nodo/{nodo_id}/eliminar")
def nodo_eliminar(nodo_id: int, sp: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_perm("p_procesos", "editar"))):
    nodo = db.query(ProcesoNodo).get(nodo_id)
    if not nodo:
        return RedirectResponse(RUTA, status_code=303)
    exigir_empresa(user, db, nodo.empresa_id)
    empresa_id, padre_id = nodo.empresa_id, nodo.parent_id
    db.delete(nodo)  # cascade: se lleva todo lo que cuelga de él
    db.commit()
    return _volver(empresa_id, sp, foco=padre_id)
