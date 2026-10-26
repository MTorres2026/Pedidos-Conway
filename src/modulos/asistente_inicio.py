"""
Motor del Asistente Híbrido de Inicio para CineStock (sin dependencias de Qt).

Cómo funciona:
  1) Analiza la pregunta en lenguaje natural (con memoria de seguimiento en el
     chat y sinónimos operativos de cine: inventario, conciliación, mermas,
     albarán, ERP, matinales…).
  2) Busca primero en procedimientos confirmados (conocimiento.json) evitando
     falsos positivos por verbos genéricos ("hacer", "cambiar"…).
  3) Busca en la Biblioteca de Conocimiento (PDF, Word, PowerPoint, Excel,
     correos .eml), divide cada manual por secciones/diapositivas/pasos y
     extrae el bloque completo que responde a la pregunta.
  4) Opcionalmente, si hay OPENAI_API_KEY o GEMINI_API_KEY configurada en .env
     y el modo IA está activo, redacta una explicación paso a paso basada
     ESTRÍCTAMENTE en esos fragmentos (con caída automática al modo local si no
     hay red o falla la llamada).
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .conocimiento import cargar_conocimiento


# ======================================================================
# Normalización, raíces y sinónimos operativos de cine
# ======================================================================

PALABRAS_GENERICAS_PREGUNTA = frozenset("""
a al algo algun alguna algunas alguno algunos ante ayer bien buenas buenos
cada casi cine cines cinestock como con conmigo conocer consulta consultar
contesta contestar correo correos cosa cosas cual cuales cuando darme de
debe debemos deberia debo decir decirme del desde despues dime donde duda
dudas ejemplo el ella ellas ellos en entonces entre era es esa esas ese
eso esos esta estaba estan estas este esto estos explicar explicame explica
favor forma formas funciona funcionar gracias gustaria haber habia hace
hacemos hacen hacer haces hacia hago han hasta hay hecho hola igual indica
indicarme info informacion las le les lo los manera maneras manual manuales
mas me mi mientras mis modo mostrar muestra muy necesito nos nosotros nueva
nuevo o otra otras otro otros para paso pasos pero pide pedir poco podemos
podria por porque pregunta preguntar proceso procesos procedimiento
procedimientos puede pueden puedo que quien quienes quiero saber sale salen
se segun ser si siempre sin sobre solo su sus tal tales tambien te tenemos
tengo tiene tienen toda todas todo todos tras tu tus un una unas uno unos
usar uso va vamos van ver vez veces viendo visto ya yo
""".split())

PALABRAS_SEGUIMIENTO = frozenset("""
advertencia advertencias atencion comprobar comprobacion comprobaciones
cuidado despues detalle detalles diferencia diferencias ejemplo error
falla fallo importante nota notas ojo pasa precaucion problema revisar
revision siguiente siguientes verificar
""".split())

# Sinónimos unidireccionales/bidireccionales del día a día del cine.
GRUPOS_SINONIMOS: Tuple[Tuple[str, ...], ...] = (
    ("inventario", "conteo", "recuento", "stock", "existencia"),
    ("conciliacion", "conciliar", "cuadre", "cuadrar", "regularizacion", "descuadre", "diferencia"),
    ("merma", "baja", "caducidad", "caducado", "rotura", "desperdicio", "tirar"),
    ("recepcion", "recepcionar", "albaran", "entrega", "mercancia"),
    ("pedido", "solicitud", "erp", "peoplesoft", "orden"),
    ("devolucion", "abono", "incidencia", "reclamacion"),
    ("traspaso", "traspasar", "transferencia", "prestamo"),
    ("matinal", "colegio", "escolar", "instituto", "grupo"),
    ("programacion", "sala", "horario", "pase", "sesion", "cinema manager"),
    ("taquilla", "entrada", "vista", "devolver entrada", "reembolso"),
    ("cierre", "apertura", "arqueo", "caja", "recaudacion"),
    ("horno", "merrychef", "cocina", "temperatura", "limpieza"),
)


def sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def normalizar(texto: str) -> str:
    return sin_acentos(texto).lower()


def _simplificar_termino(palabra: str) -> str:
    """Reduce plurales e infinitivos frecuentes en español sin romper siglas."""
    p = normalizar(palabra).strip()
    if len(p) <= 3:
        return p
    if p.endswith("ciones") and len(p) > 7:
        return p[:-6] + "cion"
    if p.endswith("siones") and len(p) > 7:
        return p[:-6] + "sion"
    if p.endswith("ces") and len(p) > 5:
        return p[:-3] + "z"
    if p.endswith("es") and len(p) > 5 and p[-3] not in "aeiou":
        return p[:-2]
    if p.endswith("s") and len(p) > 4 and not p.endswith(("ss", "us", "is")):
        return p[:-1]
    return p


def _raiz_busqueda(termino: str) -> str:
    """Raíz común para casar verbo y sustantivo (conciliar <-> conciliación)."""
    t = _simplificar_termino(termino)
    for sufijo, corte in (
        ("acion", 5),
        ("icion", 5),
        ("imiento", 7),
        ("amiento", 7),
        ("ando", 4),
        ("iendo", 5),
    ):
        if t.endswith(sufijo) and len(t) - corte >= 4:
            return t[:-corte]
    if len(t) >= 6 and t.endswith(("ar", "er", "ir")):
        return t[:-2]
    return t


def extraer_terminos_consulta(
    pregunta: str,
    historial_preguntas: Optional[Sequence[str]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Devuelve (terminos_principales, terminos_sinonimos).
    Si la pregunta es de seguimiento ("¿y si es de Disney?", "¿qué debo comprobar?"),
    incorpora el tema de la pregunta inmediatamente anterior.
    """
    tokens = [
        _simplificar_termino(t)
        for t in re.findall(r"[a-zA-Z0-9áéíóúüñÁÉÍÓÚÜÑ]+", pregunta or "")
    ]
    principales: List[str] = []
    for t in tokens:
        if len(t) < 3 and t not in ("oc", "id", "3d", "qr"):
            continue
        if t in PALABRAS_GENERICAS_PREGUNTA:
            continue
        if t not in principales:
            principales.append(t)

    # Detectar pregunta de continuación en el chat
    texto_norm = normalizar(pregunta or "").strip(" ¿?¡!.,;:")
    es_continuacion = bool(
        re.match(r"^(y\b|pero\b|entonces\b|ademas\b|tambien\b|o\b|que hay que\b)", texto_norm)
    )
    solo_seguimiento = principales and all(p in PALABRAS_SEGUIMIENTO for p in principales)

    if historial_preguntas and (not principales or solo_seguimiento or (es_continuacion and len(principales) <= 2)):
        for anterior in reversed(historial_preguntas):
            prev_principales, _ = extraer_terminos_consulta(anterior, None)
            prev_temas = [p for p in prev_principales if p not in PALABRAS_SEGUIMIENTO]
            if prev_temas:
                for p in prev_temas[:3]:
                    if p not in principales:
                        principales.append(p)
                break

    # Si el usuario escribió únicamente palabras genéricas, rescatamos las de >= 4 letras
    if not principales:
        for t in tokens:
            if len(t) >= 4 and t not in principales:
                principales.append(t)

    # Expandir sinónimos de dominio
    sinonimos: List[str] = []
    raices_principales = {_raiz_busqueda(p) for p in principales}
    for grupo in GRUPOS_SINONIMOS:
        grupo_norm = [_simplificar_termino(x) for x in grupo]
        coincide = any(
            p in grupo_norm or any(_raiz_busqueda(g) == _raiz_busqueda(p) for g in grupo_norm)
            for p in principales
        )
        if coincide:
            for candidato in grupo_norm:
                for sub in candidato.split():
                    if len(sub) >= 3 and _raiz_busqueda(sub) not in raices_principales and sub not in sinonimos:
                        sinonimos.append(sub)

    return principales, sinonimos


