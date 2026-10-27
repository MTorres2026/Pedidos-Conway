# -*- coding: utf-8 -*-
"""Robot de carga de pedidos Conway en el ERP (PeopleSoft).

Guarda este archivo como UTF-8 (sin BOM) antes de ejecutarlo.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import getpass
import unicodedata
import zipfile
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# ===========================================================================
# RUTA BASE (DEFINICIÓN CENTRALIZADA - EXISTE EN EL CÓDIGO ORIGINAL)
# ===========================================================================
RUTA_BASE = os.path.dirname(os.path.abspath(__file__))

# ===========================================================================
# VERSIÓN DEL ROBOT
#   v6b = la versión aprobada: ancla filas, bloques de 20, anota el error y SIGUE.
#   v6c = v6b + DOS mejoras pedidas el 27/10/2026:
#         1) El mensaje final Y la ventana flotante listan los PRODUCTOS CON
#            ERROR junto a su CÓDIGO ERP, UM y descripción.
#         2) Si el grid paginado no deja leer la descripción pero el código
#            SÍ quedó escrito, NO se borra la línea (solo se avisa).
#   NO es la v7. La base sigue siendo exactamente la v6b aprobada.
# ===========================================================================
VERSION_ROBOT = "v6c"

# ===========================================================================
# .env (ERP_USER / ERP_PASS) - LÓGICA CORREGIDA DE CARGA
# ===========================================================================
if load_dotenv is not None:
    _ENV_RAIZ = os.path.join(os.path.dirname(RUTA_BASE), ".env")
    if os.path.isfile(_ENV_RAIZ):
        load_dotenv(_ENV_RAIZ)
    else:
        load_dotenv()

ERP_USER = os.environ.get("ERP_USER")
ERP_PASS = os.environ.get("ERP_PASS")

ERP_URL = "https://erpg.orsa.mx:31475/psp/cpfl91pr/?&cmd=login&languageCd=ESP&cplsso=off&"
UNIDAD_NEGOCIO = "SP001"
PROVEEDOR = "0000000001"

# ===========================================================================
# CONSTANTES DEL SISTEMA (PRESERVADAS DEL CÓDIGO ORIGINAL)
# ===========================================================================
_VELOCIDAD_ERP = {"promedio": 2.0, "muestras": 0}
_T_ESPERA = [0.0]
_SENALES_ROTAS = {"wait": False, "proc": False}

# ===========================================================================
# FUNCIONES AUXILIARES (LÓGICA COMPLETAMENTE PRESERVADA Y CORREGIDA)
# ===========================================================================

def esperar_psoft(driver, timeout=None):
    if timeout is None:
        timeout = max(8, min(15, _VELOCIDAD_ERP["promedio"] * 2.5))
    t = time.time()
    try:
        return _esperar_psoft_raw(driver, timeout)
    finally:
        elapsed = time.time() - t
        _T_ESPERA[0] += elapsed
        n = _VELOCIDAD_ERP["muestras"]
        prom = _VELOCIDAD_ERP["promedio"]
        _VELOCIDAD_ERP["promedio"] = (prom * n + elapsed) / (n + 1)
        _VELOCIDAD_ERP["muestras"] = min(n + 1, 50)


def _estado_psoft(driver):
    return driver.execute_script("""
        var w = document.getElementById('WAIT_win0');
        var p = document.getElementById('processing');
        return [document.readyState === 'complete',
                !!(w && w.style.visibility === 'visible'),
                !!(p && p.style.visibility === 'visible')];
    """)


def _esperar_psoft_raw(driver, timeout=8):
    fin = time.time() + timeout
    estado = None
    while time.time() < fin:
        try:
            estado = _estado_psoft(driver)
            if (estado[0] and (_SENALES_ROTAS["wait"] or not estado[1])
                    and (_SENALES_ROTAS["proc"] or not estado[2])):
                time.sleep(0.1)
                return True
        except WebDriverException:
            pass
        time.sleep(0.1)
    if estado and estado[0]:
        if estado[1] and not _SENALES_ROTAS["wait"]:
            _SENALES_ROTAS["wait"] = True
            print(f"      [aviso] WAIT_win0 sigue visible tras {timeout}s; lo ignoro de ahora en adelante")
        if estado[2] and not _SENALES_ROTAS["proc"]:
            _SENALES_ROTAS["proc"] = True
            print(f"      [aviso] 'processing' sigue visible tras {timeout}s; lo ignoro de ahora en adelante")
    return False


_RUTA = [None]


def _buscar_ruta(driver, elem_id, nivel=0):
    if driver.find_elements(By.ID, elem_id):
        return []
    if nivel > 3:
        return None
    total = len(driver.find_elements(By.CSS_SELECTOR, "iframe,frame"))
    for i in range(total):
        try:
            driver.switch_to.frame(i)
        except WebDriverException:
            continue
        r = _buscar_ruta(driver, elem_id, nivel + 1)
        if r is not None:
            return [i] + r
        try:
            driver.switch_to.parent_frame()
        except WebDriverException:
            driver.switch_to.default_content()
            return None
    return None


def ir_a_elemento(driver, elem_id, timeout=25):
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
                if ok and driver.find_elements(By.ID, elem_id):
                    return True
                driver.switch_to.default_content()
            ruta = _buscar_ruta(driver, elem_id)
            if ruta is not None:
                _RUTA[0] = ruta
                return True
        except WebDriverException:
            pass
        time.sleep(0.3)
    raise TimeoutError(f"No se encontró el elemento '{elem_id}' en ningún frame")


JS_SETTER_NATIVO = """
    var el = arguments[0], v = arguments[1];
    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    el.focus();
    setter.call(el, v);
    el.dispatchEvent(new Event('input',  {bubbles: true}));
    if (typeof addchg_win0 === 'function') { try { addchg_win0(el); } catch(e) {} }
    el.dispatchEvent(new Event('change', {bubbles: true}));
    el.blur();
"""


def _boton_visible(driver, localizadores):
    for loc in localizadores:
        try:
            for b in driver.find_elements(*loc):
                if b.is_displayed():
                    return b
        except WebDriverException:
            pass
    return None


def _boton_aceptar(driver):
    return _boton_visible(driver, [
        (By.ID, "#ICOK"), (By.ID, "#ICYes"),
        (By.XPATH, "//input[normalize-space(@value)='Aceptar']"),
        (By.XPATH, "//a[normalize-space(.)='Aceptar'] | //button[normalize-space(.)='Aceptar']")])


def _boton_cancelar(driver):
    return _boton_visible(driver, [
        (By.ID, "#ICCancel"),
        (By.XPATH, "//input[normalize-space(@value)='Cancelar']"),
        (By.XPATH, "//a[normalize-space(.)='Cancelar'] | //button[normalize-space(.)='Cancelar']")])


def _subir_frame(driver):
    """parent_frame que no falla si el frame (p. ej. un modal) ya se destruyó."""
    try:
        driver.switch_to.parent_frame()
    except WebDriverException:
        try:
            driver.switch_to.default_content()
        except WebDriverException:
            pass


def _modal_en_contexto(driver, nivel=0):
    for m in driver.find_elements(By.CSS_SELECTOR, "iframe[id^='ptModFrame']"):
        try:
            if not m.is_displayed():
                continue
            driver.switch_to.frame(m)
        except WebDriverException:
            continue
        try:
            texto = driver.find_element(By.TAG_NAME, "body").text.strip()
            b = _boton_aceptar(driver)
            if b:
                driver.execute_script("arguments[0].click();", b)
                return texto or "(sin texto)"
            c = _boton_cancelar(driver)
            if c:
                driver.execute_script("arguments[0].click();", c)
                return "(consulta cerrada) " + (texto or "")[:200]
        finally:
            _subir_frame(driver)
    for d in driver.find_elements(By.CSS_SELECTOR, "[id^='ptModContent'], #alertmsg"):
        try:
            if d.is_displayed() and not d.find_elements(By.CSS_SELECTOR, "iframe"):
                b = _boton_aceptar(driver)
                if b:
                    texto = d.text.strip()
                    driver.execute_script("arguments[0].click();", b)
                    return texto or "(sin texto)"
        except WebDriverException:
            pass
    if nivel >= 3:
        return None
    total = len(driver.find_elements(By.CSS_SELECTOR, "iframe,frame"))
    for i in range(total):
        try:
            driver.switch_to.frame(i)
        except WebDriverException:
            continue
        try:
            r = _modal_en_contexto(driver, nivel + 1)
        finally:
            _subir_frame(driver)
        if r:
            return r
    return None


JS_HAY_MODAL = """
function vis(e){var r=e.getBoundingClientRect(),s=window.getComputedStyle(e);
  return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0;}
