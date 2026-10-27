# -*- coding: utf-8 -*-
"""Catálogo SUPERMAESTRO: una sola fuente de verdad para el receptor y el robot.

Problema que arregla (detectado el 27/10/2026 con el pedido de 100 líneas):

  * La pantalla «CATÁLOGO SUPERMAESTRO» (src/ui/catalogo.py) guardaba en
    data/supermaestro.json.
  * El receptor (src/receptor.py) leía data/supermaestro_flow.json, que NO
    existía. Y si faltaba, se callaba y dejaba el pedido SIN corregir.
    Resultado: el robot recibía los idErp/umErp viejos de Flow y el ERP
    rechazaba 4 artículos.

Cómo queda:

  * Se leen AMBOS archivos data/ (el que exista) para no romper nada.
  * data/ sigue FUERA de Git (.gitignore), como quiere la encargada.
  * Las correcciones verificadas en el ERP van en
    src/recursos/correcciones_erp.json, que SÍ está en Git. Tienen PRIORIDAD
    sobre el catálogo local: así llegan al PC del cine con un simple git pull
    y un catálogo local desactualizado no puede volver a colar una UM mala.
  * Si no hay catálogo, se AVISA en la consola en vez de fallar en silencio.
"""

import json
import sys
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SUPERMAESTRO_LOCAL = BASE_DIR / "data" / "supermaestro.json"
SUPERMAESTRO_FLOW = BASE_DIR / "data" / "supermaestro_flow.json"
CORRECCIONES_ERP = Path(__file__).resolve().parent / "recursos" / "correcciones_erp.json"


def _leer_json_seguro(ruta):
    """Devuelve el JSON, o None si no existe. Nunca revienta: solo avisa."""
    try:
        with open(ruta, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"[CATÁLOGO] AVISO: no se pudo leer {ruta}: {e}")
        try:
            sys.stdout.flush()
        except Exception:
            pass
        return None


def _productos_de(datos):
    """Acepta {'SUPERMAESTRO': [...]}, {'CORRECCIONES': [...]} o una lista."""
    if isinstance(datos, dict):
        for clave in ("SUPERMAESTRO", "CORRECCIONES"):
            if isinstance(datos.get(clave), list):
                return datos[clave]
        return []
    return datos if isinstance(datos, list) else []


def correcciones_erp():
    """Lista de correcciones verificadas (nunca falla: devuelve [])."""
    return _productos_de(_leer_json_seguro(CORRECCIONES_ERP) or [])


def no_existentes():
    """Nombres marcados como NO_EXISTE_EN_ERP (para avisar, no para sustituir)."""
    return [
        str(c.get("PRODUCTO", "")).strip()
        for c in correcciones_erp()
        if str(c.get("ESTADO", "")).upper() == "NO_EXISTE_EN_ERP"
    ]


def cargar_catalogo():
    """Devuelve (productos, avisos) con las correcciones ERP ya aplicadas.

    - productos: lista de filas con, al menos, ID VISTA / PRODUCTO / ID ERP / UM ERP.
    - avisos: lista de strings para imprimir en la consola del receptor.
    """
    avisos = []
    productos = []

    for ruta in (SUPERMAESTRO_LOCAL, SUPERMAESTRO_FLOW):
        datos = _leer_json_seguro(ruta)
        if datos is None:
            continue
        lista = _productos_de(datos)
        if lista:
            productos = [dict(p) for p in lista]
            break

    if not productos:
        avisos.append(
            "No hay catálogo local (data/supermaestro.json ni "
            "data/supermaestro_flow.json). Solo se aplicarán las correcciones ERP."
        )

    corr = correcciones_erp()
    if not corr:
        avisos.append(
            f"No se pudieron leer las correcciones ERP versionadas ({CORRECCIONES_ERP.name})."
        )

    por_vista = {}
    for p in productos:
        v = str(p.get("ID VISTA", "") or "").strip()
        if v:
            por_vista.setdefault(v, []).append(p)

    for c in corr:
        v = str(c.get("ID VISTA", "") or "").strip()
        if not v:
            continue
        estado = str(c.get("ESTADO", "") or "").upper()
        if estado == "NO_EXISTE_EN_ERP":
            # No se inventa un código sustituto. Se deja la línea tal cual
            # llega de Flow para que el robot v6b la anote como incidencia real
            # y SIGA con las demás (decisión de la encargada: "LA DEJAMOS").
            avisos.append(
                f"'{c.get('PRODUCTO', '')}' (ID VISTA {v}) ya NO EXISTE en el ERP: "
                "no se sustituye; el informe la marcará."
            )
            continue
        id_erp = str(c.get("ID ERP", "") or "").strip()
        um_erp = str(c.get("UM ERP", "") or "").strip().split("/")[0].strip()
        if not id_erp and not um_erp:
            continue
        destinos = por_vista.get(v)
        if destinos:
            for d in destinos:
                if id_erp:
                    d["ID ERP"] = id_erp
                if um_erp:
                    d["UM ERP"] = um_erp
                d["_CORREGIDO_ERP"] = True
        else:
            fila = dict(c)
            fila["_CORREGIDO_ERP"] = True
            productos.append(fila)
            por_vista.setdefault(v, []).append(fila)

    return productos, avisos


def indice_por_vista(productos):
    """{ID VISTA: [fila, fila, ...]} para buscar rápido por línea de pedido."""
    indice = {}
    for p in productos:
        vista = str(p.get("ID VISTA", "") or "").strip()
        if vista and vista != "—":
            indice.setdefault(vista, []).append(p)
    return indice


def _normaliza(s):
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", str(s or "").lower())
        if not unicodedata.combining(ch)
    )


def mejor_candidato(candidatos, descripcion):
    """De varias filas con el mismo ID VISTA, elige la que más se parece al
    nombre que trae el pedido (familias con variantes, p. ej. Haribo)."""
    desc = _normaliza(descripcion)
    return max(
        candidatos,
        key=lambda p: sum(
            1 for w in _normaliza(p.get("PRODUCTO", "")).split()
            if len(w) > 2 and w in desc
        ),
    )


def aplicar_a_linea(linea, indice):
    """Corrige in-place idErp/umErp/unidad/codigo de UNA línea del pedido.

    Devuelve (id_erp, um_erp) aplicados, o None si no hubo corrección.
    No toca líneas de granel (origen == 'granel').
    """
    if linea.get("origen") == "granel":
        return None
    vista = str(linea.get("idVista", "") or "").strip()
    candidatos = indice.get(vista)
    if not candidatos:
        return None
    mejor = mejor_candidato(candidatos, linea.get("descripcion", ""))
    id_erp = str(mejor.get("ID ERP", "") or "").strip()
    um_erp = str(mejor.get("UM ERP", "") or "").strip().split("/")[0].strip()
    if not id_erp and not um_erp:
        return None
    if id_erp:
        linea["codigo"] = id_erp
        linea["idArticulo"] = id_erp
        linea["idErp"] = id_erp
    if um_erp:
        linea["umErp"] = um_erp
        linea["unidad"] = um_erp
    return (id_erp, um_erp)


def aplicar_a_lineas(lineas, indice=None):
    """Aplica el catálogo a una lista de líneas. Devuelve el nº de corregidas."""
    if indice is None:
        productos, _ = cargar_catalogo()
        indice = indice_por_vista(productos)
    corregidas = 0
    for linea in lineas or []:
        if isinstance(linea, dict) and aplicar_a_linea(linea, indice):
            corregidas += 1
    return corregidas