# ======================================================================
# Modelos de datos de salida
# ======================================================================

@dataclass
class ProcedimientoCoincidente:
    id: str
    titulo: str
    categoria: str
    sistema: str
    objetivo: str
    pasos: List[str] = field(default_factory=list)
    comprobaciones: List[str] = field(default_factory=list)
    advertencias: List[str] = field(default_factory=list)
    respuesta_adaptada: str = ""
    ultima_revision: str = ""
    fuente_descripcion: str = ""


@dataclass
class PasajeManual:
    doc_id: int
    titulo_documento: str
    tipo_documento: str
    fecha: str
    categoria: str
    remitente: str
    estado: str
    titulo_seccion: str
    texto_seccion: str
    puntuacion: float


@dataclass
class RespuestaAsistente:
    pregunta: str
    terminos: List[str] = field(default_factory=list)
    procedimiento: Optional[ProcedimientoCoincidente] = None
    pasajes: List[PasajeManual] = field(default_factory=list)
    texto_ia: str = ""
    proveedor_ia: str = ""
    aviso_ia: str = ""
    biblioteca_vacia: bool = False
    total_documentos: int = 0
    error_biblioteca: Optional[str] = None

    @property
    def tiene_contenido(self) -> bool:
        return self.procedimiento is not None or bool(self.pasajes)


# ======================================================================
# 1) Búsqueda precisa en procedimientos confirmados (conocimiento.json)
# ======================================================================

def _cargar_lista_procedimientos() -> List[Dict]:
    datos = cargar_conocimiento()
    lista = datos.get("procedimientos", [])
    if lista:
        return lista
    # Respaldo directo por si solo se actualizaron asistente_inicio.py + inicio.py
    ruta_modulo = Path(__file__).resolve()
    for candidata in (
        ruta_modulo.parent.parent.parent / "data" / "conocimiento.json",
        ruta_modulo.parent.parent / "datos" / "conocimiento.json",
    ):
        if candidata.is_file():
            try:
                return json.loads(candidata.read_text(encoding="utf-8")).get("procedimientos", [])
            except Exception:
                continue
    return []


def buscar_procedimiento_confirmado(
    pregunta: str,
    terminos_principales: Optional[Sequence[str]] = None,
) -> Optional[ProcedimientoCoincidente]:
    """
    Busca un procedimiento oficial en conocimiento.json exigiendo coincidencia
    temática real (evita falsos positivos por verbos sueltos como 'hacer' o 'cambiar').
    """
    if not pregunta or not pregunta.strip():
        return None

    procedimientos = _cargar_lista_procedimientos()
    if not procedimientos:
        return None

    limpia = re.sub(r"[¿?¡!.,;:]", " ", normalizar(pregunta))
    limpia = re.sub(r"\s+", " ", limpia).strip()

    if terminos_principales is None:
        terminos_principales, _ = extraer_terminos_consulta(pregunta)

    # Palabras demasiado genéricas por sí solas para disparar un procedimiento
    verbos_ambiguos = {"cambio", "cambiar", "mover", "muevo", "gestion", "gestionar", "tramitar"}
    terminos_fuertes = [
        t for t in terminos_principales
        if t not in verbos_ambiguos and t not in PALABRAS_SEGUIMIENTO
    ]

    mejor_proc: Optional[Dict] = None
    mejor_pts = 0.0

    for proc in procedimientos:
        pts = 0.0
        preguntas_clave = [
            re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;:]", " ", normalizar(pk))).strip()
            for pk in proc.get("preguntas_clave", [])
        ]

        # 1. Coincidencia directa con preguntas clave
        for pk in preguntas_clave:
            if not pk:
                continue
            if pk in limpia or (len(limpia) >= 12 and limpia in pk):
                pts += 25.0
                break

        cabecera_norm = normalizar(" ".join([
            proc.get("titulo", ""),
            proc.get("categoria", ""),
            proc.get("sistema", ""),
            " ".join(proc.get("preguntas_clave", [])),
        ]))
        cuerpo_norm = normalizar(" ".join([
            proc.get("objetivo", ""),
            proc.get("respuesta", ""),
            " ".join(proc.get("pasos", [])),
            " ".join(proc.get("comprobaciones", [])),
            " ".join(proc.get("advertencias", [])),
        ]))

        aciertos_fuertes_cabecera = 0
        aciertos_totales = 0

        for t in terminos_principales:
            raiz = re.escape(_raiz_busqueda(t))
            patron = re.compile(r"(?<!\w)" + raiz)
            en_cab = bool(patron.search(cabecera_norm))
            en_cuerpo = bool(patron.search(cuerpo_norm))
            if en_cab or en_cuerpo:
                aciertos_totales += 1
                pts += 8.0 if en_cab else 3.0
                if t in terminos_fuertes and en_cab:
                    aciertos_fuertes_cabecera += 1
                    pts += 5.0

        # Guardia antifalsos positivos: si no casó una pregunta clave entera,
        # exigimos al menos un término específico del tema en cabecera o >=2 términos reales.
        if pts < 25.0:
            if terminos_fuertes and aciertos_fuertes_cabecera == 0 and aciertos_totales < 2:
                continue
            if not terminos_fuertes and aciertos_totales < 2:
                continue
            if len(terminos_fuertes) >= 2 and aciertos_totales / len(terminos_principales) < 0.5:
                continue

        if pts > mejor_pts and pts >= 11.0:
            mejor_pts = pts
            mejor_proc = proc

    if not mejor_proc:
        return None

    fuente = mejor_proc.get("fuente") or {}
    return ProcedimientoCoincidente(
        id=mejor_proc.get("id", ""),
        titulo=mejor_proc.get("titulo", "Procedimiento confirmado"),
        categoria=mejor_proc.get("categoria", ""),
        sistema=mejor_proc.get("sistema", ""),
        objetivo=mejor_proc.get("objetivo", ""),
        pasos=list(mejor_proc.get("pasos", [])),
        comprobaciones=list(mejor_proc.get("comprobaciones", [])),
        advertencias=list(mejor_proc.get("advertencias", [])),
        respuesta_adaptada=mejor_proc.get("respuesta", ""),
        ultima_revision=mejor_proc.get("ultima_revision", ""),
        fuente_descripcion=fuente.get("descripcion", "") if isinstance(fuente, dict) else "",
    )