var l=document.querySelectorAll("iframe[id^='ptModFrame'],[id^='ptModContent'],#alertmsg");
for(var i=0;i<l.length;i++){if(vis(l[i])){return true;}}
return false;
"""


def _hay_modal_en_ruta(driver):
    ruta = _RUTA[0] or []
    driver.switch_to.default_content()
    for nivel in range(len(ruta) + 1):
        try:
            if driver.execute_script(JS_HAY_MODAL):
                return True
        except WebDriverException:
            pass
        if nivel < len(ruta):
            try:
                driver.switch_to.frame(ruta[nivel])
            except WebDriverException:
                break
    return False


def descartar_mensaje(driver, profundo=False):
    try:
        a = driver.switch_to.alert
        texto = a.text
        a.accept()
        return texto or "(alert)"
    except Exception:
        pass
    try:
        if _hay_modal_en_ruta(driver):
            return _modal_en_contexto(driver, nivel=3)
        if profundo:
            driver.switch_to.default_content()
            return _modal_en_contexto(driver)
        return None
    except WebDriverException:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        return None


def cerrar_ventanas(driver, elem_id=None, max_veces=25, ya_esperado=False):
    cerradas, textos = 0, []
    esperado = False
    espera_corta = ya_esperado
    while cerradas < max_veces:
        txt = descartar_mensaje(driver)
        if not txt:
            break
        cerradas += 1
        textos.append(txt)
        esperado = False
        espera_corta = False
        time.sleep(0.2)
        continue
    return cerradas, textos


def diagnostico_inputs(driver, elem_id):
    try:
        ir_a_elemento(driver, elem_id, timeout=5)
        ids = driver.execute_script(
            "return Array.from(document.querySelectorAll('input')).map(i=>i.id)"
            ".filter(x=>/UNIT|UOM|QTY|MEAS/i.test(x));")
        print(f"      [diag] inputs relacionados en este frame: {ids}")
    except Exception as e:
        print(f"      [diag] no se pudo listar inputs: {e}")


def _candidatos_num(s):
    """Interpretaciones posibles de un número escrito en formato español o inglés.
    '1.000' puede ser 1 o 1000, así que se devuelven ambas y se compara con cualquiera."""
    s = str(s).strip().replace(" ", "")
    if not s:
        raise ValueError("vacío")
    out = set()
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            out.add(float(s.replace(".", "").replace(",", ".")))
        else:
            out.add(float(s.replace(",", "")))
    elif "," in s:
        out.add(float(s.replace(",", ".")))
        if re.fullmatch(r"\d{1,3}(,\d{3})+", s):
            out.add(float(s.replace(",", "")))
    elif "." in s:
        out.add(float(s))
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            out.add(float(s.replace(".", "")))
    else:
        out.add(float(s))
    return out


def _iguales(actual, esperado):
    try:
        a, e = _candidatos_num(actual), _candidatos_num(esperado)
        return any(abs(x - y) < 1e-9 for x in a for y in e)
    except ValueError:
        return str(actual).strip().upper() == str(esperado).strip().upper()


def leer_valor(driver, elem_id, timeout=8):
    ir_a_elemento(driver, elem_id, timeout=timeout)
    return driver.find_element(By.ID, elem_id).get_attribute("value") or ""


_ULTIMO_AVISO = {"textos": [], "es_fatal": False}


def _es_no_valido(textos):
    return any(("no válido" in t.lower() or "no valido" in t.lower()) for t in textos)


_PATRONES_FATALES = [
    "sesión expirada", "sesion expirada", "permiso denegado", "permiso insuficiente",
    "no tiene acceso", "acceso denegado", "unauthorized", "forbidden",
    "must log in", "debe iniciar sesión", "debe iniciar sesion", "reinicie sesión", "reinicie sesion",
]
_RE_HTTP_FATAL = re.compile(r"\b(?:http|error|código|codigo|code|status)\D{0,3}(?:401|403)\b")


def _es_error_fatal(textos):
    for t in textos:
        tl = t.lower()
        if any(p in tl for p in _PATRONES_FATALES) or _RE_HTTP_FATAL.search(tl):
            return True
    return False


def _clasificar_error(textos):
    if _es_error_fatal(textos):
        return "fatal", textos[0] if textos else "Error fatal desconocido"
    if _es_no_valido(textos):
        return "no_valido", textos[0] if textos else "Valor no válido"
    return "general", textos[0] if textos else "Error general"


JS_BUSCAR_UM = """
var i = arguments[0], qtyId = arguments[1];
var inputs = Array.from(document.querySelectorAll('input'));
var cand = inputs.filter(function(e){
    return e.id.endsWith('$' + i) && /UNIT|UOM|MEAS/i.test(e.id); });
