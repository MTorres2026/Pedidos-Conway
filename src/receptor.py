#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Receptor CineStock -> Conway.
Versión corregida 2026-10-19 sobre la base del usuario (FIX 1-6 conservados).
Cambios de esta revisión marcados con  # [C1] ... [C9]  (ver changelog aparte).
"""
import io
import json
import sys
import re
import os
import glob
import time
import unicodedata
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from urllib.parse import unquote
VERSION = "2026-10-19"
# Que imprimir nunca rompa el receptor (consola de Windows o sin consola)
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
HOST = "0.0.0.0"
PORT = 8765
RECEPTOR_ACTIVO = False
# ============================================================
# RUTAS
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
PEDIDO_ACTUAL = BASE_DIR / "data" / "pedido_actual.json"
HISTORIAL_DIR = BASE_DIR / "data" / "historial_pedidos"
PDF_DIR = BASE_DIR / "data" / "pdf_pedidos"
SUPERMAESTRO = BASE_DIR / "data" / "supermaestro_flow.json"
# [C1] El orderId viene de la red y acaba dentro de nombres de fichero y de un
# patrón de glob. Se valida: sin puntos, sin barras, sin metacaracteres.
# Así se bloquean a la vez el path traversal ("../../x") y el glob inyectado
# (un orderId "*" haría que TODO el historial pareciese duplicado y se
# descartaran pedidos reales en silencio).
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
# [C6] Guardia de arranque contra carpetas sincronizadas.
MARCAS_SYNC = ("onedrive", "dropbox", "google drive", "googledrive", "icloud")
def ruta_sincronizada(p: Path) -> bool:
    partes = [unicodedata.normalize("NFC", q).lower() for q in p.resolve().parts]
    return any(any(m in q for m in MARCAS_SYNC) for q in partes)
# ============================================================
# ESCRITURA SEGURA  [C3][C5]
# ============================================================
def _replace_con_reintentos(tmp: Path, destino: Path, intentos: int = 5) -> None:
    """os.replace es atómico, pero en Windows FALLA con PermissionError si otro
    proceso (típicamente OneDrive sincronizando) tiene el destino abierto.
    Sin reintentos, ese choque rompería el guardado del pedido."""
    for i in range(intentos):
        try:
            os.replace(tmp, destino)
            return
        except PermissionError:
            if i == intentos - 1:
                raise
            time.sleep(0.05 * (i + 1))
def escribir_texto_atomico(destino: Path, texto: str) -> None:
    tmp = destino.with_name(destino.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
        f.flush()
        os.fsync(f.fileno())
    _replace_con_reintentos(tmp, destino)
def escribir_bytes_atomico(destino: Path, datos: bytes) -> None:
    tmp = destino.with_name(destino.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(datos)
        f.flush()
        os.fsync(f.fileno())
    _replace_con_reintentos(tmp, destino)
# ============================================================
# FUSIÓN DE GRANELES
# ============================================================
_FAMILIAS_GRANEL_ERP = {
    "GOMA": "1001050004",
    "CARAMELO": "1001050001",
    "CHOCOLATE": "1001050002",
}
def _linea_granel_a_pedido(g):
    desc = str(g.get("descripcionCompleta") or g.get("nombreCorto") or g.get("descripcion") or "").strip()
    um = str(g.get("um") or g.get("unidad") or "").strip().upper()
    cant = g.get("cantidadPedida", g.get("cantidad", 0))
    categoria = str(g.get("categoria", "")).strip().upper()
    familia_id = _FAMILIAS_GRANEL_ERP.get(categoria, "")
    if not familia_id:
        for cat_familia, fid in _FAMILIAS_GRANEL_ERP.items():
            if cat_familia in categoria:
                familia_id = fid
                break
    return {
        "descripcion": desc,
        "codigo": familia_id,
        "idArticulo": familia_id,
        "unidad": um,
        "cantidadPedida": cant,
        "cantidad": cant,
        "origen": "granel",
        "familiaGranel": categoria,
        "pendienteCodigo": not bool(familia_id),
        "fuente": "GRANEL",
    }
_UM_GRANEL = ("KG", "GR", "G", "LT", "L", "ML", "M", "MT", "M2", "M3", "PZ", "UN", "CA", "BL")
_PATRONES_LINEA_GRANEL = [
    re.compile(r"^\s*(\d{4,})\s+(.+?)\s+([\d]+(?:[.,]\d+)?)\s+([A-Za-z0-9²]{1,4})\s*$"),
    re.compile(r"^\s*(\d{4,})\s+(.+?)\s+([A-Za-z0-9²]{1,4})\s+([\d]+(?:[.,]\d+)?)\s*$"),
    re.compile(r"^\s*(\d{4,})\s+([\d]+(?:[.,]\d+)?)\s+([A-Za-z0-9²]{1,4})\s*$"),
]
def _texto_pdf(pdf_bytes):
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception:
        pass
    for modulo in ("pypdf", "PyPDF2"):
        try:
            mod = __import__(modulo)
            lector = mod.PdfReader(io.BytesIO(pdf_bytes))
            return "\n".join((page.extract_text() or "") for page in lector.pages)
        except Exception:
            continue
    return ""
def _parsear_lineas_granel_texto(texto):
    lineas = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        for patron in _PATRONES_LINEA_GRANEL:
            m = patron.match(linea)
            if not m:
                continue
            grupos = m.groups()
            codigo = grupos[0]
            if len(grupos) == 4:
                desc, cant, um = grupos[1], grupos[2], grupos[3]
                if patron is _PATRONES_LINEA_GRANEL[1]:
                    desc, cant, um = grupos[1], grupos[3], grupos[2]
            else:
                desc, cant, um = "", grupos[1], grupos[2]
            um_limpia = um.strip().upper()
            if um_limpia not in _UM_GRANEL:
                continue
            lineas.append({
                "codigo": codigo,
                "idArticulo": codigo,
                "descripcion": desc.strip(),
                "cantidadPedida": cant.replace(",", "."),
                "cantidad": cant.replace(",", "."),
                "unidad": um_limpia,
                "origen": "granel",
            })
            break
    return lineas
def extraer_lineas_granel(pdf_bytes):
    if not pdf_bytes:
        return [], None
    texto = _texto_pdf(pdf_bytes)
    if not texto.strip():
        return [], ("El PDF de granel no contiene texto extraíble.")
    lineas = _parsear_lineas_granel_texto(texto)
    if not lineas:
        return [], ("El PDF de granel contiene texto pero no se reconoció ninguna línea.")
    return lineas, None
def _cant_norm(v) -> str:
    """[C8] Compara cantidades como número, no como texto: '3', '3.0' y '3,0'
    son la misma cantidad y antes no se reconocían como duplicadas."""
    try:
        return f"{float(str(v).replace(',', '.')):.3f}"
    except Exception:
        return str(v)
# ============================================================
# SERVIDOR
# ============================================================
class CineStockRequestHandler(BaseHTTPRequestHandler):
    # FIX 1 (conservado): una conexión lenta o muerta no bloquea el servidor.
    timeout = 30
    def enviar_json(self, codigo_http, respuesta):
        cuerpo = json.dumps(respuesta, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo_http)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(cuerpo)
        self.wfile.flush()
        self.close_connection = True
    # ------------------------------------------------------------
    # [C4] NUEVO ORDEN DE ESCRITURA: PDFs -> historial -> pedido_actual.
    # Antes pedido_actual.json se escribía PRIMERO, con dos consecuencias:
    #   - la pantalla/robot podía ver un pedido cuyos PDFs aún no existían
    #     (rompe el invariante de tu doc maestro 6.1);
    #   - el punto de commit no era el historial, que es justo lo que
    #     consulta el check de duplicados y lo que lee el robot.
    # Ahora el historial es el commit: si el proceso muere antes de él,
    # el reintento vuelve a hacer todo y no queda estado a medias visible.
    # ------------------------------------------------------------
    def guardar_pedido(self, data, order_id, fecha_archivo):
        PEDIDO_ACTUAL.parent.mkdir(parents=True, exist_ok=True)
        HISTORIAL_DIR.mkdir(parents=True, exist_ok=True)
        datos_pedido = data.get("pedido", {})
        datos_pedido["fechaRecepcion"] = datetime.now().strftime("%d/%m/%Y")
        datos_pedido["fechaRecepcionISO"] = datetime.now().isoformat(timespec="seconds")
        data["pedido"] = datos_pedido
        # --- 1) HISTORIAL (punto de commit), escritura atómica ---
        nombre_historial = f"{fecha_archivo.strftime('%Y%m%d_%H%M%S')}_pedido_{order_id}.json"
        archivo_historial = HISTORIAL_DIR / nombre_historial
        if archivo_historial.exists():
            # Solo reachable si el check de duplicados y esto compiten; con el
            # servidor monohilo no debería pasar. Se conserva el sufijo de
            # microsegundos en vez de sobrescribir, para no perder nada.
            nombre_historial = f"{fecha_archivo.strftime('%Y%m%d_%H%M%S_%f')}_pedido_{order_id}.json"
            archivo_historial = HISTORIAL_DIR / nombre_historial
        escribir_texto_atomico(archivo_historial, json.dumps(data, indent=2, ensure_ascii=False))
        # --- 2) pedido_actual, el último ---
        escribir_texto_atomico(PEDIDO_ACTUAL, json.dumps(data, indent=2, ensure_ascii=False))
        return archivo_historial
    # [C3] Los PDFs también se escriben de forma atómica. Antes era un
    # write_bytes directo al nombre final: un lector (la pantalla, el robot)
    # o un corte a mitad de escritura veía un PDF truncado.
    def guardar_pdf(self, pdf_bytes, order_id, fecha_archivo, sufijo=""):
        if not pdf_bytes:
            return None
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        nombre_pdf = f"{fecha_archivo.strftime('%Y%m%d_%H%M%S')}_pedido_{order_id}{sufijo}.pdf"
        archivo_pdf = PDF_DIR / nombre_pdf
        if archivo_pdf.exists():
            nombre_pdf = f"{fecha_archivo.strftime('%Y%m%d_%H%M%S_%f')}_pedido_{order_id}{sufijo}.pdf"
            archivo_pdf = PDF_DIR / nombre_pdf
        escribir_bytes_atomico(archivo_pdf, pdf_bytes)
        return archivo_pdf
    def separar_multipart(self, raw_body, content_type):
        match = re.search(r'boundary="?([^";]+)"?', content_type, re.IGNORECASE)
        if not match:
            raise ValueError("No se encontró boundary en Content-Type multipart.")
        boundary = match.group(1)
        boundary_bytes = b"--" + boundary.encode("utf-8")
        partes = raw_body.split(boundary_bytes)
        data, pdf_bytes, pdf_granel_bytes = None, None, None
        for parte in partes:
            if not parte or parte.startswith(b"--"):
                continue
            # FIX 3 (conservado): quita los CRLF de cortesía del multipart.
            if parte.startswith(b"\r\n"):
                parte = parte[2:]
            if parte.endswith(b"\r\n"):
                parte = parte[:-2]
            if not parte:
                continue
            separador = b"\r\n\r\n"
            posicion = parte.find(separador)
            if posicion < 0:
                continue
            cabeceras = parte[:posicion]
            contenido = parte[posicion + len(separador):]
            cabeceras_texto = cabeceras.decode("utf-8", errors="replace")
            match_nombre = re.search(r'name="([^"]+)"', cabeceras_texto, re.IGNORECASE)
            if not match_nombre:
                continue
            nombre_campo = match_nombre.group(1)
            if nombre_campo == "pedido":
                data = json.loads(contenido.decode("utf-8"))
            elif nombre_campo == "pdf":
                pdf_bytes = contenido
            elif nombre_campo == "pdf_granel":
                pdf_granel_bytes = contenido
        return data, pdf_bytes, pdf_granel_bytes
    def aplicar_supermaestro(self, data):
        try:
            cat = json.loads(SUPERMAESTRO.read_text(encoding="utf-8"))
            productos = cat.get("SUPERMAESTRO", cat) if isinstance(cat, dict) else cat
        except Exception:
            return data
        por_vista = {}
        for p in productos:
            vista = str(p.get("ID VISTA", "") or "").strip()
            if vista and vista != "—":
                por_vista.setdefault(vista, []).append(p)
        def _normaliza(s):
            return "".join(c for c in str(s or "").lower() if c.isalnum())
        lineas = data.get("lineas", [])
        for linea in lineas:
            if linea.get("origen") == "granel":
                continue
            candidatos = por_vista.get(str(linea.get("idVista", "")))
            if not candidatos:
                continue
            desc = _normaliza(linea.get("descripcion", ""))
            mejor = max(candidatos, key=lambda p: sum(1 for w in _normaliza(p.get("PRODUCTO", "")).split() if len(w) > 2 and w in desc))
            id_erp = str(mejor.get("ID ERP", "") or "").strip()
            um_erp = str(mejor.get("UM ERP", "") or "").strip().split("/")[0].strip()
            if id_erp:
                linea["codigo"] = id_erp
                linea["idArticulo"] = id_erp
                linea["idErp"] = id_erp
            if um_erp:
                linea["umErp"] = um_erp
                linea["unidad"] = um_erp
        return data
    def fusionar_granel(self, data, pdf_granel_bytes):
        data = self.aplicar_supermaestro(data)
        graneles = data.get("granel")
        if graneles:
            lineas_granel = [_linea_granel_a_pedido(g) for g in graneles]
            aviso = None
        else:
            lineas_granel, aviso = extraer_lineas_granel(pdf_granel_bytes)
        if aviso:
            print(f"[GRANEL] {aviso}")
            sys.stdout.flush()
        if not lineas_granel:
            return data, None, []
        lineas = data.get("lineas")
        if lineas is None:
            pedido = data.get("pedido", {})
            lineas = pedido.get("lineas", [])
            pedido["lineas"] = lineas
            data["pedido"] = pedido
        data["lineas"] = lineas
        # [C2] Las líneas de granel SIN código ERP NO entran en "lineas".
        # "lineas" es lo que lee el robot del ERP: antes una categoría de
        # granel no mapeada acababa ahí con codigo="" y el robot intentaba
        # cursar un artículo sin código. Ahora se quedan aparte, en
        # "granelPendiente", y se avisa en la respuesta y en consola.
        sin_codigo = []
        for linea in lineas_granel:
            if linea.get("pendienteCodigo"):
                sin_codigo.append(linea)
                continue
            duplicada = any(
                str(l.get("descripcion", "")).strip().lower() == str(linea.get("descripcion", "")).strip().lower()
                and _cant_norm(l.get("cantidadPedida", l.get("cantidad"))) == _cant_norm(linea.get("cantidadPedida"))
                for l in data["lineas"]
            )
            if not duplicada:
                data["lineas"].append(linea)
        data["granelPendiente"] = sin_codigo
        if sin_codigo:
            cats = sorted({str(l.get("familiaGranel", "?")) for l in sin_codigo})
            print(f"[GRANEL] AVISO: {len(sin_codigo)} línea(s) SIN código ERP no enviadas al robot: {', '.join(cats)}.")
            sys.stdout.flush()
        print(f"[GRANEL] {len(lineas_granel) - len(sin_codigo)} línea(s) de granel fusionadas en el pedido.")
        sys.stdout.flush()
        return data, None, sin_codigo
    def do_POST(self):
        if self.path != "/pedido":
            self.enviar_json(404, {"ok": False, "error": "Ruta no encontrada"})
            return
        content_length = self.headers.get("Content-Length")
        if not content_length:
            self.enviar_json(400, {"ok": False, "error": "Falta Content-Length"})
            return
        # FIX 2 (conservado): límite de tamaño para no reventar la RAM.
        MAX_BODY = 30 * 1024 * 1024  # 30 MB
        try:
            length = int(content_length)
            if length <= 0 or length > MAX_BODY:
                self.enviar_json(413, {"ok": False, "error": "Pedido demasiado grande"})
                return
        except ValueError:
            self.enviar_json(400, {"ok": False, "error": "Content-Length inválido"})
            return
        try:
            raw_body = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "")
            data, pdf_bytes, pdf_granel_bytes = None, None, None
            if content_type.lower().startswith("multipart/form-data"):
                (data, pdf_bytes, pdf_granel_bytes) = self.separar_multipart(raw_body, content_type)
                if data is None:
                    self.enviar_json(400, {"ok": False, "error": "No se encontró el campo pedido dentro del multipart."})
                    return
            else:
                data = json.loads(raw_body.decode("utf-8"))
            # =================================================
            # [C1] VALIDACIÓN DEL orderId ANTES DE TOCAR NADA
            # =================================================
            order_id_recibido = str(data.get("pedido", {}).get("orderId", "") or "")
            if not order_id_recibido:
                self.enviar_json(400, {"ok": False, "error": "El pedido no trae orderId."})
                return
            if not ORDER_ID_RE.match(order_id_recibido):
                self.enviar_json(400, {"ok": False, "error": "orderId con formato no válido."})
                return
            # =================================================
            # CHECK DE DUPLICADOS (glob escapado)
            # =================================================
            patron = str(HISTORIAL_DIR / f"*_pedido_{glob.escape(order_id_recibido)}.json")
            archivos_existentes = glob.glob(patron)
            if archivos_existentes:
                print(f"[AVISO] El pedido {order_id_recibido} ya existe. Ignorando duplicado.")
                sys.stdout.flush()
                self.enviar_json(200, {
                    "ok": True,
                    "duplicado": True,
                    "orderId": order_id_recibido,
                    "mensaje": "Pedido ya recibido previamente (ignorado).",
                })
                return
            # =================================================
            data, aviso_granel, sin_codigo = self.fusionar_granel(data, pdf_granel_bytes)
            try:
                ts = data.get("pedido", {}).get("timestamp")
                fecha_archivo = datetime.fromtimestamp(int(ts) / 1000) if ts else datetime.now()
            except Exception:
                fecha_archivo = datetime.now()
            # [C4] PDFs primero, luego historial (commit), luego pedido_actual.
            archivo_pdf = self.guardar_pdf(pdf_bytes, order_id_recibido, fecha_archivo)
            archivo_pdf_granel = self.guardar_pdf(pdf_granel_bytes, order_id_recibido, fecha_archivo, sufijo="_granel")
            archivo_historial = self.guardar_pedido(data, order_id_recibido, fecha_archivo)
            print()
            print("=" * 60)
            print("PEDIDO RECIBIDO DE CINESTOCK")
            print("=" * 60)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 60)
            print(f"Pedido guardado en: {PEDIDO_ACTUAL}")
            print(f"Historial guardado en: {archivo_historial}")
            print(f"Pedido Conway ID: {order_id_recibido}")
            if archivo_pdf:
                print(f"PDF final guardado en: {archivo_pdf}")
            else:
                print("PDF final: no recibido en este pedido")
            if archivo_pdf_granel:
                print(f"PDF granel guardado en: {archivo_pdf_granel}")
            else:
                print("PDF granel: no recibido en este pedido")
            print("=" * 60)
            print()
            sys.stdout.flush()
            # FIX 4 (conservado) + [C7] orderId y contadores en la respuesta.
            self.enviar_json(200, {
                "ok": True,
                "duplicado": False,
                "orderId": order_id_recibido,
                "pdfRecibido": archivo_pdf is not None,
                "pdfGranelRecibido": archivo_pdf_granel is not None,
                "lineas": len(data.get("lineas", [])),
                "granelPendiente": len(sin_codigo),
            })
        except (ValueError, json.JSONDecodeError) as e:
            print("ERROR DE DATOS:", e)
            sys.stdout.flush()
            self.enviar_json(400, {"ok": False, "error": f"Datos inválidos: {str(e)}"})
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("ERROR procesando pedido:", e)
            sys.stdout.flush()
            self.enviar_json(500, {"ok": False, "error": "Error interno del receptor: " + str(e)})
    # [C7] /ping para el botón "PROBAR CONEXIÓN" de la app y para un watchdog.
    #      /pedido/<orderId> para resolver el caso ambiguo: tras un timeout,
    #      la app puede preguntar si el pedido llegó en vez de reenviar a ciegas.
    def do_GET(self):
        if self.path == "/ping":
            self.enviar_json(200, {"ok": True, "pong": True, "version": VERSION})
            return
        if self.path == "/supermaestro":
            try:
                cat = json.loads(SUPERMAESTRO.read_text(encoding="utf-8"))
                self.enviar_json(200, cat)
            except Exception:
                self.enviar_json(404, {"ok": False, "error": "supermaestro.json no disponible"})
            return
        if self.path.startswith("/pedido/"):
            oid = unquote(self.path[len("/pedido/"):])
            if not ORDER_ID_RE.match(oid):
                self.enviar_json(400, {"ok": False, "error": "orderId con formato no válido."})
                return
            existentes = glob.glob(str(HISTORIAL_DIR / f"*_pedido_{glob.escape(oid)}.json"))
            self.enviar_json(200, {
                "ok": True,
                "orderId": oid,
                "existe": bool(existentes),
                "archivo": Path(existentes[0]).name if existentes else None,
            })
            return
        self.enviar_json(404, {"ok": False, "error": "Ruta no encontrada. Usa POST /pedido, GET /ping o GET /pedido/<id>"})
    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} -> {format % args}")
        sys.stdout.flush()
# ============================================================
# ARRANQUE
# ============================================================
def run_server():
    global RECEPTOR_ACTIVO
    # [C6] Aviso (no bloqueo) si data/ vive en una carpeta sincronizada.
    # OneDrive manteniendo ficheros abiertos provoca PermissionError en los
    # os.replace y, con ficheros -wal/-shm de SQLite, corrupción. Aquí solo
    # hay JSON, pero el aviso cuesta cero y evita un disgusto.
    if ruta_sincronizada(BASE_DIR / "data"):
        print("!" * 60)
        print("  AVISO: data/ está dentro de una carpeta sincronizada")
        print("  (OneDrive/Dropbox/Drive). Muévela a una ruta local,")
        print("  por ejemplo C:\\CineStock\\data\\")
        print("!" * 60)
        sys.stdout.flush()
    server_address = (HOST, PORT)
    httpd = HTTPServer(server_address, CineStockRequestHandler)
    RECEPTOR_ACTIVO = True
    print("=" * 60)
    print("  SERVIDOR RECEPTOR CINESTOCK INICIADO")
    print(f"  versión: {VERSION}")
    print("=" * 60)
    print(f"  Escuchando en: http://{HOST}:{PORT}/pedido")
    print(f"  Ping:          http://{HOST}:{PORT}/ping")
    print(f"  Último pedido: {PEDIDO_ACTUAL}")
    print(f"  Historial:     {HISTORIAL_DIR}")
    print(f"  PDFs finales:  {PDF_DIR}")
    print("=" * 60)
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        RECEPTOR_ACTIVO = False
        print("\nServidor receptor detenido.")
        httpd.server_close()
if __name__ == "__main__":
    run_server()