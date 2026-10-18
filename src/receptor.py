#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Receptor CineStock -> Conway.
"""

import io
import json
import sys
import re
import glob  # <--- Importante para buscar los duplicados
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
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


# ============================================================
# SERVIDOR
# ============================================================

class CineStockRequestHandler(BaseHTTPRequestHandler):

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

    def guardar_pedido(self, data):
        PEDIDO_ACTUAL.parent.mkdir(parents=True, exist_ok=True)
        HISTORIAL_DIR.mkdir(parents=True, exist_ok=True)

        datos_pedido = data.get("pedido", {})
        fecha_recepcion = datetime.now().strftime("%d/%m/%Y")
        datos_pedido["fechaRecepcion"] = fecha_recepcion

        if "pedido" not in data:
            data["pedido"] = datos_pedido

        order_id = str(datos_pedido.get("orderId", "sin_id"))
        fecha_informe = str(datos_pedido.get("fechaInforme", ""))
        timestamp = datos_pedido.get("timestamp")

        PEDIDO_ACTUAL.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        try:
            if timestamp:
                fecha_archivo = datetime.fromtimestamp(int(timestamp) / 1000)
            else:
                fecha_archivo = datetime.now()
        except Exception:
            fecha_archivo = datetime.now()

        fecha_archivo_str = fecha_archivo.strftime("%Y%m%d_%H%M%S")
        nombre_historial = f"{fecha_archivo_str}_pedido_{order_id}.json"
        archivo_historial = HISTORIAL_DIR / nombre_historial

        if archivo_historial.exists():
            nombre_historial = f"{fecha_archivo.strftime('%Y%m%d_%H%M%S_%f')}_pedido_{order_id}.json"
            archivo_historial = HISTORIAL_DIR / nombre_historial

        archivo_historial.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        return order_id, fecha_informe, fecha_archivo, archivo_historial

    def guardar_pdf(self, pdf_bytes, order_id, fecha_archivo, sufijo=""):
        if not pdf_bytes:
            return None
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        fecha_archivo_str = fecha_archivo.strftime("%Y%m%d_%H%M%S")
        nombre_pdf = f"{fecha_archivo_str}_pedido_{order_id}{sufijo}.pdf"
        archivo_pdf = PDF_DIR / nombre_pdf

        if archivo_pdf.exists():
            nombre_pdf = f"{fecha_archivo.strftime('%Y%m%d_%H%M%S_%f')}_pedido_{order_id}{sufijo}.pdf"
            archivo_pdf = PDF_DIR / nombre_pdf

        archivo_pdf.write_bytes(pdf_bytes)
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

            parte = parte.lstrip(b"\r\n").rstrip(b"\r\n")
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
            return data, aviso

        lineas = data.get("lineas")
        if lineas is None:
            pedido = data.get("pedido", {})
            lineas = pedido.get("lineas", [])
            pedido["lineas"] = lineas
            data["pedido"] = pedido
        data["lineas"] = lineas

        for linea in lineas_granel:
            duplicada = any(
                str(l.get("descripcion", "")).strip().lower() == str(linea.get("descripcion", "")).strip().lower()
                and str(l.get("cantidadPedida") or l.get("cantidad")) == str(linea.get("cantidadPedida"))
                for l in data["lineas"]
            )
            if not duplicada:
                data["lineas"].append(linea)

        pendientes = sum(1 for l in lineas_granel if l.get("pendienteCodigo"))
        print(f"[GRANEL] {len(lineas_granel)} línea(s) de granel fusionadas en el pedido ({pendientes} sin código ERP).")
        sys.stdout.flush()

        return data, None

    def do_POST(self):
        if self.path != "/pedido":
            self.enviar_json(404, {"ok": False, "error": "Ruta no encontrada"})
            return

        content_length = self.headers.get("Content-Length")
        if not content_length:
            self.enviar_json(400, {"ok": False, "error": "Falta Content-Length"})
            return

        try:
            length = int(content_length)
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
            # AQUÍ ESTÁ EL CHECK DE DUPLICADOS (AHORA SÍ BIEN)
            # =================================================
            order_id_recibido = str(data.get("pedido", {}).get("orderId", ""))
            if order_id_recibido:
                archivos_existentes = glob.glob(str(HISTORIAL_DIR / f"*_pedido_{order_id_recibido}.json"))
                if archivos_existentes:
                    print(f"[AVISO] El pedido {order_id_recibido} ya existe. Ignorando duplicado.")
                    sys.stdout.flush()
                    self.enviar_json(200, {"ok": True, "duplicado": True, "mensaje": "Pedido ya recibido previamente (ignorado)."})
                    return
            # =================================================

            data, aviso_granel = self.fusionar_granel(data, pdf_granel_bytes)

            (order_id, fecha_informe, fecha_archivo, archivo_historial) = self.guardar_pedido(data)

            archivo_pdf = self.guardar_pdf(pdf_bytes, order_id, fecha_archivo)
            archivo_pdf_granel = self.guardar_pdf(pdf_granel_bytes, order_id, fecha_archivo, sufijo="_granel")

            print()
            print("=" * 60)
            print("PEDIDO RECIBIDO DE CINESTOCK")
            print("=" * 60)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 60)
            print(f"Pedido guardado en: {PEDIDO_ACTUAL}")
            print(f"Historial guardado en: {archivo_historial}")
            print(f"Pedido Conway ID: {order_id}")

            if fecha_informe:
                print(f"Fecha informe: {fecha_informe}")
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

            self.enviar_json(200, {
                "ok": True,
                "pdfRecibido": archivo_pdf is not None,
                "pdfGranelRecibido": archivo_pdf_granel is not None,
                "lineasGranel": len(data.get("lineas", [])) or 0
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

    def do_GET(self):
        if self.path == "/supermaestro":
            try:
                cat = json.loads(SUPERMAESTRO.read_text(encoding="utf-8"))
                self.enviar_json(200, cat)
            except Exception:
                self.enviar_json(404, {"ok": False, "error": "supermaestro.json no disponible"})
            return
        self.enviar_json(404, {"ok": False, "error": "Método GET no soportado. Usa POST /pedido"})

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} -> {format % args}")
        sys.stdout.flush()


# ============================================================
# ARRANQUE
# ============================================================

def run_server():
    global RECEPTOR_ACTIVO
    server_address = (HOST, PORT)
    httpd = HTTPServer(server_address, CineStockRequestHandler)
    RECEPTOR_ACTIVO = True

    print("=" * 60)
    print("  SERVIDOR RECEPTOR CINESTOCK INICIADO")
    print("=" * 60)
    print(f"  Escuchando en: http://{HOST}:{PORT}/pedido")
    print("  Listo para recibir pedidos desde Motorola en red local")
    print(f"  Último pedido: {PEDIDO_ACTUAL}")
    print(f"  Historial: {HISTORIAL_DIR}")
    print(f"  PDFs finales: {PDF_DIR}")
    print("=" * 60)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        RECEPTOR_ACTIVO = False
        print("\nServidor receptor detenido.")
        httpd.server_close()


if __name__ == "__main__":
    run_server()