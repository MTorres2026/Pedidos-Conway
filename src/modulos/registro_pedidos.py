"""
Registro de pedidos: la "matrícula" de cada pedido (sin dependencias de Qt
ni de librerías externas, para que pueda usarlo también el robot del ERP).

Cada pedido que sale de FLOW tiene un identificador interno (FLOW-1792...)
y, con el tiempo, recibe tres números "oficiales":

    ID SOLICITUD  Aparece arriba a la izquierda en el ERP (donde antes ponía
                  NEXT, p. ej. 0000447789 -> 447789) cuando la persona pulsa
                  GUARDAR. Lo lee el robot solo (o se anota a mano).
    ID PEDIDO     Se genera después en PeopleSoft. Se anota a mano o
                  importando el correo (.eml).
    ID RECEPCION  Se crea en el ERP al recepcionar el pedido en el cine.
                  Se anota a mano, junto con la factura y la fecha/hora.

Todo se guarda en  data/registro_pedidos.db  (SQLite) y, además, los tres
números se copian dentro del archivo del pedido en data/historial_pedidos/
(claves idSolicitud, idPedidoErp, idRecepcion dentro de "pedido").
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ----------------------------------------------------------------------
# Errores
# ----------------------------------------------------------------------

class ErrorRegistro(Exception):
    """Error con un mensaje pensado para enseñarse al usuario."""


class IdNoValido(ErrorRegistro):
    pass


class IdDuplicado(ErrorRegistro):
    pass


# ----------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------

CAMPOS_ID = ("id_solicitud", "id_pedido", "id_recepcion")
ETIQUETAS_ID = {
    "id_solicitud": "ID solicitud",
    "id_pedido": "ID pedido",
    "id_recepcion": "ID recepción",
}
# Añadimos factura_archivo aquí para que la base de datos sepa que existe
CAMPOS_TEXTO = ("factura_numero", "notas", "factura_archivo")
CAMPOS_FECHA = ("factura_fecha", "recepcion_fecha")
CAMPOS_HORA = ("recepcion_hora",)

CLAVES_HISTORIAL = {
    "id_solicitud": "idSolicitud",
    "id_pedido": "idPedidoErp",
    "id_recepcion": "idRecepcion",
}

ORIGENES = ("robot", "manual", "correo")

MIN_DIGITOS_ID = 4
MAX_DIGITOS_ID = 12


# ----------------------------------------------------------------------
# Utilidades de validación
# ----------------------------------------------------------------------

def normalizar_id(valor) -> str:
    """
    Deja solo los dígitos y quita los ceros de la izquierda:
    "0000407128" -> "407128". Devuelve "" si no hay dígitos.
    """
    digitos = re.sub(r"\D", "", str(valor if valor is not None else ""))
    return digitos.lstrip("0")


def validar_id(campo: str, valor) -> str:
    """Devuelve el ID normalizado, "" si está vacío, o lanza IdNoValido."""
    texto = str(valor if valor is not None else "").strip()
    if not texto:
        return ""
    norm = normalizar_id(texto)
    etiqueta = ETIQUETAS_ID.get(campo, campo)
    if not norm:
        raise IdNoValido(f"{etiqueta}: «{texto}» no contiene ningún número.")
    if not (MIN_DIGITOS_ID <= len(norm) <= MAX_DIGITOS_ID):
        raise IdNoValido(
            f"{etiqueta}: «{texto}» no parece un ID válido "
            f"(debe tener entre {MIN_DIGITOS_ID} y {MAX_DIGITOS_ID} cifras)."
        )
    return norm


def validar_fecha(campo: str, valor) -> str:
    """Acepta dd/mm/aaaa o aaaa-mm-dd. Devuelve ISO (aaaa-mm-dd) o ""."""
    texto = str(valor if valor is not None else "").strip()
    if not texto:
        return ""
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            continue
    raise ErrorRegistro(f"{campo}: «{texto}» no es una fecha válida (usa dd/mm/aaaa).")


def validar_hora(campo: str, valor) -> str:
    texto = str(valor if valor is not None else "").strip()
    if not texto:
        return ""
    m = re.fullmatch(r"(\d{1,2})[:.hH](\d{2})", texto)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    raise ErrorRegistro(f"{campo}: «{texto}» no es una hora válida (usa hh:mm).")


def fecha_para_mostrar(iso: str) -> str:
    """'2026-10-17' -> '17/10/2026'; '2026-10-17T21:30:00' -> '17/10/2026 21:30'."""
    if not iso:
        return ""
    try:
        if "T" in iso:
            return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


# ----------------------------------------------------------------------
# Resultado del análisis de un correo
# ----------------------------------------------------------------------

@dataclass
class CorreoAnalizado:
    asunto: str = ""
    fecha: str = ""
    candidatos: List[Tuple[str, str]] = field(default_factory=list)  # (id, motivo)
    pedido_sugerido: Optional[str] = None    # order_id
    motivo_sugerencia: str = ""


# ----------------------------------------------------------------------
# Registro
# ----------------------------------------------------------------------

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS pedidos (
    order_id          TEXT PRIMARY KEY,
    fecha_envio       TEXT NOT NULL DEFAULT '',
    total_lineas      INTEGER NOT NULL DEFAULT 0,
    id_pedido         TEXT NOT NULL DEFAULT '',
    id_pedido_origen  TEXT NOT NULL DEFAULT '',
    id_pedido_fecha   TEXT NOT NULL DEFAULT '',
    id_solicitud         TEXT NOT NULL DEFAULT '',
    id_solicitud_origen  TEXT NOT NULL DEFAULT '',
    id_solicitud_fecha   TEXT NOT NULL DEFAULT '',
    id_recepcion         TEXT NOT NULL DEFAULT '',
    id_recepcion_origen  TEXT NOT NULL DEFAULT '',
    id_recepcion_fecha   TEXT NOT NULL DEFAULT '',
    factura_numero    TEXT NOT NULL DEFAULT '',
    factura_fecha     TEXT NOT NULL DEFAULT '',
    factura_archivo   TEXT NOT NULL DEFAULT '',
    recepcion_fecha   TEXT NOT NULL DEFAULT '',
    recepcion_hora    TEXT NOT NULL DEFAULT '',
    notas             TEXT NOT NULL DEFAULT '',
    creado            TEXT NOT NULL,
    actualizado       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eventos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id  TEXT NOT NULL,
    fecha     TEXT NOT NULL,
    tipo      TEXT NOT NULL,
    detalle   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_eventos_pedido ON eventos(order_id);
"""


