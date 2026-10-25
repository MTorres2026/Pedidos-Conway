"""
Vigilante del ID de Solicitud para el robot del ERP (PeopleSoft).

Cuando el robot termina de cargar las líneas en Mantenimiento de Solicitudes,
Chrome se queda abierto y una persona revisa y pulsa GUARDAR.
En ese momento, arriba a la izquierda (debajo de «Unidad Negocio: SP001» y
encima de «Nombre Solicitud»), el campo «ID Solicitud:» pasa de «NEXT» al
número real con ceros a la izquierda (p. ej. «0000447789» -> «447789»).

Este vigilante, que sigue conectado a ese mismo Chrome, mira la pantalla cada
pocos segundos y, en cuanto aparece el número, lo anota automáticamente como
`id_solicitud` en el registro de pedidos de CineStock (data/registro_pedidos.db
y data/historial_pedidos/) y termina.

El vigilante SOLO LEE la pantalla: no hace clic ni escribe nada, y nunca
pulsa Guardar.

Uso (desde robot_erp.py, al final de main):

    from robot_id_pedido import vigilar_id_pedido
    vigilar_id_pedido(driver, ruta)        # ruta = archivo de datos del pedido
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# El registro vive en src/modulos/ (junto a este archivo, en src/).
_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.insert(0, str(_AQUI))

# Campos del encabezado de Solicitud en PeopleSoft donde se muestra el ID.
# Antes de pulsar Guardar suele poner "NEXT"; después, p. ej. "0000447789".
CAMPOS_ID_ERP = (
    "REQ_HDR_REQ_ID",
    "REQ_HDR_REQ_ID$0",
    "PV_REQ_HDR_REQ_ID",
    "REQ_HDR_WRK_REQ_ID",
    "EP_REQ_HDR_REQ_ID",
)

# En PeopleSoft (Mantenimiento de Solicitudes), arriba a la izquierda aparece:
#   ID Solicitud:   0000447789
PATRON_ID_SOLICITUD = re.compile(
    r"(?is)\bID\s*(?:DE\s*)?SOLICITUD\s*[:：]?\s*(\d{4,12})(?![/\-\d])\b"
)
# Respaldo por si en alguna vista la etiqueta dijera "ID Pedido:"
PATRON_ID = re.compile(
    r"(?is)\bID\s*(?:DE\s*)?(?:SOLICITUD|PEDIDO)\s*[:：]?\s*(\d{4,12})(?![/\-\d])\b"
)

_MIN_CIFRAS = 4
_MAX_CIFRAS = 12

_JS_LEER = """
var r = {campos: {}, texto: ''};
var ids = arguments[0];
function leerEl(el) {
  if (!el) return '';
  var v = (el.value !== undefined && el.value !== '') ? el.value : el.textContent;
  return (v || '').trim();
}
for (var i = 0; i < ids.length; i++) {
  var el = document.getElementById(ids[i]);
  if (el) {
    r.campos[ids[i]] = leerEl(el);
  }
}
// Cualquier otro span/input de cabecera tipo *REQ_HDR*REQ_ID*
var candidatos = document.querySelectorAll('[id*="REQ_ID"]');
for (var j = 0; j < candidatos.length; j++) {
  var cid = candidatos[j].id || '';
  if (/^(?:PV_|EP_|CPLPO_)?REQ_HDR(?:_WRK)?_REQ_ID(?:\\$\\d+)?$/i.test(cid) && !(cid in r.campos)) {
    r.campos[cid] = leerEl(candidatos[j]);
  }
}
// Búsqueda por la etiqueta visible exacta «ID Solicitud:» (celda contigua en PeopleSoft)
var etiquetas = document.querySelectorAll('span, label, td, th, div');
for (var k = 0; k < etiquetas.length; k++) {
  var nodo = etiquetas[k];
  if (nodo.children && nodo.children.length > 2) continue;
  var txt = (nodo.textContent || '').replace(/\\s+/g, ' ').trim();
  if (/^id\\s*(?:de\\s*)?solicitud\\s*[:：]?$/i.test(txt)) {
    var val = '';
    if (nodo.htmlFor) {
      val = leerEl(document.getElementById(nodo.htmlFor));
    }
    if (!val && nodo.nextElementSibling) {
      val = leerEl(nodo.nextElementSibling);
    }
    if (!val && nodo.closest) {
      var celda = nodo.closest('td, th');
      if (celda && celda.nextElementSibling) {
        val = leerEl(celda.nextElementSibling);
      }
    }
    if (val) {
      r.campos['etiqueta_id_solicitud'] = val;
      break;
    }
  }
}
r.texto = (document.body ? (document.body.innerText || '') : '').slice(0, 30000);
return r;
"""


def _validar_numero_solicitud(bruto: str) -> Optional[str]:
    """
    Si `bruto` es un ID de solicitud válido (p. ej. '0000447789' o '447789'),
    devuelve el número sin ceros a la izquierda ('447789').
    Rechaza 'NEXT', fechas ('20/10/2026') o códigos como '0000000001' ('1').
    """
    limpio = (bruto or "").strip()
    if not limpio or limpio.upper() == "NEXT":
        return None
    if not re.fullmatch(r"\d{4,12}", limpio):
        return None
    sin_ceros = limpio.lstrip("0")
    if _MIN_CIFRAS <= len(sin_ceros) <= _MAX_CIFRAS:
        return sin_ceros
    return None


def order_id_desde_datos(ruta_datos) -> Optional[str]:
    """Identificador FLOW del pedido: del contenido del JSON o del nombre del archivo."""
    ruta = Path(str(ruta_datos))
    try:
        obj = json.loads(ruta.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            bloque = obj.get("pedido")
            if isinstance(bloque, dict) and bloque.get("orderId") not in (None, ""):
                return str(bloque["orderId"])
            if obj.get("orderId") not in (None, ""):
                return str(obj["orderId"])
    except Exception:
        pass
    m = re.match(r"(?:\d{8}_\d{6}_)?pedido_(.+)\.json$", ruta.name)
    return m.group(1) if m else None


def _leer_frames(driver, profundidad: int = 0, maxima: int = 3) -> List[Dict]:
    """Lee la página y, recursivamente, sus marcos (el ERP usa muchos iframes)."""
    lecturas: List[Dict] = []
    try:
        lecturas.append(driver.execute_script(_JS_LEER, list(CAMPOS_ID_ERP)) or {})
    except Exception:
        pass
    if profundidad < maxima:
        try:
            n = len(driver.find_elements("css selector", "iframe, frame"))
        except Exception:
            n = 0
        for i in range(n):
            try:
                driver.switch_to.frame(i)
            except Exception:
                continue
            try:
                lecturas.extend(_leer_frames(driver, profundidad + 1, maxima))
            finally:
                try:
                    driver.switch_to.parent_frame()
                except Exception:
                    pass
    return lecturas


def leer_pantalla(driver) -> List[Dict]:
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        return _leer_frames(driver)
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass


def extraer_id(lecturas: List[Dict]) -> Optional[Tuple[str, str]]:
    """(id_solicitud, de dónde salió) o None si todavía pone NEXT / no hay número."""
    for lectura in lecturas:
        for campo, valor in (lectura.get("campos") or {}).items():
            norm = _validar_numero_solicitud(valor)
            if norm:
                return norm, f"campo {campo} ({valor.strip()})"
    for lectura in lecturas:
        texto = lectura.get("texto") or ""
        m = PATRON_ID_SOLICITUD.search(texto)
        if m:
            norm = _validar_numero_solicitud(m.group(1))
            if norm:
                return norm, f"texto «ID Solicitud: {m.group(1)}» en pantalla"
    for lectura in lecturas:
        texto = lectura.get("texto") or ""
        m = PATRON_ID.search(texto)
        if m:
            norm = _validar_numero_solicitud(m.group(1))
            if norm:
                return norm, f"texto en pantalla ({m.group(1)})"
    return None


def escribir_diagnostico(order_id: str, lecturas: List[Dict], motivo: str) -> Optional[Path]:
    """Guarda qué veía el vigilante, para poder ajustarlo si no encontró el número."""
    try:
        carpeta = _AQUI.parent / "data"
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / f"diagnostico_id_solicitud_{order_id}.txt"
        lineas = [f"{datetime.now().isoformat(timespec='seconds')}  {motivo}", ""]
        for n, lec in enumerate(lecturas):
            lineas.append(f"--- marco {n} ---")
            for campo, valor in (lec.get("campos") or {}).items():
                lineas.append(f"campo {campo} = {valor!r}")
            for linea in (lec.get("texto") or "").splitlines():
                if re.search(r"\bID\b|NEXT|SOLICITUD|PEDIDO", linea, re.IGNORECASE):
                    lineas.append(linea.strip()[:200])
        ruta.write_text("\n".join(lineas[:400]), encoding="utf-8")
        return ruta
    except Exception:
        return None


def _datos_del_pedido(ruta_datos) -> Tuple[str, int]:
    """(fecha de envío ISO, nº de líneas) leídos del archivo de datos, si se puede."""
    try:
        obj = json.loads(Path(str(ruta_datos)).read_text(encoding="utf-8"))
        bloque = obj.get("pedido", {}) if isinstance(obj, dict) else {}
        ts = bloque.get("timestamp")
        fecha = (datetime.fromtimestamp(ts / 1000).isoformat(timespec="seconds")
                 if isinstance(ts, (int, float)) and ts > 0
                 else str(bloque.get("fechaRecepcionISO") or ""))
        total = bloque.get("totalLineas")
        if not isinstance(total, int):
            total = len(obj.get("lineas", [])) if isinstance(obj, dict) else 0
        return fecha, total
    except Exception:
        return "", 0


def _guardar_en_registro(order_id: str, id_solicitud: str, log: Callable[[str], None],
                         ruta_datos=None) -> bool:
    try:
        from modulos.registro_pedidos import Registro
        registro = Registro()
        fecha, total = _datos_del_pedido(ruta_datos) if ruta_datos else ("", 0)
        registro.asegurar(order_id, fecha, total)
        registro.fijar(order_id, origen="robot", id_solicitud=id_solicitud)
        return True
    except Exception as e:  # sin registro, al menos queda en un archivo
        log(f"[aviso] No se pudo anotar en el registro: {e}")
        try:
            carpeta = _AQUI.parent / "data" / "ids_pendientes"
            carpeta.mkdir(parents=True, exist_ok=True)
            (carpeta / f"{order_id}.json").write_text(
                json.dumps({"order_id": order_id, "id_solicitud": id_solicitud,
                            "fecha": datetime.now().isoformat(timespec="seconds")}),
                encoding="utf-8",
            )
        except Exception:
            pass
        return False


def vigilar_id_solicitud(
    driver,
    ruta_datos,
    minutos: float = 90,
    intervalo: float = 2.0,
    log: Callable[[str], None] = print,
    dormir: Callable[[float], None] = time.sleep,
    reloj: Callable[[], float] = time.monotonic,
    al_detectar: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """
    Espera a que aparezca el ID Solicitud en PeopleSoft tras pulsar Guardar y lo
    anota automáticamente como `id_solicitud` en CineStock.
    Devuelve el ID normalizado (p. ej. '447789'), o None si se cerró Chrome / se agotó el tiempo.
    """
    order_id = order_id_desde_datos(ruta_datos)
    if not order_id:
        log("[aviso] No sé qué pedido es (falta orderId en el archivo de datos): "
            "no se vigilará el ID de Solicitud.")
        return None

    log("")
    log(f"Esperando a que pulses GUARDAR en el ERP para el pedido ({order_id}).")
    log("En cuanto 'ID Solicitud: NEXT' cambie al número, se anotará solo en CineStock.")
    log(f"No cierres esta ventana negra hasta entonces (máximo {int(minutos)} minutos).")

    limite = reloj() + minutos * 60
    candidato: Optional[str] = None
    ultimas: List[Dict] = []

    while reloj() < limite:
        try:
            driver.title  # si Chrome se cerró, esto lanza una excepción
        except Exception:
            log("Chrome se ha cerrado: se deja de vigilar.")
            if ultimas:
                escribir_diagnostico(order_id, ultimas, "Chrome cerrado sin ID de Solicitud")
            return None

        lecturas = leer_pantalla(driver)
        if lecturas:
            ultimas = lecturas
        encontrado = extraer_id(lecturas)

        if encontrado:
            id_solicitud, fuente = encontrado
            # Se exige verlo dos veces seguidas, para no anotar un valor a medias.
            if candidato == id_solicitud:
                _guardar_en_registro(order_id, id_solicitud, log, ruta_datos)
                log(f"✅ ID de Solicitud detectado: {id_solicitud}  ({fuente}).")
                log("Anotado automáticamente en CineStock (Registro de pedidos). Ya puedes cerrar.")
                if al_detectar is not None:
                    try:
                        al_detectar(id_solicitud)
                    except Exception:
                        pass
                return id_solicitud
            candidato = id_solicitud
        else:
            candidato = None

        dormir(intervalo)

    ruta = escribir_diagnostico(order_id, ultimas, f"Sin ID de Solicitud tras {int(minutos)} minutos")
    log("No se detectó el ID de Solicitud. Puedes anotarlo a mano en CineStock "
        "(Registro de pedidos).")
    if ruta:
        log(f"Se guardó un informe de lo que veía el robot en: {ruta}")
    return None


# Alias de compatibilidad para llamadas existentes (robot_erp.py, etc.)
vigilar_id_pedido = vigilar_id_solicitud
