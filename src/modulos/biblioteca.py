"""
Biblioteca de conocimiento de CineStock (sin dependencias de Qt).

Guarda correos (.eml) y documentos (PDF, PowerPoint, Word, Excel, texto)
en una base SQLite con búsqueda de texto completo, y puntúa la importancia
de cada documento (reciente, con adjuntos, palabras clave del cine/ERP...)
para poder sugerir los más útiles.

Dónde guarda las cosas (junto a la carpeta data/ de CineStock):
    data/conocimiento/biblioteca.db      -> índice y textos
    data/conocimiento/originales/        -> copia de cada archivo añadido

Lectura de formatos:
    .eml  .docx  .pptx  .xlsx  .txt  .md  .csv  .html   -> solo librería estándar
    .pdf                                                 -> necesita "pypdf"
                                                            (pip install pypdf)
"""

from __future__ import annotations

import hashlib
import html
import math
import re
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
import zipfile
from io import BytesIO
from dataclasses import dataclass, field
from datetime import date, datetime
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Optional
from xml.etree import ElementTree as ET


# ----------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------

EXTENSIONES_SOPORTADAS = (
    ".eml", ".pdf", ".pptx", ".docx", ".xlsx",
    ".txt", ".md", ".csv", ".html", ".htm", ".zip",
    ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
)

EXTENSIONES_IMAGEN = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")

EXTENSIONES_ANTIGUAS = {
    ".ppt": ".pptx", ".doc": ".docx", ".xls": ".xlsx", ".msg": ".eml",
}

MAX_TEXTO = 300_000

ESTADOS = ("NORMAL", "DESTACADO", "ARCHIVADO")

# Palabras que indican contenido útil para el día a día del cine / ERP.
# Se comparan sin acentos y en minúsculas.
PALABRAS_CLAVE = (
    "erp", "cinema manager", "conway", "pedido", "albaran",
    "factura", "abono", "proveedor", "inventario", "stock", "merma",
    "procedimiento", "instruccion", "instrucciones", "protocolo",
    "normativa", "obligatorio", "importante", "urgente", "a partir de",
    "cambio", "precio", "tarifa", "incidencia", "caducidad", "devolucion",
    "cierre", "apertura", "mantenimiento", "programacion", "estreno",
    "horario", "turno", "manual", "paso a paso", "sustituto", "sustitutos",
    "rotura de stock", "unidades por caja",
)

MARCAS_AUTOMATICO = (
    "noreply", "no-reply", "no_reply", "donotreply", "respuesta automatica",
    "automatic reply", "fuera de la oficina", "out of office",
    "unsubscribe", "darse de baja", "newsletter",
)


# Categorías automáticas (palabra completa, sin acentos). Gana la que más suma.
CATEGORIAS = {
    "Pedidos y ERP": (
        "pedido", "pedidos", "erp", "conway", "albaran", "orden de compra",
        "oc", "interface", "tarifa", "tarifas", "frozen", "mapeo",
        "sustituto", "sustitutos", "proveedor", "catalogo", "articulo",
    ),
    "Inventario y activos": (
        "inventario", "inventarios", "stock", "activos", "merma", "conteo",
        "bajas", "traspaso", "traspasos", "caducidad", "conciliacion",
    ),
    "Cocina y producto": (
        "horno", "merrychef", "platos", "pizza", "pizzas", "tekes",
        "limpieza", "cocina",
    ),
    "Taquilla, programacion y colegios": (
        "matinales", "colegios", "pases", "grupos", "taquilla", "yelmopass",
        "programacion", "estreno", "cinema manager", "vista", "cupones",
        "fiesta del cine", "disney",
    ),
    "Sistemas y soporte": (
        "freshservice", "ticket", "tms", "rosetta", "acceso", "geniza",
        "cloud", "incidencia",
    ),
    "RRHH y normativa": (
        "permiso", "normativa", "horas", "integridad", "valores",
        "embajadores", "bonos", "convenio", "nomina",
    ),
}


def _regex_palabras(palabras):
    ordenadas = sorted({_normalizar(p) for p in palabras}, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(p) for p in ordenadas) + r")(?!\w)")


_RE_CLAVE = None
_RE_CATEGORIAS: Dict[str, "re.Pattern"] = {}


def categorizar(titulo: str, texto: str) -> str:
    global _RE_CATEGORIAS
    if not _RE_CATEGORIAS:
        _RE_CATEGORIAS = {n: _regex_palabras(p) for n, p in CATEGORIAS.items()}
    t = _normalizar(titulo or "")
    cuerpo = _normalizar((texto or "")[:6000])  # el principio, no los adjuntos enormes
    mejor, mejor_n = "Otros", 1
    for nombre, rx in _RE_CATEGORIAS.items():
        n = 3 * len(rx.findall(t)) + len(rx.findall(cuerpo))
        if n > mejor_n:
            mejor, mejor_n = nombre, n
    return mejor


PALABRAS_VACIAS = frozenset("""
a al algo ante con como cual cuales cuando de del donde el ella ellos en entre
era es esta este esto estos fue ha hacer hago hace han hay la las le les lo los
mas me mi mis muy no nos o para pero por porque que quien quiero se ser si sin
sobre su sus te tengo tiene tu tus un una uno unas unos va ya y puedo debo
necesito saber hacemos hacen cuanto cuantos cuanta
""".split())


def _simplificar(p: str) -> str:
    """Quita el plural más común: albaranes -> albaran, cambios -> cambio."""
    if len(p) > 5 and p.endswith("es"):
        return p[:-2]
    if len(p) > 4 and p.endswith("s"):
        return p[:-1]
    return p