def _raiz_proyecto() -> Path:
    # src/modulos/registro_pedidos.py -> raíz del proyecto = parents[2]
    return Path(__file__).resolve().parents[2]


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Registro:

    def __init__(
        self,
        ruta_db: Optional[Path] = None,
        historial_dir: Optional[Path] = None,
    ):
        raiz = _raiz_proyecto()
        self.ruta_db = Path(ruta_db) if ruta_db else raiz / "data" / "registro_pedidos.db"
        self.historial_dir = (
            Path(historial_dir) if historial_dir else raiz / "data" / "historial_pedidos"
        )
        self.ruta_db.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._abrir()) as con:
            con.executescript(_ESQUEMA)
            
            # --- PARCHE PARA BASES DE DATOS ANTIGUAS ---
            columnas = {fila[1] for fila in con.execute("PRAGMA table_info(pedidos)")}
            if "factura_archivo" not in columnas:
                con.execute("ALTER TABLE pedidos ADD COLUMN factura_archivo TEXT NOT NULL DEFAULT ''")
            # ------------------------------------------
            
            con.commit()

    # Una conexión por operación: así pueden usarlo a la vez el robot y la
    # pantalla (procesos distintos) sin bloquearse.
    def _abrir(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.ruta_db), timeout=15)
        con.row_factory = sqlite3.Row
        return con

    # ---------------- altas ----------------

    def asegurar(self, order_id, fecha_envio: str = "", total_lineas: int = 0) -> Dict:
        order_id = str(order_id).strip()
        if not order_id:
            raise ErrorRegistro("Falta el identificador del pedido.")
        with closing(self._abrir()) as con:
            fila = con.execute(
                "SELECT * FROM pedidos WHERE order_id=?", (order_id,)
            ).fetchone()
            if fila is None:
                ahora = _ahora()
                con.execute(
                    "INSERT INTO pedidos (order_id, fecha_envio, total_lineas,"
                    " creado, actualizado) VALUES (?,?,?,?,?)",
                    (order_id, fecha_envio or ahora, int(total_lineas or 0), ahora, ahora),
                )
                con.execute(
                    "INSERT INTO eventos (order_id, fecha, tipo, detalle) VALUES (?,?,?,?)",
                    (order_id, ahora, "alta", f"{total_lineas or 0} líneas"),
                )
                con.commit()
                fila = con.execute(
                    "SELECT * FROM pedidos WHERE order_id=?", (order_id,)
                ).fetchone()
            elif int(total_lineas or 0) > 0 and fila["total_lineas"] == 0:
                # Registro creado "a medias" (p. ej. por el robot): se completa.
                con.execute(
                    "UPDATE pedidos SET total_lineas=?, fecha_envio=? WHERE order_id=?",
                    (int(total_lineas), fecha_envio or fila["fecha_envio"], order_id),
                )
                con.commit()
                fila = con.execute(
                    "SELECT * FROM pedidos WHERE order_id=?", (order_id,)
                ).fetchone()
            return dict(fila)

    def fijar(self, order_id, origen: str = "manual", volcar: bool = True, **campos) -> Dict:
        """
        Anota IDs / factura / fechas de un pedido. Ejemplos:
            fijar("FLOW-1792...", origen="robot", id_pedido="407128")
            fijar("FLOW-1792...", id_recepcion="55012", factura_numero="F-123",
                  factura_fecha="12/10/2026")
        Un valor vacío ("") borra el campo.
        """
        if origen not in ORIGENES:
            raise ErrorRegistro(f"Origen no válido: {origen}")
        order_id = str(order_id).strip()
        conocidos = set(CAMPOS_ID) | set(CAMPOS_TEXTO) | set(CAMPOS_FECHA) | set(CAMPOS_HORA)
        desconocidos = set(campos) - conocidos
        if desconocidos:
            raise ErrorRegistro(f"Campos desconocidos: {', '.join(sorted(desconocidos))}")

        # Validar todo ANTES de escribir nada.
        limpios: Dict[str, str] = {}
        for campo, valor in campos.items():
            if campo in CAMPOS_ID:
                limpios[campo] = validar_id(campo, valor)
            elif campo in CAMPOS_FECHA:
                limpios[campo] = validar_fecha(campo, valor)
            elif campo in CAMPOS_HORA:
                limpios[campo] = validar_hora(campo, valor)
            else:
                limpios[campo] = str(valor if valor is not None else "").strip()

        with closing(self._abrir()) as con:
            actual = con.execute(
                "SELECT * FROM pedidos WHERE order_id=?", (order_id,)
            ).fetchone()
            if actual is None:
                raise ErrorRegistro(f"No existe el pedido {order_id} en el registro.")

            ahora = _ahora()
            cambios: Dict[str, str] = {}
            eventos: List[Tuple[str, str]] = []

            for campo, nuevo in limpios.items():
                anterior = actual[campo]
                if nuevo == anterior:
                    continue

                if campo in CAMPOS_ID and nuevo:
                    otro = con.execute(
                        f"SELECT order_id FROM pedidos WHERE {campo}=? AND order_id<>?",
                        (nuevo, order_id),
                    ).fetchone()
                    if otro is not None:
                        raise IdDuplicado(
                            f"{ETIQUETAS_ID[campo]} {nuevo} ya está asignado al "
                            f"pedido {otro['order_id']}."
                        )

                # El robot nunca pisa algo que una persona ya escribió a mano.
                if (
                    origen == "robot" and campo in CAMPOS_ID and anterior
                    and actual[campo + "_origen"] in ("manual", "correo")
                ):
                    eventos.append((
                        "robot_conflicto",
                        f"{campo}: el robot leyó {nuevo} pero ya había {anterior} "
                        f"({actual[campo + '_origen']}); se mantiene el existente.",
                    ))
                    continue

                cambios[campo] = nuevo
                if campo in CAMPOS_ID:
                    cambios[campo + "_origen"] = origen if nuevo else ""
                    cambios[campo + "_fecha"] = ahora if nuevo else ""
                eventos.append((campo, f"{anterior or '-'} -> {nuevo or '-'} ({origen})"))

            if cambios:
                cambios["actualizado"] = ahora
                con.execute(
                    "UPDATE pedidos SET "
                    + ", ".join(f"{k}=?" for k in cambios)
                    + " WHERE order_id=?",
                    list(cambios.values()) + [order_id],
                )
            for tipo, detalle in eventos:
                con.execute(
                    "INSERT INTO eventos (order_id, fecha, tipo, detalle) VALUES (?,?,?,?)",
                    (order_id, ahora, tipo, detalle),
                )
            con.commit()
            resultado = dict(con.execute(
                "SELECT * FROM pedidos WHERE order_id=?", (order_id,)
            ).fetchone())

        if volcar and any(c in CAMPOS_ID for c in cambios):
            self.volcar_en_historial(order_id)
        return resultado

    def eliminar(self, order_id) -> None:
        """Borra un pedido del registro y sus eventos asociados."""
        order_id = str(order_id).strip()
        with closing(self._abrir()) as con:
            con.execute("DELETE FROM eventos WHERE order_id=?", (order_id,))
            con.execute("DELETE FROM pedidos WHERE order_id=?", (order_id,))
            con.commit()

    # ---------------- consultas ----------------

    def obtener(self, order_id) -> Optional[Dict]:
        with closing(self._abrir()) as con:
            fila = con.execute(
                "SELECT * FROM pedidos WHERE order_id=?", (str(order_id),)
            ).fetchone()
            return dict(fila) if fila else None

    def listar(self, limite: int = 500) -> List[Dict]:
        with closing(self._abrir()) as con:
            return [dict(f) for f in con.execute(
                "SELECT * FROM pedidos ORDER BY fecha_envio DESC, rowid DESC LIMIT ?",
                (limite,),
            )]

    def buscar(self, texto: str, limite: int = 200) -> List[Dict]:
        """Busca por cualquier ID (con o sin ceros), nº de factura, notas o id interno."""
        texto = (texto or "").strip()
        if not texto:
            return self.listar(limite)
        norm = normalizar_id(texto)
        with closing(self._abrir()) as con:
            patron = f"%{texto}%"
            consulta = (
                "SELECT * FROM pedidos WHERE order_id LIKE ? OR factura_numero LIKE ?"
                " OR notas LIKE ?"
            )
            params: List = [patron, patron, patron]
            if norm:
                for campo in CAMPOS_ID:
                    consulta += f" OR {campo} LIKE ?"
                    params.append(f"{norm}%")
            consulta += " ORDER BY fecha_envio DESC, rowid DESC LIMIT ?"
            params.append(limite)
            return [dict(f) for f in con.execute(consulta, params)]

    def eventos(self, order_id) -> List[Dict]:
        with closing(self._abrir()) as con:
            return [dict(f) for f in con.execute(
                "SELECT fecha, tipo, detalle FROM eventos WHERE order_id=? ORDER BY id",
                (str(order_id),),
            )]

    @staticmethod
    def estado_de(rec: Dict) -> str:
        if not rec.get("id_solicitud") and not rec.get("id_pedido"):
            return "Pendiente de guardar en el ERP"
        if not rec.get("id_solicitud"):
            return "Falta ID solicitud"
        if not rec.get("id_pedido"):
            return "Guardado en ERP · falta ID pedido"
        if not rec.get("id_recepcion"):
            return "Pedido emitido · pendiente de recepción"
        if not rec.get("factura_numero"):
            return "Recepcionado · falta factura"
        return "Completo"

    @staticmethod
    def nombre_visible(rec: Dict) -> str:
        """El nombre que se enseña al usuario: ID pedido o ID solicitud si ya existen."""
        if rec.get("id_pedido"):
            return f"Pedido {rec['id_pedido']}"
        if rec.get("id_solicitud"):
            return f"Solicitud {rec['id_solicitud']}"
        return rec.get("order_id", "")

    # ---------------- sincronizar con el historial ----------------

    def sincronizar_historial(self, historial_dir: Optional[Path] = None) -> int:
        """
        Da de alta en el registro los pedidos que ya están en
        data/historial_pedidos/ y todavía no constan. Devuelve cuántos añadió.
        """
        carpeta = Path(historial_dir) if historial_dir else self.historial_dir
        nuevos = 0
        if carpeta.is_dir():
            with closing(self._abrir()) as con:
                conocidos = {
                    f["order_id"] for f in con.execute(
                        "SELECT order_id FROM pedidos WHERE total_lineas > 0")
                }
            for archivo in sorted(carpeta.glob("*.json")):
                m = re.search(r"_pedido_(.+)\.json$", archivo.name)
                if not m or m.group(1) in conocidos:
                    continue
                try:
                    datos = json.loads(archivo.read_text(encoding="utf-8"))
                    bloque = datos.get("pedido", datos) if isinstance(datos, dict) else {}
                    order_id = str(bloque.get("orderId") or m.group(1))
                    if order_id in conocidos:
                        continue
                    fecha = ""
                    ts = bloque.get("timestamp")
                    if isinstance(ts, (int, float)) and ts > 0:
                        fecha = datetime.fromtimestamp(ts / 1000).isoformat(timespec="seconds")
                    if not fecha:
                        fecha = datetime.fromtimestamp(archivo.stat().st_mtime).isoformat(
                            timespec="seconds")
                    total = bloque.get("totalLineas")
                    if not isinstance(total, int):
                        total = len(datos.get("lineas", [])) if isinstance(datos, dict) else 0
                    self.asegurar(order_id, fecha, total)
                    conocidos.add(order_id)
                    nuevos += 1
                except Exception:
                    continue  # archivo raro: se ignora

        # Recupera también IDs de solicitud que el robot hubiera dejado en espera
        pendientes_dir = self.ruta_db.parent / "ids_pendientes"
        if pendientes_dir.is_dir():
            for pend in sorted(pendientes_dir.glob("*.json")):
                try:
                    info = json.loads(pend.read_text(encoding="utf-8"))
                    oid = str(info.get("order_id") or pend.stem).strip()
                    id_sol = str(info.get("id_solicitud") or "").strip()
                    if oid and id_sol:
                        self.asegurar(oid)
                        self.fijar(oid, origen="robot", id_solicitud=id_sol)
                        pend.unlink(missing_ok=True)
                except Exception:
                    continue
        return nuevos

    def volcar_en_historial(self, order_id) -> int:
        """
        Copia los 3 IDs dentro de los archivos del pedido en el historial
        (pedido.idPedidoErp, pedido.idSolicitud, pedido.idRecepcion).
        Devuelve cuántos archivos tocó. Nunca lanza errores: es un extra.
        """
        rec = self.obtener(order_id)
        if rec is None or not self.historial_dir.is_dir():
            return 0
        tocados = 0
        for archivo in self.historial_dir.glob("*_pedido_*.json"):
            if not archivo.name.endswith(f"_pedido_{order_id}.json"):
                continue
            try:
                datos = json.loads(archivo.read_text(encoding="utf-8"))
                if not isinstance(datos, dict):
                    continue
                bloque = datos.setdefault("pedido", {})
                if not isinstance(bloque, dict):
                    continue
                for campo, clave in CLAVES_HISTORIAL.items():
                    if rec.get(campo):
                        bloque[clave] = rec[campo]
                    else:
                        bloque.pop(clave, None)
                destino = archivo.with_suffix(".json.tmp")
                destino.write_text(
                    json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(destino, archivo)
                tocados += 1
            except Exception:
                continue
        return tocados

    # ---------------- correo de solicitud ----------------

    _PATRONES_CORREO = (
        (re.compile(r"po[_ \-]?id\W{0,8}(\d{6,10})"), "PO_ID"),
        (re.compile(r"id\s*(?:de\s*)?solicitud\W{0,12}(\d{5,10})"), "ID de solicitud"),
        (re.compile(r"solicitud\s*(?:numero|num\.?|n[ºo°.]*)?\W{0,10}(\d{5,10})"), "solicitud"),
        (re.compile(r"orden\s+de\s+compra\W{0,15}(\d{5,10})"), "orden de compra"),
        (re.compile(r"pedido\s*(?:numero|num\.?|n[ºo°.]*)?\W{0,10}(\d{5,10})"), "pedido"),
        (re.compile(r"\b[a-z]{2}\d{3}_(\d{6,10})_"), "nombre del PDF adjunto"),
    )

    @staticmethod
    def _texto_de_correo(ruta: Path) -> Tuple[str, str, str]:
        """(asunto, fecha_iso, texto con adjuntos). Usa la biblioteca si está."""
        try:
            try:
                from .biblioteca import _leer_correo  # type: ignore
            except ImportError:
                from modulos.biblioteca import _leer_correo  # type: ignore
            c = _leer_correo(ruta)
            return c.asunto, c.fecha, c.texto
        except Exception:
            pass
        from email import policy
        from email.parser import BytesParser
        with open(ruta, "rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)
        cuerpo = ""
        parte = msg.get_body(preferencelist=("plain", "html"))
        if parte is not None:
            try:
                cuerpo = parte.get_content()
            except Exception:
                cuerpo = ""
        nombres = " ".join(
            a.get_filename() or "" for a in msg.iter_attachments()
        )
        return str(msg.get("subject", "") or ""), "", f"{cuerpo}\n{nombres}"

    def analizar_correo(self, ruta) -> CorreoAnalizado:
        """
        Lee un .eml y propone qué números parecen ser el ID de pedido, y a
        qué solicitud/pedido corresponde. Siempre lo confirma la persona.
        """
        ruta = Path(ruta)
        asunto, fecha, texto = self._texto_de_correo(ruta)
        # nombres de archivo de los adjuntos, por si el número solo está ahí
        try:
            from email import policy
            from email.parser import BytesParser
            with open(ruta, "rb") as f:
                msg = BytesParser(policy=policy.default).parse(f)
            texto += "\n" + " ".join(a.get_filename() or "" for a in msg.iter_attachments())
        except Exception:
            pass

        t = re.sub(r"\s+", " ", _sin_acentos(f"{asunto} {texto}").lower())
        vistos: Dict[str, str] = {}
        for patron, motivo in self._PATRONES_CORREO:
            for m in patron.finditer(t):
                norm = normalizar_id(m.group(1))
                if norm and norm not in vistos and \
                        MIN_DIGITOS_ID <= len(norm) <= MAX_DIGITOS_ID:
                    vistos[norm] = motivo
        analizado = CorreoAnalizado(
            asunto=asunto, fecha=fecha, candidatos=list(vistos.items())
        )

        # ¿Algún número del correo es el ID solicitud de un pedido conocido?
        pedidos = self.listar(1000)
        por_id_solicitud = {p["id_solicitud"]: p for p in pedidos if p["id_solicitud"]}
        numeros = {normalizar_id(n) for n in re.findall(r"\d{5,10}", t)}
        for numero in numeros:
            if numero in por_id_solicitud:
                analizado.pedido_sugerido = por_id_solicitud[numero]["order_id"]
                analizado.motivo_sugerencia = (
                    f"el correo menciona el ID solicitud {numero}"
                )
                # ese número es el ID solicitud, no puede ser a la vez el ID pedido
                analizado.candidatos = [
                    (i, m) for i, m in analizado.candidatos if i != numero
                ]
                break
        if analizado.pedido_sugerido is None:
            pendientes = [
                p for p in pedidos if p["id_solicitud"] and not p["id_pedido"]
            ]
            if pendientes:
                # la solicitud que se guardó en el ERP más recientemente
                pendientes.sort(key=lambda p: p["id_solicitud_fecha"], reverse=True)
                analizado.pedido_sugerido = pendientes[0]["order_id"]
                analizado.motivo_sugerencia = (
                    "es la última solicitud guardada en el ERP que aún no tiene ID pedido"
                )
        return analizado