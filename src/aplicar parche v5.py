# -*- coding: utf-8 -*-
"""Parche v5 para robot_erp.py: pedidos grandes con grid PAGINADO.

Uso:  python "aplicar parche v5.py" [ruta\\robot_erp.py]
Hace copia de seguridad, valida la sintaxis y escribe resultado_parche_v5.txt.
Si ya está aplicado, no toca nada.
"""
import ast
import os
import shutil
import sys
from datetime import datetime

NUEVO = r'''
JS_HAY_FILA = "return document.querySelectorAll(\"input[id^='REQ_LINE_WRK_INV_ITEM_ID$']\").length > 0;"

JS_FILAS_VISIBLES = """
var r = [];
document.querySelectorAll("input[id^='REQ_LINE_WRK_INV_ITEM_ID$']").forEach(function(e){
  var i = parseInt(e.id.split('$').pop(), 10);
  if (!isNaN(i)) r.push([i, (e.value || '').trim()]);
});
r.sort(function(a, b){ return a[0] - b[0]; });
return r;
"""

JS_PAGINADOR = """
var m = (document.body.innerText || '').match(/(\\d+)\\s*-\\s*(\\d+)\\s+de\\s+(\\d+)/);
return m ? [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)] : null;
"""


def _buscar_ruta_grid(driver, nivel=0):
    """Como _buscar_ruta, pero busca CUALQUIER fila del grid (no solo $0)."""
    try:
        if driver.execute_script(JS_HAY_FILA):
            return []
    except WebDriverException:
        pass
    if nivel > 3:
        return None
    total = len(driver.find_elements(By.CSS_SELECTOR, "iframe,frame"))
    for i in range(total):
        try:
            driver.switch_to.frame(i)
        except WebDriverException:
            continue
        r = _buscar_ruta_grid(driver, nivel + 1)
        if r is not None:
            return [i] + r
        try:
            driver.switch_to.parent_frame()
        except WebDriverException:
            driver.switch_to.default_content()
            return None
    return None


def ir_a_grid(driver, timeout=25):
    """Entra en el frame del grid de líneas, haya en pantalla la página que haya
    (las filas pueden llamarse $21...$41 en la última página: $0 no tiene que existir)."""
    fin = time.time() + timeout
    while time.time() < fin:
        try:
            driver.switch_to.default_content()
            if _RUTA[0] is not None:
                ok = True
                for i in _RUTA[0]:
                    try:
                        driver.switch_to.frame(i)
                    except WebDriverException:
                        ok = False
                        break
                if ok and driver.execute_script(JS_HAY_FILA):
                    return True
                driver.switch_to.default_content()
            ruta = _buscar_ruta_grid(driver)
            if ruta is not None:
                _RUTA[0] = ruta
                return True
        except WebDriverException:
            pass
        time.sleep(0.3)
    raise TimeoutError("No se encontró el elemento 'REQ_LINE_WRK_INV_ITEM_ID$n' en ningún frame")


def _leer_paginador(driver):
    """(desde, hasta, total) del texto «1-21 de 146», o None si el grid no pagina."""
    try:
        r = driver.execute_script(JS_PAGINADOR)
        return tuple(r) if r else None
    except WebDriverException:
        return None


def contar_filas(driver):
    ir_a_grid(driver, timeout=15)
    return len(driver.execute_script(JS_FILAS_VISIBLES) or [])


def verificar_pantalla_activa(driver, elem_id=None, timeout=5):
    try:
        ir_a_grid(driver, timeout=timeout)
        return True
    except TimeoutError:
        return False


def _primera_fila_libre(driver, max_filas=None):
    """Índice ($n) de la primera fila VISIBLE con el artículo vacío (o None)."""
    try:
        for idx, val in (driver.execute_script(JS_FILAS_VISIBLES) or []):
            if not val:
                return int(idx)
    except Exception:
        pass
    return None


def _click_siguiente(driver):
    def _buscar():
        for e in driver.find_elements(By.XPATH,
                "//a[contains(@title,'Siguiente') or contains(@title,'Next')]"):
            try:
                if e.is_displayed():
                    driver.execute_script("arguments[0].click();", e)
                    return True
            except WebDriverException:
                continue
        return False
    driver.switch_to.default_content()
    ok = _en_frame_todos(driver, _buscar)
    if ok:
        esperar_psoft(driver, timeout=15)
    return ok


def _añadir_una_fila(driver, wait, ya_creadas, inf):
    """Añade UNA fila, salta a «Último» y devuelve el índice $n de la fila vacía
    donde escribir (la localiza mirando el DOM, nunca la calcula)."""
    for intento in (1, 2, 3):
        try:
            _click_paginador(driver, "Último")
            ir_a_grid(driver, timeout=20)
            esperar_psoft(driver)
            libre = _primera_fila_libre(driver)
            if libre is not None:
                return libre   # ya hay una vacía (p. ej. un intento anterior sí la creó)
            antes = _leer_paginador(driver)
            driver.execute_script("window.prompt = function(t) { return '1'; };")
            boton_mas = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "img.PTROWADDMULTI")))
            driver.execute_script("arguments[0].click();", boton_mas)
            esperar_psoft(driver, timeout=30)
            _click_paginador(driver, "Último")
            fin = time.time() + 30
            while time.time() < fin:
                try:
                    ir_a_grid(driver, timeout=10)
                    libre = _primera_fila_libre(driver)
                except Exception:
                    libre = None
                if libre is not None:
                    despues = _leer_paginador(driver)
                    if antes and despues and despues[2] != antes[2] + 1:
                        print(f"      [aviso] el ERP indica {despues[2]} filas (antes {antes[2]})")
                    return libre
                time.sleep(1)
            print(f"   [aviso] la fila nueva no aparece (intento {intento} de 3)")
            recuperar(driver)
        except Exception as e:
            print(f"   [aviso] al añadir una fila: {resumen_error(e)}")
            try:
                recuperar(driver)
            except Exception:
                pass
    return None


def cargar_lineas(driver, wait, datos, inf):
    """Primera tanda (~20, llena la página 1) y después UNA fila por vez:
    se añade, se salta a «Último», se localiza la fila vacía en el DOM y se escribe ahí."""
    total = len(datos)
    TAM_PRIMERA = 20
    print(f"5. Rellenando líneas (primera tanda de {TAM_PRIMERA}, después de 1 en 1)...")
    creadas = 1
    if total > 1:
        creadas = max(1, crear_filas(driver, wait, min(total, 1 + TAM_PRIMERA), inf, ya_creadas=1))
        time.sleep(0.5)
        try:
            cerrar_ventanas(driver)
        except Exception:
            pass

    def ejecutar(i, fila_dom):
        """True si se puede seguir; False si hay que parar."""
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

    rellenadas = 0
    while rellenadas < min(creadas, total):
        if not ejecutar(rellenadas, None):
            return
        rellenadas += 1
    while rellenadas < total:
        destino = _añadir_una_fila(driver, wait, creadas, inf)
        if destino is None:
            inf.general(f"Solo se pudieron crear {creadas} filas de {total}",
                        "las líneas restantes se han marcado abajo; añádelas a mano")
            _marcar_pendientes(inf, datos, rellenadas, "No se llegó a procesar (no se pudo añadir fila)")
            break
        creadas += 1
        if not ejecutar(rellenadas, destino):
            return
        rellenadas += 1
    verificar_final(driver, datos, inf)


def verificar_final(driver, datos, inf):
    """Recorre las páginas del grid (Primero -> Siguiente...) y compara lo que hay
    en pantalla con los datos. Lo que no se pueda ver se avisa, sin falsos errores."""
    print("6. Verificación final de las líneas...")
    leidos = {}
    try:
        ir_a_grid(driver, timeout=10)
        _click_paginador(driver, "Primero")
        ultimo_desde = 0
        for _ in range(40):
            ir_a_grid(driver, timeout=10)
            pag = _leer_paginador(driver)
            bruto = driver.execute_script(JS_LEER_TODAS_DOM, _UM_INFO["patron"]) or {}
            idxs = sorted(int(k) for k in bruto)
            if not idxs:
                break
            desde = pag[0] if pag else 1
            # si los IDs son relativos a la página (empiezan en 0), se desplazan
            desp = (desde - 1) if (desde > 1 and idxs[0] < desde - 1) else 0
            for k, v in bruto.items():
                leidos[int(k) + desp] = v
            if not pag or pag[1] >= pag[2] or desde <= ultimo_desde:
                break
            ultimo_desde = desde
            if not _click_siguiente(driver):
                break
        _click_paginador(driver, "Primero")
    except Exception as e:
        inf.general("No se pudo hacer la verificación final de las líneas", resumen_error(e))
        return
    no_visibles = 0
    for i in range(len(datos)):
        n = i + 1
        if n in inf.lineas:
            continue
        fila = datos.iloc[i]
        art = str(fila["articulo"]).strip()
        um = str(fila["um"]).strip().upper()
        prod = str(fila["producto"]).strip() if ("producto" in datos.columns and pd.notna(fila["producto"])) else ""
        try:
            qty = fmt_cantidad(fila["cantidad"])
        except Exception:
            qty = str(fila["cantidad"])
        if i not in leidos:
            no_visibles += 1
            continue
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
        inf.general(f"{no_visibles} líneas no se han podido revisar al final "
                    "(la pantalla las muestra por páginas)",
                    "cada línea se validó en su momento al escribirla")
'''