def palabras_utiles(pregunta: str) -> List[str]:
    vistas: List[str] = []
    for p in re.findall(r"\w+", _normalizar(pregunta)):
        if len(p) < 3 or p in PALABRAS_VACIAS:
            continue
        p = _simplificar(p)
        if p not in vistas:
            vistas.append(p)
    return vistas


class FaltaLibreria(Exception):
    """Falta una librería opcional (por ejemplo pypdf)."""


# ----------------------------------------------------------------------
# Resultados
# ----------------------------------------------------------------------

@dataclass
class ResultadoAlta:
    ok: bool
    mensaje: str
    archivo: str = ""
    duplicado: bool = False
    id: Optional[int] = None


@dataclass
class Resultado:
    id: int
    titulo: str
    tipo: str
    fecha: str
    remitente: str
    importancia: float
    estado: str
    fragmento: str = ""
    adjuntos: int = 0
    motivos: str = ""
    categoria: str = ""


# ----------------------------------------------------------------------
# Utilidades de texto
# ----------------------------------------------------------------------

def _sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _normalizar(texto: str) -> str:
    return _sin_acentos(texto).lower()


class _HtmlATexto(HTMLParser):
    _SALTOS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: List[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._ignorar += 1
        elif tag in self._SALTOS:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._ignorar:
            self._ignorar -= 1
        elif tag in self._SALTOS:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._ignorar:
            self.partes.append(data)


def _html_a_texto(contenido: str) -> str:
    p = _HtmlATexto()
    try:
        p.feed(contenido)
        p.close()
    except Exception:
        return html.unescape(re.sub(r"<[^>]+>", " ", contenido))
    return _limpiar("".join(p.partes))


def _limpiar(texto: str) -> str:
    texto = texto.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n[ \t]+", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def _leer_texto_plano(ruta: Path) -> str:
    datos = ruta.read_bytes()
    for codificacion in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", errors="replace")


# ----------------------------------------------------------------------
# Extracción por formato
# ----------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _texto_docx(ruta: Path) -> str:
    salida: List[str] = []
    with zipfile.ZipFile(ruta) as z:
        nombres = set(z.namelist())
        partes = ["word/document.xml"] + sorted(
            n for n in nombres
            if re.match(r"word/(header|footer|footnotes|endnotes)\d*\.xml$", n)
        )
        for parte in partes:
            if parte not in nombres:
                continue
            raiz = ET.fromstring(z.read(parte))
            for par in raiz.iter(_W + "p"):
                t = "".join(x.text or "" for x in par.iter(_W + "t"))
                if t.strip():
                    salida.append(t)
    return "\n".join(salida)


def _texto_pptx(ruta: Path) -> str:
    salida: List[str] = []
    with zipfile.ZipFile(ruta) as z:
        nombres = z.namelist()

        def numero(n: str) -> int:
            m = re.search(r"(\d+)\.xml$", n)
            return int(m.group(1)) if m else 0

        diapositivas = sorted(
            (n for n in nombres if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=numero,
        )
        notas = {
            numero(n): n for n in nombres
            if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)
        }

        def parrafos(parte: str) -> List[str]:
            raiz = ET.fromstring(z.read(parte))
            lineas = []
            for par in raiz.iter(_A + "p"):
                t = "".join(x.text or "" for x in par.iter(_A + "t"))
                if t.strip():
                    lineas.append(t)
            return lineas

        for parte in diapositivas:
            n = numero(parte)
            salida.append(f"--- Diapositiva {n} ---")
            salida.extend(parrafos(parte))
            if n in notas:
                lineas_nota = parrafos(notas[n])
                if lineas_nota:
                    salida.append("(Notas) " + " ".join(lineas_nota))
    return "\n".join(salida)


def _texto_xlsx(ruta: Path) -> str:
    salida: List[str] = []
    with zipfile.ZipFile(ruta) as z:
        nombres = set(z.namelist())
        if "xl/workbook.xml" in nombres:
            raiz = ET.fromstring(z.read("xl/workbook.xml"))
            hojas = [h.get("name", "") for h in raiz.iter(_S + "sheet")]
            if hojas:
                salida.append("Hojas: " + ", ".join(hojas))
        if "xl/sharedStrings.xml" in nombres:
            raiz = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in raiz.iter(_S + "si"):
                t = "".join(x.text or "" for x in si.iter(_S + "t"))
                if t.strip():
                    salida.append(t)
        for hoja in sorted(n for n in nombres
                           if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
            raiz = ET.fromstring(z.read(hoja))
            for inline in raiz.iter(_S + "is"):
                t = "".join(x.text or "" for x in inline.iter(_S + "t"))
                if t.strip():
                    salida.append(t)
    return "\n".join(salida)


def _texto_pdf(ruta: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError:
            raise FaltaLibreria(
                "Falta la librería pypdf para leer PDF. En WinPython ejecuta: "
                "pip install pypdf"
            )
    lector = PdfReader(str(ruta))
    if getattr(lector, "is_encrypted", False):
        try:
            lector.decrypt("")
        except Exception:
            raise ValueError("El PDF está protegido con contraseña.")
    partes = []
    for pagina in lector.pages:
        try:
            partes.append(pagina.extract_text() or "")
        except Exception:
            partes.append("")
    return "\n".join(partes)


# ----------------------------------------------------------------------
# Lectura de texto en imágenes (OCR) - opcional
# ----------------------------------------------------------------------

OCR_INSTALACION = (
    "Para leer el texto de las imágenes instala una vez (en la consola de "
    "WinPython): pip install rapidocr-onnxruntime"
)
MIN_BYTES_IMAGEN = 15_000        # por debajo suelen ser logos / iconos
MIN_LADO_IMAGEN = (200, 100)     # ancho, alto mínimos si Pillow está instalado
MAX_IMAGENES_POR_CORREO = 15
MAX_PAGINAS_PDF_OCR = 10


def _imagen_merece_ocr(datos: bytes) -> bool:
    if len(datos) < MIN_BYTES_IMAGEN:
        return False
    try:
        from PIL import Image
        with Image.open(BytesIO(datos)) as im:
            return im.width >= MIN_LADO_IMAGEN[0] and im.height >= MIN_LADO_IMAGEN[1]
    except ImportError:
        return True
    except Exception:
        return False


def _buscar_tesseract() -> Optional[str]:
    exe = shutil.which("tesseract")
    if exe:
        return exe
    for candidato in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if Path(candidato).exists():
            return candidato
    return None


class _MotorOcr:
    """
    Motor de OCR perezoso. Orden de preferencia:
      1) RapidOCR  (pip install rapidocr-onnxruntime; no necesita instalar nada más)
      2) Tesseract (programa externo, si está instalado)
    Si no hay ninguno, disponible() es False y la biblioteca funciona igual
    que antes (sin leer imágenes).
    """

    def __init__(self):
        self.nombre: Optional[str] = None
        self._probado = False
        self._rapid = None
        self._tess: Optional[str] = None
        self._idiomas: Optional[str] = None

    def disponible(self) -> bool:
        if not self._probado:
            self._probado = True
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore
                self._rapid = RapidOCR()
                self.nombre = "RapidOCR"
                return True
            except Exception:
                self._rapid = None
            exe = _buscar_tesseract()
            if exe:
                self._tess = exe
                self._idiomas = self._elegir_idiomas(exe)
                self.nombre = "Tesseract"
        return self.nombre is not None

    @staticmethod
    def _flags():
        return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    def _elegir_idiomas(self, exe: str) -> Optional[str]:
        try:
            salida = subprocess.run(
                [exe, "--list-langs"], capture_output=True, text=True,
                timeout=20, creationflags=self._flags(),
            ).stdout.split()
        except Exception:
            return None
        if "spa" in salida and "eng" in salida:
            return "spa+eng"
        if "spa" in salida:
            return "spa"
        if "eng" in salida:
            return "eng"
        return None

    def leer(self, datos: bytes) -> str:
        if not self.disponible():
            raise FaltaLibreria(OCR_INSTALACION)
        if self._rapid is not None:
            try:
                resultado, _ = self._rapid(datos)
            except Exception:
                import numpy as np
                from PIL import Image
                resultado, _ = self._rapid(np.array(Image.open(BytesIO(datos)).convert("RGB")))
            lineas = [r[1] for r in (resultado or []) if float(r[2]) >= 0.5]
            return "\n".join(lineas)
        return self._leer_tesseract(datos)

    def _leer_tesseract(self, datos: bytes) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "i.png"
            try:
                from PIL import Image
                with Image.open(BytesIO(datos)) as im:
                    im = im.convert("RGB")
                    if im.width < 1400:  # ampliar mejora la lectura de capturas
                        factor = 2 if im.width >= 700 else 3
                        im = im.resize((im.width * factor, im.height * factor))
                    im.save(img, format="PNG")
            except Exception:
                img.write_bytes(datos)
            cmd = [self._tess, str(img), "stdout", "--psm", "6"]
            if self._idiomas:
                cmd += ["-l", self._idiomas]
            r = subprocess.run(
                cmd, capture_output=True, timeout=90,
                creationflags=self._flags(),
            )
            return r.stdout.decode("utf-8", errors="replace")


def _texto_imagen(ruta: Path, ocr) -> str:
    if ocr is None:
        raise FaltaLibreria(OCR_INSTALACION)
    datos = ruta.read_bytes()
    if not _imagen_merece_ocr(datos):
        return ""
    return ocr(datos) or ""


def _ocr_pdf_escaneado(ruta: Path, ocr) -> str:
    """OCR de las primeras páginas de un PDF sin texto (necesita pypdfium2)."""
    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError:
        return ""
    try:
        pdf = pdfium.PdfDocument(str(ruta))
        partes = []
        for i in range(min(len(pdf), MAX_PAGINAS_PDF_OCR)):
            imagen = pdf[i].render(scale=2).to_pil()
            buf = BytesIO()
            imagen.save(buf, format="PNG")
            partes.append(ocr(buf.getvalue()) or "")
        return "\n".join(partes)
    except Exception:
        return ""


MAX_ZIP_ARCHIVOS = 100
MAX_ZIP_BYTES = 200 * 1024 * 1024


def _texto_zip(ruta: Path, ocr=None) -> str:
    """
    Lee los documentos de dentro de un .zip (un solo nivel). Nunca extrae a
    carpetas del usuario: cada archivo se lee en memoria y se pasa por un
    archivo temporal con nombre propio (no se usa el nombre del zip, así que
    no hay riesgo de rutas peligrosas).
    """
    partes: List[str] = []
    ignorados = 0
    with zipfile.ZipFile(ruta) as z:
        entradas = [i for i in z.infolist() if not i.is_dir()]
        if len(entradas) > MAX_ZIP_ARCHIVOS or \
                sum(i.file_size for i in entradas) > MAX_ZIP_BYTES:
            raise ValueError("El .zip es demasiado grande (límite: 100 archivos / 200 MB).")
        with tempfile.TemporaryDirectory() as tmp:
            for n, info in enumerate(entradas):
                nombre = info.filename
                ext = Path(nombre).suffix.lower()
                if ext not in EXTENSIONES_SOPORTADAS or ext in (".zip", ".eml"):
                    ignorados += 1
                    partes.append(f"[Archivo: {nombre}] (no se lee este formato)")
                    continue
                try:
                    destino = Path(tmp) / f"z{n}{ext}"
                    destino.write_bytes(z.read(info))
                    texto = extraer_texto(destino, ocr)
                    if texto.strip() or ext not in EXTENSIONES_IMAGEN:
                        partes.append(f"[Archivo: {nombre}]\n{texto}")
                except FaltaLibreria:
                    partes.append(f"[Archivo: {nombre}] (falta una librería para leerlo)")
                except Exception:
                    partes.append(f"[Archivo: {nombre}] (no se pudo leer)")
    return "\n\n".join(partes)


def extraer_texto(ruta: Path, ocr=None) -> str:
    """
    Texto de un documento (no correo). Lanza FaltaLibreria o ValueError.
    ocr: función bytes->str (o None si no hay OCR instalado).
    """
    ext = ruta.suffix.lower()
    try:
        if ext == ".docx":
            texto = _texto_docx(ruta)
        elif ext == ".pptx":
            texto = _texto_pptx(ruta)
        elif ext == ".xlsx":
            texto = _texto_xlsx(ruta)
        elif ext == ".pdf":
            texto = _texto_pdf(ruta)
            if len(texto.strip()) < 50 and ocr is not None:
                extra = _ocr_pdf_escaneado(ruta, ocr)
                if extra.strip():
                    texto = extra
        elif ext == ".zip":
            texto = _texto_zip(ruta, ocr)
        elif ext in EXTENSIONES_IMAGEN:
            texto = _texto_imagen(ruta, ocr)
        elif ext in (".html", ".htm"):
            texto = _html_a_texto(_leer_texto_plano(ruta))
        elif ext in (".txt", ".md", ".csv"):
            texto = _leer_texto_plano(ruta)
        else:
            raise ValueError(f"Formato no soportado: {ext}")
    except (FaltaLibreria, ValueError):
        raise
    except zipfile.BadZipFile:
        raise ValueError("El archivo está dañado o no es un documento válido.")
    except ET.ParseError:
        raise ValueError("No se pudo leer el contenido interno del documento.")
    return _limpiar(texto)[:MAX_TEXTO]


# ----------------------------------------------------------------------
# Correos
# ----------------------------------------------------------------------

@dataclass
class _Correo:
    asunto: str
    remitente: str
    destinatarios: str
    fecha: str            # ISO yyyy-mm-dd o ""
    texto: str
    adjuntos: int
    message_id: str


def _fecha_iso(valor) -> str:
    if not valor:
        return ""
    try:
        dt = parsedate_to_datetime(str(valor))
        return dt.date().isoformat()
    except Exception:
        return ""


def _leer_correo(ruta: Path, ocr=None) -> _Correo:
    with open(ruta, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)

    asunto = str(msg.get("subject", "") or "").strip()
    remitente = str(msg.get("from", "") or "").strip()
    destinatarios = ", ".join(
        str(msg.get(c, "") or "").strip() for c in ("to", "cc")
        if msg.get(c)
    )
    fecha = _fecha_iso(msg.get("date"))
    message_id = str(msg.get("message-id", "") or "").strip()

    def contenido_de(tipo: str) -> str:
        parte = msg.get_body(preferencelist=(tipo,))
        if parte is None:
            return ""
        try:
            contenido = parte.get_content()
            if isinstance(contenido, bytes):
                contenido = contenido.decode("utf-8", errors="replace")
            return _html_a_texto(contenido) if tipo == "html" else _limpiar(contenido)
        except Exception:
            return ""

    # Se prefiere el texto plano, salvo que sea casi vacío (p. ej. "mira la
    # versión HTML") y el HTML tenga más contenido.
    cuerpo = contenido_de("plain")
    if len(cuerpo) < 40:
        cuerpo_html = contenido_de("html")
        if len(cuerpo_html) > len(cuerpo):
            cuerpo = cuerpo_html

    textos_adjuntos: List[str] = []
    n_adjuntos = 0
    with tempfile.TemporaryDirectory() as tmp:
        n_imagenes = 0
        for adj in msg.iter_attachments():
            nombre = adj.get_filename()
            if adj.get_content_maintype() == "image":
                # Capturas dentro del correo: se leen con OCR (si está instalado).
                if ocr is not None and n_imagenes < MAX_IMAGENES_POR_CORREO:
                    try:
                        datos = adj.get_payload(decode=True) or b""
                        if _imagen_merece_ocr(datos):
                            n_imagenes += 1
                            t = (ocr(datos) or "").strip()
                            if t:
                                textos_adjuntos.append(f"[Imagen: {nombre or 'sin nombre'}]\n{t}")
                    except Exception:
                        pass
                continue
            if not nombre:
                continue
            n_adjuntos += 1
            ext = Path(nombre).suffix.lower()
            if ext not in EXTENSIONES_SOPORTADAS or ext == ".eml":
                textos_adjuntos.append(f"[Adjunto: {nombre}]")
                continue
            try:
                datos = adj.get_payload(decode=True) or b""
                destino = Path(tmp) / f"adj_{n_adjuntos}{ext}"
                destino.write_bytes(datos)
                t = extraer_texto(destino, ocr)
                textos_adjuntos.append(f"[Adjunto: {nombre}]\n{t}")
            except Exception:
                textos_adjuntos.append(f"[Adjunto: {nombre}] (no se pudo leer)")

    texto = cuerpo
    if textos_adjuntos:
        texto = (texto + "\n\n" + "\n\n".join(textos_adjuntos)).strip()

    return _Correo(
        asunto=asunto,
        remitente=remitente,
        destinatarios=destinatarios,
        fecha=fecha,
        texto=texto[:MAX_TEXTO],
        adjuntos=n_adjuntos,
        message_id=message_id,
    )


_PREFIJOS_RESPUESTA = re.compile(
    r"^\s*((re|rv|fw|fwd|enc|r)\s*(\[\d+\])?\s*:\s*)+", re.IGNORECASE
)


def hilo_de(asunto: str) -> str:
    base = _PREFIJOS_RESPUESTA.sub("", asunto or "")
    base = re.sub(r"\s+", " ", _normalizar(base)).strip()
    return base


# ----------------------------------------------------------------------
# Puntuación de importancia
# ----------------------------------------------------------------------

def puntuar(
    tipo: str,
    fecha_iso: str,
    texto: str,
    remitente: str,
    asunto: str,
    adjuntos: int,
    mensajes_en_hilo: int = 1,
    hoy: Optional[date] = None,
):
    """Devuelve (puntos 0-100, motivos en texto). Todo es explicable."""
    hoy = hoy or date.today()
    puntos = 0.0
    motivos: List[str] = []

    # Recencia
    if fecha_iso:
        try:
            fecha = date.fromisoformat(fecha_iso)
            edad = max(0, (hoy - fecha).days)
            peso = 35.0 if tipo == "correo" else 25.0
            puntos += peso * math.exp(-edad / 540.0)
            if edad <= 180:
                motivos.append(f"reciente (hace {edad} días)")
            else:
                motivos.append(f"antiguo ({edad // 30} meses)")
        except ValueError:
            pass

    # Palabras clave
    norm = _normalizar(f"{asunto}\n{texto}")
    global _RE_CLAVE
    if _RE_CLAVE is None:
        _RE_CLAVE = _regex_palabras(PALABRAS_CLAVE)
    encontradas = sorted(set(_RE_CLAVE.findall(norm)))
    if encontradas:
        puntos += min(28.0, 4.0 * len(encontradas))
        motivos.append("palabras clave: " + ", ".join(encontradas[:6]))

    # Adjuntos
    if adjuntos:
        puntos += 8
        motivos.append(f"{adjuntos} adjunto(s)")

    # Tipo
    if tipo != "correo":
        puntos += 10
        motivos.append("documento / manual")

    # Longitud
    n = len(texto or "")
    if n >= 1500:
        puntos += 10
        motivos.append("contenido extenso")
    elif n >= 500:
        puntos += 5
    elif n < 150:
        puntos -= 15
        motivos.append("muy corto")

    # Hilo largo
    if mensajes_en_hilo > 1:
        puntos += min(9.0, 3.0 * (mensajes_en_hilo - 1))
        motivos.append(f"hilo con {mensajes_en_hilo} mensajes")

    # Automáticos
    cabecera = _normalizar(f"{remitente} {asunto} {(texto or '')[:400]}")
    if any(m in cabecera for m in MARCAS_AUTOMATICO):
        puntos -= 30
        motivos.append("parece automático")

    puntos = max(0.0, min(100.0, puntos))
    return round(puntos, 1), "; ".join(motivos)


# ----------------------------------------------------------------------
# Biblioteca
# ----------------------------------------------------------------------

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    clave         TEXT UNIQUE NOT NULL,
    titulo        TEXT NOT NULL,
    tipo          TEXT NOT NULL,
    archivo       TEXT NOT NULL,
    ruta_copia    TEXT NOT NULL,
    fecha_doc     TEXT NOT NULL DEFAULT '',
    fecha_alta    TEXT NOT NULL,
    remitente     TEXT NOT NULL DEFAULT '',
    destinatarios TEXT NOT NULL DEFAULT '',
    adjuntos      INTEGER NOT NULL DEFAULT 0,
    texto         TEXT NOT NULL DEFAULT '',
    hilo          TEXT NOT NULL DEFAULT '',
    importancia   REAL NOT NULL DEFAULT 0,
    motivos       TEXT NOT NULL DEFAULT '',
    categoria     TEXT NOT NULL DEFAULT '',
    estado        TEXT NOT NULL DEFAULT 'NORMAL'
);
CREATE INDEX IF NOT EXISTS idx_docs_hilo ON docs(hilo);
CREATE TABLE IF NOT EXISTS ocr_cache (
    hash  TEXT PRIMARY KEY,
    texto TEXT NOT NULL
);
"""


def _carpeta_por_defecto() -> Path:
    # src/modulos/biblioteca.py -> raíz del proyecto = parents[2]
    return Path(__file__).resolve().parents[2] / "data" / "conocimiento"


class Biblioteca:

    def __init__(self, carpeta: Optional[Path] = None):
        self.carpeta = Path(carpeta) if carpeta else _carpeta_por_defecto()
        self.originales = self.carpeta / "originales"
        self.originales.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.carpeta / "biblioteca.db"))
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_ESQUEMA)
        self._fts = self._crear_fts()
        self._con.commit()
        self._motor_ocr = _MotorOcr()

    # ---------------- infraestructura ----------------

    def _crear_fts(self) -> bool:
        for opciones in (
            "tokenize='unicode61 remove_diacritics 2'",
            "tokenize='unicode61'",
        ):
            try:
                self._con.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5("
                    f"titulo, texto, meta, {opciones})"
                )
                return True
            except sqlite3.OperationalError:
                continue
        return False

    @property
    def ocr_nombre(self) -> Optional[str]:
        """'RapidOCR', 'Tesseract' o None si no hay lectura de imágenes."""
        self._motor_ocr.disponible()
        return self._motor_ocr.nombre

    def _ocr_callable(self):
        if not self._motor_ocr.disponible():
            return None

        def leer(datos: bytes) -> str:
            h = hashlib.sha256(datos).hexdigest()
            fila = self._con.execute(
                "SELECT texto FROM ocr_cache WHERE hash=?", (h,)
            ).fetchone()
            if fila:
                return fila["texto"]
            try:
                texto = _limpiar(self._motor_ocr.leer(datos))
            except Exception:
                return ""  # un fallo no se guarda en la caché
            self._con.execute(
                "INSERT OR REPLACE INTO ocr_cache (hash, texto) VALUES (?,?)",
                (h, texto),
            )
            self._con.commit()
            return texto

        return leer

    def cerrar(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass

    # ---------------- altas ----------------

    def _leer_documento(self, ruta: Path, ocr) -> Dict:
        """Lee un archivo y devuelve sus campos (sin tocar la base de datos)."""
        ext = ruta.suffix.lower()
        if ext == ".eml":
            correo = _leer_correo(ruta, ocr)
            return dict(
                tipo="correo",
                titulo=correo.asunto
                or re.sub(r"[_\s]+", " ", ruta.stem).strip() or "(sin asunto)",
                texto=correo.texto,
                remitente=correo.remitente,
                destinatarios=correo.destinatarios,
                fecha=correo.fecha,
                adjuntos=correo.adjuntos,
                base_clave=("mid:" + correo.message_id.lower()
                            if correo.message_id else None),
                hilo=hilo_de(correo.asunto),
            )
        texto = extraer_texto(ruta, ocr)
        tipo = ext.lstrip(".")
        if tipo in ("md", "csv", "htm", "html"):
            tipo = "texto"
        elif ext in EXTENSIONES_IMAGEN:
            tipo = "imagen"
        return dict(
            tipo=tipo, titulo=ruta.stem, texto=texto, remitente="",
            destinatarios="",
            fecha=datetime.fromtimestamp(ruta.stat().st_mtime).date().isoformat(),
            adjuntos=0, base_clave=None, hilo="",
        )

    def reprocesar(self, doc_id: int) -> ResultadoAlta:
        """Vuelve a leer un documento ya guardado (p. ej. tras instalar OCR)."""
        f = self._con.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
        if not f:
            return ResultadoAlta(False, "No existe en la biblioteca.")
        ruta = Path(f["ruta_copia"])
        if not ruta.is_file():
            return ResultadoAlta(False, "Falta la copia original guardada.", f["archivo"])
        try:
            d = self._leer_documento(ruta, self._ocr_callable())
        except Exception as e:
            return ResultadoAlta(False, f"No se pudo leer: {e}", f["archivo"])
        texto = d["texto"]
        if texto == f["texto"] and d["adjuntos"] == f["adjuntos"]:
            return ResultadoAlta(True, "Sin cambios.", f["archivo"], duplicado=True, id=doc_id)
        categoria = categorizar(f["titulo"], texto)
        self._con.execute(
            "UPDATE docs SET texto=?, adjuntos=?, categoria=? WHERE id=?",
            (texto, d["adjuntos"], categoria, doc_id),
        )
        if self._fts:
            self._con.execute("DELETE FROM docs_fts WHERE rowid=?", (doc_id,))
            meta = f"{f['remitente']} {f['destinatarios']} {f['archivo']} {categoria}"
            self._con.execute(
                "INSERT INTO docs_fts (rowid, titulo, texto, meta) VALUES (?,?,?,?)",
                (doc_id, f["titulo"], texto, meta),
            )
        self._recalcular_hilo(f["hilo"])
        self._con.commit()
        return ResultadoAlta(True, "Actualizado.", f["archivo"], id=doc_id)

    def reprocesar_todo(self, progreso=None) -> List[ResultadoAlta]:
        ids = [r["id"] for r in self._con.execute("SELECT id FROM docs ORDER BY id")]
        resultados: List[ResultadoAlta] = []
        for i, doc_id in enumerate(ids):
            if progreso is not None:
                ficha = self.obtener(doc_id)
                if progreso(i, len(ids), ficha["archivo"] if ficha else "") is False:
                    break
            resultados.append(self.reprocesar(doc_id))
        if progreso is not None:
            progreso(len(resultados), len(ids), "")
        return resultados

    def agregar_archivo(self, ruta) -> ResultadoAlta:
        ruta = Path(ruta)
        nombre = ruta.name
        ext = ruta.suffix.lower()

        if not ruta.is_file():
            return ResultadoAlta(False, "No existe el archivo.", nombre)

        if ext in EXTENSIONES_ANTIGUAS:
            return ResultadoAlta(
                False,
                f"Formato antiguo ({ext}). Guárdalo como "
                f"{EXTENSIONES_ANTIGUAS[ext]} y vuelve a añadirlo.",
                nombre,
            )
        if ext not in EXTENSIONES_SOPORTADAS:
            return ResultadoAlta(False, f"Formato no soportado ({ext}).", nombre)

        try:
            d = self._leer_documento(ruta, self._ocr_callable())
        except FaltaLibreria as e:
            return ResultadoAlta(False, str(e), nombre)
        except ValueError as e:
            return ResultadoAlta(False, str(e), nombre)
        except Exception as e:
            return ResultadoAlta(False, f"No se pudo leer: {e}", nombre)

        tipo, titulo, texto = d["tipo"], d["titulo"], d["texto"]
        remitente, destinatarios = d["remitente"], d["destinatarios"]
        fecha, adjuntos = d["fecha"], d["adjuntos"]
        base_clave, hilo = d["base_clave"], d["hilo"]

        if base_clave is None:
            base_clave = "file:" + hashlib.sha256(ruta.read_bytes()).hexdigest()
        clave = hashlib.sha256(base_clave.encode("utf-8")).hexdigest()

        existente = self._con.execute(
            "SELECT id FROM docs WHERE clave=?", (clave,)
        ).fetchone()
        if existente:
            return ResultadoAlta(
                True, "Ya estaba en la biblioteca.", nombre,
                duplicado=True, id=existente["id"],
            )

        aviso = ""
        if not texto.strip():
            aviso = " (sin texto legible: ¿PDF escaneado o vacío?)" if tipo != "imagen" else " (no se encontró texto en la imagen)"
            texto = ""

        segura = re.sub(r"[^\w.\- ]", "_", nombre)[:120]
        destino = self.originales / f"{clave[:10]}_{segura}"
        try:
            shutil.copy2(ruta, destino)
        except Exception as e:
            return ResultadoAlta(False, f"No se pudo copiar el original: {e}", nombre)

        if not hilo:
            hilo = f"doc:{clave[:16]}"

        categoria = categorizar(titulo, texto)
        cur = self._con.execute(
            "INSERT INTO docs (clave, titulo, tipo, archivo, ruta_copia, fecha_doc,"
            " fecha_alta, remitente, destinatarios, adjuntos, texto, hilo, categoria)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                clave, titulo, tipo, nombre, str(destino), fecha,
                datetime.now().isoformat(timespec="seconds"),
                remitente, destinatarios, adjuntos, texto, hilo, categoria,
            ),
        )
        nuevo_id = cur.lastrowid

        if self._fts:
            meta = f"{remitente} {destinatarios} {nombre} {categoria}"
            self._con.execute(
                "INSERT INTO docs_fts (rowid, titulo, texto, meta) VALUES (?,?,?,?)",
                (nuevo_id, titulo, texto, meta),
            )

        self._recalcular_hilo(hilo)
        self._con.commit()
        return ResultadoAlta(True, "Añadido." + aviso, nombre, id=nuevo_id)

    def agregar_varios(
        self,
        rutas,
        progreso: Optional[Callable[[int, int, str], bool]] = None,
    ) -> List[ResultadoAlta]:
        """
        progreso(hechos, total, nombre) -> False para cancelar.
        """
        rutas = [Path(r) for r in rutas]
        resultados: List[ResultadoAlta] = []
        for i, ruta in enumerate(rutas):
            if progreso is not None and progreso(i, len(rutas), ruta.name) is False:
                break
            resultados.append(self.agregar_archivo(ruta))
        if progreso is not None:
            progreso(len(resultados), len(rutas), "")
        return resultados

    def agregar_carpeta(self, carpeta, progreso=None) -> List[ResultadoAlta]:
        carpeta = Path(carpeta)
        rutas = sorted(
            p for p in carpeta.rglob("*")
            if p.is_file()
            and p.suffix.lower() in EXTENSIONES_SOPORTADAS + tuple(EXTENSIONES_ANTIGUAS)
        )
        return self.agregar_varios(rutas, progreso)

    # ---------------- puntuación ----------------

    def _recalcular_hilo(self, hilo: str) -> None:
        filas = self._con.execute(
            "SELECT * FROM docs WHERE hilo=?", (hilo,)
        ).fetchall()
        n = len(filas)
        for f in filas:
            puntos, motivos = puntuar(
                f["tipo"], f["fecha_doc"], f["texto"], f["remitente"],
                f["titulo"], f["adjuntos"], n,
            )
            self._con.execute(
                "UPDATE docs SET importancia=?, motivos=? WHERE id=?",
                (puntos, motivos, f["id"]),
            )

    def recalcular_todo(self) -> None:
        for fila in self._con.execute("SELECT DISTINCT hilo FROM docs").fetchall():
            self._recalcular_hilo(fila["hilo"])
        self._con.commit()

    # ---------------- consultas ----------------

    @staticmethod
    def _a_resultado(f, fragmento: str = "") -> Resultado:
        if not fragmento:
            fragmento = (f["texto"] or "")[:200].replace("\n", " ")
        return Resultado(
            id=f["id"], titulo=f["titulo"], tipo=f["tipo"], fecha=f["fecha_doc"],
            remitente=f["remitente"], importancia=f["importancia"],
            estado=f["estado"], fragmento=fragmento,
            adjuntos=f["adjuntos"], motivos=f["motivos"],
            categoria=f["categoria"],
        )

    @staticmethod
    def _condicion_filtro(filtro: str):
        if filtro == "archivados":
            return "d.estado = 'ARCHIVADO'"
        base = "d.estado <> 'ARCHIVADO'"
        extra = {
            "correos": " AND d.tipo = 'correo'",
            "documentos": " AND d.tipo <> 'correo'",
            "destacados": " AND d.estado = 'DESTACADO'",
        }.get(filtro, "")
        return base + extra

    def listar(self, filtro: str = "todos", limite: int = 500) -> List[Resultado]:
        if filtro == "sugeridos":
            return self.sugeridos(limite)
        filas = self._con.execute(
            f"SELECT d.* FROM docs d WHERE {self._condicion_filtro(filtro)}"
            " ORDER BY (d.estado='DESTACADO') DESC, d.fecha_doc DESC, d.id DESC"
            " LIMIT ?",
            (limite,),
        ).fetchall()
        return [self._a_resultado(f) for f in filas]

    def sugeridos(self, n: int = 15) -> List[Resultado]:
        """Los más importantes, uno por hilo (el más reciente del hilo)."""
        filas = self._con.execute(
            "SELECT d.* FROM docs d WHERE d.estado <> 'ARCHIVADO'"
            " ORDER BY (d.estado='DESTACADO') DESC, d.importancia DESC,"
            " d.fecha_doc DESC"
        ).fetchall()
        vistos = set()
        salida: List[Resultado] = []
        for f in filas:
            if f["hilo"] in vistos:
                continue
            vistos.add(f["hilo"])
            salida.append(self._a_resultado(f))
            if len(salida) >= n:
                break
        return salida

    @staticmethod
    def _palabras(consulta: str) -> List[str]:
        return [
            p for p in re.findall(r"\w+", _normalizar(consulta))
            if len(p) > 1
        ]

    def buscar(
        self, consulta: str, filtro: str = "todos", limite: int = 50
    ) -> List[Resultado]:
        palabras = self._palabras(consulta)
        if not palabras:
            return self.listar(filtro, limite)

        cond = self._condicion_filtro(filtro)

        if self._fts:
            for union in (" ", " OR "):
                expr = union.join(f'"{p}"*' for p in palabras)
                try:
                    filas = self._con.execute(
                        "SELECT d.*, snippet(docs_fts, 1, '«', '»', '…', 18) AS frag"
                        " FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid"
                        f" WHERE docs_fts MATCH ? AND {cond}"
                        " ORDER BY bm25(docs_fts, 6.0, 1.0, 2.0)"
                        " - 0.04 * d.importancia - (CASE d.estado WHEN 'DESTACADO'"
                        " THEN 2 ELSE 0 END)"
                        " LIMIT ?",
                        (expr, limite),
                    ).fetchall()
                except sqlite3.OperationalError:
                    filas = []
                if filas or len(palabras) == 1:
                    break
            return [self._a_resultado(f, (f["frag"] or "").replace("\n", " "))
                    for f in filas]

        # Sin FTS5: búsqueda simple (todas las palabras)
        filas = self._con.execute(
            f"SELECT d.* FROM docs d WHERE {cond}"
        ).fetchall()
        salida = []
        for f in filas:
            conjunto = _normalizar(f"{f['titulo']} {f['texto']} {f['remitente']}")
            if all(p in conjunto for p in palabras):
                salida.append(self._a_resultado(f))
        salida.sort(key=lambda r: r.importancia, reverse=True)
        return salida[:limite]

    def respuesta_rapida(self, pregunta: str, n: int = 3) -> List[Resultado]:
        """
        Para el cuadro de preguntas de Inicio: los n mejores resultados para
        una pregunta escrita con frases normales ("¿cómo hago un pedido de
        frozen?"). Quita palabras vacías, simplifica plurales y solo devuelve
        documentos que contengan todas las palabras útiles (si son 1-2) o al
        menos el 60 % (si son más).
        """
        palabras = palabras_utiles(pregunta)
        if not palabras:
            return []
        candidatos = self.buscar(" ".join(palabras), "todos", 25)
        n_pal = len(palabras)
        minimo = n_pal if n_pal <= 2 else math.ceil(n_pal * 0.6)
        salida: List[Resultado] = []
        for r in candidatos:
            ficha = self.obtener(r.id)
            conjunto = _normalizar(f"{ficha['titulo']} {ficha['texto']}")
            aciertos = sum(
                1 for p in palabras
                if re.search(r"(?<!\w)" + re.escape(p), conjunto)
            )
            if aciertos >= minimo:
                salida.append(r)
            if len(salida) >= n:
                break
        return salida

    # ---------------- ficha y cambios ----------------

    def obtener(self, doc_id: int) -> Optional[Dict]:
        f = self._con.execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
        return dict(f) if f else None

    def marcar(self, doc_id: int, estado: str) -> None:
        if estado not in ESTADOS:
            raise ValueError(f"Estado no válido: {estado}")
        self._con.execute("UPDATE docs SET estado=? WHERE id=?", (estado, doc_id))
        self._con.commit()

    def eliminar(self, doc_id: int) -> None:
        f = self._con.execute(
            "SELECT ruta_copia, hilo FROM docs WHERE id=?", (doc_id,)
        ).fetchone()
        if not f:
            return
        self._con.execute("DELETE FROM docs WHERE id=?", (doc_id,))
        if self._fts:
            self._con.execute("DELETE FROM docs_fts WHERE rowid=?", (doc_id,))
        self._recalcular_hilo(f["hilo"])
        self._con.commit()
        try:
            Path(f["ruta_copia"]).unlink(missing_ok=True)
        except Exception:
            pass

    def estadisticas(self) -> Dict[str, int]:
        c = self._con.execute(
            "SELECT COUNT(*) total,"
            " SUM(tipo='correo') correos,"
            " SUM(tipo<>'correo') documentos,"
            " SUM(estado='ARCHIVADO') archivados,"
            " SUM(estado='DESTACADO') destacados FROM docs"
        ).fetchone()
        return {k: int(c[k] or 0) for k in c.keys()}