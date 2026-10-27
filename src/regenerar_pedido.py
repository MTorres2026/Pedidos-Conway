# -*- coding: utf-8 -*-
r"""Reaplica las CORRECCIONES ERP a un pedido YA GUARDADO en data/.

¿Para qué sirve?
----------------
El pedido de 100 líneas del 27/10/2026 se guardó ANTES de arreglar el
catálogo, así que sigue teniendo los códigos/UM viejos. No basta con volver
a enviarlo desde el móvil: el receptor (src/receptor.py) IGNORA pedidos
duplicados por orderId. Este script corrige los archivos ya guardados, sin
tocar el ERP y sin pedirle nada al móvil.

SOLO se corrigen los artículos de src/recursos/correcciones_erp.json
(verificados en el ERP). Todo lo demás se deja EXACTAMENTE como lo mandó
Flow. Es importante: hay familias con variantes que comparten «ID VISTA»
pero tienen UM distintas (Kit Kat, Skittles...); aplicar el catálogo entero
cambiaría UMs que ya están bien.

Actualiza:
  * data/pedido_actual.json                        (si existe)
  * data/pedidos_para_procesar/pedido_*.json      (el más reciente, o todos con --todos)

NO toca:
  * data/historial_pedidos/  (el histórico se queda como se recibió)
  * .env, ni ninguna credencial
  * el ERP: este script solo lee/escribe archivos locales

Uso (desde la raíz del proyecto):

  python src\regenerar_pedido.py
  python src\regenerar_pedido.py --todos
  python src\regenerar_pedido.py --restaurar
  python src\regenerar_pedido.py "data\pedidos_para_procesar\pedido_100.json"

  --restaurar  recupera primero la copia .bak_* más reciente de cada archivo.
               Sirve para deshacer una ejecución anterior y volver a aplicar
               solo las correcciones buenas.

Antes de sobrescribir, guarda una copia .bak_<fecha> junto a cada archivo.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from catalogo_erp import (  # noqa: E402
    aplicar_a_linea,
    avisos_estado,
    familias_protegidas,
    indice_correcciones,
    resumen_familias,
    no_existentes,
)

PEDIDO_ACTUAL = BASE_DIR / "data" / "pedido_actual.json"
PROCESAR_DIR = BASE_DIR / "data" / "pedidos_para_procesar"


def _leer(ruta):
    with open(ruta, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _escribir(ruta, datos):
    copia = ruta.with_name(
        ruta.name + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    shutil.copy2(ruta, copia)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)
    return copia


def _lineas_de(datos):
    if isinstance(datos, dict):
        return datos.get("lineas") or datos.get("pedido", {}).get("lineas") or []
    return datos if isinstance(datos, list) else []


def _ultimo_backup(ruta):
    candidatos = sorted(ruta.parent.glob(ruta.name + ".bak_*"))
    return candidatos[-1] if candidatos else None


def restaurar(ruta):
    copia = _ultimo_backup(ruta)
    if copia is None:
        print(f"    [aviso] no hay copia .bak_* de {ruta.name}; se deja como está")
        return False
    shutil.copy2(copia, ruta)
    print(f"    restaurado desde: {copia.name}")
    return True


def corregir_archivo(ruta, indice):
    datos = _leer(ruta)
    lineas = _lineas_de(datos)
    if not lineas:
        return 0, [], "sin líneas", ""

    detalle_familias = resumen_familias(lineas)

    cambios = []
    for n, linea in enumerate(lineas, 1):
        if not isinstance(linea, dict):
            continue
        antes = (
            str(linea.get("codigo", "") or ""),
            str(linea.get("unidad", "") or ""),
        )
        aplicar_a_linea(linea, indice)
        despues = (
            str(linea.get("codigo", "") or ""),
            str(linea.get("unidad", "") or ""),
        )
        if antes != despues:
            cambios.append(
                f"    línea {n:>3} | {str(linea.get('descripcion', ''))[:42]:<42} "
                f"| {antes[0] or '(vacío)'} / {antes[1] or '(vacío)'}"
                f"  ->  {despues[0]} / {despues[1]}"
            )

    if cambios:
        copia = _escribir(ruta, datos)
        return len(cambios), cambios, f"copia: {copia.name}", detalle_familias
    return 0, [], "sin cambios (ya estaba corregido)", detalle_familias


def main():
    ap = argparse.ArgumentParser(
        description="Reaplica las correcciones ERP a pedidos ya guardados."
    )
    ap.add_argument("archivo", nargs="?", default=None,
                    help="Ruta a un pedido concreto. Si se omite, se corrigen "
                         "pedido_actual.json y el más reciente de pedidos_para_procesar/.")
    ap.add_argument("--todos", action="store_true",
                    help="Corregir TODOS los pedidos de pedidos_para_procesar/.")
    ap.add_argument("--restaurar", action="store_true",
                    help="Restaurar primero la copia .bak_* más reciente de cada archivo.")
    args = ap.parse_args()

    indice = indice_correcciones()
    correcciones = sum(len(v) for v in indice.values())

    print("=" * 78)
    print("  REGENERAR PEDIDO CON LAS CORRECCIONES ERP VERIFICADAS")
    print("=" * 78)
    print(f"  Correcciones activas: {correcciones} artículos")
    print("  (el resto de líneas se dejan EXACTAMENTE como las mandó Flow)")
    fams = familias_protegidas()
    if fams:
        print("  Familias BLINDADAS (intocables): " + ", ".join(fams))
    for a in avisos_estado():
        print(f"  [aviso] {a}")
    for nombre in no_existentes():
        print(f"  [aviso] '{nombre}' ya no existe en el ERP: se deja su línea "
              "para que el informe la marque (decisión: LA DEJAMOS).")
    print()

    if args.archivo:
        rutas = [Path(args.archivo)]
    else:
        rutas = []
        if PEDIDO_ACTUAL.is_file():
            rutas.append(PEDIDO_ACTUAL)
        if PROCESAR_DIR.is_dir():
            candidatos = sorted(
                PROCESAR_DIR.glob("pedido_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            rutas.extend(candidatos if args.todos else candidatos[:1])

    if not rutas:
        print("  No encontré ningún pedido que corregir.")
        print(f"  Busqué: {PEDIDO_ACTUAL} y {PROCESAR_DIR / 'pedido_*.json'}")
        return

    if args.restaurar:
        print("  -- Restaurando copias de seguridad --")
        for ruta in rutas:
            if ruta.is_file():
                print(f"  {ruta.name}")
                restaurar(ruta)
        print()

    total = 0
    for ruta in rutas:
        if not ruta.is_file():
            print(f"  [aviso] no existe: {ruta}")
            continue
        try:
            n, cambios, nota, detalle_familias = corregir_archivo(ruta, indice)
        except Exception as e:
            print(f"  [ERROR] {ruta}: {e}")
            continue
        try:
            nombre = str(ruta.relative_to(BASE_DIR)) if ruta.is_relative_to(BASE_DIR) else str(ruta)
        except AttributeError:
            nombre = str(ruta)
        print(f"  {nombre}")
        print(f"    {n} línea(s) corregida(s) · {nota}")
        for c in cambios:
            print(c)
        if detalle_familias:
            print("    familias BLINDADAS intactas (código/UM real):")
            for linea in detalle_familias:
                print(linea)
        print()
        total += n

    print("=" * 78)
    print(f"  TOTAL: {total} línea(s) corregida(s) en {len(rutas)} archivo(s).")
    print("  Comprueba que SOLO aparecen las líneas de los artículos")
    print("  verificados en el ERP. Las demás NO deben cambiar.")
    print("  NO se ha tocado el ERP; el robot sigue sin pulsar Guardar.")
    print("=" * 78)


if __name__ == "__main__":
    main()
