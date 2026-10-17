"""
Vigilante del ID PEDIDO para el robot del ERP.

Cuando el robot termina de cargar las líneas, Chrome se queda abierto y una
persona revisa y pulsa GUARDAR. En ese momento el ERP enseña arriba el
"ID PEDIDO: XXXXXX". Este vigilante, que sigue conectado a ese mismo Chrome,
mira la pantalla cada pocos segundos y, en cuanto aparece el número, lo
anota en el registro de pedidos (data/registro_pedidos.db) y termina.

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

# Campo del ERP (PeopleSoft) donde se muestra el ID de la solicitud/pedido.
# Antes de guardar suele poner "NEXT"; después, el número.
CAMPOS_ID_ERP = ("REQ_HDR_REQ_ID",)

# Texto que se ve en pantalla: "ID PEDIDO: 123456" (o "ID de solicitud ...").
PATRON_ID = re.compile(
    r"(?is)\bID\s*(?:DE\s*)?(?:PEDIDO|SOLICITUD)\s*[:：]?\s*(\d{5,10})\b"
)

_JS_LEER = """
var r = {campos: {}, texto: ''};
var ids = arguments[0];
for (var i = 0; i < ids.length; i++) {
  var el = document.getElementById(ids[i]);
  if (el) {
    var v = (el.value !== undefined && el.value !== '') ? el.value : el.textContent;
    r.campos[ids[i]] = (v || '').trim();
  }
}
r.texto = (document.body ? (document.body.innerText || '') : '').slice(0, 30000);
return r;
"""


def order_id_desde_datos(ruta_datos) -> Optional[str]:
    """Identificador FLOW del pedido: del contenido del JSON o del nombre del archivo."""
    ruta = Path(str(ruta_datos))
    try:
        obj = json.loads(ruta.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            bloque = obj.get("pedido")
            if isinstance(bloque, dict) and bloque.get("orderId"):
                return str(bloque["orderId"])
            if obj.get("orderId"):
                return str(obj["orderId"])
    except Exception:
        pass
    m = re.match(r"pedido_(.+)\.json$", ruta.name)
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
    """(id, de dónde salió) o None si todavía no hay un número."""
    for lectura in lecturas:
        for campo, valor in (lectura.get("campos") or {}).items():
            digitos = re.sub(r"\D", "", valor or "")
            if re.fullmatch(r"\d{5,10}", digitos or ""):
                return digitos.lstrip("0"), f"campo {campo}"
    for lectura in lecturas:
        m = PATRON_ID.search(lectura.get("texto") or "")
        if m:
            return m.group(1).lstrip("0"), "texto «ID PEDIDO» en pantalla"
    return None


def escribir_diagnostico(order_id: str, lecturas: List[Dict], motivo: str) -> Optional[Path]:
    """Guarda qué veía el vigilante, para poder ajustarlo si no encontró el número."""
    try:
        carpeta = _AQUI.parent / "data"
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / f"diagnostico_id_pedido_{order_id}.txt"
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
                 if isinstance(ts, (int, float)) and ts > 0 else "")
        total = bloque.get("totalLineas")
        if not isinstance(total, int):
            total = len(obj.get("lineas", [])) if isinstance(obj, dict) else 0
        return fecha, total
    except Exception:
        return "", 0


def _guardar_en_registro(order_id: str, id_pedido: str, log: Callable[[str], None],
                         ruta_datos=None) -> bool:
    try:
        from modulos.registro_pedidos import Registro
        registro = Registro()
        fecha, total = _datos_del_pedido(ruta_datos) if ruta_datos else ("", 0)
        registro.asegurar(order_id, fecha, total)
        registro.fijar(order_id, origen="robot", id_pedido=id_pedido)
        return True
    except Exception as e:  # sin registro, al menos queda en un archivo
        log(f"[aviso] No se pudo anotar en el registro: {e}")
        try:
            carpeta = _AQUI.parent / "data" / "ids_pendientes"
            carpeta.mkdir(parents=True, exist_ok=True)
            (carpeta / f"{order_id}.json").write_text(
                json.dumps({"order_id": order_id, "id_pedido": id_pedido,
                            "fecha": datetime.now().isoformat(timespec="seconds")}),
                encoding="utf-8",
            )
        except Exception:
            pass
        return False


def vigilar_id_pedido(
    driver,
    ruta_datos,
    minutos: float = 90,
    intervalo: float = 3.0,
    log: Callable[[str], None] = print,
    dormir: Callable[[float], None] = time.sleep,
    reloj: Callable[[], float] = time.monotonic,
) -> Optional[str]:
    """
    Espera a que aparezca el ID PEDIDO tras pulsar Guardar y lo anota.
    Devuelve el ID, o None si se cerró Chrome / se acabó el tiempo.
    """
    order_id = order_id_desde_datos(ruta_datos)
    if not order_id:
        log("[aviso] No sé qué pedido es (falta orderId en el archivo de datos): "
            "no se vigilará el ID pedido.")
        return None

    log("")
    log(f"Esperando a que guardes el pedido en el ERP ({order_id}).")
    log("El número de ID PEDIDO se anotará solo. No cierres esta ventana negra")
    log(f"hasta que lo veas (máximo {int(minutos)} minutos).")

    limite = reloj() + minutos * 60
    candidato: Optional[str] = None
    ultimas: List[Dict] = []

    while reloj() < limite:
        try:
            driver.title  # si Chrome se cerró, esto lanza una excepción
        except Exception:
            log("Chrome se ha cerrado: se deja de vigilar.")
            if ultimas:
                escribir_diagnostico(order_id, ultimas, "Chrome cerrado sin ID")
            return None

        lecturas = leer_pantalla(driver)
        if lecturas:
            ultimas = lecturas
        encontrado = extraer_id(lecturas)

        if encontrado:
            id_pedido, fuente = encontrado
            # Se exige verlo dos veces seguidas, para no anotar un valor a medias.
            if candidato == id_pedido:
                _guardar_en_registro(order_id, id_pedido, log, ruta_datos)
                log(f"ID PEDIDO detectado: {id_pedido}  ({fuente}).")
                log("Anotado en el registro de CineStock. Ya puedes cerrar esta ventana.")
                return id_pedido
            candidato = id_pedido
        else:
            candidato = None

        dormir(intervalo)

    ruta = escribir_diagnostico(order_id, ultimas, f"Sin ID tras {int(minutos)} minutos")
    log("No se detectó el ID PEDIDO. Puedes anotarlo a mano en CineStock "
        "(Registro de pedidos).")
    if ruta:
        log(f"Se guardó un informe de lo que veía el robot en: {ruta}")
    return None
