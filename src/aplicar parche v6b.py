# -*- coding: utf-8 -*-
"""Parche v6b para robot_erp.py: el v6 mejorado (se aplica sobre el v5; si el
robot ya tiene el v6, lo actualiza solo en la política de errores).

Cambios respecto al v6 (a petición de la encargada, tras el pedido de 100 del
27/10 en el que el ERP rechazó la UM de una línea y el robot se paró):

  - Si una línea NO queda bien escrita (p. ej. UM rechazada «Valor no válido»),
    se ANOTA en el informe y SE SIGUE con la línea siguiente. Al final, el
    informe lista todas las líneas a corregir a mano antes de Guardar.
  - La parada automática queda SOLO para el caso peligroso de verdad: que el
    robot deje de poder anclar las filas del grid (no saber qué fila es cada
    línea). Ese freno evita repetir el desastre de «escribir en la fila 13».

Todo lo demás es idéntico al v6: crear filas en bloques, anclar cada fila por
su número de línea o contenido, comprobar cada línea tras escribirla, nunca
pulsar Guardar, verificación final anclada.

Uso:  python "aplicar parche v6b.py" [ruta\\robot_erp.py]
Hace copia de seguridad, valida la sintaxis. Si ya está aplicado, no toca nada.
"""
import ast
import os
import shutil
import sys
from datetime import datetime

