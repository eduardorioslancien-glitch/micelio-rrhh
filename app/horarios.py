# -*- coding: utf-8 -*-
"""Control de Asistencia > Horarios (07/10/2026).

Un horario tiene un nombre y, para cada día de la semana que se trabaja, la
hora de inicio y la de fin de la jornada. A la jornada de cada día se le resta
1 hora de refrigerio, y el total de horas de la semana no puede pasar de 48.
Los horarios alimentan la lista desplegable "Horario" de Registro de Pedidos
y se publican en Trabaja con Nosotros."""
import os
import re
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import get_db
from .models import (
    Horario, PedidoPersonal, User, DIAS_SEMANA, HORAS_REFRIGERIO, MAX_HORAS_SEMANALES,
)
from .auth import require_perm
from .rrhh import _ctx, _a_lima

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.filters["lima"] = _a_lima
router = APIRouter()
RUTA = "/rrhh/asistencia/horarios"
_HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _volver(error: str = "", ok: str = ""):
    url = RUTA
    if error:
        url += "?error=" + quote(error)
    elif ok:
        url += "?ok=" + quote(ok)
    return RedirectResponse(url, status_code=303)


def _leer_dias(form) -> tuple:
    """Lee dia_N / ini_N / fin_N del formulario. Devuelve (dias_dict, error)."""
    dias = {}
    for i in range(7):
        if not form.get(f"dia_{i}"):
            continue
        ini, fin = (form.get(f"ini_{i}") or "").strip(), (form.get(f"fin_{i}") or "").strip()
        if not (_HHMM.match(ini) and _HHMM.match(fin)):
            return None, f"{DIAS_SEMANA[i]}: indica la hora de inicio y la de fin."
        ini = ini.zfill(5)
        fin = fin.zfill(5)
        if Horario._minutos(fin) <= Horario._minutos(ini):
            return None, f"{DIAS_SEMANA[i]}: la hora de fin debe ser posterior a la de inicio."
        dias[str(i)] = {"inicio": ini, "fin": fin}
    if not dias:
        return None, "Marca al menos un día de trabajo."
    return dias, ""


def _validar(nombre: str, dias: dict, excluir_id=None, db: Session = None) -> str:
    h = Horario(nombre=nombre, dias=dias)
    for i in range(7):
        if str(i) in dias and h.horas_dia(i) <= 0:
            return (f"{DIAS_SEMANA[i]}: la jornada tiene que ser mayor a {int(HORAS_REFRIGERIO)} hora "
                    "(a cada día se le resta 1 hora de refrigerio).")
    if h.total_semanal > MAX_HORAS_SEMANALES:
        return (f"El horario suma {h.total_semanal:g} horas a la semana (ya descontado el refrigerio): "
                f"no puede pasar de {MAX_HORAS_SEMANALES:g}.")
    if db is not None:
        q = db.query(Horario).filter(Horario.nombre == nombre)
        if excluir_id:
            q = q.filter(Horario.id != excluir_id)
        if q.first():
            return "Ya existe un horario con ese nombre."
    return ""


@router.get(RUTA, response_class=HTMLResponse)
def horarios_list(request: Request, error: str = "", ok: str = "", db: Session = Depends(get_db),
                  user: User = Depends(require_perm("horarios", "ver"))):
    horarios = db.query(Horario).order_by(Horario.nombre).all()
    en_uso = {h.id: db.query(PedidoPersonal).filter(PedidoPersonal.horario_id == h.id).count() for h in horarios}
    return templates.TemplateResponse(request, "rrhh_horarios.html", _ctx(
        request, user, horarios=horarios, en_uso=en_uso, dias_semana=DIAS_SEMANA,
        refrigerio=HORAS_REFRIGERIO, max_semanal=MAX_HORAS_SEMANALES, error=error, ok=ok, active="horarios",
    ))


@router.post(RUTA)
async def horario_crear(request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require_perm("horarios", "editar"))):
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    if not nombre:
        return _volver(error="Ponle un nombre al horario.")
    dias, error = _leer_dias(form)
    error = error or _validar(nombre, dias or {}, None, db)
    if error:
        return _volver(error=error)
    db.add(Horario(nombre=nombre, dias=dias, activo=True))
    db.commit()
    return _volver(ok=f"Horario «{nombre}» creado.")


@router.post(RUTA + "/{horario_id}/editar")
async def horario_editar(horario_id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(require_perm("horarios", "editar"))):
    h = db.query(Horario).get(horario_id)
    if not h:
        return _volver(error="Ese horario ya no existe.")
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    if not nombre:
        return _volver(error="Ponle un nombre al horario.")
    dias, error = _leer_dias(form)
    error = error or _validar(nombre, dias or {}, horario_id, db)
    if error:
        return _volver(error=error)
    h.nombre, h.dias = nombre, dias
    db.commit()
    return _volver(ok=f"Horario «{nombre}» actualizado.")


@router.post(RUTA + "/{horario_id}/toggle")
def horario_toggle(horario_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_perm("horarios", "editar"))):
    h = db.query(Horario).get(horario_id)
    if h:
        h.activo = not h.activo
        db.commit()
    return _volver()


@router.post(RUTA + "/{horario_id}/eliminar")
def horario_eliminar(horario_id: int, db: Session = Depends(get_db),
                     user: User = Depends(require_perm("horarios", "editar"))):
    h = db.query(Horario).get(horario_id)
    if not h:
        return _volver()
    if db.query(PedidoPersonal).filter(PedidoPersonal.horario_id == h.id).count():
        return _volver(error="Este horario ya se usa en pedidos de personal: desactívalo en vez de eliminarlo.")
    db.delete(h)
    db.commit()
    return _volver(ok="Horario eliminado.")
