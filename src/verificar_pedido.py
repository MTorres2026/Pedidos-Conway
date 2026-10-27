# -*- coding: utf-8 -*-
r"""Comprueba QUE el JSON que va a leer el robot tiene las correcciones ERP.

Es SOLO DE LECTURA: no modifica ningún archivo y no toca el ERP.
Ejecútalo justo ANTES de lanzar el robot, en la raíz del proyecto:

    python src\verificar_pedido.py
    python src\verificar_pedido.py "data\pedidos_para_procesar\pedido_OTRO.json"

¿Para qué sirve?
----------------
El 27/10/2026 se lanzó una carrera con un JSON que TODAVÍA tenía los códigos
viejos (Bolsa Asa Papel IAH, Cuchara Madera 1003020087, Tapa Vaso GDE).
Además, si se pulsa «Enviar a ERP» desde la pestaña «Pedidos» de CineStock,
esa pantalla REESCRIBE el JSON desde lo que tiene en memoria y puede perder
las correcciones. Este script lo detecta antes de perder 10-15 minutos.

Comprueba:
  * que hay 100 líneas;
  * las 3 correcciones verificadas en el ERP (líneas 26, 28, 29);
  * que la línea 30 (Tapa Infantil) sigue intacta: se deja a propósito;
  * que las familias con variantes conservan sus UM (Kit Kat HSI, Skittle HWD);
  * que no hay campos sinónimos con valores distintos (el robot lee el PRIMER
    alias que encuentra: si 'um' y 'unidad' no coinciden, puede leer el viejo).
"""

import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    # Una sola fuente de verdad: la lista de familias de correcciones_erp.json.
    from catalogo_erp import familia_de as _familia_de
except Exception:
    _familia_de = None

BASE_DIR = Path(__file__).resolve().parent.parent
PREDETERMINADO = BASE_DIR / "data" / "pedidos_para_procesar" / "pedido_FLOW-1793107796283.json"

# Mismos alias que SINONIMOS en robot_erp.py (solo los que aparecen en pedidos).
ALIAS_ARTICULO = {
    "articulo", "art", "codigo", "codigoarticulo", "cod", "codart", "sku",
    "item", "itemid", "idarticulo", "idart", "referencia", "ref",
    "invitemid", "reqlinewrkinvitemid", "codigoproducto", "codproducto",
    "idproducto", "productid", "productcode", "material",
}
ALIAS_UM = {
    "um", "unidad", "unidadmedida", "unidaddemedida", "uom", "unit",
    "unitofmeasure", "unitmeasure", "medida", "umedida", "umcod",
}
ALIAS_DESC = {
    "producto", "descripcion", "nombre", "description", "desc", "name",
    "nombreproducto", "descripcionarticulo", "denominacion", "detalle",
}


def _clave(s):
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _elegir(linea, alias):
    """Igual que el robot: el PRIMER alias en orden de inserción gana."""
    for k, v in linea.items():
        if _clave(k) in alias and v not in (None, ""):
            return str(v).strip(), k
    return "", ""


def _conflictos(linea, alias, elegido):
    """Otros alias con un valor DISTINTO al que leería el robot."""
    out = []
    valor = str(elegido or "").strip().upper()
    for k, v in linea.items():
        if _clave(k) in alias and v not in (None, "") and str(v).strip().upper() != valor:
            out.append(f"{k}={v}")
    return out