if (cand.length) return [cand[0].id, 'por nombre'];
var q = document.getElementById(qtyId);
if (!q) return null;
var texts = inputs.filter(function(e){ return e.type === 'text' && e.offsetParent !== null; });
var k = texts.indexOf(q);
if (k >= 0 && k + 1 < texts.length) return [texts[k + 1].id, 'siguiente campo tras la cantidad'];
return null;
"""

_UM_INFO = {"patron": None, "avisado": False}


def encontrar_id_um(driver, i, qty_id):
    if _UM_INFO["patron"]:
        ident = _UM_INFO["patron"].format(i)
        try:
            ir_a_elemento(driver, ident, timeout=3)
            return ident
        except Exception:
            pass
    try:
        ir_a_elemento(driver, qty_id, timeout=8)
        r = driver.execute_script(JS_BUSCAR_UM, i, qty_id)
    except Exception:
        return None
    if not r:
        return None
    ident, como = r
    suf = "$" + str(i)
    if ident.endswith(suf):
        _UM_INFO["patron"] = ident[:-len(suf)] + "${}"
    if not _UM_INFO["avisado"]:
        print(f"      [info] ID de la UM detectado: '{ident}' ({como})")
        _UM_INFO["avisado"] = True
    return ident


def escribir_campo(driver, elem_id, valor, intentos=5, confirmar_con=Keys.TAB, verificar=True,
                   ventanas=False, pre=0.3, asentar=0.7):
    valor = str(valor)
    for n in range(1, intentos + 1):
        ir_a_elemento(driver, elem_id)
        esperar_psoft(driver)
        time.sleep(pre if n == 1 else 1.5)
        el = driver.find_element(By.ID, elem_id)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        if n <= 3:
            activo = driver.execute_script(
                "return document.activeElement ? document.activeElement.id : null;")
            if activo != elem_id:
                try:
                    ActionChains(driver).move_to_element(el).click().perform()
                except WebDriverException:
                    driver.execute_script("arguments[0].focus();", el)
                time.sleep(0.1)
                activo = driver.execute_script(
                    "return document.activeElement ? document.activeElement.id : null;")
                if activo != elem_id:
                    driver.execute_script("arguments[0].focus(); arguments[0].select();", el)
                    time.sleep(0.1)
            el.send_keys(Keys.CONTROL, "a")
            el.send_keys(Keys.DELETE)
            el.send_keys(valor)
            el.send_keys(confirmar_con)
        else:
            driver.execute_script(JS_SETTER_NATIVO, el, valor)
        esperar_psoft(driver)
        if asentar:
            time.sleep(asentar)
            esperar_psoft(driver)
        if ventanas:
            n_ven, textos = cerrar_ventanas(driver, elem_id, ya_esperado=True)
            _ULTIMO_AVISO["textos"].extend(textos)
            if n_ven:
                print(f"      {n_ven} ventana(s) cerradas con Aceptar tras '{elem_id}' "
                      f"(texto: {textos[0][:90]!r})")
            tipo_error, _ = _clasificar_error(textos)
            if tipo_error == "fatal":
                _ULTIMO_AVISO["es_fatal"] = True
                return False
            if tipo_error == "no_valido":
                return False
        else:
            msg = descartar_mensaje(driver)
            if msg:
                _ULTIMO_AVISO["textos"].append(msg)
                tipo_error, _ = _clasificar_error([msg])
                if tipo_error == "fatal":
                    _ULTIMO_AVISO["es_fatal"] = True
                    return False
                print(f"      MENSAJE DEL ERP en '{elem_id}': {limpiar(msg, 120)}")
        if not verificar:
            return True
        try:
            actual = leer_valor(driver, elem_id)
        except Exception:
            actual = ""
        if _iguales(actual, valor):
            return True
        if valor and actual:
            largo_esperado = len(valor)
            largo_actual = len(actual)
            if largo_actual > 0 and largo_actual < largo_esperado * 0.5:
                print(f"      intento {n}: '{elem_id}' quedó TRUNCADO: '{actual}' (esperado '{valor}')")
                break
        print(f"      intento {n}: '{elem_id}' quedó con '{actual}' (esperado '{valor}')")
        if n == 2:
            diagnostico_inputs(driver, elem_id)
    return False


class Informe:
    def __init__(self, total):
        self.total = total
        self.lineas = {}
        self.generales = []
        self.fatal = None
        self.captura = None
        self.archivo = None

    def linea(self, n, art, cant, um, producto, motivo, detalle=""):
        d = self.lineas.setdefault(n, {"articulo": art, "cantidad": cant, "um": um,
                                       "producto": producto, "problemas": []})
        texto = motivo + (f"  ({detalle})" if detalle else "")
        if texto not in d["problemas"]:
            d["problemas"].append(texto)

    def general(self, motivo, detalle=""):
        texto = motivo + (f"  ({detalle})" if detalle else "")
        if texto not in self.generales:
            self.generales.append(texto)

    @property
    def hay_errores(self):
        return bool(self.lineas or self.generales or self.fatal)

    def resumen_productos_con_error(self):
        """Lista COMPACTA de productos con error, con su CÓDIGO ERP.

        Se usa en el mensaje final de la consola y en la VENTANA FLOTANTE,
        para que la encargada vea de un vistazo QUÉ producto falla y con
        QUÉ código, sin tener que abrir el informe .txt.
        """
        if not self.lineas:
            return ""
        L = [
            "=" * 74,
            "PRODUCTOS QUE HAN DADO ERROR (con su CÓDIGO ERP)",
            "=" * 74,
        ]
        for n in sorted(self.lineas):
            d = self.lineas[n]
            codigo = str(d.get("articulo") or "").strip() or "(sin código)"
            um = str(d.get("um") or "").strip() or "(sin UM)"
            prod = str(d.get("producto") or "").strip() or "(sin descripción)"
            L.append(f"  Fila {n:>3} | CODIGO ERP: {codigo} | UM: {um} | {prod}")
            for p in d["problemas"][:2]:
                L.append(f"           -> {p}")
        L.append("=" * 74)
        L.append(f"  Total: {len(self.lineas)} producto(s)/línea(s) con error.")
        return "\n".join(L)

    def texto(self):
        malas = len(self.lineas)
        L = []
        if self.fatal:
            L += ["EL PROGRAMA SE DETUVO ANTES DE TERMINAR:", f"   {self.fatal}", ""]
        L.append(f"RESULTADO: {self.total - malas} de {self.total} líneas sin incidencias, "
                 f"{malas} con problemas.")
        resumen = self.resumen_productos_con_error()
        if resumen:
            L += ["", resumen]
        if self.generales:
            L += ["", "PROBLEMAS GENERALES (cabecera / sistema):"]
            L += [f"   - {g}" for g in self.generales]
        if self.lineas:
            L += ["", "LÍNEAS CON PROBLEMAS (revísalas a mano en el ERP):"]
            for n in sorted(self.lineas):
                d = self.lineas[n]
                cab = f"   Fila {n}  |  Artículo {d['articulo']}  |  Cant. {d['cantidad']}  |  UM {d['um'] or '(vacía)'}"
                if d["producto"]:
                    cab += f"  |  {d['producto']}"
                L.append("")
                L.append(cab)
                for p in d["problemas"]:
                    L.append(f"        -> {p}")
        L += ["", "QUÉ HACER:",
              "   1) Corrige a mano en el ERP las líneas indicadas (código de artículo, UM o cantidad).",
              "   2) Revisa el resto del pedido y pulsa Guardar tú mismo (el programa NO guarda).",
              "   3) Si hace falta, corrige el archivo de datos y vuelve a ejecutar solo con esas líneas.",
              "   (Chrome se queda abierto: no lo cierres hasta haber guardado el pedido.)"]
        if self.archivo:
            L += ["", f"Este informe se ha guardado en: {self.archivo}"]
        if self.captura:
            L += [f"Captura de la pantalla: {self.captura}"]
        return "\n".join(L)


def limpiar(t, n=220):
    return " ".join(str(t).split())[:n]


def resumen_error(e, n=160):
    lineas = [l.strip() for l in str(e).splitlines() if l.strip()]
    txt = " ".join(lineas[:2]) if lineas else type(e).__name__
    if txt.startswith("Message:"):
        txt = txt[len("Message:"):].strip()
    if isinstance(e, TimeoutError) or "No se encontró el elemento" in txt:
        return "no se encontró el campo en la pantalla (la fila no existe o la pantalla cambió)"
    return txt[:n]


def _es_error_erp(texto):
    t = texto.lower()
    return any(k in t for k in ("no válido", "no valido", "error", "obligatorio",
                                "no existe", "no se puede", "no se ha podido", "incorrect"))


def _a_float(x):
    t = str(x).strip().replace(" ", "")
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    else:
        t = t.replace(",", ".")
    return float(t)


def fmt_cantidad(x):
    return ("%.4f" % _a_float(x)).rstrip("0").rstrip(".").replace(".", ",")


def _datos_linea(datos, i):
    """(n, artículo, cantidad, UM, producto) de la fila i del DataFrame, tolerante a vacíos."""
    f = datos.iloc[i]
    prod = ""
    if "producto" in datos.columns and pd.notna(f["producto"]):
        prod = str(f["producto"]).strip()
    um = "" if pd.isna(f["um"]) else str(f["um"]).strip().upper()
    return (i + 1, str(f["articulo"]).strip(), str(f["cantidad"]), um, prod)


def chrome_vivo(driver):
    try:
        driver.current_url
        return True
    except Exception:
        return False


def recuperar(driver):
    _RUTA[0] = None
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    for _ in range(6):
        try:
            if not descartar_mensaje(driver, profundo=True):
                break
            esperar_psoft(driver, timeout=4)
        except Exception:
            break




def mostrar_ventana(titulo, texto, error=True):
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title(titulo)
        root.attributes("-topmost", True)
        tk.Label(root, text=("ATENCIÓN: hay líneas que revisar" if error else "Carga finalizada"),
                 font=("Segoe UI", 14, "bold"), fg="white",
                 bg=("#b00020" if error else "#1b6e2f"), pady=8).pack(fill="x")
        marco = tk.Frame(root)
        marco.pack(fill="both", expand=True, padx=8, pady=8)
        sb = tk.Scrollbar(marco)
        sb.pack(side="right", fill="y")
        txt = tk.Text(marco, width=115, height=min(30, max(10, texto.count("\n") + 2)),
                      wrap="word", font=("Consolas", 10), yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.insert("1.0", texto)
        txt.config(state="disabled")

        def copiar():
            root.clipboard_clear()
            root.clipboard_append(texto)
            root.update()
        barra = tk.Frame(root)
        barra.pack(pady=(0, 8))
        tk.Button(barra, text="Copiar", command=copiar, width=14).pack(side="left", padx=6)
        tk.Button(barra, text="Cerrar", command=root.destroy, width=14).pack(side="left", padx=6)
        root.update_idletasks()
        root.lift()
        root.focus_force()
        root.after(1500, lambda: root.attributes("-topmost", False))
        root.mainloop()
        return "tkinter"
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, texto[:3000], titulo, (0x30 if error else 0x40) | 0x1000)
        return "windows"
    except Exception:
        print("\n" + "=" * 70 + f"\n{titulo}\n" + "=" * 70 + f"\n{texto}\n")
        return "consola"


def guardar_informe(inf, driver=None):
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    if driver is not None and chrome_vivo(driver):
        try:
            ruta = os.path.join(RUTA_BASE, f"pantalla_pedido_{marca}.png")
            driver.switch_to.default_content()
            if driver.save_screenshot(ruta):
                inf.captura = ruta
        except Exception:
            pass
    try:
        ruta = os.path.join(RUTA_BASE, f"errores_pedido_{marca}.txt")
        inf.archivo = ruta
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(inf.texto())
    except Exception:
        inf.archivo = None


def abrir_pedido(driver, wait, fase):
    fase["txt"] = "Abrir el ERP"
    driver.get(ERP_URL)
    time.sleep(4)
    try:
        ir_a_elemento(driver, "userid", timeout=3)
    except Exception:
        pass
    driver.switch_to.default_content()
    for _ in range(2):
        try:
            btn = WebDriverWait(driver, 6).until(EC.element_to_be_clickable((
                By.XPATH,
                "//input[(@type='submit' or @type='button') and "
                "(contains(@value,'Conectar') or contains(@value,'Iniciar') or @id='signin')]")))
            btn.click()
            time.sleep(2)
        except Exception:
            break
    fase["txt"] = "Inicio de sesión de Microsoft (usuario / contraseña / MFA)"
    driver.switch_to.default_content()
    wait_ms = WebDriverWait(driver, 25)
    try:
        time.sleep(2)
        if "login.microsoftonline.com" in driver.current_url or driver.find_elements(By.ID, "i0116"):
            c = wait_ms.until(EC.element_to_be_clickable((By.ID, "i0116")))
            c.clear()
            c.send_keys(ERP_USER)
            wait_ms.until(EC.element_to_be_clickable((By.ID, "idSIButton9"))).click()
            p = wait_ms.until(EC.element_to_be_clickable((By.ID, "i0118")))
            p.clear()
            p.send_keys(ERP_PASS)
            wait_ms.until(EC.element_to_be_clickable((By.ID, "idSIButton9"))).click()
            time.sleep(3)
            try:
                b = driver.find_element(By.ID, "idSIButton9")
                if b.is_displayed():
                    b.click()
            except Exception:
                pass
    except Exception as e:
        print(f"Aviso Microsoft (quizá ya estaba logueado / MFA pendiente): {e}")
    fase["txt"] = "Navegar por los menús del ERP"
    print("Navegando por los menús del ERP...")
    time.sleep(4)
    for menu_id in ["pthnavbca_PORTAL_ROOT_OBJECT", "fldra_EPPO_PURCHASING",
                    "EPCO_REQUISITIONS", "crefli_EP_REQUISITIONS_GBL"]:
        driver.switch_to.default_content()
        WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, menu_id))).click()
        time.sleep(2)
    fase["txt"] = "Pantalla inicial de Solicitudes (Unidad de Negocio / Añadir)"
    ir_a_elemento(driver, "REQ_ADD_SRCH_BUSINESS_UNIT")
    esperar_psoft(driver)
    bu = driver.find_element(By.ID, "REQ_ADD_SRCH_BUSINESS_UNIT")
    bu.clear()
    bu.send_keys(UNIDAD_NEGOCIO)
    time.sleep(0.5)
    bu.send_keys(Keys.TAB)
    esperar_psoft(driver)
    ir_a_elemento(driver, "REQ_ADD_SRCH_BUSINESS_UNIT")
    boton_accion = None
    for sel in [(By.ID, "#ICSave"), (By.XPATH, "//input[@value='Añadir']"), (By.ID, "#ICSearch")]:
        for cand in driver.find_elements(*sel):
            if cand.is_displayed():
                boton_accion = cand
                break
        if boton_accion is not None:
            break
    if boton_accion is None:
        raise RuntimeError("No se encontró el botón Añadir/Buscar en la pantalla inicial")
    print(f"   Pulsando botón: {boton_accion.get_attribute('id') or boton_accion.get_attribute('value')}")
    driver.execute_script("arguments[0].click();", boton_accion)
    fase["txt"] = "Carga de la pantalla del pedido"
    print("Esperando a que cargue la pantalla del pedido...")
    ir_a_elemento(driver, "REQ_HDR_REQ_NAME", timeout=40)
    esperar_psoft(driver, timeout=40)
    time.sleep(3)


def rellenar_cabecera(driver, inf, meta=None):
    meta = meta or {}
    if meta.get("nombre_pedido"):
        nombre_pedido = meta["nombre_pedido"]
        print(f"   (Nombre de pedido tomado del archivo de datos: {nombre_pedido})")
    else:
        hoy = datetime.now()
        dias = (1 - hoy.weekday()) % 7 or 7
        nombre_pedido = f"Conway {(hoy + timedelta(days=dias)).strftime('%d/%m/%Y')}"
    print(f"3. Nombre de solicitud: {nombre_pedido}")
    try:
        if escribir_campo(driver, "REQ_HDR_REQ_NAME", nombre_pedido):
            print("   OK: nombre escrito y verificado.")
        else:
            print("   ¡NO se pudo fijar el nombre tras todos los intentos!")
            inf.general("No se pudo escribir el Nombre de Solicitud", f"escribe a mano: {nombre_pedido}")
    except Exception as e:
        inf.general("Error al escribir el Nombre de Solicitud", resumen_error(e))
    print(f"   Proveedor: {PROVEEDOR}")
    try:
        if escribir_campo(driver, "CPLPO_REQ_DFLT_VENDOR_ID", PROVEEDOR):
            print("   OK: proveedor escrito y verificado.")
        else:
            print("   ¡NO se pudo fijar el proveedor!")
            inf.general("No se pudo escribir el ID de Proveedor", f"debería ser {PROVEEDOR}")
    except Exception as e:
        inf.general("Error al escribir el ID de Proveedor", resumen_error(e))
    try:
        if leer_valor(driver, "REQ_HDR_REQ_NAME").strip() != nombre_pedido:
            print("   El nombre se perdió tras validar el proveedor, reescribiendo...")
            if not escribir_campo(driver, "REQ_HDR_REQ_NAME", nombre_pedido):
                inf.general("El Nombre de Solicitud se borra al validar el proveedor",
                            f"escribe a mano: {nombre_pedido}")
    except Exception as e:
        inf.general("No se pudo comprobar el Nombre de Solicitud", resumen_error(e))


JS_CONTAR_FILAS = ("return document.querySelectorAll("
                   "\"input[id^='REQ_LINE_WRK_INV_ITEM_ID$']\").length;")




def crear_filas(driver, wait, total, inf, ya_creadas=1):
    """Crea filas hasta llegar a «total», EN TANDAS (nunca todas de golpe).

    Se usa SOLO para la primera tanda (llenar la primera página)."""
    if total <= 1:
        return max(1, ya_creadas)
    existentes = ya_creadas
    TAM_TANDA = 20
    sin_progreso = 0
    while existentes < total:
        faltan = min(TAM_TANDA, total - existentes)
        nuevo = existentes
        try:
            ir_a_grid(driver, timeout=20)
            esperar_psoft(driver)
            driver.execute_script(f"window.prompt = function(t) {{ return '{faltan}'; }};")
            boton_mas = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "img.PTROWADDMULTI")))
            driver.execute_script("arguments[0].click();", boton_mas)
            objetivo = existentes + faltan - 1
            try:
                ir_a_elemento(driver, f"REQ_LINE_WRK_INV_ITEM_ID${objetivo}", timeout=90)
            except Exception:
                pass
            esperar_psoft(driver, timeout=30)
            nuevo = contar_filas(driver)
        except Exception as e:
            print(f"   [aviso] la tanda de {faltan} filas falló: {resumen_error(e)}")
            try:
                esperar_psoft(driver, timeout=60)
                recuperar(driver)
                nuevo = contar_filas(driver)
            except Exception:
                nuevo = existentes
        if nuevo > existentes:
            sin_progreso = 0
            existentes = nuevo
            print(f"   Tanda lista: hay {existentes} filas en pantalla (objetivo {total})")
        else:
            sin_progreso += 1
            if sin_progreso >= 2:
                print("   El ERP no añade más filas; sigo con las que hay.")
                break
            TAM_TANDA = max(5, TAM_TANDA // 2)
            print(f"   Reintento con tandas más pequeñas: {TAM_TANDA}...")
    if existentes < total:
        inf.general(f"Solo hay {existentes} filas en la pantalla de {total} necesarias",
                    "las líneas que no caben se han marcado abajo; añádelas a mano")
    return existentes


_CTX_PREV = {"fila": None, "i": None, "um_id": None}
_DESC = {"fiable": False, "avisado": False}


JS_DESC = r"""
var i = arguments[0];
var els = Array.from(document.querySelectorAll('[id*="DESCR"]')).filter(function(e){
    return e.id.endsWith('$' + i) && !/UNIT|UOM|MEAS|CAT|UM_/i.test(e.id); });