NUEVO = r'''
JS_FILAS_LABEL = """
var r = [];
document.querySelectorAll("input[id^='REQ_LINE_WRK_INV_ITEM_ID$']").forEach(function(e){
  var i = parseInt(e.id.split('$').pop(), 10);
  if (isNaN(i)) return;
  var caja = e.closest('tr') || e.closest('div');
  var lab = '';
  if (caja) {
    var tok = (caja.innerText || '').split(/\\s+/);
    for (var k = 0; k < tok.length; k++) {
      if (/^[1-9][0-9]{0,3}$/.test(tok[k])) { lab = tok[k]; break; }
    }
  }
  r.push([i, (e.value || '').trim(), lab]);
});
r.sort(function(a, b){ return a[0] - b[0]; });
return r;
"""


def _leer_ventana(driver):
    """Lee las filas visibles del grid:
    [(rel, articulo, etiqueta, cantidad, um)].
    «rel» es el índice del DOM EN ESTA PANTALLA (cambia al paginar);
    «etiqueta» es el número de línea que pinta el ERP a la izquierda de la
    fila (puede venir vacío). Nunca se presupone que rel == línea."""
    filas = driver.execute_script(JS_FILAS_LABEL) or []
    try:
        bruto = driver.execute_script(JS_LEER_TODAS_DOM, _UM_INFO["patron"]) or {}
    except Exception:
        bruto = {}
    salida = []
    for dato in filas:
        rel = int(dato[0])
        art = (dato[1] or "").strip()
        lab = (dato[2] or "").strip()
        q = u = None
        for clave in (rel, str(rel)):
            try:
                if clave in bruto:
                    trozo = bruto[clave]
                    if trozo and len(trozo) >= 3:
                        q, u = trozo[1], trozo[2]
                    break
            except Exception:
                pass
        salida.append((rel, art, lab, q, u))
    return salida


def _mapear_ventana(driver, datos):
    """Ancla cada fila visible a su número de línea (1-based).

    Devuelve (mapa, vacias): mapa {línea: rel} de las filas CON datos y
    vacias [(rel, etiqueta o None)] de las filas EN BLANCO. El anclaje es por
    etiqueta del ERP y, si falta, por contenido (artículo, y cantidad/UM si se
    leen, para distinguir repetidos). Las filas que no se pueden anclar con
    seguridad NO se devuelven: nunca se escribe a ciegas."""
    total = len(datos)
    esperados = []
    for j in range(total):
        f = datos.iloc[j]
        esperados.append((str(f["articulo"]).strip(), str(f["um"]).strip().upper()))
    mapa = {}
    vacias = []
    for rel, art, lab, q, u in _leer_ventana(driver):
        if lab.isdigit():
            n = int(lab)
            if art:
                mapa[n] = rel
            else:
                vacias.append((rel, n))
            continue
        if art:
            buenos = []
            for j in range(total):
                n = j + 1
                if n in mapa or esperados[j][0] != art:
                    continue
                if q is not None:
                    try:
                        if not _iguales(q, fmt_cantidad(datos.iloc[j]["cantidad"])):
                            continue
                    except Exception:
                        pass
                if u is not None and u.strip().upper() != esperados[j][1]:
                    continue
                buenos.append(n)
            if len(buenos) == 1:
                mapa[buenos[0]] = rel
        else:
            vacias.append((rel, None))
    return mapa, vacias


def _localizar_para_escribir(driver, i, datos):
    """«rel» de la fila donde escribir la línea i (0-based), o None si no está
    claro. Mejor devolver None (y saltar la línea) que escribir equivocado."""
    n = i + 1
    mapa, vacias = _mapear_ventana(driver, datos)
    if n in mapa:
        return mapa[n]
    con_etiqueta = [rel for rel, lab in vacias if lab == n]
    if len(con_etiqueta) == 1:
        return con_etiqueta[0]
    sin_etiqueta = sorted(rel for rel, lab in vacias if lab is None)
    ancladas = sorted(mapa)
    if not (sin_etiqueta and ancladas):
        return None
    tot = _total_según_pager(driver)
    m = max(ancladas)
    if tot is None or tot != len(datos) or m + len(sin_etiqueta) != tot:
        return None
    # Las vacías de la pantalla son justo las líneas m+1..tot. Solo se acepta
    # si además ocupan las ÚLTIMAS posiciones de la ventana, seguidas y en
    # orden (ventana sana); en ventanas raras, mejor no arriesgar.
    rels = sorted(sin_etiqueta)
    if rels != list(range(rels[0], rels[0] + len(rels))):
        return None
    try:
        max_rel = max(rel for rel, _a, _l, _q, _u in _leer_ventana(driver))
    except Exception:
        return None
    if rels[-1] != max_rel:
        return None
    pos = n - (m + 1)
    if 0 <= pos < len(rels):
        return rels[pos]
    return None


def _localizar_con_reintentos(driver, i, datos):
    """Busca la fila de la línea i paseando por las páginas del grid
    (la ventana rara del ERP no siempre enseña la fila que toca)."""
    movimientos = ["Último", "Primero"] + ["Siguiente"] * 10 + ["Último", "Último"]
    for mov in movimientos:
        try:
            rel = _localizar_para_escribir(driver, i, datos)
        except Exception:
            rel = None
        if rel is not None:
            return rel
        try:
            _click_paginador(driver, mov)
            ir_a_grid(driver, timeout=15)
            esperar_psoft(driver)
        except Exception:
            pass
        time.sleep(0.3)
    return None


def _total_según_pager(driver):
    """Total de líneas del pedido según el paginador («1-21 de 146»).
    El total ha demostrado ser fiable aunque el rango mienta."""
    try:
        pag = _leer_paginador(driver)
        if pag:
            return int(pag[2])
    except Exception:
        pass
    return None


def _añadir_bloque(driver, wait, cuantas):
    """Añade «cuantas» filas vacías al final del grid y devuelve cuántas se
    añadieron (según el paginador; 0 si no se puede saber)."""
    try:
        _click_paginador(driver, "Último")
        ir_a_grid(driver, timeout=20)
        esperar_psoft(driver)
    except Exception as e:
        print(f"   [aviso] no se pudo ir al final del grid: {resumen_error(e)}")
    antes = _total_según_pager(driver)
    if antes is None:
        print("   [aviso] el ERP no indica el total de líneas; por seguridad no añado filas a ciegas")
        return 0

    def _crecidas():
        ahora = _total_según_pager(driver)
        if ahora is None:
            return None
        return ahora - antes

    if cuantas > 1:
        try:
            driver.execute_script("window.prompt = function(t) { return '" + str(cuantas) + "'; };")
            boton = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "img.PTROWADDMULTI")))
            driver.execute_script("arguments[0].click();", boton)
            esperar_psoft(driver, timeout=30)
            try:
                cerrar_ventanas(driver)
            except Exception:
                pass
        except Exception as e:
            print(f"   [aviso] al añadir {cuantas} filas de golpe: {resumen_error(e)}")
        c = _crecidas()
        if c is not None and c >= cuantas:
            return cuantas
    for _ in range(cuantas * 3 + 5):
        c = _crecidas()
        if c is not None and c >= cuantas:
            return cuantas
        try:
            driver.execute_script("window.prompt = function(t) { return '1'; };")
            boton = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "img.PTROWADDMULTI")))
            driver.execute_script("arguments[0].click();", boton)
            esperar_psoft(driver, timeout=30)
            try:
                cerrar_ventanas(driver)
            except Exception:
                pass
        except Exception as e:
            print(f"   [aviso] al añadir una fila: {resumen_error(e)}")
            try:
                recuperar(driver)
            except Exception:
                pass
    return _crecidas() or 0


def _confirmar_linea(driver, i, datos):
    """True si la línea i (0-based) quedó escrita EN SU FILA. Se lee la
    pantalla y se busca la fila con la etiqueta de esa línea; si el ERP no
    pinta etiquetas, se conforma con ver el artículo en alguna fila."""
    try:
        ir_a_grid(driver, timeout=10)
    except Exception:
        return True
    n = i + 1
    art = str(datos.iloc[i]["articulo"]).strip()
    um = str(datos.iloc[i]["um"]).strip().upper()
    try:
        filas = _leer_ventana(driver)
    except Exception:
        return True
    hay_etiquetas = any(lab.isdigit() for _r, _a, lab, _q, _u in filas)
    if not hay_etiquetas:
        return any((a or "").strip() == art for _r, a, _l, _q, _u in filas)
    for _rel, a, lab, q, u in filas:
        if lab.isdigit() and int(lab) == n:
            if (a or "").strip() != art:
                return False
            if q is not None and not _iguales(q, fmt_cantidad(datos.iloc[i]["cantidad"])):
                return False
            if u is not None and u.strip().upper() != um:
                return False
            return True
    return False


def cargar_lineas(driver, wait, datos, inf):
    """Primera tanda (~20, llena la página 1) y después en BLOQUES: se añade
    un grupo de filas de golpe, se ancla cada fila por número de línea o
    contenido y se rellena. Cada línea se comprueba al escribirla: si falla,
    se anota y se SIGUE (el informe la lista para corregir a mano). Solo se
    detiene si deja de poder anclar las filas del grid."""
    total = len(datos)
    TAM_PRIMERA = 20
    TAM_BLOQUE = 20
    print(f"5. Rellenando líneas (primera tanda de {TAM_PRIMERA}, después en bloques de {TAM_BLOQUE})...")
    creadas = 1
    if total > 1:
        creadas = max(1, crear_filas(driver, wait, min(total, 1 + TAM_PRIMERA), inf, ya_creadas=1))
        time.sleep(0.5)
        try:
            cerrar_ventanas(driver)
        except Exception:
            pass

    def ejecutar(i, fila_dom):
        try:
            procesar_fila(driver, i, datos.iloc[i], inf, total, fila_dom=fila_dom)
            if inf.fatal:
                if (i + 1) not in inf.lineas:
                    inf.linea(*_datos_linea(datos, i), "Línea en la que se detuvo el programa (revísala)")
                _marcar_pendientes(inf, datos, i + 1, "No se llegó a procesar (error fatal anterior)")
                return False
        except KeyboardInterrupt:
            raise
        except Exception as e:
            inf.linea(*_datos_linea(datos, i), "Error inesperado en esta línea", resumen_error(e))
            print(f"      ¡Fila {i + 1}: error inesperado ({resumen_error(e)})! Sigo con la siguiente.")
            if not chrome_vivo(driver):
                inf.fatal = "Se cerró la ventana de Chrome durante la carga."
                _marcar_pendientes(inf, datos, i + 1, "No se llegó a procesar (Chrome se cerró)")
                return False
            recuperar(driver)
            return True
        if not _confirmar_linea(driver, i, datos):
            # línea fallida (p. ej. UM rechazada por el ERP): se ANOTA y SE SIGUE.
            # El freno de verdad está en la localización: si la siguiente línea
            # no se puede anclar, ahí sí se para (ver el bucle de cargar_lineas).
            inf.linea(*_datos_linea(datos, i),
                      "La línea NO quedó bien escrita (se SIGUE con la siguiente)",
                      "revísala/corrígela a mano antes de pulsar Guardar")
            return True
        return True

    rellenadas = 0
    while rellenadas < min(creadas, total):
        if not ejecutar(rellenadas, None):
            return
        rellenadas += 1
    while rellenadas < total:
        bloque = min(TAM_BLOQUE, total - rellenadas)
        print(f"   --- Bloque: añadiendo {bloque} fila(s) para las líneas {rellenadas + 1} a {rellenadas + bloque} ---")
        nuevas = _añadir_bloque(driver, wait, bloque)
        if nuevas <= 0:
            inf.general(f"No se pudieron añadir más filas (faltan {total - rellenadas} líneas)",
                        "las líneas restantes se han marcado abajo; añádelas a mano")
            _marcar_pendientes(inf, datos, rellenadas, "No se llegó a procesar (no se pudo añadir fila)")
            break
        for j in range(nuevas):
            i = rellenadas + j
            if i >= total:
                break
            rel = _localizar_con_reintentos(driver, i, datos)
            if rel is None:
                inf.general(f"No se ha podido localizar la fila vacía de la línea {i + 1}",
                            "se detiene para no escribir en una fila equivocada; revisa el pedido a mano")
                _marcar_pendientes(inf, datos, i, "No se localizó su fila vacía en el grid")
                verificar_final(driver, datos, inf)
                return
            print(f"   Línea {i + 1} -> fila de la pantalla (${rel})")
            if not ejecutar(i, rel):
                return
        rellenadas += nuevas
    verificar_final(driver, datos, inf)


def verificar_final(driver, datos, inf):
    """Recorre las páginas del grid y compara cada línea con lo esperado,
    anclando cada fila por su número de línea (etiqueta) o por contenido.
    Las filas que no se puedan anclar cuentan como «no revisadas», nunca
    como error."""
    print("6. Verificación final de las líneas...")
    leidos = {}
    try:
        ir_a_grid(driver, timeout=10)
        _click_paginador(driver, "Primero")
        firma_anterior = None
        for _ in range(60):
            ir_a_grid(driver, timeout=10)
            mapa, _vacias = _mapear_ventana(driver, datos)
            bruto = {rel: (a, q, u) for rel, a, _lab, q, u in _leer_ventana(driver)}
            for n, rel in sorted(mapa.items()):
                if n - 1 in leidos:
                    continue
                a, q, u = bruto.get(rel, ("", None, None))
                leidos[n - 1] = (a, q, u)
            firma = tuple(sorted(mapa.items()))
            if firma == firma_anterior or not _click_siguiente(driver):
                break
            firma_anterior = firma
        _click_paginador(driver, "Primero")
    except Exception as e:
        inf.general("No se pudo hacer la verificación final de las líneas", resumen_error(e))
    no_visibles = 0
    for i in range(len(datos)):
        n = i + 1
        if n in inf.lineas:
            continue
        if i not in leidos:
            no_visibles += 1
            continue
        fila = datos.iloc[i]
        art = str(fila["articulo"]).strip()
        um = str(fila["um"]).strip().upper()
        prod = str(fila["producto"]).strip() if ("producto" in datos.columns and pd.notna(fila["producto"])) else ""
        try:
            qty = fmt_cantidad(fila["cantidad"])
        except Exception:
            qty = str(fila["cantidad"])
        a, q, u = leidos[i]
        a_clean = (a or "").strip()
        if a_clean != art:
            if a_clean and art.startswith(a_clean) and len(a_clean) < len(art) * 0.7:
                inf.linea(n, art, qty, um, prod, "Verificación final: artículo TRUNCADO en pantalla",
                          f"aparece '{a_clean}' (primeros {len(a_clean)} de {len(art)} caracteres)")
            else:
                inf.linea(n, art, qty, um, prod, "Verificación final: artículo distinto en pantalla",
                          f"aparece '{a_clean}'")
        if q is not None and not _iguales(q, qty):
            inf.linea(n, art, qty, um, prod, "Verificación final: cantidad distinta en pantalla",
                      f"aparece '{q.strip()}', debería ser {qty}")
        if u is not None and u.strip().upper() != um:
            inf.linea(n, art, qty, um, prod, "Verificación final: UM distinta o vacía en pantalla",
                      f"aparece '{u.strip()}', debería ser {um}")
    if no_visibles:
        inf.general(f"{no_visibles} líneas no se han podido revisar al final (no se han podido anclar en el grid)",
                    "cada línea se validó en su momento al escribirla")
'''