# ======================================================================
# 2) División inteligente de manuales en secciones / diapositivas / pasos
# ======================================================================

_RX_DIAPOSITIVA = re.compile(r"^---\s*Diapositiva\s+(\d+)\s*---\s*$", re.IGNORECASE)
_RX_ADJUNTO = re.compile(r"^\[(Adjunto|Archivo|Imagen):\s*([^\]]+)\]\s*$", re.IGNORECASE)
_RX_ENCABEZADO_MD = re.compile(r"^(#{1,4})\s+(.+)$")
_RX_PASO_LISTA = re.compile(
    r"^\s*(?:[-•*▪▸]|\d{1,2}[.)]\s+|paso\s+\d+\b)", re.IGNORECASE
)
_RX_TITULO_SECCION = re.compile(
    r"^(?:(?:apartado|seccion|sección|bloque|capitulo|capítulo|tema)\s+\d+[:.\-\s]+.+|"
    r"\d{1,2}(?:\.\d{1,2})+\s+\S.+)$",
    re.IGNORECASE,
)

MAX_CHARS_SECCION = 1450
MIN_CHARS_FUSION = 110


def _es_linea_encabezado(linea: str, linea_anterior_vacia: bool) -> Optional[str]:
    """Si la línea abre una sección nueva del manual, devuelve su título limpio."""
    s = linea.strip()
    if not s or len(s) > 95:
        return None

    m_diap = _RX_DIAPOSITIVA.match(s)
    if m_diap:
        return f"Diapositiva {m_diap.group(1)}"

    m_adj = _RX_ADJUNTO.match(s)
    if m_adj:
        tipo_adj, nombre_adj = m_adj.group(1).capitalize(), m_adj.group(2).strip()
        return f"{tipo_adj}: {nombre_adj}"

    m_md = _RX_ENCABEZADO_MD.match(s)
    if m_md:
        return m_md.group(2).strip()

    if not linea_anterior_vacia:
        return None

    if _RX_TITULO_SECCION.match(s):
        return s

    # Línea corta en MAYÚSCULAS que actúa como epígrafe de manual (ej. "CONCILIACIÓN DE STOCK")
    letras = [c for c in s if c.isalpha()]
    if 5 <= len(letras) <= 65 and len(s) <= 75 and not s.endswith((",", ";", ".")):
        mayus = sum(1 for c in letras if c.isupper())
        if mayus == len(letras):
            return s

    return None