return els.map(function(e){
    return [e.id, ((e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') ? e.value : (e.textContent || '')).trim()]; });
"""


def leer_descripcion(driver, i):
    try:
        ir_a_elemento(driver, f"REQ_LINE_WRK_INV_ITEM_ID${i}", timeout=5)
        r = driver.execute_script(JS_DESC, i)
    except Exception:
        return None
    if not r:
        return None
    if not _DESC["avisado"]:
        print(f"      [info] campo(s) de descripción del producto: {[x[0] for x in r]}")
        _DESC["avisado"] = True
    return max((x[1] for x in r), key=len)


def _vacio(v):
    v = str(v).strip()
    if not v:
        return True
    try:
        return _a_float(v) == 0
    except ValueError:
        return False


def limpiar_fila(driver, i, um_id=None):
    art_id = f"REQ_LINE_WRK_INV_ITEM_ID${i}"
    qty_id = f"REQ_LINE_QTY_REQ${i}"
    try:
        if um_id is None:
            um_id = encontrar_id_um(driver, i, qty_id)
        campos = [c for c in (um_id, qty_id, art_id) if c]
        for _ in range(2):
            for campo, conf in ((um_id, Keys.TAB), (qty_id, Keys.TAB), (art_id, Keys.ENTER)):
                if not campo:
                    continue
                try:
                    escribir_campo(driver, campo, "", intentos=1, verificar=False,
                                   confirmar_con=conf, ventanas=True, pre=0.1, asentar=0)
                except Exception:
                    pass
            cerrar_ventanas(driver, art_id, ya_esperado=True)
            restos = []
            for c in campos:
                try:
                    if not _vacio(leer_valor(driver, c, timeout=4)):
                        restos.append(c)
                except Exception:
                    pass
            if not restos:
                return True
        return False
    except Exception:
        return False




def escribir_campo_grid(driver, elem_id, valor, intentos=5, confirmar_con=Keys.TAB,
                        verificar=True, ventanas=False, pre=0.3, asentar=0.7):
    """Como escribir_campo, pero pensado para las filas del grid: lleva el
    scroll y el foco al campo con JAVASCRIPT (los clics de ratón van por
    coordenadas de pantalla y, con el grid largo, podían caer en otra fila)."""
    valor = str(valor)
    for n in range(1, intentos + 1):
        ir_a_elemento(driver, elem_id)
        esperar_psoft(driver)
        time.sleep(pre if n == 1 else 1.5)
        el = driver.find_element(By.ID, elem_id)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.2)
        driver.execute_script("arguments[0].focus(); arguments[0].select();", el)
        time.sleep(0.1)
        activo = driver.execute_script(
            "return document.activeElement ? document.activeElement.id : null;")
        if activo != elem_id:
            try:
                ActionChains(driver).move_to_element(el).click().perform()
            except WebDriverException:
                driver.execute_script("arguments[0].focus();", el)
            time.sleep(0.1)
            activo = driver.execute_script(
                "return document.activeElement ? document.activeElement.id : null;")
            if activo != elem_id:
                driver.execute_script("arguments[0].focus(); arguments[0].select();", el)
                time.sleep(0.1)
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.DELETE)
        el.send_keys(valor)
        el.send_keys(confirmar_con)
        esperar_psoft(driver)
        if asentar:
            time.sleep(asentar)
            esperar_psoft(driver)
        if ventanas:
            n_ven, textos = cerrar_ventanas(driver, elem_id, ya_esperado=True)
            _ULTIMO_AVISO["textos"].extend(textos)
            if n_ven:
                print(f"      {n_ven} ventana(s) cerradas con Aceptar tras '{elem_id}' "
                      f"(texto: {textos[0][:90]!r})")
            tipo_error, _ = _clasificar_error(textos)
            if tipo_error == "fatal":
                _ULTIMO_AVISO["es_fatal"] = True
                return False
            if tipo_error == "no_valido":
                return False
        else:
            msg = descartar_mensaje(driver)
            if msg:
                _ULTIMO_AVISO["textos"].append(msg)
                tipo_error, _ = _clasificar_error([msg])
                if tipo_error == "fatal":
                    _ULTIMO_AVISO["es_fatal"] = True
                    return False
                print(f"      MENSAJE DEL ERP en '{elem_id}': {limpiar(msg, 120)}")
        if not verificar:
            return True
        try:
            actual = leer_valor(driver, elem_id)
        except Exception:
            actual = ""
        if _iguales(actual, valor):
            return True
        if valor and actual:
            largo_actual = len(actual)
            if 0 < largo_actual < len(valor) * 0.5:
                print(f"      intento {n}: '{elem_id}' quedó TRUNCADO: '{actual}' (esperado '{valor}')")
                break
        print(f"      intento {n}: '{elem_id}' quedó con '{actual}' (esperado '{valor}')")
        if n == 2:
            diagnostico_inputs(driver, elem_id)
    return False




def escribir_campo_grid(driver, elem_id, valor, intentos=5, confirmar_con=Keys.TAB,
                        verificar=True, ventanas=False, pre=0.3, asentar=0.7):
    """Como escribir_campo, pero pensado para las filas del grid: lleva el
    scroll y el foco al campo con JAVASCRIPT (los clics de ratón van por
    coordenadas de pantalla y, con el grid largo, podían caer en otra fila)."""
    valor = str(valor)
    for n in range(1, intentos + 1):
        ir_a_elemento(driver, elem_id)
        esperar_psoft(driver)
        time.sleep(pre if n == 1 else 1.5)
        el = driver.find_element(By.ID, elem_id)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.2)
        driver.execute_script("arguments[0].focus(); arguments[0].select();", el)
        time.sleep(0.1)
        activo = driver.execute_script(
            "return document.activeElement ? document.activeElement.id : null;")
        if activo != elem_id:
            try:
                ActionChains(driver).move_to_element(el).click().perform()
            except WebDriverException:
                driver.execute_script("arguments[0].focus();", el)
            time.sleep(0.1)
            activo = driver.execute_script(
                "return document.activeElement ? document.activeElement.id : null;")
            if activo != elem_id:
                driver.execute_script("arguments[0].focus(); arguments[0].select();", el)
                time.sleep(0.1)
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.DELETE)
        el.send_keys(valor)
        el.send_keys(confirmar_con)
        esperar_psoft(driver)
        if asentar:
            time.sleep(asentar)
            esperar_psoft(driver)
        if ventanas:
            n_ven, textos = cerrar_ventanas(driver, elem_id, ya_esperado=True)
            _ULTIMO_AVISO["textos"].extend(textos)
            if n_ven:
                print(f"      {n_ven} ventana(s) cerradas con Aceptar tras '{elem_id}' "
                      f"(texto: {textos[0][:90]!r})")
            tipo_error, _ = _clasificar_error(textos)
            if tipo_error == "fatal":
                _ULTIMO_AVISO["es_fatal"] = True
                return False
            if tipo_error == "no_valido":
                return False
        else:
            msg = descartar_mensaje(driver)
            if msg:
                _ULTIMO_AVISO["textos"].append(msg)
                tipo_error, _ = _clasificar_error([msg])
                if tipo_error == "fatal":
                    _ULTIMO_AVISO["es_fatal"] = True
                    return False
                print(f"      MENSAJE DEL ERP en '{elem_id}': {limpiar(msg, 120)}")
        if not verificar:
            return True
        try:
            actual = leer_valor(driver, elem_id)
        except Exception:
            actual = ""
        if _iguales(actual, valor):
            return True
        if valor and actual:
            largo_actual = len(actual)
            if 0 < largo_actual < len(valor) * 0.5:
                print(f"      intento {n}: '{elem_id}' quedó TRUNCADO: '{actual}' (esperado '{valor}')")
                break
        print(f"      intento {n}: '{elem_id}' quedó con '{actual}' (esperado '{valor}')")
        if n == 2:
            diagnostico_inputs(driver, elem_id)
    return False


def _en_frame_todos(driver, fn, nivel=0):
    """Ejecuta fn() en el documento actual y en todos los frames anidados."""
    try:
        if fn():
            return True
    except WebDriverException:
        pass
    if nivel >= 4:
        return False
    total = len(driver.find_elements(By.CSS_SELECTOR, "iframe,frame"))
    for i in range(total):
        try:
            driver.switch_to.frame(i)
        except WebDriverException:
            continue
        try:
            if _en_frame_todos(driver, fn, nivel + 1):
                return True
        finally:
            _subir_frame(driver)
    return False


def _click_paginador(driver, texto, timeout=15):
    """Pulsa un enlace del paginador del grid («Último», «Primero»...), si existe."""
    def _buscar():
        for e in driver.find_elements(By.XPATH,
                "//a[normalize-space(.)='" + texto + "'] | //button[normalize-space(.)='" + texto + "']"):
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
        esperar_psoft(driver, timeout=timeout)
    return ok


def procesar_fila(driver, i, fila_datos, inf, n_pantalla, fila_dom=None):
    n = i + 1
    art = str(fila_datos["articulo"]).strip()
    um = "" if pd.isna(fila_datos["um"]) else str(fila_datos["um"]).strip().upper()
    producto = ""
    if "producto" in fila_datos.index and pd.notna(fila_datos["producto"]):
        producto = str(fila_datos["producto"]).strip()
    cant_orig = fila_datos["cantidad"]
    qty = str(cant_orig)
    fila = i if fila_dom is None else fila_dom   # fila real del grid ($n del DOM)

    def err(motivo, detalle=""):
        inf.linea(n, art, qty, um, producto, motivo, detalle)

    def vaciar(um_id=None):
        ok = limpiar_fila(driver, fila, um_id)
        if not ok:
            err("No se pudo vaciar la línea", "bórrala a mano: si no, el ERP seguirá mostrando 'Valor no válido'")
        return ok
    try:
        if pd.isna(cant_orig):
            raise ValueError("vacía")
        qty = fmt_cantidad(cant_orig)
        if _a_float(cant_orig) <= 0:
            raise ValueError("no positiva")
    except (ValueError, TypeError):
        err("Dato incorrecto en el archivo de datos: cantidad", f"valor '{cant_orig}'")
        print(f"   Fila {n}: ¡cantidad incorrecta en el archivo, se omite!")
        return
    if not um:
        err("Dato incorrecto en el archivo de datos: falta la UM")
        print(f"   Fila {n}: ¡falta la UM en el archivo, se omite!")
        return
    if fila_dom is None and i >= n_pantalla:
        err("La fila no existe en la pantalla del ERP", "no se pudieron crear todas las filas")
        print(f"   Fila {n}: ¡la fila no existe en la pantalla, se omite!")
        return
    print(f"   Fila {n}: {art} | {qty} | {um}")
    t_fila = time.time()
    t_esp0 = _T_ESPERA[0]
    # --- si la fila i de la pantalla ya tiene algo, buscar la primera fila libre ---
    try:
        contenido = "" if fila_dom is not None else leer_valor(driver, f"REQ_LINE_WRK_INV_ITEM_ID${i}", timeout=4).strip()
        if contenido:
            libre = _primera_fila_libre(driver, i + 60)
            if libre is not None and libre != i:
                print(f"      [aviso] la fila de pantalla {i + 1} no estaba libre; "
                      f"escribo en la fila {libre + 1} (alineación corregida)")
                fila = libre
    except Exception:
        pass
    if n % 10 == 0:
        if not verificar_pantalla_activa(driver):
            inf.fatal = "Se cambió de pantalla durante la carga (probablemente navegación errónea del ERP)"
            print("      ¡La pantalla cambió! Deteniendo carga.")
            return
    try:
        pend = []
        for _ in range(5):
            t = descartar_mensaje(driver)
            if not t:
                break
            pend.append(t)
            esperar_psoft(driver, timeout=4)
        if pend:
            tipo_error, msg = _clasificar_error(pend)
            if tipo_error == "fatal":
                inf.fatal = f"Error fatal del ERP detectado: {msg}"
                print(f"      ¡Error fatal: {msg}! Deteniendo carga.")
                return
            errores_no_fatales = [t for t in pend if _es_error_erp(t)]
            prev = _CTX_PREV["fila"]
            if errores_no_fatales and prev:
                inf.linea(*prev, "El ERP mostró un aviso de error justo después de esta línea",
                          limpiar(errores_no_fatales[0]))
                print(f"      ¡aviso tardío de la fila {prev[0]}! Se vacía esa línea para poder seguir.")
                if not limpiar_fila(driver, _CTX_PREV["i"], _CTX_PREV["um_id"]):
                    inf.linea(*prev, "No se pudo vaciar la línea", "bórrala a mano")
    except Exception as e:
        print(f"      [diag] fallo al procesar errores pendientes: {e}")
    _CTX_PREV.update(fila=(n, art, qty, um, producto), i=fila, um_id=None)
    art_id = f"REQ_LINE_WRK_INV_ITEM_ID${fila}"
    _ULTIMO_AVISO["textos"] = []
    _ULTIMO_AVISO["es_fatal"] = False
    try:
        ok_a = escribir_campo_grid(driver, art_id, art, confirmar_con=Keys.ENTER,
                                   ventanas=True, pre=0.1, asentar=0)
        if _ULTIMO_AVISO["es_fatal"]:
            inf.fatal = f"Error fatal en línea {n} al escribir artículo"
            return
        if ok_a:
            time.sleep(0.3)
            actual = leer_valor(driver, art_id)
            if not _iguales(actual, art):
                ok_a = False
                print(f"      [validación 2] artículo cambió tras escribir: '{actual}'")
    except Exception as e:
        err("No se pudo escribir el artículo", resumen_error(e))
        print(f"      ¡artículo: {resumen_error(e)}!")
        recuperar(driver)
        return
    t_art = time.time() - t_fila
    textos = list(_ULTIMO_AVISO["textos"])
    if _es_no_valido(textos):
        err("El ERP no reconoce el artículo (Valor no válido)", "comprueba que el código existe; la línea queda vacía")
        print(f"      ¡El ERP rechaza el artículo {art} ('Valor no válido')! Fila {n} omitida y vaciada.")
        vaciar()
        return
    for t in textos:
        if _es_error_erp(t):
            err("El ERP muestra un mensaje al validar el artículo", limpiar(t))
    if not ok_a:
        err("El artículo no se quedó escrito en la línea", "la línea queda vacía")
        print(f"      ¡el artículo {art} no se quedó escrito! Fila {n} omitida y vaciada.")
        vaciar()
        return
    desc = leer_descripcion(driver, fila)
    if desc is not None and not desc:
        for _ in range(3):
            time.sleep(0.5)
            esperar_psoft(driver, timeout=3)
            desc = leer_descripcion(driver, fila) or ""
            if desc:
                break
    if desc:
        _DESC["fiable"] = True
    elif desc is not None and _DESC["fiable"]:
        # --- v6c -----------------------------------------------------------
        # El grid paginado del ERP a veces no deja LEER la descripción aunque
        # el artículo SÍ quedó escrito. En v6b eso hacía vaciar() la línea y
        # se perdía un producto bueno (pasó en las líneas 93, 96, 98, 99 y
        # 100 del test de 100 líneas). Ahora se comprueba el código: si sigue
        # en su campo, se CONSERVA la línea y solo se avisa. Si el código no
        # está, se mantiene el comportamiento anterior (vaciar y anotar).
        try:
            actual = leer_valor(driver, art_id) or ""
        except Exception:
            actual = ""
        if actual and _iguales(actual, art):
            err("No se pudo leer la descripción, pero el código SÍ quedó escrito",
                "la línea se CONSERVA; revísala a mano antes de Guardar")
            print(f"      [v6c] fila {n}: descripción no legible, pero {art} sigue "
                  f"escrito. NO se borra la línea.")
        else:
            err("El ERP no reconoce el producto (no cargó su descripción)",
                "comprueba el código; la línea queda vacía")
            print(f"      ¡El ERP no cargó la descripción de {art}! Fila {n} omitida y vaciada.")
            vaciar()
            return
        # --------------------------------------------------------------------
    t1 = time.time()
    ok_q = False
    _ULTIMO_AVISO["textos"] = []
    _ULTIMO_AVISO["es_fatal"] = False
    try:
        ok_q = escribir_campo_grid(driver, f"REQ_LINE_QTY_REQ${fila}", qty, pre=0.1, asentar=0.2)
        if _ULTIMO_AVISO["es_fatal"]:
            inf.fatal = f"Error fatal en línea {n} al escribir cantidad"
            return
        if _es_no_valido(_ULTIMO_AVISO["textos"]):
            err("El ERP no acepta la cantidad (Valor no válido)", f"valor {qty}; la línea queda vacía")
            print(f"      ¡El ERP rechaza la cantidad {qty}! Fila {n} omitida y vaciada.")
            vaciar()
            return
        if not ok_q:
            err("La cantidad no se quedó escrita", f"esperado {qty}")
    except Exception as e:
        err("No se pudo escribir la cantidad", resumen_error(e))
        print(f"      ¡cantidad: {resumen_error(e)}!")
    t_qty = time.time() - t1
    t0 = time.time()
    _ULTIMO_AVISO["textos"] = []
    _ULTIMO_AVISO["es_fatal"] = False
    ok_u = False
    um_id = None
    try:
        um_id = encontrar_id_um(driver, fila, f"REQ_LINE_QTY_REQ${fila}")
        _CTX_PREV["um_id"] = um_id
        if um_id:
            try:
                activo = driver.execute_script(
                    "return document.activeElement ? document.activeElement.id : null;")
                if activo != um_id:
                    driver.execute_script(f"document.getElementById('{um_id}').focus();")
                    time.sleep(0.2)
                ok_u = escribir_campo_grid(driver, um_id, um, intentos=1, verificar=False,
                                           confirmar_con=Keys.ENTER, ventanas=True, pre=0.1, asentar=0)
            except Exception as e:
                print(f"      UM por ID falló ({resumen_error(e)}); escribo en el campo con foco...")
                um_id = None
        if not um_id:
            activo = driver.execute_script(
                "return document.activeElement ? document.activeElement.id : null;")
            print(f"      [diag] foco actual antes de escribir UM: {activo}")
            ActionChains(driver).send_keys(um).send_keys(Keys.ENTER).perform()
            esperar_psoft(driver)
            n_ven, tx = cerrar_ventanas(driver)
            _ULTIMO_AVISO["textos"].extend(tx)
            if n_ven:
                print(f"      {n_ven} ventana(s) cerradas con Aceptar")
            ok_u = not _es_no_valido(tx)
        if um_id and ok_u:
            for intento_um in range(2):
                try:
                    val_um = leer_valor(driver, um_id, timeout=5).strip()
                    if val_um:
                        break
                except Exception:
                    break
                if intento_um == 0:
                    print("      UM vacía tras las ventanas; buscando ventanas en todos los frames...")
                    descartar_mensaje(driver, profundo=True)
                    try:
                        ok_u = escribir_campo_grid(driver, um_id, um, intentos=1, verificar=False,
                                                   confirmar_con=Keys.ENTER, ventanas=True, pre=0.1, asentar=0)
                    except Exception:
                        break
    except Exception as e:
        err("No se pudo escribir la UM", resumen_error(e))
        print(f"      ¡UM: {resumen_error(e)}!")
        ok_u = None
        recuperar(driver)
    textos = list(_ULTIMO_AVISO["textos"])
    um_fallo = False
    if _ULTIMO_AVISO["es_fatal"]:
        inf.fatal = f"Error fatal en línea {n} al escribir UM"
        return
    if _es_no_valido(textos):
        err(f"El ERP no acepta la UM '{um}' para este artículo (Valor no válido)",
            "revisa la UM o el artículo; la línea queda vacía")
        print(f"      ¡El ERP rechaza la UM '{um}' para el artículo {art} ('Valor no válido')! Fila {n} vaciada.")
        um_fallo = True
    else:
        for t in textos:
            if _es_error_erp(t):
                err("El ERP muestra un mensaje al validar la UM", limpiar(t))
        if ok_u is False:
            err(f"La UM '{um}' no se pudo introducir", "la línea queda vacía")
            um_fallo = True
    if um_fallo:
        vaciar(um_id)
    print(f"      [tiempo] fila {n}: artículo {t_art:.1f}s | cantidad {t_qty:.1f}s | "
          f"UM {time.time() - t0:.1f}s | total {time.time() - t_fila:.1f}s "
          f"(de ellos, esperando al ERP: {_T_ESPERA[0] - t_esp0:.1f}s)")


JS_LEER_LINEAS = """
var n = arguments[0], umPat = arguments[1], r = [];
for (var i = 0; i < n; i++) {
  var a = document.getElementById('REQ_LINE_WRK_INV_ITEM_ID$' + i);
  var q = document.getElementById('REQ_LINE_QTY_REQ$' + i);
  var u = umPat ? document.getElementById(umPat.replace('{}', i)) : null;
  r.push([a ? a.value : null, q ? q.value : null, u ? u.value : null]);
}
return r;
"""


JS_DESC_TODAS = r"""
var n = arguments[0], r = [];
for (var i = 0; i < n; i++) {
  var els = Array.from(document.querySelectorAll('[id*="DESCR"]')).filter(function(e){
      return e.id.endsWith('$' + i) && !/UNIT|UOM|MEAS|CAT|UM_/i.test(e.id); });
  var t = null;
  els.forEach(function(e){
      var v = ((e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') ? e.value : (e.textContent || '')).trim();
      if (t === null || v.length > t.length) t = v; });
  r.push(t);
}
return r;
"""


JS_LEER_TODAS_DOM = """
var pat = arguments[0];
var inputs = document.querySelectorAll("input[id^='REQ_LINE_WRK_INV_ITEM_ID$']");
var r = {};
for (var k = 0; k < inputs.length; k++) {
  var m = inputs[k].id.split('$');
  var i = parseInt(m[m.length - 1], 10);
  if (isNaN(i)) continue;
  var q = document.getElementById('REQ_LINE_QTY_REQ$' + i);
  var u = pat ? document.getElementById(pat.replace('{}', i)) : null;
  r[i] = [inputs[k].value, q ? q.value : null, u ? u.value : null];
}
return r;
"""




def _marcar_pendientes(inf, datos, desde, motivo):
    for j in range(desde, len(datos)):
        inf.linea(*_datos_linea(datos, j), motivo)






EXT_SOPORTADAS = (".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv", ".txt", ".json", ".jsonl",
                  ".ndjson", ".xml", ".html", ".htm", ".dat", ".prn", ".md")

SINONIMOS = {
    "articulo": ["articulo", "art", "codigo", "codigoarticulo", "cod", "codart", "sku", "item", "itemid",
                 "idarticulo", "idart", "referencia", "ref", "invitemid", "reqlinewrkinvitemid",
                 "codigoproducto", "codproducto", "idproducto", "productid", "productcode", "material"],
    "cantidad": ["cantidad", "cant", "cantidadsolicitada", "cantidadpedida", "qty", "quantity", "qtyreq",
                 "reqlineqtyreq", "unidades", "uds", "ud", "pedir", "pedido"],
    "um": ["um", "unidad", "unidadmedida", "unidaddemedida", "uom", "unit", "unitofmeasure",
           "unitmeasure", "medida", "umedida", "umcod"],
    "producto": ["producto", "descripcion", "nombre", "description", "desc", "name", "nombreproducto",
                 "descripcionarticulo", "denominacion", "detalle"],
}


def _clave(s):
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s.lower())


_SIN_INV = {}
for _nombre_canon, _lista_sin in SINONIMOS.items():
    for _sin in [_nombre_canon] + _lista_sin:
        _SIN_INV.setdefault(_clave(_sin), _nombre_canon)


def _canon(nombre):
    k = _clave(nombre)
    if k in _SIN_INV:
        return _SIN_INV[k]
    ultimo = re.split(r"[./]", str(nombre))[-1]
    return _SIN_INV.get(_clave(ultimo))


def _decodificar(b):
    if b.startswith(b"\xef\xbb\xbf"):
        return b.decode("utf-8-sig"), "UTF-8"
    if b.startswith((b"\xff\xfe", b"\xfe\xff")):
        return b.decode("utf-16"), "UTF-16"
    if b[:400].count(b"\x00") > 20:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return b.decode(enc), "UTF-16"
            except UnicodeDecodeError:
                pass
    try:
        return b.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        return b.decode("cp1252", errors="replace"), "Windows-1252"


def detectar_tipo(b):
    if b[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(b)) as z:
                nombres = z.namelist()
                if "mimetype" in nombres and b"opendocument.spreadsheet" in z.read("mimetype"):
                    return "ods"
                if any(n.startswith("xl/") for n in nombres):
                    return "xlsx"
        except Exception:
            pass
        return "zip"
    if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls"
    if b[:5] == b"%PDF-":
        return "pdf"
    texto, _ = _decodificar(b[:4000])
    ini = texto.lstrip()
    if ini[:1] in ("[", "{"):
        return "json"
    if ini[:1] == "<":
        bajo = ini[:3000].lower()
        return "html" if ("<html" in bajo or "<table" in bajo or "<!doctype html" in bajo) else "xml"
    return "texto"


def _celda(v):
    if v is None:
        return ""
    if isinstance(v, float) and v != v:
        return ""
    return str(v).strip()


def _grids_excel(b, tipo):
    motor = {"xlsx": "openpyxl", "ods": "odf", "xls": "xlrd"}[tipo]
    libreria = {"xlsx": "openpyxl", "ods": "odfpy", "xls": "xlrd"}[tipo]
    try:
        hojas = pd.read_excel(io.BytesIO(b), sheet_name=None, header=None, dtype=str, engine=motor)
    except ImportError:
        raise ValueError(f"Falta instalar una librería para leer archivos .{tipo}.\n"
                         f"Abre una consola y ejecuta:  pip install {libreria}")
    return [(str(nombre), [[_celda(c) for c in fila] for fila in df.values.tolist()])
            for nombre, df in hojas.items()]


def _partir(linea, sep):
    if sep == "\t":
        return [c.strip() for c in linea.split("\t")]
    if sep == "|":
        return [c.strip() for c in linea.strip().strip("|").split("|")]
    if sep in (";", ","):
        return [c.strip() for c in next(csv.reader([linea], delimiter=sep))]
    if sep == "2esp":
        return [c.strip() for c in re.split(r"\s{2,}", linea.strip())]
    return linea.split()


_DELIMITADORES = (("tabulador", "\t"), ("barra vertical |", "|"), ("punto y coma ;", ";"),
                 ("coma ,", ","), ("espacios", "2esp"), ("espacio", "esp"))


def _grid_desde_texto(texto):
    lineas = [l.rstrip() for l in texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lineas = [l for l in lineas if l.strip() and not l.strip().startswith("```")
              and not re.fullmatch(r"[\s|:+\-=_]+", l)]
    if not lineas:
        raise ValueError("el archivo no contiene datos")
    muestra = lineas[:60]
    for nombre, sep in _DELIMITADORES:
        try:
            filas = [_partir(l, sep) for l in muestra]
        except Exception:
            continue
        (ncols, freq), = Counter(len(f) for f in filas).most_common(1)
        if ncols >= 2 and freq / len(filas) >= 0.7:
            return [_partir(l, sep) for l in lineas], nombre
    raise ValueError("no se reconoce cómo están separadas las columnas")


def _registros_json(obj, prof=0):
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            return "registros", obj
        if obj and all(isinstance(x, (list, tuple)) for x in obj):
            return "grid", [[_celda(c) for c in fila] for fila in obj]
        if obj and all(isinstance(x, str) for x in obj):
            return "texto", "\n".join(obj)
        return None
    if isinstance(obj, dict):
        vals = list(obj.values())
        if vals and all(isinstance(v, list) for v in vals) and len({len(v) for v in vals}) == 1 \
                and any(_canon(k) for k in obj):
            n = len(vals[0])
            return "registros", [{k: obj[k][i] for k in obj} for i in range(n)]
        if vals and all(isinstance(v, dict) for v in vals) and not any(_canon(k) for k in obj) \
                and all(any(_canon(k) for k in v) for v in vals):
            return "registros", vals
        for v in vals:
            if isinstance(v, (list, dict)) and prof < 3:
                r = _registros_json(v, prof + 1)
                if r:
                    return r
        if any(_canon(k) for k in obj):
            return "registros", [obj]
    return None


_META_CLAVES = {
    "proveedor": {"proveedor", "idproveedor", "vendor", "vendorid", "supplier"},
    "unidad_negocio": {"unidadnegocio", "unidaddenegocio", "businessunit", "bu"},
    "nombre_pedido": {"nombrepedido", "nombresolicitud", "nombredelpedido", "reqname", "nombrerequisicion"},
}


def _meta_json(obj):
    meta = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v).strip():
                for destino, claves in _META_CLAVES.items():
                    if _clave(k) in claves:
                        meta[destino] = str(v).strip()
    return meta


def _grid_desde_json(texto):
    try:
        obj = json.loads(texto)
        forma = "JSON"
    except ValueError:
        try:
            obj = [json.loads(l) for l in texto.splitlines() if l.strip()]
            forma = "JSON Lines"
        except ValueError:
            raise ValueError("el archivo parece JSON pero tiene errores de sintaxis")
    r = _registros_json(obj)
    if not r:
        raise ValueError("no encontró ninguna lista de líneas dentro del JSON")
    meta = _meta_json(obj)
    tipo, datos = r
    if tipo == "registros":
        df = pd.json_normalize(datos, sep=".")
        return [list(map(str, df.columns))] + [[_celda(c) for c in f] for f in df.values.tolist()], forma, meta
    if tipo == "grid":
        return datos, forma + " (lista de listas)", meta
    g, _ = _grid_desde_texto(datos)
    return g, forma + " (lista de textos)", meta


def _grid_desde_xml(b):
    import xml.etree.ElementTree as ET
    try:
        raiz = ET.fromstring(b)
    except ET.ParseError as e:
        raise ValueError(f"el XML tiene errores de sintaxis ({e})")

    def nom(e):
        return e.tag.split("}")[-1]

    def registro(e):
        r = {k.split("}")[-1]: v for k, v in e.attrib.items()}
        for h in e:
            if len(h) == 0:
                r[nom(h)] = (h.text or "").strip()
            else:
                for k, v in h.attrib.items():
                    r[nom(h) + "." + k.split("}")[-1]] = v
                for hh in h:
                    r[nom(h) + "." + nom(hh)] = (hh.text or "").strip()
        return r
    cuenta = Counter(e.tag for e in raiz.iter())
    elegido = None
    for tag, n in cuenta.most_common():
        elems = list(raiz.iter(tag))
        if elems[0] is raiz:
            continue
        if any(len(e) or e.attrib for e in elems):
            elegido = elems
            break
    if elegido is None:
        elegido = [raiz]
    regs = [registro(e) for e in elegido]
    cols = []
    for r in regs:
        for k in r:
            if k not in cols:
                cols.append(k)
    return [cols] + [[_celda(r.get(c)) for c in cols] for r in regs]


def _grids_html(texto):
    try:
        tablas = pd.read_html(io.StringIO(texto))
    except ImportError:
        raise ValueError("Falta instalar una librería para leer tablas HTML.\n"
                         "Abre una consola y ejecuta:  pip install lxml")
    except ValueError:
        raise ValueError("el HTML no contiene ninguna tabla")
    res = []
    for k, t in enumerate(tablas, 1):
        cab = [str(c) for c in t.columns]
        res.append((f"tabla {k}", [cab] + [[_celda(c) for c in f] for f in t.values.tolist()]))
    return res


def _tipo_col(vals):
    vals = [v for v in vals if v != ""]
    if not vals:
        return "vacia"

    def frac(p):
        return sum(1 for v in vals if re.fullmatch(p, v)) / len(vals)
    if frac(r"\d{6,}(\.0+)?") >= 0.8:
        return "articulo"
    if frac(r"\d+([.,]\d+)?") >= 0.8:
        return "numero"
    if frac(r"(?=.*[A-Za-z])[A-Za-z0-9]{2,5}") >= 0.8:
        return "codigo"
    return "texto"


def _es_secuencia(vals):
    try:
        nums = [int(v) for v in vals]
    except ValueError:
        return False
    return len(nums) >= 2 and (nums == list(range(1, len(nums) + 1)) or nums == list(range(len(nums))))


def _normalizar(grid):
    grid = [[_celda(c) for c in f] for f in grid]
    grid = [f for f in grid if any(f)]
    if not grid:
        raise ValueError("no hay datos")
    ancho = max(len(f) for f in grid)
    grid = [f + [""] * (ancho - len(f)) for f in grid]
    cab_idx = None
    for k, f in enumerate(grid[:25]):
        if len({_canon(c) for c in f if c and _canon(c)}) >= 2:
            cab_idx = k
            break
    if cab_idx is not None:
        cab, datos = grid[cab_idx], grid[cab_idx + 1:]
    else:
        cab, datos = [""] * ancho, grid
    if not datos:
        raise ValueError("hay cabecera pero ninguna línea de datos")
    col = {}
    for j, c in enumerate(cab):
        cn = _canon(c) if c else None
        if cn and cn not in col:
            col[cn] = j
    falta = [c for c in ("articulo", "cantidad", "um") if c not in col]
    if falta:
        libres = [j for j in range(ancho) if j not in col.values()]
        tipos = {j: _tipo_col([f[j] for f in datos]) for j in libres}
        numeros = [j for j in libres if tipos[j] == "numero"]
        if len(numeros) >= 2 and _es_secuencia([f[numeros[0]] for f in datos if f[numeros[0]]]):
            libres.remove(numeros[0])
            numeros.pop(0)
        for nombre, tipo in (("articulo", "articulo"), ("cantidad", "numero"), ("um", "codigo")):
            if nombre in col:
                continue
            for j in libres:
                if tipos[j] == tipo:
                    col[nombre] = j
                    libres.remove(j)
                    break
        if "producto" not in col:
            for j in libres:
                if tipos[j] == "texto":
                    col["producto"] = j
                    break
    falta = [c for c in ("articulo", "cantidad", "um") if c not in col]
    if falta:
        raise ValueError("no pude identificar la(s) columna(s): " + ", ".join(falta))
    filas, sin_art, avisos = [], 0, []
    for f in datos:
        d = {k: f[j] for k, j in col.items()}
        if not any(d.values()):
            continue
        if not d["articulo"]:
            sin_art += 1
            continue
        d["articulo"] = re.sub(r"\.0+$", "", d["articulo"])
        d["um"] = d["um"].upper()
        d["cantidad"] = re.sub(r"^(\d+)\.0+$", r"\1", d["cantidad"])
        filas.append(d)
    if not filas:
        raise ValueError("no hay ninguna línea con artículo")
    if sin_art:
        avisos.append(f"{sin_art} fila(s) sin artículo se han ignorado")
    if any(re.fullmatch(r"\d[.,]?\d*[eE][+\-]?\d+", d["articulo"]) for d in filas):
        avisos.append("hay artículos en notación científica: Excel estropeó los códigos")
    cols = ["articulo", "cantidad", "um"] + (["producto"] if "producto" in col else [])
    return pd.DataFrame([{c: d.get(c, "") for c in cols} for d in filas], columns=cols), avisos, cab_idx is not None


def leer_datos(ruta):
    with open(ruta, "rb") as fh:
        b = fh.read()
    if not b.strip():
        raise ValueError("el archivo está vacío")
    tipo = detectar_tipo(b)
    ext = os.path.splitext(ruta)[1].lower()
    desc, avisos, meta = "", [], {}
    if tipo in ("pdf", "zip"):
        raise ValueError({"pdf": "es un PDF: expórtalo a Excel/CSV primero",
                          "zip": "es un archivo comprimido o un formato no reconocido"}[tipo])
    candidatos = []
    if tipo in ("xlsx", "ods", "xls"):
        nombre_tipo = {"xlsx": "Excel (.xlsx)", "ods": "OpenDocument (.ods)", "xls": "Excel antiguo (.xls)"}[tipo]
        for hoja, g in _grids_excel(b, tipo):
            candidatos.append((f"{nombre_tipo}, hoja «{hoja}»", g))
    else:
        texto, enc = _decodificar(b)
        if tipo == "json":
            try:
                g, forma, meta = _grid_desde_json(texto)
                candidatos.append((forma, g))
            except ValueError:
                tipo = "texto"
        if tipo == "xml":
            candidatos.append(("XML", _grid_desde_xml(b)))
        elif tipo == "html":
            for nombre, g in _grids_html(texto):
                candidatos.append((f"HTML ({nombre})", g))
        if tipo == "texto":
            g, sep = _grid_desde_texto(texto)
            candidatos.append((f"Texto con columnas separadas por {sep} ({enc})", g))
    errores = []
    for d, g in candidatos:
        try:
            df, av, con_cab = _normalizar(g)
        except ValueError as e:
            errores.append(f"{d}: {e}")
            continue
        desc = d + ("" if con_cab else ", sin cabecera (columnas deducidas por su contenido)")
        esperado = {"xlsx": (".xlsx", ".xlsm"), "xls": (".xls",), "ods": (".ods",),
                    "json": (".json", ".jsonl", ".ndjson"), "xml": (".xml",), "html": (".html", ".htm")}.get(tipo)
        if esperado and ext and ext not in esperado:
            avisos.append(f"el archivo se llama «{ext}» pero su contenido es {desc.split(',')[0]}")
        elif tipo == "texto" and ext in (".xlsx", ".xlsm", ".xls", ".ods", ".json"):
            avisos.append(f"el archivo se llama «{ext}» pero en realidad es texto/CSV")
        return df, {"formato": desc, "avisos": avisos + av, "meta": meta}
    raise ValueError("No se pudo interpretar el contenido como una lista de líneas.\n  " + "\n  ".join(errores))


def buscar_archivo_datos(archivo_forzado=None):
    if archivo_forzado:
        p = archivo_forzado.strip('"')
        if not os.path.isfile(p):
            raise FileNotFoundError(f"No existe el archivo indicado:\n{p}")
        return p, ""
    cands = []
    for f in os.listdir(RUTA_BASE):
        base, ext = os.path.splitext(f)
        p = os.path.join(RUTA_BASE, f)
        if f.startswith(("~$", ".")) or not os.path.isfile(p):
            continue
        if base.lower().startswith("datos") and ext.lower() in EXT_SOPORTADAS:
            cands.append(p)
    if not cands:
        raise FileNotFoundError(
            "No se encontró ningún archivo de datos en la carpeta del programa:\n" + RUTA_BASE +
            "\n\nColoca uno llamado «datos» con cualquier extensión: " + ", ".join(EXT_SOPORTADAS))
    cands.sort(key=os.path.getmtime, reverse=True)
    nota = ""
    if len(cands) > 1:
        nota = (f"hay {len(cands)} archivos «datos.*»; se usa el más reciente. Ignorados: " +
                ", ".join(os.path.basename(c) for c in cands[1:]))
    return cands[0], nota


def cargar_datos(archivo_forzado=None):
    ruta, nota = buscar_archivo_datos(archivo_forzado)
    df, info = leer_datos(ruta)
    if nota:
        info["avisos"].insert(0, nota)
    return ruta, df, info


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


def crear_driver():
    """Chrome se deja ABIERTO al terminar el script (detach) para poder revisar y guardar el pedido."""
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("detach", True)
    try:
        return webdriver.Chrome(options=opts)
    except WebDriverException as e_nativo:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
        except Exception as e_wm:
            raise RuntimeError(f"No se pudo iniciar Chrome. Selenium: {resumen_error(e_nativo)} | "
                               f"webdriver_manager: {resumen_error(e_wm)}")


def _parse_argumentos():
    parser = argparse.ArgumentParser(
        description="Robot de carga de pedidos Conway en el ERP (PeopleSoft)."
    )
    parser.add_argument(
        "archivo", nargs="?", default=None,
        help=("Ruta al archivo de datos del pedido (JSON, Excel, CSV...). "
              "Si no se indica, se busca un archivo 'datos.*' en la carpeta del programa.")
    )
    parser.add_argument(
        "--auto", action="store_true",
        help=("Modo no interactivo: no pide confirmación de las líneas ni usuario/contraseña "
              "por teclado. ERP_USER y ERP_PASS deben venir del .env. Pensado para que lo "
              "lance CineStock automáticamente al pulsar 'Enviar a ERP'.")
    )
    return parser.parse_args()


def main():
    global ERP_USER, ERP_PASS, PROVEEDOR, UNIDAD_NEGOCIO
    args = _parse_argumentos()
    try:
        ruta, datos, info = cargar_datos(args.archivo)
    except Exception as e:
        mostrar_ventana("Pedido ERP - Error", f"No se pudo leer el archivo de datos:\n\n{e}", error=True)
        return
    print(f"Archivo de datos: {ruta}")
    print(f"Formato detectado: {info['formato']}")
    for a in info["avisos"]:
        print(f"   [aviso] {a}")
    meta = info.get("meta", {})
    if "proveedor" in meta:
        PROVEEDOR = meta["proveedor"]
        print(f"   Proveedor (del archivo de datos): {PROVEEDOR}")
    if "unidad_negocio" in meta:
        UNIDAD_NEGOCIO = meta["unidad_negocio"]
        print(f"   Unidad de Negocio (del archivo de datos): {UNIDAD_NEGOCIO}")
    print(f"{len(datos)} líneas a cargar:")
    print(datos.to_string(index=False))
    if args.auto:
        print("Modo --auto: se omite la confirmación de líneas "
              "(ya se revisaron en CineStock antes de pulsar 'Enviar a ERP').")
    else:
        if input("¿Son estas las líneas correctas? Enter = continuar, N = cancelar: ").strip().lower() == "n":
            print("Cancelado: sustituye el archivo de datos por el correcto y vuelve a ejecutar.")
            return
    if args.auto:
        if not ERP_USER or not ERP_PASS:
            mensaje = ("Modo --auto: faltan ERP_USER y/o ERP_PASS.\n\n"
                       "Añade esas dos variables al archivo .env de la raíz del proyecto "
                       "(junto a OPENAI_API_KEY) antes de lanzar el robot automáticamente.")
            print(f"\n¡{mensaje}")
            mostrar_ventana("Pedido ERP - Faltan credenciales", mensaje, error=True)
            return
    else:
        ERP_USER = ERP_USER or input("Usuario Microsoft: ")
        ERP_PASS = ERP_PASS or getpass.getpass("Contraseña Microsoft: ")
    if os.name == "nt":
        os.system("taskkill /f /im chromedriver.exe >nul 2>&1")
        time.sleep(1)
    inf = Informe(len(datos))
    driver = None
    fase = {"txt": "Abrir Chrome"}
    try:
        driver = crear_driver()
        wait = WebDriverWait(driver, 20)
        try:
            abrir_pedido(driver, wait, fase)
        except Exception as e:
            inf.fatal = f"Fallo en el paso «{fase['txt']}»: {resumen_error(e)}"
            print(f"\n¡{inf.fatal}")
        if not inf.fatal:
            rellenar_cabecera(driver, inf, meta)
            cargar_lineas(driver, wait, datos, inf)
            print("Carga finalizada. NO se ha pulsado Guardar (hazlo manualmente).")
        else:
            _marcar_pendientes(inf, datos, 0, "No se llegó a procesar")
    except KeyboardInterrupt:
        inf.fatal = "Interrumpido por el usuario (Ctrl+C)."
    except Exception as e:
        inf.fatal = f"Error inesperado: {resumen_error(e)}"
        print(f"\n¡{inf.fatal}")
        
    if inf.hay_errores:
        guardar_informe(inf, driver)
        # v6c: inf.texto() ya incluye el resumen con CÓDIGOS ERP. Se imprime
        # en la consola y, sobre todo, se pone AL PRINCIPIO de la ventana
        # flotante para que sea lo primero que vea la encargada.
        resumen = inf.resumen_productos_con_error()
        print("\n" + inf.texto())
        print("\n" + "=" * 74)
        print("  MENSAJE FINAL: arriba tienes los PRODUCTOS CON ERROR")
        print("  junto a su CÓDIGO ERP. La misma lista está en la ventana")
        print("  flotante y en el informe .txt.")
        print("=" * 74)

        txt_error = (f"Se han cargado {len(datos) - len(inf.lineas)} líneas. "
                     f"Hay {len(inf.lineas)} incidencias.\n\n"
                     "👉 IMPORTANTE: Ve a Chrome, revisa las líneas marcadas, "
                     "corrígelas a mano y pulsa GUARDAR en el ERP.\n\n"
                     "NO CIERRES ESTA VENTANA NI LA CONSOLA NEGRA. El robot se "
                     "quedará esperando para leer el ID de Solicitud en cuanto "
                     "pulses Guardar.")
        if resumen:
            txt_error = (
                "PRODUCTOS QUE HAN DADO ERROR (CÓDIGO ERP)\n"
                "Revísalos en el ERP antes de Guardar:\n\n"
                + resumen + "\n\n" + "-" * 74 + "\n\n" + txt_error
            )
        mostrar_ventana("Pedido ERP - Revisión necesaria", txt_error, error=True)
    else:
        txt_ok = (f"¡Las {len(datos)} líneas se han cargado correctamente!\n\n"
                  "👉 PULSA 'GUARDAR' EN EL ERP AHORA.\n\n"
                  "NO CIERRES ESTA VENTANA NI LA CONSOLA NEGRA. El robot se quedará esperando en segundo plano para leer el ID de Solicitud automáticamente en cuanto pulses Guardar, y lo anotará en tu Registro de Pedidos.")
        print("\n" + txt_ok)
        mostrar_ventana("Pedido ERP - Carga finalizada", txt_ok, error=False)
        
    # --- VIGILANTE EN SU SITIO CORRECTO ---
    if args.auto and driver is not None and not inf.fatal:
        try:
            from robot_id_pedido import vigilar_id_pedido
            print("\n" + "="*60)
            print(" 🕵️‍♂️ ESPERANDO A QUE PULSES GUARDAR EN EL ERP...")
            print(" (Deja esta ventana abierta. El robot leerá el ID solo)")
            print("="*60 + "\n")
            # 'ruta' aquí SÍ existe (la del cargar_datos)
            vigilar_id_pedido(driver, ruta)
        except Exception as e:
            print(f"[aviso] No se pudo vigilar el ID de pedido: {e}")
    # ---------------------------------------------------

    # CORREGIDO: antes se hacía driver.quit() aquí, cerrando Chrome y perdiendo el pedido sin guardar.
    if not args.auto:
        input("Pulsa Enter para cerrar esta consola (Chrome seguirá abierto)...")
    else:
        print("Modo --auto: Chrome queda abierto. Vigilante finalizado.")


if __name__ == "__main__":
    main()