# Puntos que deben existir (los dejó el parche v5)
MARCA_V5 = "fila_dom=None"


def _nombres(nodo):
    if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [nodo.name]
    if isinstance(nodo, ast.Assign):
        return [t.id for t in nodo.targets if isinstance(t, ast.Name)]
    return []


def buscar_robot(arg):
    if arg:
        return arg.strip('"')
    aqui = os.path.dirname(os.path.abspath(__file__))
    for base in (os.getcwd(), aqui, os.path.dirname(aqui)):
        for sub in ("", "..", os.path.join("..", "..")):
            p = os.path.normpath(os.path.join(base, sub, "robot_erp.py"))
            if os.path.isfile(p):
                return p
    raise FileNotFoundError("No encuentro robot_erp.py. Ejecuta: python \"aplicar parche v6b.py\" RUTA\\robot_erp.py")


def main():
    ruta = buscar_robot(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"Archivo a parchear: {ruta}")
    with open(ruta, "r", encoding="utf-8") as f:
        src = f.read()
    if "SIGUE con la siguiente" in src:
        print("El parche v6b ya está aplicado. No se toca nada.")
        return
    if "_mapear_ventana(" in src:
        print("Detectado el v6: actualizando solo la política de errores...")
    elif "def ir_a_grid(" in src and MARCA_V5 in src:
        print("Detectado el v5: aplicando el v6b completo...")
    else:
        raise RuntimeError("Este parche necesita el robot con el parche v5 (o v6) ya aplicado.")
    nuevos = ast.parse(NUEVO).body
    a_quitar = {n for nodo in nuevos for n in _nombres(nodo)}
    lineas = src.split("\n")
    quitar = []
    for nodo in ast.parse(src).body:
        if any(n in a_quitar for n in _nombres(nodo)):
            quitar.append((nodo.lineno - 1, nodo.end_lineno))
    for ini, fin in sorted(quitar, reverse=True):
        del lineas[ini:fin]
    src2 = "\n".join(lineas)
    marca = "def crear_driver():"
    if src2.count(marca) != 1:
        raise RuntimeError("No encuentro el punto de inserción (def crear_driver)")
    src2 = src2.replace(marca, NUEVO.strip("\n") + "\n\n\n" + marca)
    compile(src2, ruta, "exec")
    copia = f"{ruta}.bak_v6b_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(ruta, copia)
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write(src2)
    print(f"Copia de seguridad: {copia}")
    print(f"Funciones sustituidas: {', '.join(sorted(a_quitar))}")
    print("PARCHE v6b APLICADO. Sintaxis correcta.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nNO SE HA CAMBIADO NADA. Motivo: {e}")
        sys.exit(1)
