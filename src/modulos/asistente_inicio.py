"""
Motor del Asistente Híbrido de Inicio para CineStock (sin dependencias de Qt).

Cómo funciona:
  1) Analiza la pregunta en lenguaje natural (con memoria de seguimiento en el
     chat y sinónimos operativos de cine: inventario, stock, conteo,
     conciliación, mermas, albarán, ERP, matinales…).
  2) Busca primero en procedimientos confirmados (conocimiento.json) evitando
     falsos positivos por verbos genéricos.
  3) Limpia el texto extraído de PDFs/Word corporativos (quita marcas de agua
     "Confidencial", cabeceras repetidas "ESP-... Página X de Y", cortes de
     línea a mitad de frase y espacios rotos en eñes/tildes), divide cada
     manual por epígrafes reales ("10. Cierre de Día", diapositivas, apartados)
     y distingue entre un apartado dedicado al tema y una simple mención de pasada.
  4) Si hay OPENAI_API_KEY o GEMINI_API_KEY activa, genera una guía tipo manual
     paso a paso filtrando cualquier ruido ajeno y avisando con honestidad si
     el manual específico aún no está subido a 📚 Conocimiento.
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import ssl
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

GRUPOS_SINONIMOS: Tuple[Tuple[str, ...], ...] = (
    ("inventario", "conteo", "recuento", "stock", "existencia", "toma fisica"),
    ("conciliacion", "conciliar", "cuadre", "cuadrar", "regularizacion", "descuadre", "variacion"),
    ("merma", "baja", "caducidad", "caducado", "rotura", "desperdicio"),
    ("recepcion", "recepcionar", "albaran", "entrega", "mercancia"),
    ("pedido", "solicitud", "erp", "peoplesoft", "orden"),
    ("devolucion", "abono", "incidencia", "reclamacion"),
    ("traspaso", "traspasar", "transferencia", "prestamo"),
    ("matinal", "colegio", "escolar", "instituto", "grupo"),
    ("programacion", "sala", "horario", "pase", "sesion", "cinema manager"),
    ("taquilla", "entrada", "vista", "reembolso"),
    ("cierre", "apertura", "arqueo", "caja", "recaudacion", "etv", "deposito"),
    ("horno", "merrychef", "cocina", "temperatura", "limpieza"),
)


def sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def normalizar(texto: str) -> str:
    return sin_acentos(texto).lower()


def _simplificar_termino(palabra: str) -> str:
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

    texto_norm = normalizar(pregunta or "").strip(" ¿?¡!.,;:")
    es_continuacion = bool(
        re.match(r"^(y\b|pero\b|entonces\b|ademas\b|tambien\b|o\b|que hay que\b)", texto_norm)
    )
    solo_seguimiento = principales and all(p in PALABRAS_SEGUIMIENTO for p in principales)

    if historial_preguntas and (
        not principales or solo_seguimiento or (es_continuacion and len(principales) <= 2)
    ):
        for anterior in reversed(historial_preguntas):
            prev_principales, _ = extraer_terminos_consulta(anterior, None)
            prev_temas = [p for p in prev_principales if p not in PALABRAS_SEGUIMIENTO]
            if prev_temas:
                for p in prev_temas[:3]:
                    if p not in principales:
                        principales.append(p)
                break

    if not principales:
        for t in tokens:
            if len(t) >= 4 and t not in principales:
                principales.append(t)

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
                    if (
                        len(sub) >= 3
                        and _raiz_busqueda(sub) not in raices_principales
                        and sub not in sinonimos
                    ):
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
    es_mencion_lateral: bool = False


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

    @property
    def solo_mencion_lateral(self) -> bool:
        return (
            self.procedimiento is None
            and bool(self.pasajes)
            and all(p.es_mencion_lateral for p in self.pasajes)
        )


# ======================================================================
# 1) Búsqueda precisa en procedimientos confirmados (conocimiento.json)
# ======================================================================

def _cargar_lista_procedimientos() -> List[Dict]:
    datos = cargar_conocimiento()
    lista = datos.get("procedimientos", [])
    if lista:
        return lista
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
    if not pregunta or not pregunta.strip():
        return None

    procedimientos = _cargar_lista_procedimientos()
    if not procedimientos:
        return None

    limpia = re.sub(r"[¿?¡!.,;:]", " ", normalizar(pregunta))
    limpia = re.sub(r"\s+", " ", limpia).strip()

    if terminos_principales is None:
        terminos_principales, _ = extraer_terminos_consulta(pregunta)

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

        for pk in preguntas_clave:
            if pk and (pk in limpia or (len(limpia) >= 12 and limpia in pk)):
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
# 2) Limpieza de PDFs corporativos y división precisa en secciones
# ======================================================================

_RX_DIAPOSITIVA = re.compile(r"^---\s*Diapositiva\s+(\d+)\s*---\s*$", re.IGNORECASE)
_RX_ADJUNTO = re.compile(r"^\[(Adjunto|Archivo|Imagen):\s*([^\]]+)\]\s*$", re.IGNORECASE)
_RX_ENCABEZADO_MD = re.compile(r"^(#{1,4})\s+(.+)$")
_RX_PASO_LISTA = re.compile(
    r"^\s*(?:[-•*▪▸]|\d{1,2}[.)]\s+|paso\s+\d+\b|nota\s*:|importante\s*:|atenci[oó]n\s*:)",
    re.IGNORECASE,
)

# Ruido típico de páginas PDF corporativas (marcas de agua, cabeceras/pies de página)
_RX_LINEA_RUIDO_PDF = re.compile(
    r"^(?:"
    r"confidencial(?:\s+uso\s+interno)?|"
    r"uso\s+exclusivo\s+interno|"
    r"p[aá]gina\s+\d+\s+de\s+\d+|"
    r"page\s+\d+\s+of\s+\d+|"
    r"[A-Z]{2,5}-[A-Z0-9]{2,6}-[A-Z0-9\-]+\s+.+\b(?:p[aá]gina|page)\s+\d+\s+(?:de|of)\s+\d+"
    r")\s*$",
    re.IGNORECASE,
)

# Verbos/inicios que delatan una instrucción o frase de paso ("4. Presionar el botón...")
# frente a un epígrafe de sección ("10. Cierre de Día")
_INICIOS_FRASE_PASO = frozenset("""
si en al el la los las un una para por con sin tras antes despues durante
cuando una vez ingresar ingresa presionar presiona verificar verifica
seleccionar selecciona pulsar pulsa entrar entra acceder accede hacer haz
comprobar comprueba revisar revisa introducir introduce escribir escribe
anotar anota imprimir imprime contar cuenta abrir abre cerrar cierra
guardar guarda validar valida marcar marca elegir elige colocar coloca
dar confirmar confirma esperar espera repetir repite asegurar asegurate
""".split())

MAX_CHARS_SECCION = 1400
MIN_CHARS_FUSION = 90


def limpiar_texto_manual(texto: str) -> str:
    """
    Limpia marcas de agua/pies de página de PDFs corporativos (ej. 'Confidencial',
    'ESP-TLC-... Página 21 de 29'), repara cortes de tildes/eñes ('contrase ña')
    y une saltos de línea artificiales a mitad de frase en PDFs.
    """
    if not texto:
        return ""

    t = texto.replace("\r\n", "\n").replace("\r", "\n")
    # Reparar espacios rotos junto a caracteres acentuados/ñ frecuentes en pypdf
    t = re.sub(r"([a-zA-ZáéíóúÁÉÍÓÚ])\s+(ñ|Ñ)([a-záéíóú])", r"\1\2\3", t)
    t = re.sub(r"\b([a-zA-Z])\s+([áéíóúÁÉÍÓÚ][a-z]{2,})", r"\1\2", t)

    lineas_filtradas: List[str] = []
    for cruda in t.split("\n"):
        s = re.sub(r"[ \t]+", " ", cruda).strip()
        if _RX_LINEA_RUIDO_PDF.match(s):
            continue
        # Eliminar pie incrustado al final de una línea
        s = re.sub(
            r"\s+[A-Z]{2,5}-[A-Z0-9]{2,6}-[A-Z0-9\-]+\s+.+\bP[aá]gina\s+\d+\s+de\s+\d+\s*$",
            "",
            s,
            flags=re.IGNORECASE,
        ).strip()
        lineas_filtradas.append(s)

    # Reflujo suave: unir líneas partidas a mitad de oración por el ancho del PDF
    reflujo: List[str] = []
    for s in lineas_filtradas:
        if not s:
            if reflujo and reflujo[-1] != "":
                reflujo.append("")
            continue

        if (
            reflujo
            and reflujo[-1] != ""
            and not reflujo[-1].endswith((".", ":", ";", "?", "!", "---", "]"))
            and not _RX_DIAPOSITIVA.match(reflujo[-1])
            and not _RX_ADJUNTO.match(reflujo[-1])
            and not _RX_ENCABEZADO_MD.match(reflujo[-1])
            and not _RX_PASO_LISTA.match(s)
            and not _parece_encabezado_aislado(reflujo[-1])
            and not _parece_encabezado_aislado(s)
            and (s[0].islower() or s[0] in "“\"'()0123456789€%")
        ):
            reflujo[-1] = reflujo[-1] + " " + s
        else:
            reflujo.append(s)

    return "\n".join(reflujo).strip()


def _parece_encabezado_aislado(linea: str) -> bool:
    """Detecta si una línea es un título de sección (incluso sin líneas en blanco en PDF/DOCX)."""
    s = linea.strip()
    if not s or len(s) > 88:
        return False
    if _RX_DIAPOSITIVA.match(s) or _RX_ADJUNTO.match(s) or _RX_ENCABEZADO_MD.match(s):
        return True

    # Numeración decimal: "2.1 Conciliación de stock", "3.2.1 Toma física"
    if re.match(r"^\d{1,2}(?:\.\d{1,2})+\.?\s+[A-ZÁÉÍÓÚÜÑ]\S.*$", s) and not s.endswith("."):
        return True

    # Prefijos explícitos de manual
    if re.match(
        r"^(?:apartado|secci[oó]n|bloque|cap[ií]tulo|anexo|procedimiento)\s+[A-Z0-9]+[:.\-\s]+.+$",
        s,
        re.IGNORECASE,
    ) and not s.endswith("."):
        return True

    # Epígrafe en MAYÚSCULAS ("PROCESO DE INVENTARIO SEMANAL")
    letras = [c for c in s if c.isalpha()]
    if 5 <= len(letras) <= 65 and len(s) <= 78 and not s.endswith((",", ";", ".")):
        if all(c.isupper() for c in letras):
            return True

    # Epígrafe numerado corto tipo manual ("10. Cierre de Día", "4. Toma de Inventario")
    # Se distingue de un paso ("4. Ingresar el folio...") porque NO acaba en punto/dos puntos,
    # es breve y no arranca con verbo imperativo/infinitivo ni condicional de frase.
    m_num = re.match(r"^(\d{1,2})\.\s+([A-ZÁÉÍÓÚÜÑ][^\n]+)$", s)
    if m_num and not s.endswith((".", ":", ";", ",")):
        resto = m_num.group(2).strip()
        palabras = resto.split()
        primera = normalizar(re.sub(r"[^\w]", "", palabras[0])) if palabras else ""
        dos_primeras = " ".join(normalizar(w) for w in palabras[:2])
        if (
            1 <= len(palabras) <= 8
            and len(resto) <= 58
            and primera not in _INICIOS_FRASE_PASO
            and dos_primeras not in _INICIOS_FRASE_PASO
        ):
            return True

    return False


def _extraer_titulo_encabezado(linea: str) -> Optional[str]:
    s = linea.strip()
    if not _parece_encabezado_aislado(s):
        return None
    m_diap = _RX_DIAPOSITIVA.match(s)
    if m_diap:
        return f"Diapositiva {m_diap.group(1)}"
    m_adj = _RX_ADJUNTO.match(s)
    if m_adj:
        return f"{m_adj.group(1).capitalize()}: {m_adj.group(2).strip()}"
    m_md = _RX_ENCABEZADO_MD.match(s)
    if m_md:
        return m_md.group(2).strip()
    return s


def dividir_documento_en_secciones(texto: str, titulo_doc: str = "") -> List[Tuple[str, str]]:
    """
    Limpia el documento y lo divide en tuplas (titulo_seccion, contenido)
    respetando epígrafes de PDFs/Word (incluso sin líneas en blanco), diapositivas y listas.
    """
    texto_limpio = limpiar_texto_manual(texto)
    if not texto_limpio:
        return []

    lineas = texto_limpio.split("\n")
    secciones_brutas: List[Tuple[str, List[str]]] = []
    titulo_actual = ""
    acumulado: List[str] = []

    for linea in lineas:
        posible_titulo = _extraer_titulo_encabezado(linea)
        if posible_titulo is not None:
            cuerpo_previo = "\n".join(acumulado).strip()
            if cuerpo_previo:
                secciones_brutas.append((titulo_actual, acumulado))
                acumulado = []
            titulo_actual = posible_titulo
            continue
        acumulado.append(linea)

    if "\n".join(acumulado).strip():
        secciones_brutas.append((titulo_actual, acumulado))

    # Subdividir bloques largos sin cortar listas de pasos
    resultado: List[Tuple[str, str]] = []
    for tit, lns in secciones_brutas:
        bloque_texto = "\n".join(lns).strip()
        if len(bloque_texto) <= MAX_CHARS_SECCION:
            resultado.append((tit, bloque_texto))
            continue

        # Si el PDF venía sin líneas dobles, partir por inicios de paso o líneas largas
        parrafos = [p.strip() for p in re.split(r"\n{2,}", bloque_texto) if p.strip()]
        if len(parrafos) == 1:
            parrafos = [p.strip() for p in bloque_texto.split("\n") if p.strip()]

        ventana: List[str] = []
        largo_ventana = 0
        parte_num = 1

        for par in parrafos:
            es_paso = bool(_RX_PASO_LISTA.match(par))
            # Si reinicia numeración a "1." tras un bloque ya grande, abrir nueva ventana
            es_paso_uno = bool(re.match(r"^1[.)]\s+", par))
            corte_natural = es_paso_uno and largo_ventana >= 450
            continuar_lista = (
                ventana
                and es_paso
                and not corte_natural
                and largo_ventana + len(par) <= MAX_CHARS_SECCION + 400
            )
            if ventana and (corte_natural or (largo_ventana + len(par) > MAX_CHARS_SECCION and not continuar_lista)):
                subtitulo = f"{tit} (parte {parte_num})" if tit and parte_num > 1 else tit
                resultado.append((subtitulo, "\n".join(ventana)))
                parte_num += 1
                if ventana[-1].rstrip().endswith(":") and len(ventana[-1]) < 160:
                    ventana = [ventana[-1], par]
                else:
                    ventana = [par]
                largo_ventana = sum(len(x) for x in ventana)
            else:
                ventana.append(par)
                largo_ventana += len(par) + 1

        if ventana:
            subtitulo = f"{tit} (parte {parte_num})" if tit and parte_num > 1 else tit
            resultado.append((subtitulo, "\n".join(ventana)))

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
            fusionadas.append((tit, f"{cont}\n{sig_cont}"))
            i += 2
        else:
            fusionadas.append((tit, cont))
            i += 1

    return fusionadas


# ======================================================================
# 3) Puntuación de secciones (priorizando tema principal frente a menciones)
# ======================================================================

def _bono_recencia(fecha_iso: str) -> float:
    if not fecha_iso or len(fecha_iso) < 4:
        return 0.0
    try:
        anio = int(fecha_iso[:4])
        diferencia = max(0, date.today().year - anio)
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
) -> Tuple[float, int, bool]:
    """
    Devuelve (puntuacion_total, n_terminos_principales_acertados, es_mencion_lateral).
    """
    norm_tit_doc = normalizar(titulo_doc)
    norm_tit_sec = normalizar(titulo_seccion)
    norm_cuerpo = normalizar(texto_seccion)

    pts = 0.0
    aciertos_principales = 0
    aciertos_en_titulos = 0
    lineas_con_termino = 0
    posiciones_aciertos: List[int] = []

    lineas_norm = [normalizar(ln) for ln in texto_seccion.splitlines() if ln.strip()]

    aciertos_en_seccion = 0
    for t in principales:
        raiz = re.escape(_raiz_busqueda(t))
        rx = re.compile(r"(?<!\w)" + raiz)

        m_cuerpo = list(rx.finditer(norm_cuerpo))
        en_tit_sec = bool(rx.search(norm_tit_sec))
        en_tit_doc = bool(rx.search(norm_tit_doc))

        if m_cuerpo or en_tit_sec:
            aciertos_principales += 1
            aciertos_en_seccion += 1
            if en_tit_sec:
                pts += 28.0
                aciertos_en_titulos += 1
            if en_tit_doc:
                pts += 20.0
                aciertos_en_titulos += 1
            if m_cuerpo:
                pts += 11.0 + min(len(m_cuerpo) - 1, 4) * 2.5
                posiciones_aciertos.append(m_cuerpo[0].start())
                lineas_con_termino = max(
                    lineas_con_termino,
                    sum(1 for ln in lineas_norm if rx.search(ln)),
                )
        elif en_tit_doc:
            # El título del archivo coincide, pero esta diapositiva/página concreta
            # (ej. portada) no menciona el término: no suma salvo que el cuerpo tenga sinónimos.
            aciertos_en_titulos += 1

    if len(posiciones_aciertos) >= 2:
        posiciones_aciertos.sort()
        min_dist = min(
            posiciones_aciertos[i + 1] - posiciones_aciertos[i]
            for i in range(len(posiciones_aciertos) - 1)
        )
        if min_dist <= 65:
            pts += 10.0
        elif min_dist <= 160:
            pts += 5.0

    aciertos_sin = 0
    aciertos_sin_en_titulos = 0
    for s in sinonimos:
        rx_s = re.compile(r"(?<!\w)" + re.escape(_raiz_busqueda(s)))
        en_ts = bool(rx_s.search(norm_tit_sec))
        en_td = bool(rx_s.search(norm_tit_doc))
        en_c = bool(rx_s.search(norm_cuerpo))
        if en_ts or en_c:
            aciertos_sin += 1
            pts += 8.0 if (en_ts or en_td) else 3.5
            if en_ts or en_td:
                aciertos_sin_en_titulos += 1

    if aciertos_en_seccion == 0 and aciertos_sin == 0:
        return 0.0, 0, False

    # Detectar "mención lateral": la sección tiene título propio de OTRO proceso
    # (ej. "10. Cierre de Día"), ni el título del doc ni el de la sección hablan
    # del tema preguntado, y el término aparece solo de refilón en 1 línea/paso.
    tiene_titulo_ajeno = bool(
        titulo_seccion
        and not titulo_seccion.lower().startswith(("diapositiva ", "adjunto:", "archivo:", "imagen:"))
        and aciertos_en_titulos == 0
        and aciertos_sin_en_titulos == 0
    )
    es_mencion_lateral = bool(
        aciertos_en_titulos == 0
        and aciertos_sin_en_titulos == 0
        and lineas_con_termino <= 1
        and (tiene_titulo_ajeno or len(lineas_norm) >= 3)
    )

    if es_mencion_lateral:
        # Penalizamos fuerte para que cualquier manual real del tema gane siempre,
        # pero conservamos puntuación > 0 por si es la única referencia en toda la biblioteca.
        pts *= 0.35
    else:
        lineas_pasos = sum(
            1 for ln in texto_seccion.splitlines() if _RX_PASO_LISTA.match(ln)
        )
        if lineas_pasos >= 2:
            pts += min(lineas_pasos * 1.6, 8.0)

    if estado_doc == "DESTACADO":
        pts += 5.0
    pts += min(max(importancia_doc, 0.0) * 0.12, 4.5)
    pts += _bono_recencia(fecha_doc)

    return pts, aciertos_principales, es_mencion_lateral


def buscar_pasajes_biblioteca(
    biblioteca,
    pregunta: str,
    principales: Sequence[str],
    sinonimos: Sequence[str],
    max_documentos: int = 4,
) -> List[PasajeManual]:
    """
    Busca exhaustivamente por términos y sinónimos en Biblioteca (sin cortar en
    los primeros 15 correos) y selecciona los apartados exactos del tema.
    """
    if biblioteca is None or (not principales and not sinonimos):
        return []

    candidatos_por_id: Dict[int, object] = {}

    # Consultas individuales por cada término principal y cada sinónimo clave
    # para que FTS5 nunca descarte un manual llamado "Stock" al preguntar "inventario"
    terminos_busqueda: List[str] = []
    if len(principales) > 1:
        terminos_busqueda.append(" ".join(principales))
    for p in principales:
        if p not in terminos_busqueda:
            terminos_busqueda.append(p)
        raiz = _raiz_busqueda(p)
        if len(raiz) >= 4 and raiz not in terminos_busqueda:
            terminos_busqueda.append(raiz)
    for s in sinonimos[:6]:
        if s not in terminos_busqueda:
            terminos_busqueda.append(s)

    for q in terminos_busqueda:
        try:
            for r in biblioteca.buscar(q, "todos", 30):
                if r.id not in candidatos_por_id:
                    candidatos_por_id[r.id] = r
        except Exception:
            continue

    if not candidatos_por_id:
        try:
            for r in biblioteca.listar("todos", 300):
                candidatos_por_id[r.id] = r
        except Exception:
            return []

    n_princ = len(principales)
    minimo_principales = 1 if n_princ <= 2 else max(1, math.ceil(n_princ * 0.5))

    mejores_por_doc: List[Tuple[float, bool, List[PasajeManual]]] = []

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

        evaluadas: List[Tuple[float, int, bool, int, str, str]] = []
        for idx, (tit_sec, txt_sec) in enumerate(secciones):
            pts, n_aciertos, lateral = _puntuar_seccion(
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
                evaluadas.append((pts, n_aciertos, lateral, idx, tit_sec, txt_sec))

        if not evaluadas:
            continue

        evaluadas.sort(key=lambda x: x[0], reverse=True)
        mejor_pts, mejor_aciertos, mejor_lat, mejor_idx, mejor_tit, mejor_txt = evaluadas[0]

        if n_princ > 0 and mejor_aciertos < minimo_principales and mejor_pts < 16.0 and not mejor_lat:
            continue

        def _crear_pasaje(t_sec: str, c_sec: str, p_sec: float, lat: bool) -> PasajeManual:
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
                es_mencion_lateral=lat,
            )

        seleccion_doc = [_crear_pasaje(mejor_tit, mejor_txt, mejor_pts, mejor_lat)]

        # Si el manual principal tiene varios apartados directos sobre el tema (ej. Conteo + Cierre de inventario),
        # incluimos hasta 2 bloques más para que la IA construya el manual completo de principio a fin.
        if not mejor_lat and len(evaluadas) > 1:
            for seg_pts, seg_aciertos, seg_lat, seg_idx, seg_tit, seg_txt in evaluadas[1:3]:
                if not seg_lat and (seg_pts >= mejor_pts * 0.60 or abs(seg_idx - mejor_idx) == 1) and seg_aciertos >= 1:
                    seleccion_doc.append((seg_idx, _crear_pasaje(seg_tit, seg_txt, seg_pts, seg_lat)))  # type: ignore

            # Ordenar por orden real en el documento si se añadieron extras
            if len(seleccion_doc) > 1:
                con_indices = [(mejor_idx, seleccion_doc[0])] + list(seleccion_doc[1:])  # type: ignore
                con_indices.sort(key=lambda par: par[0])
                seleccion_doc = [p for _, p in con_indices[:3]]

        mejores_por_doc.append((mejor_pts, mejor_lat, seleccion_doc))

    mejores_por_doc.sort(key=lambda item: item[0], reverse=True)

    # Si existe al menos un documento que trata el tema de forma directa (no lateral),
    # descartamos las menciones laterales de otros manuales para no meter ruido.
    hay_directos = any(not lat for _, lat, _ in mejores_por_doc)
    filtrados = [
        (pts, lista)
        for pts, lat, lista in mejores_por_doc
        if (not hay_directos) or (not lat)
    ]

    salida: List[PasajeManual] = []
    for pos, (_, lista_pasajes) in enumerate(filtrados[:max_documentos]):
        if pos == 0:
            salida.extend(lista_pasajes[:3])
        else:
            salida.append(lista_pasajes[0])

    return salida


# ======================================================================
# 4) Consulta completa local
# ======================================================================

def consultar_base_local(
    pregunta: str,
    biblioteca=None,
    historial_preguntas: Optional[Sequence[str]] = None,
) -> RespuestaAsistente:
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
                    biblioteca, pregunta, terminos, sinonimos, max_documentos=4
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
# 5) Síntesis tipo Manual Paso a Paso con IA (OpenAI / Gemini robusto en Windows)
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
        tipo_coincidencia = (
            "MENCIÓN LATERAL EN OTRO PROCESO"
            if pasaje.es_mencion_lateral
            else "APARTADO RELEVANTE"
        )
        meta = " | ".join(
            x for x in (
                pasaje.titulo_documento,
                f"Sección: {pasaje.titulo_seccion}" if pasaje.titulo_seccion else "",
                f"Fecha: {pasaje.fecha}" if pasaje.fecha else "",
                tipo_coincidencia,
            ) if x
        )
        bloques.append(f"[FUENTE {i}: {meta}]\n{pasaje.texto_seccion}")

    return "\n\n---\n\n".join(bloques)


_PROMPT_SISTEMA_RAG = (
    "Eres el redactor de manuales operativos internos de CineStock para el equipo del cine.\n"
    "Tu misión es responder a la pregunta del compañero generando una GUÍA TIPO MANUAL PASO A PASO "
    "clara, exacta y limpia, basándote ÚNICAMENTE en el CONTEXTO proporcionado.\n\n"
    "REGLAS OBLIGATORIAS:\n"
    "1. EXACTITUD Y LIMPIEZA: Filtra y descarta cualquier paso del contexto que pertenezca a "
    "otro trámite distinto (por ejemplo, si preguntan por 'inventario' y en el fragmento "
    "aparecen pasos previos de mensajería/ETV o depósitos, NO los incluyas).\n"
    "2. SI SÍ ESTÁ EL PROCEDIMIENTO EN EL CONTEXTO, redáctalo con este formato de manual:\n"
    "   ## 📋 Procedimiento paso a paso\n"
    "   Breve línea indicando dónde/cuándo se hace (según el documento).\n"
    "   1. **Acción clara**: explicación exacta del paso.\n"
    "   2. **Siguiente acción**: explicación exacta.\n"
    "   ## ⚠️ Comprobaciones y notas importantes\n"
    "   - Puntos clave, validaciones o advertencias que mencione el manual.\n"
    "   Cita al final entre corchetes el documento origen, p. ej. [Nombre del manual].\n"
    "3. SI EN EL CONTEXTO NO ESTÁ EL MANUAL COMPLETO DEL PROCESO (solo una mención lateral, "
    "como validar un reporte antes del cierre de día): dilo en la PRIMERA FRASE con total "
    "claridad ('⚠️ En los documentos cargados actualmente en Conocimiento no está el manual "
    "completo de cómo hacer [X]; únicamente aparece esta referencia relacionada: ...'), "
    "explica brevemente ese dato real sin inventar el resto del procedimiento y sugiere "
    "añadir el manual específico en la pestaña 📚 Conocimiento.\n"
    "4. PROHIBIDO INVENTAR menús, botones o pasos que no figuren literalmente en el CONTEXTO."
)


def _crear_contexto_ssl() -> ssl.SSLContext:
    """Usa los certificados de certifi en Windows si están instalados para evitar fallos SSL."""
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _peticion_json_https(
    url: str,
    headers: Dict[str, str],
    payload: Dict,
    timeout: float,
) -> Dict:
    cuerpo = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=cuerpo, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=_crear_contexto_ssl()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _explicar_http_error(proveedor: str, err: urllib.error.HTTPError) -> str:
    detalle = ""
    try:
        raw = err.read().decode("utf-8", errors="ignore")
        info = json.loads(raw)
        detalle = (
            info.get("error", {}).get("code")
            or info.get("error", {}).get("message")
            or ""
        )
    except Exception:
        detalle = ""

    if err.code == 401:
        return (
            f"La clave de {proveedor} en tu archivo .env no es válida o ha caducado (HTTP 401). "
            "Mostrando directamente el apartado de tus manuales."
        )
    if err.code == 429:
        if "insufficient_quota" in str(detalle).lower() or "quota" in str(detalle).lower():
            return (
                f"Tu cuenta de {proveedor} no tiene saldo/cuota disponible (HTTP 429). "
                "Puedes recargarla o usar una clave gratuita GEMINI_API_KEY en .env. "
                "Mostrando el apartado limpio de tus manuales."
            )
        return f"Límite temporal de peticiones en {proveedor} (HTTP 429). Mostrando tus manuales."
    return f"{proveedor} devolvió HTTP {err.code}. Mostrando el apartado limpio de tus manuales."


def sintetizar_con_ia(
    pregunta: str,
    contexto_rag: str,
    historial_turnos: Optional[Sequence[Tuple[str, str]]] = None,
    timeout: float = 18.0,
) -> Tuple[str, str, str]:
    """
    Llama a OpenAI o Gemini para redactar la guía tipo manual paso a paso.
    Devuelve (texto_generado, nombre_proveedor, aviso_si_falla).
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
        f"CONTEXTO EXTRAÍDO DE LA BIBLIOTECA DE CINESTOCK:\n{contexto_rag}\n\n"
        f"PREGUNTA DEL COMPAÑERO:\n{pregunta}"
    )

    try:
        if proveedor == "OpenAI":
            modelo = _leer_clave_env_local("OPENAI_MODEL") or "gpt-4o-mini"
            datos = _peticion_json_https(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                payload={
                    "model": modelo,
                    "temperature": 0.1,
                    "max_tokens": 850,
                    "messages": [
                        {"role": "system", "content": _PROMPT_SISTEMA_RAG},
                        {"role": "user", "content": mensaje_usuario},
                    ],
                },
                timeout=timeout,
            )
            texto = (
                datos.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            return (texto, f"OpenAI · {modelo}", "")

        if proveedor == "Gemini":
            modelo = _leer_clave_env_local("GEMINI_MODEL") or "gemini-2.5-flash"
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{modelo}:generateContent"
            )
            datos = _peticion_json_https(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                payload={
                    "system_instruction": {"parts": [{"text": _PROMPT_SISTEMA_RAG}]},
                    "contents": [{"role": "user", "parts": [{"text": mensaje_usuario}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 900},
                },
                timeout=timeout,
            )
            partes = (
                datos.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )
            texto = "".join(p.get("text", "") for p in partes).strip()
            return (texto, f"Gemini · {modelo}", "")

    except urllib.error.HTTPError as e:
        return ("", proveedor, _explicar_http_error(proveedor, e))
    except Exception as e:
        motivo = "certificado SSL" if "SSL" in str(e).upper() else "conexión de red"
        return (
            "",
            proveedor,
            f"No se pudo conectar con {proveedor} ({motivo}); mostrando el apartado limpio de tus manuales.",
        )

    return ("", "", "")


# ======================================================================
# 6) Formateo HTML limpio tipo Manual para QTextBrowser
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
    escapado = html.escape(texto_plano or "")
    raices = [
        re.escape(_raiz_busqueda(t))
        for t in terminos
        if len(_raiz_busqueda(t)) >= 3
    ]
    if not raices:
        return escapado

    rx_raices = re.compile(r"^(?:" + "|".join(raices) + r")", re.IGNORECASE)

    def _reemplazar_palabra(match: re.Match) -> str:
        palabra = match.group(0)
        if rx_raices.match(normalizar(palabra)):
            return (
                '<span style="background-color:#FEF08A; color:#0F172A; '
                f'font-weight:600;">{palabra}</span>'
            )
        return palabra

    partes = re.split(r"(&[a-zA-Z0-9#]+;)", escapado)
    for i in range(0, len(partes), 2):
        partes[i] = re.sub(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]{3,}", _reemplazar_palabra, partes[i])
    return "".join(partes)


def formatear_bloque_manual_html(texto: str, terminos: Sequence[str]) -> str:
    lineas_html: List[str] = []
    for linea in (texto or "").splitlines():
        s = linea.strip()
        if not s:
            lineas_html.append('<div style="height:5px;"></div>')
            continue

        m_num = re.match(r"^(\d{1,2}[.)])\s+(.*)$", s)
        if m_num:
            num_esc = html.escape(m_num.group(1))
            resto = resaltar_terminos_html(m_num.group(2), terminos)
            lineas_html.append(
                f'<div style="margin:4px 0 4px 8px;">'
                f'<b style="color:#1D4ED8;">{num_esc}</b> {resto}</div>'
            )
        elif re.match(r"^(nota|importante|atenci[oó]n)\s*:", s, re.IGNORECASE):
            lineas_html.append(
                f'<div style="margin:5px 0 5px 8px; padding:4px 8px; '
                f'background-color:#FFFBEB; border-left:3px solid #D97706; color:#92400E;">'
                f"{resaltar_terminos_html(s, terminos)}</div>"
            )
        elif re.match(r"^[-•*▪▸]\s+", s):
            cuerpo_v = resaltar_terminos_html(re.sub(r"^[-•*▪▸]\s+", "", s), terminos)
            lineas_html.append(
                f'<div style="margin:3px 0 3px 10px;">'
                f'<span style="color:#1D4ED8; font-weight:bold;">•</span> {cuerpo_v}</div>'
            )
        else:
            lineas_html.append(f'<div style="margin:2px 0;">{resaltar_terminos_html(s, terminos)}</div>')

    return "".join(lineas_html)


def formatear_markdown_ia_html(texto_ia: str) -> str:
    lineas_out: List[str] = []
    for cruda in (texto_ia or "").splitlines():
        s = cruda.strip()
        if not s:
            lineas_out.append('<div style="height:6px;"></div>')
            continue

        esc = html.escape(s)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
        esc = re.sub(
            r"\[([^\[\]]{2,90})\]",
            r'<span style="color:#1E40AF; font-weight:600;">[\1]</span>',
            esc,
        )

        if esc.startswith("### "):
            lineas_out.append(
                f'<div style="margin:8px 0 3px 0; font-size:13px; font-weight:bold; color:#0F172A;">{esc[4:]}</div>'
            )
        elif esc.startswith("## "):
            lineas_out.append(
                f'<div style="margin:8px 0 4px 0; font-size:14px; font-weight:bold; color:#162B4D;">{esc[3:]}</div>'
            )
        elif esc.startswith("# "):
            lineas_out.append(
                f'<div style="margin:8px 0 4px 0; font-size:14.5px; font-weight:bold; color:#162B4D;">{esc[2:]}</div>'
            )
        elif re.match(r"^\d{1,2}[.)]\s+", esc):
            m = re.match(r"^(\d{1,2}[.)])\s+(.*)$", esc)
            if m:
                lineas_out.append(
                    f'<div style="margin:4px 0 4px 8px;">'
                    f'<b style="color:#0F766E;">{m.group(1)}</b> {m.group(2)}</div>'
                )
            else:
                lineas_out.append(f'<div style="margin:2px 0;">{esc}</div>')
        elif re.match(r"^[-•*]\s+", esc):
            cuerpo = re.sub(r"^[-•*]\s+", "", esc)
            lineas_out.append(
                f'<div style="margin:3px 0 3px 10px;">'
                f'<span style="color:#0F766E; font-weight:bold;">•</span> {cuerpo}</div>'
            )
        else:
            lineas_out.append(f'<div style="margin:3px 0;">{esc}</div>')

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
    """
    Genera la respuesta visual:
      - Si la IA ha generado el manual paso a paso, muestra esa guía limpia como
        protagonista y debajo una barra compacta con los enlaces para abrir los
        documentos originales (sin ensuciar la pantalla con texto crudo duplicado).
      - Si está en modo local (o mientras la IA redacta), muestra los apartados
        limpios y reestructurados, avisando si solo hay una mención lateral.
    """
    bloques: List[str] = []

    if pensando_ia:
        bloques.append(
            '<table width="100%" cellpadding="10" cellspacing="0" bgcolor="#F0FDFA" '
            'style="border:1px solid #5EEAD4; margin-bottom:8px;">'
            '<tr><td style="color:#0F766E; font-size:13px;">'
            "<b>🤖 Generando guía paso a paso</b> a partir de tus documentos… "
            "<i>(unos segundos)</i>"
            "</td></tr></table>"
        )

    # 1) Si la IA generó la guía tipo manual, ella es la vista principal limpia
    if respuesta.texto_ia:
        cuerpo_ia = formatear_markdown_ia_html(respuesta.texto_ia)
        etiqueta_prov = html.escape(respuesta.proveedor_ia or "IA")
        bloques.append(
            '<table width="100%" cellpadding="12" cellspacing="0" bgcolor="#F8FAFC" '
            'style="border:1px solid #99F6E4; margin-bottom:8px;">'
            "<tr><td>"
            '<div style="color:#0F766E; font-size:11.5px; font-weight:bold; '
            'letter-spacing:0.3px; margin-bottom:6px;">'
            f"🤖 GUÍA GENERADA DESDE TUS MANUALES ({etiqueta_prov})"
            "</div>"
            f'<div style="color:#0F172A; font-size:13px; line-height:1.48; '
            f'background-color:#FFFFFF; padding:10px 12px; border:1px solid #E2E8F0;">'
            f"{cuerpo_ia}</div>"
            "</td></tr></table>"
        )

        # Lista compacta de documentos originales consultados (1 clic para abrir)
        if respuesta.pasajes:
            vistos = set()
            enlaces_fuentes: List[str] = []
            for pasaje in respuesta.pasajes:
                if pasaje.doc_id in vistos:
                    continue
                vistos.add(pasaje.doc_id)
                icono = ICONOS_DOC.get(pasaje.tipo_documento, "📄")
                sec = f" ({html.escape(pasaje.titulo_seccion)})" if pasaje.titulo_seccion else ""
                fec = f" · {html.escape(pasaje.fecha)}" if pasaje.fecha else ""
                enlaces_fuentes.append(
                    f'<div style="margin:3px 0; font-size:12px; color:#334155;">'
                    f'{icono}: <b>{html.escape(pasaje.titulo_documento)}</b>{sec}{fec} — '
                    f'<a href="abrir:{pasaje.doc_id}" '
                    f'style="color:#1D4ED8; font-weight:bold; text-decoration:none;">'
                    f"📂 Abrir original</a></div>"
                )
            bloques.append(
                '<table width="100%" cellpadding="8" cellspacing="0" bgcolor="#F1F5F9" '
                'style="border:1px solid #CBD5E1; margin-bottom:6px;">'
                "<tr><td>"
                '<div style="font-size:11.5px; font-weight:bold; color:#475569; margin-bottom:3px;">'
                "📚 DOCUMENTOS ORIGINALES CONSULTADOS:</div>"
                f'{"".join(enlaces_fuentes)}'
                "</td></tr></table>"
            )
        return "".join(bloques)

    # 2) Aviso claro si la llamada a IA falló (ej. cuota agotada en OpenAI o sin red)
    if respuesta.aviso_ia:
        bloques.append(
            '<table width="100%" cellpadding="8" cellspacing="0" bgcolor="#FFFBEB" '
            'style="border:1px solid #FDE68A; margin-bottom:8px;">'
            f'<tr><td style="color:#92400E; font-size:12px;">'
            f"⚠️ {html.escape(respuesta.aviso_ia)}</td></tr></table>"
        )

    # 3) Procedimiento confirmado oficial (conocimiento.json)
    if respuesta.procedimiento is not None:
        p = respuesta.procedimiento
        partes_p = [
            '<div style="color:#15803D; font-size:12px; font-weight:bold; margin-bottom:4px;">'
            "✅ PROCEDIMIENTO CONFIRMADO EN CINESTOCK</div>",
            f'<div style="font-size:14px; font-weight:bold; color:#0F172A; margin-bottom:4px;">'
            f"{html.escape(p.titulo)}</div>",
        ]
        meta_p = " · ".join(
            x for x in (p.sistema, p.categoria, f"Rev. {p.ultima_revision}" if p.ultima_revision else "") if x
        )
        if meta_p:
            partes_p.append(
                f'<div style="color:#64748B; font-size:11.5px; margin-bottom:6px;">{html.escape(meta_p)}</div>'
            )
        if p.objetivo:
            partes_p.append(
                f'<div style="color:#334155; margin-bottom:6px;"><i>{html.escape(p.objetivo)}</i></div>'
            )
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

    # 4) Apartados limpios extraídos de manuales/correos (modo local o respaldo)
    if respuesta.pasajes and not pensando_ia:
        if respuesta.solo_mencion_lateral:
            bloques.append(
                '<table width="100%" cellpadding="9" cellspacing="0" bgcolor="#FFFBEB" '
                'style="border:1px solid #FCD34D; margin-bottom:8px;">'
                '<tr><td style="color:#92400E; font-size:12.5px;">'
                "<b>⚠️ No hay un manual específico dedicado a este proceso</b> entre los "
                f"{respuesta.total_documentos or ''} documentos cargados actualmente en "
                "<b>📚 Conocimiento</b>. Solo aparece mencionado de forma secundaria en:"
                "</td></tr></table>"
            )
        else:
            bloques.append(
                '<div style="color:#1E3A8A; font-size:12px; font-weight:bold; '
                'margin:6px 0 6px 2px;">📖 APARTADOS EXTRAÍDOS DE TUS MANUALES</div>'
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
                f'<div style="color:#1D4ED8; font-weight:bold; font-size:12.5px; margin:4px 0;">'
                f"📌 Sección: {html.escape(pasaje.titulo_seccion)}</div>"
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
                f'<div style="font-size:12.5px; color:#1E293B; line-height:1.45; '
                f'background-color:#FFFFFF; padding:8px 10px; border:1px solid #E2E8F0;">'
                f"{cuerpo_html}</div>"
                f'<div style="margin-top:6px;">'
                f'<a href="abrir:{pasaje.doc_id}" '
                f'style="color:#1D4ED8; font-weight:bold; text-decoration:none; font-size:12px;">'
                f"📂 Abrir documento original</a></div>"
                "</td></tr></table>"
            )

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
                "los manuales del cine (PDF, Word, PowerPoint o correos) y generaré la guía "
                "paso a paso desde ellos."
            )
        else:
            extra = (
                "<br><small style='color:#475569;'>Si tienes el PDF o correo de ese proceso "
                "(por ejemplo en el Escritorio), añádelo en <b>📚 Conocimiento → ➕ Añadir "
                "correos / documentos…</b> y vuelve a preguntarme.</small>"
            )
        return (
            '<div style="color:#334155; font-size:13px;">'
            "No he encontrado ningún procedimiento ni manual en tu biblioteca que explique "
            f"esa consulta.{extra}</div>"
        )

    return "".join(bloques)