# (texto_viejo, texto_nuevo, obligatorio)
REEMPLAZOS = [
    ("def procesar_fila(driver, i, fila_datos, inf, n_pantalla):",
     "def procesar_fila(driver, i, fila_datos, inf, n_pantalla, fila_dom=None):", True),
    ("    fila = i   # fila real del grid; se corrige abajo si la fila i no está libre",
     "    fila = i if fila_dom is None else fila_dom   # fila real del grid ($n del DOM)", True),
    ("    if i >= n_pantalla:",
     "    if fila_dom is None and i >= n_pantalla:", True),
    ('        contenido = leer_valor(driver, f"REQ_LINE_WRK_INV_ITEM_ID${i}", timeout=4).strip()',
     '        contenido = "" if fila_dom is not None else '
     'leer_valor(driver, f"REQ_LINE_WRK_INV_ITEM_ID${i}", timeout=4).strip()', True),
    ('            ir_a_elemento(driver, "REQ_LINE_WRK_INV_ITEM_ID$0", timeout=20)',
     "            ir_a_grid(driver, timeout=20)", False),
]


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
    raise FileNotFoundError("No encuentro robot_erp.py. Ejecuta: python \"aplicar parche v5.py\" RUTA\\robot_erp.py")


def main():
    log = []

    def say(t):
        print(t)
        log.append(t)
    ruta = buscar_robot(sys.argv[1] if len(sys.argv) > 1 else None)
    say(f"Archivo a parchear: {ruta}")
    with open(ruta, "r", encoding="utf-8") as f:
        src = f.read()
    if "def ir_a_grid(" in src and "fila_dom=None" in src:
        say("El parche v5 ya está aplicado. No se toca nada.")
        return
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
    for viejo, nuevo, obligatorio in REEMPLAZOS:
        if src2.count(viejo) != 1:
            if obligatorio:
                raise RuntimeError(f"No encuentro (o está repetido) este texto en procesar_fila:\n{viejo}")
            say(f"[aviso] cambio opcional no aplicado: {viejo.strip()[:60]}")
            continue
        src2 = src2.replace(viejo, nuevo)
    compile(src2, ruta, "exec")
    copia = f"{ruta}.bak_v5_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(ruta, copia)
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write(src2)
    say(f"Copia de seguridad: {copia}")
    say(f"Funciones sustituidas: {', '.join(sorted(a_quitar))}")
    say("PARCHE v5 APLICADO. Sintaxis correcta.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nNO SE HA CAMBIADO NADA. Motivo: {e}")
        sys.exit(1)
