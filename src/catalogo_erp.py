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

¡OJO! (corregido el 27/10/2026 por la tarde)
--------------------------------------------
Aplicar el catálogo ENTERO a los pedidos es PELIGROSO: hay productos con
variantes que comparten «ID VISTA» pero tienen UM distintas (Kit Kat Normal
HSI / Kit Kat Blanco GBC, Skittle Frutas HWD / Skittle Crazy HWC...).
La primera versión de este módulo cogía la primera variante y cambiaba UMs
que ya estaban bien. Por eso:

  - aplicar_a_lineas() e indice_correcciones() usan SOLO las correcciones
    verificadas de src/recursos/correcciones_erp.json.
  - cargar_catalogo() (catálogo completo fusionado) se usa ÚNICAMENTE para
    el endpoint GET /supermaestro, para que Flow pueda descargarlo.
  - Todo lo que Flow mande bien se deja EXACTAMENTE como viene.

FAMILIAS BLINDADAS (añadido el 27/10/2026 a petición de la encargada)
----------------------------------------------------------------------
Kit Kat, Skittle, Haribo 100, Estuchados 3.90, Helados y Kojack tienen
variantes ("hijos") que comparten ID VISTA pero tienen UMs DISTINTAS.
La lista está en src/recursos/correcciones_erp.json -> FAMILIAS_PROTEGIDAS.
Ninguna línea cuyo nombre contenga una de esas palabras se toca, ni aquí,
ni en el receptor, ni en el catálogo que se sirve a Flow.
"""

import json
import re
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


def familias_protegidas():
    """Palabras clave de familias con variantes. Nunca falla (devuelve [])."""
    datos = _leer_json_seguro(CORRECCIONES_ERP)
    if not isinstance(datos, dict):
        return []
    return [
        str(x).strip()
        for x in datos.get("FAMILIAS_PROTEGIDAS", [])
        if str(x).strip()
    ]


def familia_de(descripcion):
    """Devuelve la palabra de familia que aparece en la descripción, o None."""
    texto = _normaliza(descripcion)
    for fam in familias_protegidas():
        if _normaliza(fam) in texto:
            return fam
    return None


def es_familia_protegida(descripcion):
    """True si la línea pertenece a una familia con variantes: NO SE TOCA."""
    return familia_de(descripcion) is not None


def familias_en_lineas(lineas):
    """{familia: nº de líneas} de las líneas protegidas (para informar)."""
    conteo = {}
    for linea in lineas or []:
        if not isinstance(linea, dict):
            continue
        fam = familia_de(linea.get("descripcion", ""))
        if fam:
            conteo[fam] = conteo.get(fam, 0) + 1
    return conteo


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
        nombre = str(c.get("PRODUCTO", "") or "").strip()
        if es_familia_protegida(nombre):
            avisos.append(
                f"AVISO: la corrección de '{nombre}' cae en una familia "
                "protegida; NO se aplica (revísala a mano)."
            )
            continue
        destinos = por_vista.get(v) or []
        # SOLO la fila cuyo NOMBRE coincide. Antes se modificaban TODAS las
        # filas con ese ID VISTA, y en una familia eso cambiaba la UM de los
        # "hijos" (variantes) que ya venían bien de Flow.
        coincidentes = [
            d for d in destinos
            if not es_familia_protegida(d.get("PRODUCTO", ""))
            and _mismo_producto(nombre, d.get("PRODUCTO", ""))
        ]
        if coincidentes:
            for d in coincidentes:
                if id_erp:
                    d["ID ERP"] = id_erp
                if um_erp:
                    d["UM ERP"] = um_erp
                d["_CORREGIDO_ERP"] = True
        elif destinos:
            avisos.append(
                f"AVISO: ID VISTA {v} existe en el catálogo con otro nombre; "
                f"no se pisa ninguna variante ('{nombre}')."
            )
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


def correcciones_aplicables():
    """Las ÚNICAS filas que se pueden escribir en un pedido.

    Son exclusivamente las correcciones verificadas en el ERP. Se excluyen
    las marcadas NO_EXISTE_EN_ERP (no se inventa un código sustituto).

    Esto evita el fallo del 27/10/2026: aplicar las 304 filas del catálogo
    cambiaba también las UM de familias con variantes (Kit Kat, Skittles...),
    que ya venían bien de Flow.
    """
    out = []
    for c in correcciones_erp():
        if str(c.get("ESTADO", "") or "").upper() == "NO_EXISTE_EN_ERP":
            continue
        if es_familia_protegida(c.get("PRODUCTO", "")):
            # Seguro: jamás se reescribe la UM de una familia con variantes.
            continue
        id_erp = str(c.get("ID ERP", "") or "").strip()
        um_erp = str(c.get("UM ERP", "") or "").strip().split("/")[0].strip()
        if not id_erp and not um_erp:
            continue
        fila = dict(c)
        fila["ID ERP"] = id_erp
        fila["UM ERP"] = um_erp
        out.append(fila)
    return out


def indice_correcciones():
    """Índice para aplicar a pedidos: SOLO las correcciones verificadas."""
    return indice_por_vista(correcciones_aplicables())


def avisos_estado():
    """Avisos de diagnóstico para imprimir en la consola del receptor.

    No construye el catálogo entero: solo comprueba qué hay disponible.
    """
    avisos = []
    if not SUPERMAESTRO_LOCAL.is_file() and not SUPERMAESTRO_FLOW.is_file():
        avisos.append(
            "No hay catálogo local en data/. Se aplican solo las correcciones "
            "ERP verificadas (es suficiente)."
        )
    if not correcciones_erp():
        avisos.append(
            f"AVISO: no se pudo leer {CORRECCIONES_ERP.name}. "
            "No se corregirá ningún artículo."
        )
    for nombre in no_existentes():
        avisos.append(
            f"'{nombre}' ya NO EXISTE en el ERP: no se sustituye; "
            "el informe la marcará."
        )
    fams = familias_protegidas()
    if fams:
        avisos.append(
            "Familias BLINDADAS (sus UM no se tocan NUNCA): " + ", ".join(fams)
        )
    for c in correcciones_erp():
        if es_familia_protegida(c.get("PRODUCTO", "")):
            avisos.append(
                f"AVISO: '{c.get('PRODUCTO', '')}' es una familia protegida; "
                "su corrección NO se aplica."
            )
    return avisos


def _normaliza(s):
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", str(s or "").lower())
        if not unicodedata.combining(ch)
    )


def _tokens(s):
    """Palabras significativas de un nombre (sin acentos, sin '(CI)'...)."""
    return {
        t for t in re.split(r"[^a-z0-9]+", _normaliza(s))
        if len(t) >= 3
    }


def _mismo_producto(a, b):
    """True si los dos nombres son el mismo producto, admitiendo que uno
    sea más corto ('Cuchara Madera' vs '(CI) Cuchara Madera Caja 2000').

    Si difieren en una palabra de peso ('Mediana' vs 'Pequeña',
    'Normal' vs 'Blanco'), devuelve False: son VARIANTES distintas.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


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
    if es_familia_protegida(linea.get("descripcion", "")):
        # FAMILIAS BLINDADAS: Kit Kat, Skittle, Haribo, Estuchados, Helados,
        # Kojack y sus "hijos". Sus UM vienen bien de Flow; ni tocarlas.
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
    """Aplica SOLO las correcciones ERP a una lista de líneas.

    Devuelve el nº de líneas corregidas. Si no se pasa índice, se usa
    indice_correcciones() (nunca el catálogo entero, por el problema de las
    variantes con UM distintas).
    """
    if indice is None:
        indice = indice_correcciones()
    corregidas = 0
    for linea in lineas or []:
        if isinstance(linea, dict) and aplicar_a_linea(linea, indice):
            corregidas += 1
    return corregidas