def dividir_documento_en_secciones(texto: str, titulo_doc: str = "") -> List[Tuple[str, str]]:
    """
    Divide el texto completo de un manual o correo en tuplas (titulo_seccion, contenido)
    respetando diapositivas, adjuntos, encabezados y listas de pasos numerados.
    """
    texto_limpio = (texto or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not texto_limpio:
        return []

    lineas = texto_limpio.split("\n")
    secciones_brutas: List[Tuple[str, List[str]]] = []
    titulo_actual = ""
    acumulado: List[str] = []
    anterior_vacia = True

    for linea in lineas:
        posible_titulo = _es_linea_encabezado(linea, anterior_vacia)
        if posible_titulo is not None:
            cuerpo_previo = "\n".join(acumulado).strip()
            if cuerpo_previo:
                secciones_brutas.append((titulo_actual, acumulado))
                acumulado = []
            titulo_actual = posible_titulo
            anterior_vacia = True
            continue

        acumulado.append(linea)
        anterior_vacia = not linea.strip()

    if "\n".join(acumulado).strip():
        secciones_brutas.append((titulo_actual, acumulado))

    # Subdividir secciones muy largas por párrafos sin cortar listas de pasos
    resultado: List[Tuple[str, str]] = []
    for tit, lns in secciones_brutas:
        bloque_texto = "\n".join(lns).strip()
        if len(bloque_texto) <= MAX_CHARS_SECCION:
            resultado.append((tit, bloque_texto))
            continue

        parrafos = [p.strip() for p in re.split(r"\n{2,}", bloque_texto) if p.strip()]
        ventana: List[str] = []
        largo_ventana = 0
        parte_num = 1

        for idx, par in enumerate(parrafos):
            es_continuacion_lista = (
                ventana
                and _RX_PASO_LISTA.match(par)
                and largo_ventana + len(par) <= MAX_CHARS_SECCION + 450
            )
            if ventana and largo_ventana + len(par) > MAX_CHARS_SECCION and not es_continuacion_lista:
                subtitulo = f"{tit} (parte {parte_num})" if tit and parte_num > 1 else tit
                resultado.append((subtitulo, "\n\n".join(ventana)))
                parte_num += 1
                # Conservar última frase corta si terminaba en ":" (introducción de pasos)
                if ventana[-1].rstrip().endswith(":") and len(ventana[-1]) < 160:
                    ventana = [ventana[-1], par]
                else:
                    ventana = [par]
                largo_ventana = sum(len(x) for x in ventana)
            else:
                ventana.append(par)
                largo_ventana += len(par) + 2

        if ventana:
            subtitulo = f"{tit} (parte {parte_num})" if tit and parte_num > 1 else tit
            resultado.append((subtitulo, "\n\n".join(ventana)))

    # Fusionar encabezados huérfanos muy cortos solo cuando el bloque siguiente
    # pertenece a la misma sección (no cuando abre otra diapositiva/apartado con título propio)
    fusionadas: List[Tuple[str, str]] = []
    i = 0
    while i < len(resultado):
        tit, cont = resultado[i]
        if (
            len(cont) < MIN_CHARS_FUSION
            and i + 1 < len(resultado)
            and not resultado[i + 1][0]
            and len(cont) + len(resultado[i + 1][1]) <= MAX_CHARS_SECCION + 200
        ):
            _, sig_cont = resultado[i + 1]
            fusionadas.append((tit, f"{cont}\n\n{sig_cont}"))
            i += 2
        else:
            fusionadas.append((tit, cont))
            i += 1

    return fusionadas


# ======================================================================
# 3) Puntuación y selección de los mejores apartados en Biblioteca
# ======================================================================

def _bono_recencia(fecha_iso: str) -> float:
    if not fecha_iso or len(fecha_iso) < 4:
        return 0.0
    try:
        anio = int(fecha_iso[:4])
        actual = date.today().year
        diferencia = max(0, actual - anio)
        return max(0.0, 3.0 - 0.8 * diferencia)
    except ValueError:
        return 0.0


def _puntuar_seccion(
    titulo_doc: str,
    titulo_seccion: str,
    texto_seccion: str,
    principales: Sequence[str],
    sinonimos: Sequence[str],
    estado_doc: str = "NORMAL",
    importancia_doc: float = 0.0,
    fecha_doc: str = "",
) -> Tuple[float, int]:
    """Devuelve (puntuacion_total, numero_terminos_principales_acertados)."""
    norm_tit_doc = normalizar(titulo_doc)
    norm_tit_sec = normalizar(titulo_seccion)
    norm_cuerpo = normalizar(texto_seccion)
    norm_inicio = norm_cuerpo[:180]

    pts = 0.0
    aciertos_principales = 0
    posiciones_aciertos: List[int] = []

    for t in principales:
        raiz = re.escape(_raiz_busqueda(t))
        rx = re.compile(r"(?<!\w)" + raiz)

        m_cuerpo = list(rx.finditer(norm_cuerpo))
        en_tit_doc = bool(rx.search(norm_tit_doc))
        en_tit_sec = bool(rx.search(norm_tit_sec))

        if m_cuerpo or en_tit_sec or en_tit_doc:
            aciertos_principales += 1
            if m_cuerpo:
                pts += 12.0 + min(len(m_cuerpo) - 1, 3) * 1.8
                posiciones_aciertos.append(m_cuerpo[0].start())
            if en_tit_sec:
                pts += 9.0
            if en_tit_doc:
                pts += 6.5
            if rx.search(norm_inicio):
                pts += 3.0

    # Proximidad: dos términos de la consulta muy juntos en el mismo párrafo
    if len(posiciones_aciertos) >= 2:
        posiciones_aciertos.sort()
        min_dist = min(
            posiciones_aciertos[i + 1] - posiciones_aciertos[i]
            for i in range(len(posiciones_aciertos) - 1)
        )
        if min_dist <= 65:
            pts += 8.0
        elif min_dist <= 160:
            pts += 4.0

    # Sinónimos operativos
    aciertos_sin = 0
    for s in sinonimos:
        rx_s = re.compile(r"(?<!\w)" + re.escape(_raiz_busqueda(s)))
        if rx_s.search(norm_cuerpo) or rx_s.search(norm_tit_sec) or rx_s.search(norm_tit_doc):
            aciertos_sin += 1
            pts += 3.5

    if aciertos_principales == 0 and aciertos_sin == 0:
        return 0.0, 0

    # Riqueza procedimental (listas numeradas, viñetas, verbos de operativa)
    lineas_pasos = sum(
        1 for ln in texto_seccion.splitlines() if _RX_PASO_LISTA.match(ln)
    )
    if lineas_pasos >= 2:
        pts += min(lineas_pasos * 1.4, 7.0)

    if re.search(
        r"\b(entrar|entra|acceder|pulsar|pulsa|seleccionar|selecciona|guardar|"
        r"comprobar|revisar|verificar|pestana|menu|boton|obligatorio|importante)\b",
        norm_cuerpo,
    ):
        pts += 2.5

    if estado_doc == "DESTACADO":
        pts += 5.0
    pts += min(max(importancia_doc, 0.0) * 0.12, 4.5)
    pts += _bono_recencia(fecha_doc)

    return pts, aciertos_principales


def buscar_pasajes_biblioteca(
    biblioteca,
    pregunta: str,
    principales: Sequence[str],
    sinonimos: Sequence[str],
    max_documentos: int = 3,
) -> List[PasajeManual]:
    """
    Localiza los documentos más relevantes de la Biblioteca y extrae de cada
    uno el apartado/sección completo que mejor responde a la pregunta.
    """
    if biblioteca is None or (not principales and not sinonimos):
        return []

    candidatos_por_id: Dict[int, object] = {}

    # 1) Búsqueda por términos principales y raíces
    consultas_prueba: List[str] = []
    if principales:
        consultas_prueba.append(" ".join(principales))
        raices = list(dict.fromkeys(_raiz_busqueda(p) for p in principales if len(_raiz_busqueda(p)) >= 3))
        if raices and " ".join(raices) not in consultas_prueba:
            consultas_prueba.append(" ".join(raices))
    if sinonimos:
        ampliada = list(principales[:3]) + list(sinonimos[:4])
        consultas_prueba.append(" ".join(ampliada))

    for q in consultas_prueba:
        try:
            for r in biblioteca.buscar(q, "todos", 25):
                if r.id not in candidatos_por_id:
                    candidatos_por_id[r.id] = r
        except Exception:
            continue
        if len(candidatos_por_id) >= 15:
            break

    # 2) Respaldo si FTS5 no estuviera disponible y la consulta tiene varias palabras
    if not candidatos_por_id:
        try:
            for r in biblioteca.listar("todos", 250):
                candidatos_por_id[r.id] = r
        except Exception:
            return []

    n_princ = len(principales)
    minimo_principales = 1 if n_princ <= 2 else max(1, math.ceil(n_princ * 0.5))

    mejores_por_doc: List[Tuple[float, List[PasajeManual]]] = []

    for doc_id, resumen in candidatos_por_id.items():
        try:
            ficha = biblioteca.obtener(doc_id)
        except Exception:
            ficha = None
        if not ficha:
            continue

        texto_completo = (ficha.get("texto") or "").strip()
        titulo_doc = ficha.get("titulo") or getattr(resumen, "titulo", "") or "Documento"
        if not texto_completo:
            continue

        secciones = dividir_documento_en_secciones(texto_completo, titulo_doc)
        if not secciones:
            continue

        evaluadas: List[Tuple[float, int, int, str, str]] = []
        for idx, (tit_sec, txt_sec) in enumerate(secciones):
            pts, n_aciertos = _puntuar_seccion(
                titulo_doc=titulo_doc,
                titulo_seccion=tit_sec,
                texto_seccion=txt_sec,
                principales=principales,
                sinonimos=sinonimos,
                estado_doc=ficha.get("estado") or "NORMAL",
                importancia_doc=float(ficha.get("importancia") or 0.0),
                fecha_doc=ficha.get("fecha") or "",
            )
            if pts > 0:
                evaluadas.append((pts, n_aciertos, idx, tit_sec, txt_sec))

        if not evaluadas:
            continue

        evaluadas.sort(key=lambda x: x[0], reverse=True)
        mejor_pts, mejor_aciertos, mejor_idx, mejor_tit, mejor_txt = evaluadas[0]

        # Exigir cobertura mínima salvo que haya coincidencia clara por sinónimo operativo
        if n_princ > 0 and mejor_aciertos < minimo_principales and mejor_pts < 14.0:
            continue

        def _crear_pasaje(t_sec: str, c_sec: str, p_sec: float) -> PasajeManual:
            return PasajeManual(
                doc_id=doc_id,
                titulo_documento=titulo_doc,
                tipo_documento=ficha.get("tipo") or getattr(resumen, "tipo", "texto"),
                fecha=ficha.get("fecha") or "",
                categoria=ficha.get("categoria") or getattr(resumen, "categoria", ""),
                remitente=ficha.get("remitente") or "",
                estado=ficha.get("estado") or "NORMAL",
                titulo_seccion=t_sec,
                texto_seccion=c_sec,
                puntuacion=p_sec,
            )

        seleccion_doc = [_crear_pasaje(mejor_tit, mejor_txt, mejor_pts)]

        # Si el manual principal continúa la explicación en la sección contigua o tiene
        # otro apartado muy relevante, guardamos un segundo bloque candidato para el #1.
        if len(evaluadas) > 1:
            seg_pts, seg_aciertos, seg_idx, seg_tit, seg_txt = evaluadas[1]
            if seg_pts >= mejor_pts * 0.72 and (seg_aciertos >= 1 or abs(seg_idx - mejor_idx) == 1):
                seleccion_doc.append(_crear_pasaje(seg_tit, seg_txt, seg_pts))
                # Orden natural de lectura si son secciones del mismo manual
                if seg_idx < mejor_idx:
                    seleccion_doc.reverse()

        mejores_por_doc.append((mejor_pts, seleccion_doc))

    mejores_por_doc.sort(key=lambda par: par[0], reverse=True)

    salida: List[PasajeManual] = []
    for pos, (_, lista_pasajes) in enumerate(mejores_por_doc[:max_documentos]):
        if pos == 0:
            salida.extend(lista_pasajes[:2])
        else:
            salida.append(lista_pasajes[0])

    return salida


# ======================================================================
# 4) Consulta completa (local instantánea)
# ======================================================================

def consultar_base_local(
    pregunta: str,
    biblioteca=None,
    historial_preguntas: Optional[Sequence[str]] = None,
) -> RespuestaAsistente:
    """
    Ejecuta la búsqueda completa local (<10 ms, segura en el hilo principal de Qt).
    """
    terminos, sinonimos = extraer_terminos_consulta(pregunta, historial_preguntas)
    proc = buscar_procedimiento_confirmado(pregunta, terminos)

    pasajes: List[PasajeManual] = []
    vacia = False
    total_docs = 0
    error_bib: Optional[str] = None

    if biblioteca is not None:
        try:
            stats = biblioteca.estadisticas()
            total_docs = int(stats.get("total", 0))
            vacia = total_docs == 0
            if not vacia:
                pasajes = buscar_pasajes_biblioteca(
                    biblioteca, pregunta, terminos, sinonimos, max_documentos=3
                )
        except Exception as e:
            error_bib = str(e)

    return RespuestaAsistente(
        pregunta=pregunta,
        terminos=terminos,
        procedimiento=proc,
        pasajes=pasajes,
        biblioteca_vacia=vacia,
        total_documentos=total_docs,
        error_biblioteca=error_bib,
    )


# ======================================================================
# 5) Capa opcional de redacción con IA (OpenAI / Gemini vía urllib estándar)
# ======================================================================

def _leer_clave_env_local(nombre: str) -> str:
    valor = (os.environ.get(nombre) or "").strip()
    if valor:
        return valor
    ruta_env = Path(__file__).resolve().parents[2] / ".env"
    if not ruta_env.is_file():
        return ""
    try:
        for linea in ruta_env.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = linea.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            if k.strip() == nombre:
                return v.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def detectar_proveedor_ia() -> Optional[Tuple[str, str]]:
    """
    Devuelve ('OpenAI', api_key) o ('Gemini', api_key) si existe clave configurada,
    o None si CineStock funciona únicamente en modo local.
    """
    clave_openai = _leer_clave_env_local("OPENAI_API_KEY")
    if clave_openai:
        return ("OpenAI", clave_openai)
    clave_gemini = _leer_clave_env_local("GEMINI_API_KEY")
    if clave_gemini:
        return ("Gemini", clave_gemini)
    return None


def construir_contexto_rag(respuesta: RespuestaAsistente) -> str:
    bloques: List[str] = []

    if respuesta.procedimiento is not None:
        p = respuesta.procedimiento
        lineas = [f"[PROCEDIMIENTO CONFIRMADO: {p.titulo}]"]
        if p.objetivo:
            lineas.append(f"Objetivo: {p.objetivo}")
        if p.pasos:
            lineas.append("Pasos:\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(p.pasos, 1)))
        if p.comprobaciones:
            lineas.append("Comprobaciones:\n- " + "\n- ".join(p.comprobaciones))
        if p.advertencias:
            lineas.append("Advertencias:\n- " + "\n- ".join(p.advertencias))
        bloques.append("\n".join(lineas))

    for i, pasaje in enumerate(respuesta.pasajes, 1):
        meta = " | ".join(
            x for x in (
                pasaje.titulo_documento,
                f"Fecha: {pasaje.fecha}" if pasaje.fecha else "",
                pasaje.titulo_seccion,
            ) if x
        )
        bloques.append(f"[FUENTE {i}: {meta}]\n{pasaje.texto_seccion}")

    return "\n\n---\n\n".join(bloques)


_PROMPT_SISTEMA_RAG = (
    "Eres el asistente interno de operaciones de CineStock para el equipo del cine. "
    "Responde en español, de forma clara, directa y práctica.\n"
    "REGLAS ESTRICTAS:\n"
    "1. Basa tu respuesta ÚNICAMENTE en los procedimientos confirmados y fragmentos "
    "de manuales/correos incluidos en el CONTEXTO.\n"
    "2. NO inventes menús, códigos, teléfonos ni normas que no aparezcan en el CONTEXTO.\n"
    "3. Presenta los procesos en pasos numerados claros (1., 2., 3.) y destaca cualquier "
    "advertencia o comprobación importante.\n"
    "4. Indica entre corchetes el nombre del documento del que sale la información "
    "(por ejemplo: [Manual Inventario]).\n"
    "5. Si en el contexto hay documentos de distintas fechas que se contradicen, "
    "prioriza el más reciente y menciónalo brevemente.\n"
    "6. Si el contexto no contiene el detalle exacto que preguntan, dilo con honestidad "
    "y resume solo lo que sí figura en los documentos encontrados."
)


def sintetizar_con_ia(
    pregunta: str,
    contexto_rag: str,
    historial_turnos: Optional[Sequence[Tuple[str, str]]] = None,
    timeout: float = 14.0,
) -> Tuple[str, str, str]:
    """
    Función pura de red (segura para ejecutar en un hilo secundario).
    Devuelve (texto_generado, nombre_proveedor, mensaje_aviso_si_falla).
    Nunca lanza excepciones hacia la interfaz.
    """
    deteccion = detectar_proveedor_ia()
    if not deteccion or not contexto_rag.strip():
        return ("", "", "")

    proveedor, api_key = deteccion

    resumen_historial = ""
    if historial_turnos:
        ultimos = list(historial_turnos)[-2:]
        partes_h = [f"Usuario: {q}\nAsistente: {a[:280]}" for q, a in ultimos if q]
        if partes_h:
            resumen_historial = "CONVERSACIÓN PREVIA RECIENTE:\n" + "\n".join(partes_h) + "\n\n"

    mensaje_usuario = (
        f"{resumen_historial}"
        f"CONTEXTO DE MANUALES Y PROCEDIMIENTOS DE CINESTOCK:\n{contexto_rag}\n\n"
        f"PREGUNTA ACTUAL DEL COMPAÑERO:\n{pregunta}"
    )

    try:
        if proveedor == "OpenAI":
            modelo = _leer_clave_env_local("OPENAI_MODEL") or "gpt-4o-mini"
            cuerpo = json.dumps({
                "model": modelo,
                "temperature": 0.1,
                "max_tokens": 650,
                "messages": [
                    {"role": "system", "content": _PROMPT_SISTEMA_RAG},
                    {"role": "user", "content": mensaje_usuario},
                ],
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=cuerpo,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                datos = json.loads(resp.read().decode("utf-8"))
            texto = (
                datos.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            return (texto, f"OpenAI ({modelo})", "")

        if proveedor == "Gemini":
            modelo = _leer_clave_env_local("GEMINI_MODEL") or "gemini-2.5-flash"
            cuerpo = json.dumps({
                "system_instruction": {"parts": [{"text": _PROMPT_SISTEMA_RAG}]},
                "contents": [{"role": "user", "parts": [{"text": mensaje_usuario}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 700},
            }).encode("utf-8")
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{modelo}:generateContent"
            )
            req = urllib.request.Request(
                url,
                data=cuerpo,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                datos = json.loads(resp.read().decode("utf-8"))
            partes = (
                datos.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )
            texto = "".join(p.get("text", "") for p in partes).strip()
            return (texto, f"Gemini ({modelo})", "")

    except urllib.error.HTTPError as e:
        return ("", proveedor, f"IA no disponible temporalmente (HTTP {e.code}); mostrando tus manuales directos.")
    except Exception:
        return ("", proveedor, "Sin conexión con el servicio de IA; mostrando el apartado directo de tus manuales.")

    return ("", "", "")


# ======================================================================
# 6) Formateo HTML limpio para QTextBrowser (compatible con Qt RichText)
# ======================================================================

ICONOS_DOC = {
    "correo": "✉️ Correo",
    "pdf": "📕 PDF",
    "pptx": "📊 Presentación",
    "docx": "📘 Word",
    "xlsx": "📗 Excel",
    "texto": "📄 Documento",
    "zip": "🗜 ZIP",
    "imagen": "🖼 Imagen",
}


def resaltar_terminos_html(texto_plano: str, terminos: Sequence[str]) -> str:
    """Escapa HTML y resalta con fondo suave los términos buscados."""
    escapado = html.escape(texto_plano or "")
    raices = [
        re.escape(_raiz_busqueda(t))
        for t in terminos
        if len(_raiz_busqueda(t)) >= 3
    ]
    if not raices:
        return escapado

    # Mapear posiciones sobre versión sin acentos de la cadena escapada no es 1:1
    # si hubiera entidades (&amp;), así que resaltamos palabra por palabra.
    rx_raices = re.compile(r"^(?:" + "|".join(raices) + r")", re.IGNORECASE)

    def _reemplazar_palabra(match: re.Match) -> str:
        palabra = match.group(0)
        if rx_raices.match(normalizar(palabra)):
            return (
                '<span style="background-color:#FEF08A; color:#0F172A; '
                f'font-weight:600;">{palabra}</span>'
            )
        return palabra

    # Evitar tocar nombres de entidades HTML (&amp;, &lt;, &gt;, &quot;)
    partes = re.split(r"(&[a-zA-Z0-9#]+;)", escapado)
    for i in range(0, len(partes), 2):
        partes[i] = re.sub(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]{3,}", _reemplazar_palabra, partes[i])
    return "".join(partes)


def formatear_bloque_manual_html(texto: str, terminos: Sequence[str]) -> str:
    """Convierte el texto de un apartado de manual en párrafos y listas legibles."""
    lineas_html: List[str] = []
    for linea in (texto or "").splitlines():
        s = linea.strip()
        if not s:
            lineas_html.append('<div style="height:5px;"></div>')
            continue

        resaltada = resaltar_terminos_html(s, terminos)
        m_num = re.match(r"^(\d{1,2}[.)])\s+(.*)$", s)
        if m_num:
            num_esc = html.escape(m_num.group(1))
            resto = resaltar_terminos_html(m_num.group(2), terminos)
            lineas_html.append(
                f'<div style="margin:3px 0 3px 8px;">'
                f'<b style="color:#1D4ED8;">{num_esc}</b> {resto}</div>'
            )
        elif re.match(r"^[-•*▪▸]\s+", s):
            cuerpo_v = resaltar_terminos_html(re.sub(r"^[-•*▪▸]\s+", "", s), terminos)
            lineas_html.append(
                f'<div style="margin:3px 0 3px 10px;">'
                f'<span style="color:#1D4ED8; font-weight:bold;">•</span> {cuerpo_v}</div>'
            )
        else:
            lineas_html.append(f'<div style="margin:2px 0;">{resaltada}</div>')

    return "".join(lineas_html)


def formatear_markdown_ia_html(texto_ia: str) -> str:
    """Convierte el Markdown ligero devuelto por la IA en HTML limpio para Qt."""
    lineas_out: List[str] = []
    for cruda in (texto_ia or "").splitlines():
        s = cruda.strip()
        if not s:
            lineas_out.append('<div style="height:6px;"></div>')
            continue

        esc = html.escape(s)
        # Negrita **texto**
        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
        # Citas [Manual X] en azul suave
        esc = re.sub(
            r"\[([^\[\]]{2,80})\]",
            r'<span style="color:#1E40AF; font-weight:600;">[\1]</span>',
            esc,
        )

        if s.startswith("### "):
            lineas_out.append(
                f'<div style="margin-top:6px; font-weight:bold; color:#0F172A;">{esc[4:]}</div>'
            )
        elif s.startswith("## "):
            lineas_out.append(
                f'<div style="margin-top:7px; font-size:13px; font-weight:bold; color:#0F172A;">{esc[3:]}</div>'
            )
        elif re.match(r"^\d{1,2}[.)]\s+", s):
            m = re.match(r"^(\d{1,2}[.)])\s+(.*)$", esc)
            if m:
                lineas_out.append(
                    f'<div style="margin:3px 0 3px 8px;">'
                    f'<b style="color:#0F766E;">{m.group(1)}</b> {m.group(2)}</div>'
                )
            else:
                lineas_out.append(f'<div style="margin:2px 0;">{esc}</div>')
        elif re.match(r"^[-•*]\s+", s):
            cuerpo = re.sub(r"^[-•*]\s+", "", esc)
            lineas_out.append(
                f'<div style="margin:3px 0 3px 10px;">'
                f'<span style="color:#0F766E; font-weight:bold;">•</span> {cuerpo}</div>'
            )
        else:
            lineas_out.append(f'<div style="margin:2px 0;">{esc}</div>')

    return "".join(lineas_out)


def _etiqueta_antiguedad(fecha_iso: str) -> str:
    if not fecha_iso or len(fecha_iso) < 4:
        return ""
    try:
        anio = int(fecha_iso[:4])
        if date.today().year - anio >= 2:
            return (
                ' · <span style="color:#B45309; font-weight:600;">'
                "⚠ Documento antiguo: verifica vigencia</span>"
            )
    except ValueError:
        pass
    return ""


def renderizar_respuesta_asistente_html(
    respuesta: RespuestaAsistente,
    pensando_ia: bool = False,
) -> str:
    """Genera el contenido HTML de la burbuja de respuesta del asistente."""
    bloques: List[str] = []

    # A) Estado mientras la IA redacta en segundo plano
    if pensando_ia:
        bloques.append(
            '<table width="100%" cellpadding="8" cellspacing="0" bgcolor="#F0FDFA" '
            'style="border:1px solid #99F6E4; margin-bottom:8px;">'
            '<tr><td style="color:#0F766E; font-size:12.5px;">'
            "<b>🤖 Redactando respuesta paso a paso</b> a partir de tus manuales… "
            "<i>(debajo tienes ya localizados los apartados originales)</i>"
            "</td></tr></table>"
        )

    # B) Respuesta sintetizada por IA (si está disponible)
    if respuesta.texto_ia:
        cuerpo_ia = formatear_markdown_ia_html(respuesta.texto_ia)
        etiqueta_prov = html.escape(respuesta.proveedor_ia or "IA")
        bloques.append(
            '<table width="100%" cellpadding="10" cellspacing="0" bgcolor="#F0FDFA" '
            'style="border:1px solid #5EEAD4; margin-bottom:10px;">'
            '<tr><td>'
            '<div style="color:#0F766E; font-size:12px; font-weight:bold; margin-bottom:6px;">'
            f"🤖 RESPUESTA PASO A PASO · Basada en tus manuales ({etiqueta_prov})"
            "</div>"
            f'<div style="color:#0F172A; font-size:13px; line-height:1.45;">{cuerpo_ia}</div>'
            "</td></tr></table>"
        )
    elif respuesta.aviso_ia:
        bloques.append(
            f'<div style="color:#64748B; font-size:11.5px; margin-bottom:6px;">'
            f"ℹ️ {html.escape(respuesta.aviso_ia)}</div>"
        )

    # C) Procedimiento confirmado oficial (conocimiento.json)
    if respuesta.procedimiento is not None:
        p = respuesta.procedimiento
        partes_p = [
            '<div style="color:#15803D; font-size:12px; font-weight:bold; margin-bottom:4px;">'
            "✅ PROCEDIMIENTO CONFIRMADO EN CINESTOCK</div>",
            f'<div style="font-size:14px; font-weight:bold; color:#0F172A; margin-bottom:4px;">'
            f"{html.escape(p.titulo)}</div>",
        ]
        meta_p = " · ".join(x for x in (p.sistema, p.categoria, f"Rev. {p.ultima_revision}" if p.ultima_revision else "") if x)
        if meta_p:
            partes_p.append(f'<div style="color:#64748B; font-size:11.5px; margin-bottom:6px;">{html.escape(meta_p)}</div>')
        if p.objetivo:
            partes_p.append(f'<div style="color:#334155; margin-bottom:6px;"><i>{html.escape(p.objetivo)}</i></div>')
        if p.pasos:
            partes_p.append('<div style="font-weight:bold; color:#0F172A; margin-top:4px;">Pasos:</div>')
            for i, paso in enumerate(p.pasos, 1):
                partes_p.append(
                    f'<div style="margin:3px 0 3px 8px;">'
                    f'<b style="color:#15803D;">{i}.</b> {resaltar_terminos_html(paso, respuesta.terminos)}</div>'
                )
        if p.comprobaciones:
            partes_p.append('<div style="font-weight:bold; color:#0F172A; margin-top:6px;">Comprobaciones:</div>')
            for comp in p.comprobaciones:
                partes_p.append(
                    f'<div style="margin:2px 0 2px 10px;"><span style="color:#15803D;">✓</span> '
                    f"{resaltar_terminos_html(comp, respuesta.terminos)}</div>"
                )
        if p.advertencias:
            partes_p.append('<div style="font-weight:bold; color:#B45309; margin-top:6px;">⚠ Ten en cuenta:</div>')
            for adv in p.advertencias:
                partes_p.append(
                    f'<div style="margin:2px 0 2px 10px; color:#92400E;">• '
                    f"{resaltar_terminos_html(adv, respuesta.terminos)}</div>"
                )

        bloques.append(
            '<table width="100%" cellpadding="10" cellspacing="0" bgcolor="#F0FDF4" '
            'style="border:1px solid #86EFAC; margin-bottom:10px;">'
            f'<tr><td style="font-size:13px;">{"".join(partes_p)}</td></tr></table>'
        )

    # D) Apartados extraídos de manuales y correos en Conocimiento
    if respuesta.pasajes:
        encabezado_manuales = (
            "📚 FUENTES CONSULTADAS EN TU BIBLIOTECA"
            if respuesta.texto_ia
            else "📖 APARTADOS ENCONTRADOS EN TUS MANUALES Y CORREOS"
        )
        bloques.append(
            f'<div style="color:#1E3A8A; font-size:12px; font-weight:bold; '
            f'margin:6px 0 6px 2px;">{encabezado_manuales}</div>'
        )

        for pasaje in respuesta.pasajes:
            icono = ICONOS_DOC.get(pasaje.tipo_documento, "📄 Documento")
            detalles = [icono]
            if pasaje.fecha:
                detalles.append(f"Fecha: {html.escape(pasaje.fecha)}")
            if pasaje.categoria:
                detalles.append(html.escape(pasaje.categoria))
            if pasaje.estado == "DESTACADO":
                detalles.append("⭐ Destacado")

            linea_meta = " · ".join(detalles) + _etiqueta_antiguedad(pasaje.fecha)
            sub_sec = (
                f'<div style="color:#1D4ED8; font-weight:600; font-size:12px; margin:4px 0;">'
                f"📌 {html.escape(pasaje.titulo_seccion)}</div>"
                if pasaje.titulo_seccion
                else ""
            )
            cuerpo_html = formatear_bloque_manual_html(pasaje.texto_seccion, respuesta.terminos)

            bloques.append(
                '<table width="100%" cellpadding="9" cellspacing="0" bgcolor="#F8FAFC" '
                'style="border:1px solid #CBD5E1; margin-bottom:8px;">'
                "<tr><td>"
                f'<div style="font-size:13px; font-weight:bold; color:#0F172A;">'
                f"{html.escape(pasaje.titulo_documento)}</div>"
                f'<div style="font-size:11px; color:#64748B; margin-bottom:5px;">{linea_meta}</div>'
                f"{sub_sec}"
                f'<div style="font-size:12.5px; color:#1E293B; line-height:1.42; '
                f'background-color:#FFFFFF; padding:8px; border:1px solid #E2E8F0;">'
                f"{cuerpo_html}</div>"
                f'<div style="margin-top:6px;">'
                f'<a href="abrir:{pasaje.doc_id}" '
                f'style="color:#1D4ED8; font-weight:bold; text-decoration:none; font-size:12px;">'
                f"📂 Abrir documento original</a></div>"
                "</td></tr></table>"
            )

    # E) Sin coincidencias
    if not bloques:
        if respuesta.error_biblioteca:
            extra = (
                "<br><small style='color:#64748B;'>(La biblioteca de Conocimiento no está "
                "disponible en este momento.)</small>"
            )
        elif respuesta.biblioteca_vacia:
            extra = (
                "<br><br>Todavía no hay manuales ni correos cargados en <b>📚 Conocimiento</b>. "
                "Entra en esa pestaña, pulsa <b>➕ Añadir correos / documentos…</b> para meter "
                "los manuales del cine (PDF, Word, PowerPoint o correos) y podré responderte "
                "extrayendo directamente sus pasos."
            )
        else:
            extra = (
                "<br><small style='color:#475569;'>Prueba con palabras clave concretas del "
                "proceso (por ejemplo: <b>inventario</b>, <b>conciliación</b>, <b>mermas</b>, "
                "<b>albarán</b>, <b>matinal</b>) o añade el manual correspondiente en "
                "<b>📚 Conocimiento</b>.</small>"
            )
        return (
            '<div style="color:#334155; font-size:13px;">'
            "No he encontrado ningún procedimiento ni manual en tu biblioteca que hable "
            f"de esa consulta.{extra}</div>"
        )

    return "".join(bloques)
