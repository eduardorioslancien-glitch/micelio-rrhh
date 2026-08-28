# -*- coding: utf-8 -*-
"""
Sincroniza los catálogos de Parámetros — Holdings, Unidades de Negocio,
Empresas, Líneas de Producto, Principios/Valores/Competencias, Áreas,
Gerencias, Cargos y Funciones, Esquemas de Pago, Sedes, Bancos, Centros de
Costo — desde una
base de datos hacia otra (pensado para: laptop de Eduardo -> servidor de
producción). No sincroniza logos/firmas (son archivos binarios, se suben
directo en cada entorno desde Parametrización).

Solo toca estas tablas de estructura organizacional; nunca toca Personal,
Selección, Usuarios ni ningún dato de un trabajador. Hace upsert por nombre
(no borra nada que exista del otro lado y no esté en el archivo importado),
para no romper referencias de personal que ya esté cargado en el destino.

Uso:
    1) En la laptop (con la BD local):
         python sync_parametrizacion.py export
       -> genera parametros_export.json en esta misma carpeta.

    2) Ese archivo se sube a GitHub junto con el resto del código (queda
       versionado, así "git pull" en el servidor ya lo trae solo).

    3) En el servidor, apuntando a su propia BD (ya sea local o vía
       HRAPP_DB_PATH/el volumen de Docker):
         python sync_parametrizacion.py import parametros_export.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Holding, UnidadNegocio, Empresa, LineaProducto, Catalogo, Competencia, Cargo, CargoRequisitoCompetencia,
    EsquemaPago,
)

DEFAULT_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parametros_export.json")


def export_data(out_path: str):
    db = SessionLocal()
    try:
        data = {
            "holdings": [
                {"nombre": h.nombre, "descripcion": h.descripcion, "activo": h.activo}
                for h in db.query(Holding).order_by(Holding.nombre).all()
            ],
            "unidades_negocio": [
                {
                    "nombre": u.nombre, "descripcion": u.descripcion, "activo": u.activo,
                    "holding_nombre": u.holding.nombre if u.holding else None,
                }
                for u in db.query(UnidadNegocio).order_by(UnidadNegocio.nombre).all()
            ],
            "empresas": [
                {
                    "nombre": e.nombre, "razon_social": e.razon_social, "ruc": e.ruc,
                    "regimen_laboral": e.regimen_laboral, "representante_legal": e.representante_legal,
                    "gerente_nombre": e.gerente_nombre, "gerente_email": e.gerente_email,
                    "jefe_rrhh_nombre": e.jefe_rrhh_nombre, "jefe_rrhh_email": e.jefe_rrhh_email,
                    "activo": e.activo,
                    "unidad_negocio_nombre": e.unidad_negocio.nombre if e.unidad_negocio else None,
                }
                for e in db.query(Empresa).order_by(Empresa.nombre).all()
            ],
            "lineas_producto": [
                {
                    "nombre": lp.nombre, "descripcion": lp.descripcion, "activo": lp.activo,
                    "empresa_nombre": lp.empresa.nombre if lp.empresa else None,
                }
                for lp in db.query(LineaProducto).all()
            ],
            "catalogos": [
                {"tipo": c.tipo, "nombre": c.nombre, "activo": c.activo}
                for c in db.query(Catalogo).order_by(Catalogo.tipo, Catalogo.nombre).all()
            ],
            "competencias": [
                {
                    "tipo": c.tipo, "nombre": c.nombre, "descripcion": c.descripcion,
                    "nivel_1": c.nivel_1, "nivel_2": c.nivel_2, "nivel_3": c.nivel_3, "nivel_4": c.nivel_4,
                    "conductas_no_deseadas": c.conductas_no_deseadas, "activo": c.activo,
                }
                for c in db.query(Competencia).order_by(Competencia.tipo, Competencia.nombre).all()
            ],
            "cargos": [
                {
                    "nombre": c.nombre, "descripcion": c.descripcion,
                    "funciones": c.funciones or [], "responsabilidades": c.responsabilidades or [],
                    "requisito_academico": c.requisito_academico,
                    "requisito_experiencia": c.requisito_experiencia,
                    "requisito_conocimientos": c.requisito_conocimientos,
                    "activo": c.activo,
                    "reporta_a_nombre": c.reporta_a.nombre if c.reporta_a else None,
                }
                for c in db.query(Cargo).order_by(Cargo.nombre).all()
            ],
            "cargo_requisitos": [
                {
                    "cargo_nombre": r.cargo.nombre, "competencia_tipo": r.competencia.tipo,
                    "competencia_nombre": r.competencia.nombre, "nivel_requerido": r.nivel_requerido,
                }
                for r in db.query(CargoRequisitoCompetencia).all()
            ],
            "esquemas_pago": [
                {
                    "cargo_nombre": e.cargo.nombre, "sueldo_base": e.sueldo_base,
                    "comision_variable": e.comision_variable, "movilidad": e.movilidad,
                    "combustible": e.combustible, "otros_ingresos": e.otros_ingresos, "notas": e.notas,
                }
                for e in db.query(EsquemaPago).all()
            ],
        }
    finally:
        db.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Exportado: {out_path}")
    for k, v in data.items():
        print(f"  {k}: {len(v)}")


def import_data(in_path: str):
    with open(in_path, encoding="utf-8") as f:
        data = json.load(f)

    db = SessionLocal()
    try:
        # 1. Holdings
        for h in data.get("holdings", []):
            obj = db.query(Holding).filter_by(nombre=h["nombre"]).first()
            if not obj:
                obj = Holding(nombre=h["nombre"])
                db.add(obj)
            obj.descripcion = h.get("descripcion")
            obj.activo = h.get("activo", True)
        db.commit()

        # 2. Unidades de Negocio (resuelve holding_id por nombre)
        for u in data.get("unidades_negocio", []):
            obj = db.query(UnidadNegocio).filter_by(nombre=u["nombre"]).first()
            if not obj:
                obj = UnidadNegocio(nombre=u["nombre"])
                db.add(obj)
            obj.descripcion = u.get("descripcion")
            obj.activo = u.get("activo", True)
            holding_nombre = u.get("holding_nombre")
            if holding_nombre:
                h = db.query(Holding).filter_by(nombre=holding_nombre).first()
                obj.holding_id = h.id if h else None
        db.commit()

        # 3. Empresas (resuelve unidad_negocio_id por nombre)
        for e in data.get("empresas", []):
            obj = db.query(Empresa).filter_by(nombre=e["nombre"]).first()
            if not obj:
                obj = Empresa(nombre=e["nombre"])
                db.add(obj)
            obj.razon_social = e.get("razon_social")
            obj.ruc = e.get("ruc")
            obj.regimen_laboral = e.get("regimen_laboral")
            obj.representante_legal = e.get("representante_legal")
            obj.gerente_nombre = e.get("gerente_nombre")
            obj.gerente_email = e.get("gerente_email")
            obj.jefe_rrhh_nombre = e.get("jefe_rrhh_nombre")
            obj.jefe_rrhh_email = e.get("jefe_rrhh_email")
            obj.activo = e.get("activo", True)
            un_nombre = e.get("unidad_negocio_nombre")
            if un_nombre:
                un = db.query(UnidadNegocio).filter_by(nombre=un_nombre).first()
                obj.unidad_negocio_id = un.id if un else None
        db.commit()

        # 3b. Líneas de Producto (resuelve empresa_id por nombre)
        for lp in data.get("lineas_producto", []):
            empresa_nombre = lp.get("empresa_nombre")
            empresa = db.query(Empresa).filter_by(nombre=empresa_nombre).first() if empresa_nombre else None
            if not empresa:
                print(f"  aviso: se omite línea de producto '{lp['nombre']}' (empresa '{empresa_nombre}' no encontrada)")
                continue
            obj = db.query(LineaProducto).filter_by(nombre=lp["nombre"], empresa_id=empresa.id).first()
            if not obj:
                obj = LineaProducto(nombre=lp["nombre"], empresa_id=empresa.id)
                db.add(obj)
            obj.descripcion = lp.get("descripcion")
            obj.activo = lp.get("activo", True)
        db.commit()

        # 4. Catálogos (área, gerencia, sede, banco, centro_costo)
        for c in data.get("catalogos", []):
            obj = db.query(Catalogo).filter_by(tipo=c["tipo"], nombre=c["nombre"]).first()
            if not obj:
                obj = Catalogo(tipo=c["tipo"], nombre=c["nombre"])
                db.add(obj)
            obj.activo = c.get("activo", True)
        db.commit()

        # 4. Principios, Valores y Competencias
        for c in data.get("competencias", []):
            obj = db.query(Competencia).filter_by(tipo=c["tipo"], nombre=c["nombre"]).first()
            if not obj:
                obj = Competencia(tipo=c["tipo"], nombre=c["nombre"])
                db.add(obj)
            obj.descripcion = c.get("descripcion")
            obj.nivel_1 = c.get("nivel_1")
            obj.nivel_2 = c.get("nivel_2")
            obj.nivel_3 = c.get("nivel_3")
            obj.nivel_4 = c.get("nivel_4")
            obj.conductas_no_deseadas = c.get("conductas_no_deseadas")
            obj.activo = c.get("activo", True)
        db.commit()

        # 5. Cargos y Funciones — primero los datos propios, luego reporta_a
        #    (en dos pasadas porque un cargo puede reportar a otro que
        #    todavía no exista en el destino).
        for c in data.get("cargos", []):
            obj = db.query(Cargo).filter_by(nombre=c["nombre"]).first()
            if not obj:
                obj = Cargo(nombre=c["nombre"])
                db.add(obj)
            obj.descripcion = c.get("descripcion")
            obj.funciones = c.get("funciones") or []
            obj.responsabilidades = c.get("responsabilidades") or []
            obj.requisito_academico = c.get("requisito_academico")
            obj.requisito_experiencia = c.get("requisito_experiencia")
            obj.requisito_conocimientos = c.get("requisito_conocimientos")
            obj.activo = c.get("activo", True)
        db.commit()
        for c in data.get("cargos", []):
            reporta_a_nombre = c.get("reporta_a_nombre")
            if not reporta_a_nombre:
                continue
            obj = db.query(Cargo).filter_by(nombre=c["nombre"]).first()
            padre = db.query(Cargo).filter_by(nombre=reporta_a_nombre).first()
            obj.reporta_a_id = padre.id if padre else None
        db.commit()

        # 6. Competencias requeridas por cada Cargo (MOF)
        for r in data.get("cargo_requisitos", []):
            cargo = db.query(Cargo).filter_by(nombre=r["cargo_nombre"]).first()
            comp = db.query(Competencia).filter_by(
                tipo=r["competencia_tipo"], nombre=r["competencia_nombre"]).first()
            if not cargo or not comp:
                print(f"  aviso: se omite requisito de '{r['cargo_nombre']}' "
                      f"-> '{r['competencia_nombre']}' (cargo o competencia no encontrados)")
                continue
            obj = db.query(CargoRequisitoCompetencia).filter_by(
                cargo_id=cargo.id, competencia_id=comp.id).first()
            if not obj:
                obj = CargoRequisitoCompetencia(
                    cargo_id=cargo.id, competencia_id=comp.id, nivel_requerido=r["nivel_requerido"])
                db.add(obj)
            else:
                obj.nivel_requerido = r["nivel_requerido"]
        db.commit()

        # 7. Esquemas de Pago (uno por Cargo)
        for e in data.get("esquemas_pago", []):
            cargo = db.query(Cargo).filter_by(nombre=e["cargo_nombre"]).first()
            if not cargo:
                print(f"  aviso: se omite esquema de pago de '{e['cargo_nombre']}' (cargo no encontrado)")
                continue
            obj = db.query(EsquemaPago).filter_by(cargo_id=cargo.id).first()
            if not obj:
                obj = EsquemaPago(cargo_id=cargo.id)
                db.add(obj)
            obj.sueldo_base = e.get("sueldo_base")
            obj.comision_variable = e.get("comision_variable")
            obj.movilidad = e.get("movilidad")
            obj.combustible = e.get("combustible")
            obj.otros_ingresos = e.get("otros_ingresos")
            obj.notas = e.get("notas")
        db.commit()
    finally:
        db.close()

    print("Importación completa.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="accion", required=True)
    p_export = sub.add_parser("export", help="Exporta los catálogos de la BD actual a un JSON.")
    p_export.add_argument("archivo", nargs="?", default=DEFAULT_JSON_PATH)
    p_import = sub.add_parser("import", help="Importa (upsert) los catálogos de un JSON hacia la BD actual.")
    p_import.add_argument("archivo", nargs="?", default=DEFAULT_JSON_PATH)
    args = parser.parse_args()

    if args.accion == "export":
        export_data(args.archivo)
    else:
        import_data(args.archivo)
