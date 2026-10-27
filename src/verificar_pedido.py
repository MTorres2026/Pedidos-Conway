# -*- coding: utf-8 -*-
r"""Comprueba QUE el JSON que va a leer el robot tiene las correcciones ERP.

Es SOLO DE LECTURA: no modifica ningún archivo y no toca el ERP.
Ejecútalo justo ANTES de lanzar el robot, en la raíz del proyecto:

    python src\verificar_pedido.py
    python src\verificar_pedido.py "data\pedidos_para_procesar\pedido_OTRO.json"
    python src\verificar_pedido.py "data\pedidos_para_procesar\pedido_146.json" --lineas 146

¿Para qué sirve?
----------------
El 27/10/2026 se lanzó una carrera con un JSON que TODAVÍA tenía los códigos
viejos. Además, si se pulsa «Enviar a ERP» desde la pestaña «Pedidos» de
CineStock, esa pantalla REESCRIBE el JSON desde lo que tiene en memoria y
puede perder las correcciones. Este script lo detecta antes de perder 10-15
minutos.

Busca las correcciones por NOMBRE del producto, no por número de línea, para
que sirva igual para el pedido de prueba de 100 y para el real de 146.

Comprueba:
  * el número de líneas (100 por defecto, o el que indiques con --lineas);
  * las 5 correcciones verificadas en el ERP;
  * que la Tapa Infantil sigue intacta (se deja a propósito: LA DEJAMOS);
  * que las familias con variantes conservan sus UM (Kit Kat HSI, Skittle HWD);
  * que no hay campos sinónimos con valores distintos (el robot lee el PRIMER
    alias que encuentra: si 'um' y 'unidad' no coinciden, puede leer el viejo).
"""

import argparse
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

# Correcciones ERP verificadas: (nombre del producto, código, UM).
CORRECCIONES_ESPERADAS = [
    ("(CI) Bolsa Asa Papel", "1003020091", "ICG"),
    ("(CI) Cuchara Madera", "1003020096", "GDE"),
    ("(CI) Tapa Vaso Mediana", "1003020030", "CJA"),
    ("Piruleta Corazon", "1001040022", "HYR"),
    ("Carton Palomitas XXL", "1001010014", "GEI"),
]
# Productos que YA NO EXISTEN en el ERP: se dejan, no se sustituyen.
NO_EXISTE = ["(CI) Tapa Vaso Pequeña/Infantil"]
# UM que se estropearon el 27/10 por la mañana y deben seguir intactas.
FAMILIAS_UM = [("Kit Kat Normal", "HSI"), ("Skittle Frutas", "HWD")]

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


def _tokens(s):
    """Palabras significativas, sin acentos ni signos (como catalogo_erp)."""
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = "".join(ch if ch.isalnum() else " " for ch in s)
    return {t for t in s.split() if len(t) >= 3}


def _mismo_nombre(a, b):
    """Exige EXACTAMENTE las mismas palabras (sin contar acentos ni signos).

    Así 'Piruleta Corazon' no corrige por error a su hijo azul si algún día
    se pide con otro nombre.
    """
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta) and ta == tb


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


def _familia(desc):
    if _familia_de is not None:
        try:
            return _familia_de(desc)
        except Exception:
            pass
    # Respaldo con las DOS grafías (en el pedido real se escribe «Kojak»).
    for f in ("Kit Kat", "Skittle", "Haribo", "Estuchado",
              "Helado", "Kojak", "Kojack"):
        if f.lower() in desc.lower():
            return f
    return None


def main():
    ap = argparse.ArgumentParser(
        description="Verificación previa del pedido (solo lectura)."
    )
    ap.add_argument("archivo", nargs="?", default=None,
                    help="Ruta al JSON del pedido. Si se omite, el de 100 líneas.")
    ap.add_argument("--lineas", type=int, default=100,
                    help="Nº de líneas esperado (por defecto 100). "
                         "Para el pedido real de 146 usa --lineas 146.")
    args = ap.parse_args()

    ruta = Path(args.archivo) if args.archivo else PREDETERMINADO
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
    print(f"  Líneas: {len(lineas)} (esperadas: {args.lineas})")
    problemas = []
    if len(lineas) != args.lineas:
        problemas.append(f"debería haber {args.lineas} líneas y hay {len(lineas)}")

    vistas = []
    for n, l in enumerate(lineas, 1):
        if not isinstance(l, dict):
            continue
        art, _ = _elegir(l, ALIAS_ARTICULO)
        um, _ = _elegir(l, ALIAS_UM)
        desc, _ = _elegir(l, ALIAS_DESC)
        vistas.append((n, l, art, um, desc))

    print()
    print("  Correcciones ERP (buscadas por NOMBRE, no por nº de línea):")
    for nombre, cod, um_ok in CORRECCIONES_ESPERADAS:
        encontrados = [v for v in vistas if _mismo_nombre(nombre, v[4])]
        if not encontrados:
            print(f"    {nombre:<36} -> no aparece en este pedido (se omite)")
            continue
        for n, l, art, um_real, desc in encontrados:
            ok = art == cod and um_real.upper() == um_ok
            print(f"    línea {n:>3} | {desc[:36]:<36} | {art:<10} / {um_real:<4} "
                  f"-> {'OK' if ok else 'MAL (debería ser ' + cod + ' / ' + um_ok + ')'}")
            if not ok:
                problemas.append(
                    f"línea {n} ({desc}): se leyó {art}/{um_real}, "
                    f"debería ser {cod}/{um_ok}")
            for c in (_conflictos(l, ALIAS_ARTICULO, art)
                      + _conflictos(l, ALIAS_UM, um_real)):
                problemas.append(f"línea {n}: campos incompatibles -> {c}")

    print()
    print("  Productos que YA NO EXISTEN en el ERP (se dejan, LA DEJAMOS):")
    alguno = False
    for nombre in NO_EXISTE:
        for n, _l, art, um_real, desc in vistas:
            if _mismo_nombre(nombre, desc) or _clave(nombre) in _clave(desc):
                print(f"    línea {n:>3} | {desc[:36]:<36} | {art:<10} / {um_real:<4}")
                alguno = True
    if not alguno:
        print("    (no aparece en este pedido)")

    print()
    print("  Familias con variantes (sus UM NO deben cambiar):")
    vistas_fam = 0
    for n, _l, art, um_real, desc in vistas:
        if _familia(desc):
            print(f"    línea {n:>3} | {desc[:42]:<42} | {art:<10} / {um_real}")
            vistas_fam += 1
    if not vistas_fam:
        print("    (no hay líneas de esas familias)")

    for nombre, um_buena in FAMILIAS_UM:
        for n, _l, _art, um_real, desc in vistas:
            if _mismo_nombre(nombre, desc) and um_real.upper() != um_buena:
                problemas.append(
                    f"línea {n}: {desc} debería tener UM {um_buena} y tiene {um_real}")

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
    print(f"  intactas. Puedes lanzar el robot de {args.lineas} líneas.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