def main():
    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else PREDETERMINADO
    print("=" * 78)
    print("  VERIFICACIÓN PREVIA DEL PEDIDO (no modifica nada)")
    print("=" * 78)
    print(f"  Archivo: {ruta}")
    if not ruta.is_file():
        print("  ERROR: no existe ese archivo.")
        return 1
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except Exception as e:
        print(f"  ERROR al leer el JSON: {e}")
        return 1

    lineas = datos.get("lineas") or datos.get("pedido", {}).get("lineas") or []
    print(f"  Líneas: {len(lineas)}")
    problemas = []
    if len(lineas) != 100:
        problemas.append(f"debería haber 100 líneas y hay {len(lineas)}")

    esperado = {
        26: ("1003020091", "ICG", "(CI) Bolsa Asa Papel"),
        28: ("1003020096", "GDE", "(CI) Cuchara Madera"),
        29: ("1003020030", "CJA", "(CI) Tapa Vaso Mediana"),
        # Verificados en el ERP a las 19:17 (captura de Búsqueda Art).
        38: ("1001040022", "HYR", "Piruleta Corazon"),
        43: ("1001010014", "GEI", "Carton Palomitas XXL"),
    }
    print()
    print(f"  Correcciones ERP (las {len(esperado)} que el robot DEBE llevar):")
    for n, (cod, um, _desc) in esperado.items():
        fila = lineas[n - 1]
        art, ka = _elegir(fila, ALIAS_ARTICULO)
        um_real, ku = _elegir(fila, ALIAS_UM)
        desc, _ = _elegir(fila, ALIAS_DESC)
        ok = art == cod and um_real.upper() == um
        print(f"    línea {n:>3} | {desc[:36]:<36} | {art:<10} / {um_real:<4} "
              f"-> {'OK' if ok else 'MAL (debería ser ' + cod + ' / ' + um + ')'}")
        if not ok:
            problemas.append(f"línea {n}: se leyó {art}/{um_real}, debería ser {cod}/{um}")
        for c in _conflictos(fila, ALIAS_ARTICULO, art) + _conflictos(fila, ALIAS_UM, um_real):
            problemas.append(f"línea {n}: campos incompatibles -> {c}")

    fila30 = lineas[29]
    a30, _ = _elegir(fila30, ALIAS_ARTICULO)
    u30, _ = _elegir(fila30, ALIAS_UM)
    d30, _ = _elegir(fila30, ALIAS_DESC)
    print()
    print(f"  Línea  30 | {d30[:36]:<36} | {a30:<10} / {u30:<4} -> se deja (LA DEJAMOS)")
    print("             El ERP la rechazará: es la incidencia real esperada.")

    print()
    print("  Familias con variantes (sus UM NO deben cambiar):")
    # Si no se pudo importar catalogo_erp, se usa una lista de respaldo con
    # las DOS grafias (en el pedido real se escribe «Kojak» con una sola c).
    familias_respaldo = ("Kit Kat", "Skittle", "Haribo", "Estuchado",
                         "Helado", "Kojak", "Kojack")

    def _familia(desc):
        if _familia_de is not None:
            try:
                return _familia_de(desc)
            except Exception:
                pass
        for f in familias_respaldo:
            if f.lower() in desc.lower():
                return f
        return None

    vistas = 0
    for n, fila in enumerate(lineas, 1):
        desc, _ = _elegir(fila, ALIAS_DESC)
        if _familia(desc):
            art, _ = _elegir(fila, ALIAS_ARTICULO)
            um_real, _ = _elegir(fila, ALIAS_UM)
            print(f"    línea {n:>3} | {desc[:42]:<42} | {art:<10} / {um_real}")
            vistas += 1
    if not vistas:
        print("    (no hay líneas de esas familias)")

    # Comprobación explícita de las dos UM que se estropearon por la mañana.
    for n, articulo, um_buena in ((71, "Kit Kat Normal", "HSI"), (81, "Skittle Frutas", "HWD")):
        if n <= len(lineas):
            desc, _ = _elegir(lineas[n - 1], ALIAS_DESC)
            _, ku = _elegir(lineas[n - 1], ALIAS_UM)
            um_real, _ = _elegir(lineas[n - 1], ALIAS_UM)
            if articulo.lower() in desc.lower() and um_real.upper() != um_buena:
                problemas.append(f"línea {n}: {desc} debería tener UM {um_buena} y tiene {um_real}")

    print()
    print("=" * 78)
    if problemas:
        print("  RESULTADO: PROBLEMA. NO LANCES EL ROBOT TODAVÍA.")
        for p in problemas:
            print(f"    - {p}")
        print()
        print("  Solución: vuelve a aplicar las correcciones con")
        print("      python src\\regenerar_pedido.py --restaurar")
        print("  y ejecuta otra vez esta verificación.")
        print("=" * 78)
        return 1
    print("  RESULTADO: TODO OK. El JSON tiene las correcciones y las familias")
    print("  intactas. Puedes lanzar el robot de 100 líneas.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
