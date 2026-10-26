"""
Motor de Alta Precisión del Asistente de Inicio para CineStock (sin dependencias de Qt).

Cómo garantiza encontrar SIEMPRE el manual real y descartar los que no tienen nada que ver:
  1) Escaneo directo del 100 % de los documentos de biblioteca.db (sin depender de
     cómo parta palabras FTS5 cuando hay guiones bajos como '20260918_Conciliacion_Inventarios').
  2) Normalización a prueba de nombres de archivo ('_', '-', '.', '/' pasan a espacios,
     y se casan raíces singulares/plurales como 'inventario'/'inventarios').
  3) Exigencia estricta de TODOS los conceptos de la pregunta a nivel de manual/apartado:
     - 'conciliación de inventarios' exige AMBOS ('conciliación' + 'inventario/stock').
       Rechaza conciliación laboral/bancaria y rechaza menciones sueltas en 'Cierre de Día'.
     - 'solicitud de pedido' exige AMBOS ('solicitud' + 'pedido').
       Rechaza solicitudes de vacaciones, permisos, matinales o avisos informales de 2 líneas.
  4) Soporte para manuales escritos tanto con números (1., 2.) como en párrafos/viñetas de
     PDF/PowerPoint, y detección explícita si el PDF encontrado en la biblioteca es un
     escaneado/capturas sin texto OCR todavía (mostrando enlace directo para abrirlo).
  5) Generador de Manual Paso a Paso con IA (OpenAI / Gemini) que transforma el contenido
     real del manual en una guía numerada limpia, o devuelve NO_DISPONIBLE si el texto
     recuperado pertenece en realidad a otro trámite distinto.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import posixpath
import re
import ssl
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .conocimiento import cargar_conocimiento


# ======================================================================
# 1) Normalización a prueba de nombres de archivo y raíces en español
# ======================================================================

PALABRAS_GENERICAS_PREGUNTA = frozenset("""
a al algo algun alguna algunas alguno algunos ante ayer bien buenas buenos
cada casi cine cines cinestock como con conmigo conocer consulta consultar
contesta contestar correo correos cosa cosas crear creamos creo crea cual
cuales cuando darme de debe debemos deberia debo decir decirme del desde
despues dime donde duda dudas ejemplo el ella ellas ellos en entonces entre
era es esa esas ese eso esos esta estaba estan estas este esto estos
explicar explicame explica favor forma formas funciona funcionar gestionar
gestiono gestionamos grabar grabo gracias gustaria haber habia hace hacemos
hacer haces hacia hago han hasta hay hecho hola igual indica indicarme info
informacion las le les lo los manera maneras manual manuales mas me meter
metemos meto mi mientras mis modo mostrar muestra muy necesito nos nosotros
nueva nuevo o otra otras otro otros para paso pasos pero pide pedir poco
podemos podria por porque pregunta preguntar proceso procesos procedimiento
procedimientos puede pueden puedo que quien quienes quiero realizar realizo
realizamos registrar registramos registro registra saber sacar sacamos saco
sale salen se segun ser si siempre sin sobre solo su sus tal tales tambien
te tenemos tengo tiene tienen toda todas todo todos tramitar tramito
tramitamos tras tu tus un una unas uno unos usar uso va vamos van ver vez
veces viendo visto ya yo
""".split())

PALABRAS_SEGUIMIENTO = frozenset("""
advertencia advertencias atencion comprobar comprobacion comprobaciones
cuidado despues detalle detalles ejemplo error falla fallo importante nota
notas ojo pasa precaucion problema revisar revision siguiente siguientes
verificar
""".split())

SUSTANTIVOS_CALIFICABLES = frozenset({
    "solicitud", "conciliacion", "cierre", "apertura", "cambio", "alta",
    "baja", "registro", "control", "gestion", "envio", "informe", "estado",
    "proceso", "parte", "hoja", "ficha", "consulta", "autorizacion",
})

# Equivalencias estrictas de dominio cine (sin mezclar nunca 'solicitud' con 'pedido')
CLASES_EQUIVALENCIA: Tuple[Tuple[str, ...], ...] = (
    ("inventario", "inventariar", "stock", "conteo", "recuento", "existencia"),
    ("conciliacion", "conciliar", "cuadre", "cuadrar"),
    ("merma", "baja", "desperdicio"),
    ("albaran", "recepcion", "recepcionar"),
    ("erp", "peoplesoft"),
    ("solicitud", "requisicion"),
    ("pedido", "aprovisionamiento"),
    ("devolucion", "devolver", "abono"),
    ("matinal", "colegio", "escolar"),
)


def sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def normalizar(texto: str) -> str:
    """
    Pasa a minúsculas sin acentos y convierte '_', '-', '.', '/', '\\' en espacios
    para que nombres como '20260918_Conciliacion_Inventarios.pdf' casen siempre.
    """
    base = sin_acentos(texto).lower()
    base = re.sub(r"[_\-./\\():;,\"'«»\[\]{}]+", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def _simplificar_termino(palabra: str) -> str:
    p = sin_acentos(palabra or "").lower().strip(" _-./\\")
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
    # Conjugaciones en 3ª persona del plural: registran -> registr-,
    # hacen -> hac-, para casar con registrar/registro, hacer/hecho, etc.
    if len(t) >= 7 and t.endswith(("an", "en")) and not t.endswith(("man", "men")):
        return t[:-2]
    if len(t) >= 8 and t.endswith(("o", "a")):
        return t[:-1]
    return t


def _equivalentes_de(termino: str) -> Tuple[str, ...]:
    t = _simplificar_termino(termino)
    raiz_t = _raiz_busqueda(t)
    for clase in CLASES_EQUIVALENCIA:
        clase_norm = tuple(_simplificar_termino(x) for x in clase)
        if t in clase_norm or any(_raiz_busqueda(x) == raiz_t for x in clase_norm):
            return clase_norm
    return (t,)


def _patron_variante(variante: str) -> re.Pattern:
    """Frontera de palabra alfanumérica pura (no falla con '_' ni números pegados)."""
    raiz = re.escape(_raiz_busqueda(variante))
    return re.compile(r"(?<![a-z0-9])" + raiz)


def _ranura_coincide_en(ranura: Sequence[str], texto_norm: str) -> bool:
    if not texto_norm:
        return False
    for variante in ranura:
        if _patron_variante(variante).search(texto_norm):
            return True
    return False


def _contar_ocurrencias_ranura(ranura: Sequence[str], texto_norm: str) -> int:
    if not texto_norm:
        return 0
    total = 0
    for variante in ranura:
        total += len(_patron_variante(variante).findall(texto_norm))
    return total


def extraer_terminos_consulta(
    pregunta: str,
    historial_preguntas: Optional[Sequence[str]] = None,
) -> Tuple[List[str], List[Tuple[str, ...]]]:
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

    texto_norm = normalizar(pregunta or "")
    es_continuacion = bool(
        re.match(r"^(y\b|pero\b|entonces\b|ademas\b|tambien\b|que hay que\b)", texto_norm)
    )
    solo_seguimiento = principales and all(p in PALABRAS_SEGUIMIENTO for p in principales)

    if historial_preguntas and (
        not principales or solo_seguimiento or (es_continuacion and len(principales) <= 1)
    ):
        for anterior in reversed(historial_preguntas):
            prev_principales, _ = extraer_terminos_consulta(anterior, None)
            prev_temas = [p for p in prev_principales if p not in PALABRAS_SEGUIMIENTO]
            if prev_temas:
                for p in prev_temas[:3]:
                    if p not in principales:
                        principales.append(p)
                break

    ranuras: List[Tuple[str, ...]] = []
    clases_vistas: Set[Tuple[str, ...]] = set()
    for p in principales:
        if p in PALABRAS_SEGUIMIENTO:
            continue
        eq = _equivalentes_de(p)
        if eq not in clases_vistas:
            clases_vistas.add(eq)
            ranuras.append(eq)

    return principales, ranuras


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
class AdjuntoIA:
    """Archivo binario (PDF o imagen) preparado en el hilo principal para enviar a la IA."""
    nombre: str
    mime: str          # "application/pdf", "image/png", "image/jpeg", "image/webp"
    datos_b64: str
    etiqueta_previa: str = ""


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
    sin_texto_ocr: bool = False


@dataclass
class DocumentoChatResumen:
    """Ficha resumida de un documento para listarlo en el chat conversacional."""
    doc_id: int
    titulo: str
    tipo: str = ""
    fecha: str = ""
    categoria: str = ""
    estado: str = ""


@dataclass
class RespuestaAsistente:
    pregunta: str
    terminos: List[str] = field(default_factory=list)
    procedimiento: Optional[ProcedimientoCoincidente] = None
    pasajes: List[PasajeManual] = field(default_factory=list)
    adjuntos_ia: List[AdjuntoIA] = field(default_factory=list)
    clave_cache_ia: str = ""
    texto_ia: str = ""
    proveedor_ia: str = ""
    aviso_ia: str = ""
    rechazado_por_ia: bool = False
    biblioteca_vacia: bool = False
    total_documentos: int = 0
    error_biblioteca: Optional[str] = None
    # --- Capa conversacional (chat IA solo-biblioteca) ---
    intencion_chat: str = ""          # saludo, inventario, tema, identidad, ...
    texto_chat: str = ""              # respuesta natural en primera persona
    documentos_chat: List[DocumentoChatResumen] = field(default_factory=list)

    @property
    def es_chat(self) -> bool:
        return bool(self.texto_chat)

    @property
    def tiene_contenido(self) -> bool:
        if self.rechazado_por_ia:
            return False
        return bool(self.texto_chat) or self.procedimiento is not None or bool(self.pasajes)

    @property
    def solo_documento_sin_ocr(self) -> bool:
        return (
            self.procedimiento is None
            and bool(self.pasajes)
            and all(p.sin_texto_ocr for p in self.pasajes)
        )


# ======================================================================
# 2) Búsqueda estricta en procedimientos confirmados (conocimiento.json)
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

    limpia = normalizar(pregunta)
    _, ranuras = extraer_terminos_consulta(pregunta)

    mejor_proc: Optional[Dict] = None
    mejor_pts = 0.0

    for proc in procedimientos:
        pts = 0.0
        preguntas_clave = [normalizar(pk) for pk in proc.get("preguntas_clave", [])]

        coincidencia_exacta_pk = False
        for pk in preguntas_clave:
            if pk and (pk == limpia or (len(pk) >= 14 and pk in limpia)):
                pts += 40.0
                coincidencia_exacta_pk = True
                break

        cabecera_norm = normalizar(" ".join([
            proc.get("titulo", ""),
            proc.get("categoria", ""),
            proc.get("sistema", ""),
            " ".join(proc.get("preguntas_clave", [])),
        ]))
        todo_norm = cabecera_norm + " " + normalizar(" ".join([
            proc.get("objetivo", ""),
            proc.get("respuesta", ""),
            " ".join(proc.get("pasos", [])),
            " ".join(proc.get("comprobaciones", [])),
            " ".join(proc.get("advertencias", [])),
        ]))

        if not coincidencia_exacta_pk:
            if not ranuras:
                continue
            min_ranuras = len(ranuras) if len(ranuras) <= 3 else len(ranuras) - 1
            ranuras_en_todo = sum(1 for r in ranuras if _ranura_coincide_en(r, todo_norm))
            ranuras_en_cab = sum(1 for r in ranuras if _ranura_coincide_en(r, cabecera_norm))

            if ranuras_en_todo < min_ranuras or ranuras_en_cab == 0:
                continue

            especificas = [
                r for r in ranuras
                if not any(v in SUSTANTIVOS_CALIFICABLES for v in r)
            ]
            if especificas and not all(_ranura_coincide_en(r, todo_norm) for r in especificas):
                continue
            if especificas and not any(_ranura_coincide_en(r, cabecera_norm) for r in especificas):
                continue

            pts += ranuras_en_cab * 14.0 + ranuras_en_todo * 6.0

        if pts > mejor_pts and pts >= 18.0:
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
# 3) Limpieza de PDFs corporativos y división en secciones reales
# ======================================================================

_RX_DIAPOSITIVA = re.compile(r"^---\s*Diapositiva\s+(\d+)\s*---\s*$", re.IGNORECASE)
_RX_ADJUNTO = re.compile(r"^\[(Adjunto|Archivo|Imagen):\s*([^\]]+)\]\s*$", re.IGNORECASE)
_RX_ENCABEZADO_MD = re.compile(r"^(#{1,4})\s+(.+)$")
_RX_PASO_ESTRICTO = re.compile(
    r"^\s*(?:[-•*▪▸➢]\s*\S|\d{1,2}[.)\-]\s+\S|paso\s+\d+\b)",
    re.IGNORECASE,
)
_RX_PASO_O_NOTA = re.compile(
    r"^\s*(?:[-•*▪▸➢]\s*\S|\d{1,2}[.)\-]\s+\S|paso\s+\d+\b|nota\s*:|importante\s*:|atenci[oó]n\s*:)",
    re.IGNORECASE,
)

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

_INICIOS_FRASE_PASO = frozenset("""
si en al el la los las un una para por con sin tras antes despues durante
cuando ingresar ingresa presionar presiona verificar verifica seleccionar
selecciona pulsar pulsa entrar entra acceder accede hacer haz comprobar
comprueba revisar revisa introducir introduce escribir escribe anotar anota
imprimir imprime contar cuenta abrir abre cerrar cierra guardar guarda
validar valida marcar marca elegir elige colocar coloca dar confirmar
confirma esperar espera repetir repite asegurar asegurate capturar captura
ubicar ubica desplegar despliega ejecutar ejecuta finalizar finaliza
""".split())

_RX_VERBOS_OPERATIVOS = re.compile(
    r"\b(entrar|entra|acceder|accede|pulsar|pulsa|presionar|presiona|"
    r"seleccionar|selecciona|ingresar|ingresa|introducir|introduce|"
    r"guardar|guarda|comprobar|comprueba|revisar|revisa|verificar|verifica|"
    r"imprimir|imprime|contar|cuenta|validar|valida|clic|click|"
    r"capturar|captura|desplegar|despliega|ejecutar|ejecuta|finalizar|finaliza|"
    r"pestana|modulo|boton|desplegable|menu|pantalla|ventana|campo|reporte)\b"
)

MAX_CHARS_SECCION = 1600
MIN_CHARS_FUSION = 90


def limpiar_texto_manual(texto: str) -> str:
    if not texto:
        return ""

    t = texto.replace("\r\n", "\n").replace("\r", "\n")
    # Normalizar viñetas privadas típicas de PDFs generados desde Word/PowerPoint
    for glifo in ("\uf0b7", "\uf0a7", "\uf0d8", "\uf076", "", "", "➢", ""):
        t = t.replace(glifo, "• ")
    t = re.sub(r"([a-zA-ZáéíóúÁÉÍÓÚ])\s+(ñ|Ñ)([a-záéíóú])", r"\1\2\3", t)
    t = re.sub(r"\b([a-zA-Z])\s+([áéíóúÁÉÍÓÚ][a-z]{2,})", r"\1\2", t)

    lineas_filtradas: List[str] = []
    for cruda in t.split("\n"):
        s = re.sub(r"[ \t]+", " ", cruda).strip()
        if _RX_LINEA_RUIDO_PDF.match(s):
            continue
        s = re.sub(
            r"\s+[A-Z]{2,5}-[A-Z0-9]{2,6}-[A-Z0-9\-]+\s+.+\bP[aá]gina\s+\d+\s+de\s+\d+\s*$",
            "",
            s,
            flags=re.IGNORECASE,
        ).strip()
        lineas_filtradas.append(s)

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
            and not _RX_PASO_O_NOTA.match(s)
            and not _parece_encabezado_aislado(reflujo[-1])
            and not _parece_encabezado_aislado(s)
            and (s[0].islower() or s[0] in "“\"'()0123456789€%")
        ):
            reflujo[-1] = reflujo[-1] + " " + s
        else:
            reflujo.append(s)

    return "\n".join(reflujo).strip()


def _parece_encabezado_aislado(linea: str) -> bool:
    s = linea.strip()
    if not s or len(s) > 88:
        return False
    if _RX_DIAPOSITIVA.match(s) or _RX_ADJUNTO.match(s) or _RX_ENCABEZADO_MD.match(s):
        return True

    if re.match(r"^\d{1,2}(?:\.\d{1,2})+\.?\s+[A-ZÁÉÍÓÚÜÑ]\S.*$", s) and not s.endswith("."):
        return True

    if re.match(
        r"^(?:apartado|secci[oó]n|bloque|cap[ií]tulo|anexo|procedimiento)\s+[A-Z0-9]+[:.\-\s]+.+$",
        s,
        re.IGNORECASE,
    ) and not s.endswith("."):
        return True

    letras = [c for c in s if c.isalpha()]
    if 5 <= len(letras) <= 65 and len(s) <= 78 and not s.endswith((",", ";", ".")):
        if all(c.isupper() for c in letras):
            return True

    m_num = re.match(r"^(\d{1,2})\.\s+([A-ZÁÉÍÓÚÜÑ][^\n]+)$", s)
    if m_num and not s.endswith((".", ":", ";", ",")):
        resto = m_num.group(2).strip()
        palabras = resto.split()
        primera = sin_acentos(re.sub(r"[^\w]", "", palabras[0])).lower() if palabras else ""
        dos_primeras = " ".join(sin_acentos(w).lower() for w in palabras[:2])
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

    resultado: List[Tuple[str, str]] = []
    for tit, lns in secciones_brutas:
        bloque_texto = "\n".join(lns).strip()
        if len(bloque_texto) <= MAX_CHARS_SECCION:
            resultado.append((tit, bloque_texto))
            continue

        parrafos = [p.strip() for p in re.split(r"\n{2,}", bloque_texto) if p.strip()]
        if len(parrafos) == 1:
            parrafos = [p.strip() for p in bloque_texto.split("\n") if p.strip()]

        ventana: List[str] = []
        largo_ventana = 0
        parte_num = 1

        for par in parrafos:
            es_paso = bool(_RX_PASO_O_NOTA.match(par))
            es_paso_uno = bool(re.match(r"^1[.)]\s+", par))
            corte_natural = es_paso_uno and largo_ventana >= 450
            continuar_lista = (
                ventana
                and es_paso
                and not corte_natural
                and largo_ventana + len(par) <= MAX_CHARS_SECCION + 400
            )
            if ventana and (
                corte_natural or (largo_ventana + len(par) > MAX_CHARS_SECCION and not continuar_lista)
            ):
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
# 4) Escaneo Completo y Selección de Alta Precisión en Biblioteca
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


def _obtener_todas_las_fichas(biblioteca) -> List[Dict]:
    """
    Lee directamente todas las fichas no archivadas de biblioteca.db (<3 ms para
    cientos de documentos), garantizando que ningún guion bajo, adjunto o plural
    impida localizar un manual real.
    """
    con = getattr(biblioteca, "_con", None)
    if con is not None:
        try:
            filas = con.execute(
                "SELECT * FROM docs WHERE estado != 'ARCHIVADO' ORDER BY importancia DESC, id DESC"
            ).fetchall()
            return [dict(f) for f in filas]
        except Exception:
            pass

    fichas: List[Dict] = []
    try:
        for r in biblioteca.listar("todos", 1000):
            f = biblioteca.obtener(r.id)
            if f:
                fichas.append(f)
    except Exception:
        pass
    return fichas


def _intentar_releer_texto_si_vacio(biblioteca, ficha: Dict) -> str:
    """
    Si un documento se importó en su día sin texto (p. ej. antes de tener pypdf),
    intenta extraer su texto al vuelo desde la copia local sin romper nada.
    """
    ruta_copia = ficha.get("ruta_copia") or ""
    if not ruta_copia:
        return ""
    p = Path(ruta_copia)
    if not p.is_file() and hasattr(biblioteca, "originales"):
        nombre = str(ruta_copia).replace("\\", "/").rsplit("/", 1)[-1]
        if nombre:
            p = Path(biblioteca.originales) / nombre
    if not p.is_file():
        return ""
    try:
        from .biblioteca import extraer_texto
        ocr_fn = biblioteca._ocr_callable() if hasattr(biblioteca, "_ocr_callable") else None
        return (extraer_texto(p, ocr_fn) or "").strip()
    except Exception:
        return ""


def _es_contenido_instructivo(texto: str, tipo_doc: str) -> bool:
    """Descarta correos cortos de aviso ('mandad el pedido el lunes') y hojas numéricas puras."""
    if tipo_doc == "xlsx":
        return sum(1 for ln in texto.splitlines() if _RX_PASO_ESTRICTO.match(ln)) >= 2

    n_pasos = sum(1 for ln in texto.splitlines() if _RX_PASO_ESTRICTO.match(ln))
    if tipo_doc == "correo":
        # Un correo solo cuenta como manual si trae instrucciones explícitas
        # paso a paso. La prosa amable («Buen día estimados… hoy se llevará
        # la toma de inventarios bajo los siguientes criterios…») NO es un
        # manual, aunque mencione el proceso de pasada.
        return n_pasos >= 2 or (n_pasos >= 1 and len(_RX_VERBOS_OPERATIVOS.findall(normalizar(texto))) >= 2)
    if n_pasos >= 2:
        return True

    verbos = len(_RX_VERBOS_OPERATIVOS.findall(normalizar(texto)))
    if n_pasos >= 1 and verbos >= 1:
        return True
    if verbos >= 2 and len(texto) >= 95:
        return True
    # En manuales PDF/DOCX/PPTX extensos en prosa explicativa
    if tipo_doc in ("pdf", "docx", "pptx", "texto") and len(texto) >= 220 and verbos >= 1:
        return True

    return False


_RX_SALUDO_CORREO = re.compile(
    r"^(buen(?:os)?\s+dias?|buenas?\s+(?:tardes?|noches?)|estimad[oa]s?|"
    r"hola\s+(?:equipo|a\s+todos|compañeros)|espero\s+(?:esten|esteis|se\s+encuentren|teng)|"
    r"les\s+comento|os\s+comento|te\s+comento|un\s+saludo|saludos?\s+cordiales|"
    r"quedo\s+atent|atentamente|gracias\s+por\s+(?:su|vuestra)|de\s*:|para\s*:|"
    r"cc\s*:|asunto\s*:|enviado\s*:|remitente\s*:)",
    re.IGNORECASE,
)

_RX_ACCIONABLE_EXTRA = re.compile(
    r"\b(abrir|abre|cerrar|cierra|escribir|escribe|elegir|elige|marcar|marca|"
    r"rellenar|rellena|completar|completa|buscar|busca|crear|crea|generar|genera|"
    r"registrar|registra|realizar|realiza|enviar|envia|calcular|calcula|confirmar|confirma|"
    r"bloquear|bloquea|tomar|toma|iniciar|inicia|acceder|accede|entrar|entra|"
    r"debe|deben|debera|deberan|hay\s+que|es\s+necesario|no\s+debe|antes\s+de|"
    r"pulsar|pulsa|pantalla|menu|boton|campo|pestana|modulo|sistema)\b"
)


def _es_linea_saludo_o_cortesia(texto: str) -> bool:
    return bool(_RX_SALUDO_CORREO.match(normalizar(texto or "")))


def _es_unidad_accionable(texto: str) -> bool:
    norm = normalizar(texto or "")
    if not norm or _RX_SALUDO_CORREO.match(norm):
        return False
    return bool(_RX_VERBOS_OPERATIVOS.search(norm) or _RX_ACCIONABLE_EXTRA.search(norm))


def _seccion_es_correo_sin_pasos(titulo_sec: str, texto_sec: str) -> bool:
    """True si la sección es el cuerpo amable de un correo, no un manual."""
    if (titulo_sec or "").lower().startswith(("adjunto:", "archivo:")):
        return False
    lineas = [ln.strip() for ln in (texto_sec or "").splitlines() if ln.strip()]
    if not lineas:
        return True
    n_pasos = sum(1 for ln in lineas if _RX_PASO_ESTRICTO.match(ln))
    if n_pasos >= 2:
        return False
    primeras = " ".join(lineas[:2])
    return _es_linea_saludo_o_cortesia(primeras) or (
        len(lineas) <= 3 and len(texto_sec or "") > 240 and n_pasos == 0
    )


def buscar_pasajes_biblioteca(
    biblioteca,
    pregunta: str,
    ranuras: Sequence[Tuple[str, ...]],
    max_documentos: int = 2,
) -> List[PasajeManual]:
    if biblioteca is None or not ranuras:
        return []

    fichas = _obtener_todas_las_fichas(biblioteca)
    if not fichas:
        return []

    n_ranuras = len(ranuras)
    min_ranuras_doc = n_ranuras if n_ranuras <= 3 else n_ranuras - 1
    ranuras_especificas = [
        r for r in ranuras if not any(v in SUSTANTIVOS_CALIFICABLES for v in r)
    ] or list(ranuras)

    candidatos_doc: List[Tuple[float, List[PasajeManual], bool]] = []

    for ficha in fichas:
        doc_id = int(ficha["id"])
        titulo_doc = (ficha.get("titulo") or "").strip() or "Documento"
        nombre_archivo = (ficha.get("archivo") or "").strip()
        tipo_doc = (ficha.get("tipo") or "texto").strip()
        fecha_doc = ficha.get("fecha_doc") or ficha.get("fecha") or ""
        categoria_doc = ficha.get("categoria") or ""
        remitente_doc = ficha.get("remitente") or ""
        estado_doc = ficha.get("estado") or "NORMAL"
        importancia_doc = float(ficha.get("importancia") or 0.0)

        texto_bruto = (ficha.get("texto") or "").strip()

        # Nombres de adjuntos incrustados en correos .eml cuentan como títulos
        nombres_adjuntos = " ".join(
            m.group(2) for m in re.finditer(r"\[(Adjunto|Archivo):\s*([^\]]+)\]", texto_bruto)
        )
        cabecera_doc_norm = normalizar(f"{titulo_doc} {nombre_archivo} {nombres_adjuntos}")

        # Si el documento parece ser exactamente el manual por su título pero tiene texto vacío,
        # probamos a extraerlo al vuelo (o avisar de que es un PDF de capturas sin OCR).
        ranuras_en_cabecera_doc = sum(
            1 for r in ranuras if _ranura_coincide_en(r, cabecera_doc_norm)
        )
        especificas_en_cabecera_doc = sum(
            1 for r in ranuras_especificas if _ranura_coincide_en(r, cabecera_doc_norm)
        )

        if len(texto_bruto) < 40 and ranuras_en_cabecera_doc >= min_ranuras_doc:
            recuperado = _intentar_releer_texto_si_vacio(biblioteca, ficha)
            if recuperado:
                texto_bruto = recuperado

        texto_doc_norm = normalizar(texto_bruto)
        todo_doc_norm = f"{cabecera_doc_norm} {texto_doc_norm}"

        # PUERTA 1 (OBLIGATORIA): El documento debe contener TODOS los conceptos de la pregunta.
        # Esto elimina de raíz "conciliación familiar", "solicitud de vacaciones", etc.
        ranuras_en_doc = sum(1 for r in ranuras if _ranura_coincide_en(r, todo_doc_norm))
        if ranuras_en_doc < min_ranuras_doc:
            continue
        if not all(_ranura_coincide_en(r, todo_doc_norm) for r in ranuras_especificas):
            continue

        # Caso especial: El archivo en Conocimiento se llama literalmente como el proceso buscado
        # (ej. "Conciliacion_de_Inventarios.pdf"), pero es un PDF de imágenes sin texto OCR.
        if len(texto_bruto) < 40 and ranuras_en_cabecera_doc >= min_ranuras_doc:
            candidatos_doc.append((
                85.0,
                [
                    PasajeManual(
                        doc_id=doc_id,
                        titulo_documento=titulo_doc,
                        tipo_documento=tipo_doc,
                        fecha=fecha_doc,
                        categoria=categoria_doc,
                        remitente=remitente_doc,
                        estado=estado_doc,
                        titulo_seccion="Documento basado en imágenes / capturas",
                        texto_seccion="",
                        puntuacion=85.0,
                        sin_texto_ocr=True,
                    )
                ],
                True,
            ))
            continue

        secciones = dividir_documento_en_secciones(texto_bruto, titulo_doc)
        if not secciones:
            continue

        # ¿Es un MANUAL DEDICADO específicamente al tema preguntado por su título?
        # (Ej. "04c654d25d_170608 Conciliación de inventarios.pptx" al preguntar por
        # "conciliación de inventarios" o un subapartado como "crear evento de inventario")
        es_titulo_exacto = (
            ranuras_en_cabecera_doc == n_ranuras
            and especificas_en_cabecera_doc == len(ranuras_especificas)
        )
        es_manual_dedicado = es_titulo_exacto or (
            tipo_doc in ("pptx", "pdf", "docx")
            and especificas_en_cabecera_doc >= 1
            and ranuras_en_cabecera_doc >= max(1, n_ranuras - 1)
        )

        if es_manual_dedicado:
            # En un manual o presentación dedicado al proceso buscado, conservamos la secuencia
            # ordenada de diapositivas/apartados para que la IA no pierda pasos intermedios
            # (p. ej. Diapositiva 9 Login ERP -> Diapositiva 11 Ruta de Menú -> Diapositiva 12 ID Evento).
            texto_unido = "\n\n".join(txt for _, txt in secciones)
            if tipo_doc != "pptx" and not _es_contenido_instructivo(texto_unido, tipo_doc):
                continue

            secciones_puntuadas: List[Tuple[float, int, str, str]] = []
            for idx, (tit_sec, txt_sec) in enumerate(secciones):
                norm_tit_s = normalizar(tit_sec)
                norm_sec = normalizar(f"{tit_sec} {txt_sec}")
                pts_s = 60.0
                for r in ranuras:
                    if _ranura_coincide_en(r, norm_sec):
                        pts_s += 15.0
                    if _ranura_coincide_en(r, norm_tit_s):
                        pts_s += 22.0
                if _es_contenido_instructivo(txt_sec, tipo_doc):
                    pts_s += 15.0
                elif len(txt_sec) < 60:
                    pts_s -= 25.0
                secciones_puntuadas.append((pts_s, idx, tit_sec, txt_sec))

            tiene_capitulos_independientes = any(
                tit_s
                and not tit_s.lower().startswith(("diapositiva ", "adjunto:", "archivo:", "imagen:"))
                and not re.match(r"^\d{1,2}[.)\-]\s+", tit_s)
                for _, _, tit_s, _ in secciones_puntuadas
            )

            # Si un correo reenvía un manual adjunto, solo vale el adjunto:
            # el cuerpo amable del correo (saludo, criterios, firma) nunca
            # debe convertirse en los «pasos» de la hoja.
            adjunto_exacto_idx: Optional[int] = None
            if tipo_doc == "correo":
                for s_pts, s_idx, s_tit, _s_txt in secciones_puntuadas:
                    if s_tit.lower().startswith(("adjunto:", "archivo:")) and all(
                        _ranura_coincide_en(r, normalizar(s_tit)) for r in ranuras
                    ):
                        adjunto_exacto_idx = s_idx
                        break
                if adjunto_exacto_idx is not None:
                    secciones_puntuadas = [
                        s for s in secciones_puntuadas if s[1] >= adjunto_exacto_idx
                    ]

            # Correo que reenvía una presentación: quédate SOLO con las
            # diapositivas del manual. El cuerpo del correo (saludo, criterios
            # del día antes, firma) no forma parte del procedimiento.
            if tipo_doc == "correo" and adjunto_exacto_idx is None:
                solo_manual_visual = [
                    s for s in secciones_puntuadas
                    if s[2].lower().startswith(("diapositiva", "adjunto:", "archivo:"))
                ]
                if solo_manual_visual:
                    secciones_puntuadas = solo_manual_visual

            con_chicha = [s for s in secciones_puntuadas if s[0] >= 55.0] or secciones_puntuadas
            sin_sobres = [
                s for s in con_chicha
                if not _seccion_es_correo_sin_pasos(s[2], s[3])
            ]
            if sin_sobres:
                con_chicha = sin_sobres
            con_chicha_ord_pts = sorted(con_chicha, key=lambda x: x[0], reverse=True)
            mejor_sec_pts = con_chicha_ord_pts[0][0]

            if tiene_capitulos_independientes and mejor_sec_pts >= 95.0:
                # Manual de texto con varios capítulos distintos ("Apertura de bar", "Conciliación",
                # "Mermas"): nos quedamos únicamente con el capítulo que clava la consulta.
                filtradas_doc = [s for s in con_chicha_ord_pts if s[0] >= mejor_sec_pts * 0.80]
                elegidas = sorted(filtradas_doc[:12], key=lambda x: x[1])
            else:
                # Presentación .pptx paso a paso o guía de un único proceso: conservar TODAS las
                # diapositivas/secciones operativas en su orden cronológico real (hasta 65).
                elegidas = sorted(con_chicha, key=lambda x: x[1])[:65]

            bono_dedicado = 35.0 if es_titulo_exacto else 22.0
            pts_doc = mejor_sec_pts + bono_dedicado + _bono_recencia(fecha_doc)
            if tipo_doc != "correo":
                pts_doc += 18.0  # un manual suelto manda sobre cualquier correo
            elif adjunto_exacto_idx is None:
                pts_doc -= 30.0  # correo que solo menciona el proceso, no el manual
            if estado_doc == "DESTACADO":
                pts_doc += 6.0

            pasajes_doc = [
                PasajeManual(
                    doc_id=doc_id,
                    titulo_documento=titulo_doc,
                    tipo_documento=tipo_doc,
                    fecha=fecha_doc,
                    categoria=categoria_doc,
                    remitente=remitente_doc,
                    estado=estado_doc,
                    titulo_seccion=t_s,
                    texto_seccion=c_s,
                    puntuacion=pts_doc,
                )
                for _, _, t_s, c_s in elegidas
            ]
            candidatos_doc.append((pts_doc, pasajes_doc, bool(es_titulo_exacto)))
            continue

        # Si NO es un manual dedicado por su nombre (ej. un manual grande de "Cinema Manager"
        # con 15 temas distintos, o un correo con varios asuntos), evaluamos SECCIÓN POR SECCIÓN
        # exigiendo que la sección concreta trate realmente del proceso preguntado.
        secciones_validas: List[Tuple[float, int, str, str]] = []

        for idx, (tit_sec, txt_sec) in enumerate(secciones):
            if not _es_contenido_instructivo(txt_sec, tipo_doc):
                continue

            norm_tit_sec = normalizar(tit_sec)
            norm_cuerpo = normalizar(txt_sec)
            norm_sec = f"{norm_tit_sec} {norm_cuerpo}"

            # Exigir que todos los conceptos de la consulta estén en esta sección
            # (o en título_doc + esta sección)
            ranuras_en_sec = sum(1 for r in ranuras if _ranura_coincide_en(r, norm_sec))
            ranuras_en_comb = sum(
                1 for r in ranuras if _ranura_coincide_en(r, f"{cabecera_doc_norm} {norm_sec}")
            )
            if ranuras_en_comb < min_ranuras_doc:
                continue
            if n_ranuras >= 2 and ranuras_en_sec < n_ranuras and ranuras_en_cabecera_doc == 0:
                continue

            # Guardia antimención lateral (p. ej. "10. Cierre de Día" mencionando "inventario" en 1 frase)
            es_contenedor_neutro = (
                not tit_sec
                or tit_sec.lower().startswith(("diapositiva ", "adjunto:", "archivo:", "imagen:"))
            )
            especificas_en_tit_sec = sum(
                1 for r in ranuras_especificas if _ranura_coincide_en(r, norm_tit_sec)
            )
            lineas_norm = [normalizar(ln) for ln in txt_sec.splitlines() if ln.strip()]
            lineas_con_tema = sum(
                1 for ln in lineas_norm
                if any(_ranura_coincide_en(r, ln) for r in ranuras_especificas)
            )
            ocurrencias_tema = sum(
                _contar_ocurrencias_ranura(r, norm_cuerpo) for r in ranuras_especificas
            )

            # Foco temático real: cada concepto de la pregunta debe ser central
            # en ESTA sección (en su título o repetido en el cuerpo), no basta
            # que el documento lo mencione una vez entre saludos y criterios.
            # Así un «Mail tipo día antes de la toma de inventarios» jamás
            # gana a un manual de conciliación, mermas, albaranes, etc.
            if n_ranuras >= 2 and especificas_en_tit_sec == 0 and especificas_en_cabecera_doc == 0:
                ocurrencias_por_ranura = [
                    _contar_ocurrencias_ranura(r, norm_sec) for r in ranuras_especificas
                ]
                if ocurrencias_por_ranura and min(ocurrencias_por_ranura) < 2:
                    continue
            if tipo_doc == "correo" and not norm_tit_sec.startswith(("adjunto", "archivo")):
                if _seccion_es_correo_sin_pasos(tit_sec, txt_sec):
                    continue

            if not es_contenedor_neutro and especificas_en_tit_sec == 0:
                # Tiene título propio de otra sección ("10. Cierre de Día"): solo aceptar si
                # el tema aparece de forma central/reiterada (>=3 líneas) y el doc es del área
                if lineas_con_tema < 3 and especificas_en_cabecera_doc == 0:
                    continue
            elif especificas_en_tit_sec == 0 and especificas_en_cabecera_doc == 0:
                if lineas_con_tema < 2 and ocurrencias_tema < 3:
                    continue

            pts_s = 20.0 + ranuras_en_sec * 15.0 + especificas_en_tit_sec * 28.0
            pts_s += min(lineas_con_tema * 3.0, 12.0)

            if len(ranuras) >= 2:
                juntos = any(
                    all(_ranura_coincide_en(r, ln) for r in ranuras[:2])
                    for ln in ([norm_tit_sec, cabecera_doc_norm] + lineas_norm)
                    if ln
                )
                if juntos:
                    pts_s += 16.0
                elif especificas_en_tit_sec == 0 and especificas_en_cabecera_doc == 0:
                    continue

            pts_s += _bono_recencia(fecha_doc) + min(importancia_doc * 0.1, 4.0)
            if tipo_doc == "correo":
                pts_s -= 25.0
            if estado_doc == "DESTACADO":
                pts_s += 5.0

            secciones_validas.append((pts_s, idx, tit_sec, txt_sec))

        if not secciones_validas:
            continue

        secciones_validas.sort(key=lambda x: x[0], reverse=True)
        mejor_pts, mejor_idx, mejor_tit, mejor_txt = secciones_validas[0]

        elegidas_sec = [(mejor_idx, mejor_tit, mejor_txt, mejor_pts)]
        for seg_pts, seg_idx, seg_tit, seg_txt in secciones_validas[1:3]:
            if seg_pts >= mejor_pts * 0.72 and abs(seg_idx - mejor_idx) <= 2:
                elegidas_sec.append((seg_idx, seg_tit, seg_txt, seg_pts))
        elegidas_sec.sort(key=lambda x: x[0])

        pasajes_doc = [
            PasajeManual(
                doc_id=doc_id,
                titulo_documento=titulo_doc,
                tipo_documento=tipo_doc,
                fecha=fecha_doc,
                categoria=categoria_doc,
                remitente=remitente_doc,
                estado=estado_doc,
                titulo_seccion=t_s,
                texto_seccion=c_s,
                puntuacion=p_s,
            )
            for _, t_s, c_s, p_s in elegidas_sec[:3]
        ]
        candidatos_doc.append((mejor_pts, pasajes_doc, False))

    if not candidatos_doc:
        return []

    candidatos_doc.sort(key=lambda par: par[0], reverse=True)
    # Si el ganador es el manual dedicado exacto del proceso, es el ÚNICO
    # que se muestra: mezclar un segundo documento (un correo, un aviso…)
    # contaminaba la hoja con saludos y criterios ajenos al procedimiento.
    if candidatos_doc[0][2]:
        candidatos_doc = candidatos_doc[:1]
    else:
        tope = candidatos_doc[0][0]
        candidatos_doc = [c for c in candidatos_doc if c[0] >= tope * 0.65]

    salida: List[PasajeManual] = []
    for pos, (_, lista_p, _exacto) in enumerate(candidatos_doc[:max_documentos]):
        if pos == 0:
            salida.extend(lista_p[:65])
        else:
            salida.append(lista_p[0])

    return salida


# ======================================================================
# 5) Inspector Multimodal Diapositiva a Diapositiva (.pptx / .pdf / .docx)
# ======================================================================

MAX_BYTES_PDF_DIRECTO = 20 * 1024 * 1024  # 20 MB para PDF directo
MAX_PAGINAS_PDF_ADJUNTO = 40
MAX_DIAPOSITIVAS_VISUALES = 46
VERSION_CACHE_IA = "v4_hoja_manual_solo_manual_real"

_MIMES_IMAGEN = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _resolver_ruta_documento(biblioteca, doc_id: int) -> Optional[Path]:
    if biblioteca is None:
        return None
    if hasattr(biblioteca, "resolver_original"):
        try:
            ruta = biblioteca.resolver_original(doc_id)
            if ruta and Path(ruta).is_file():
                return Path(ruta)
        except Exception:
            pass
    if hasattr(biblioteca, "obtener"):
        try:
            ficha = biblioteca.obtener(doc_id)
            if ficha:
                candidatas: List[Path] = []
                guardada = str(ficha.get("ruta_copia") or "")
                if guardada and hasattr(biblioteca, "originales"):
                    nombre = guardada.replace("\\", "/").rsplit("/", 1)[-1]
                    if nombre:
                        candidatas.append(Path(biblioteca.originales) / nombre)
                if guardada:
                    candidatas.append(Path(guardada))
                for campo in ("ruta_origen", "archivo"):
                    val = ficha.get(campo)
                    if val:
                        candidatas.append(Path(str(val)))
                for c in candidatas:
                    if c.is_file():
                        return c.resolve()
        except Exception:
            pass
    return None


def _contar_paginas_pdf_bytes(datos: bytes) -> int:
    try:
        from pypdf import PdfReader  # type: ignore
        return len(PdfReader(BytesIO(datos)).pages)
    except Exception:
        n = len(re.findall(rb"/Type\s*/Page\b", datos))
        return max(1, n)


def _comprimir_imagen_para_ia(raw: bytes, mime_orig: str, ancho_max: int = 1152) -> Tuple[bytes, str]:
    """Comprime capturas grandes a JPEG nítido (~75 KB) usando Qt (PySide6) o Pillow si existen."""
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PySide6.QtGui import QImage

        img = QImage.fromData(raw)
        w = img.width() if hasattr(img, "width") else 0
        h = img.height() if hasattr(img, "height") else 0
        if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
            if w > ancho_max:
                img = img.scaledToWidth(ancho_max, Qt.TransformationMode.SmoothTransformation)
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            if img.save(buf, "JPG", 80):
                datos_jpg = bytes(ba)
                if datos_jpg:
                    return (datos_jpg, "image/jpeg")
    except Exception:
        pass

    try:
        from PIL import Image  # type: ignore
        with Image.open(BytesIO(raw)) as im:
            im = im.convert("RGB")
            if im.width > ancho_max:
                nuevo_h = max(1, int(round(im.height * ancho_max / im.width)))
                im = im.resize((ancho_max, nuevo_h))
            out = BytesIO()
            im.save(out, format="JPEG", quality=80, optimize=True)
            return (out.getvalue(), "image/jpeg")
    except Exception:
        pass

    return (raw, mime_orig)


def _extraer_imagenes_de_pdf_bytes(datos_pdf: bytes, max_img: int = 24) -> List[AdjuntoIA]:
    """Respaldo Vision: extrae capturas incrustadas en un PDF usando pypdf."""
    salida: List[AdjuntoIA] = []
    try:
        from pypdf import PdfReader  # type: ignore
        lector = PdfReader(BytesIO(datos_pdf))
        for num_pag, pag in enumerate(lector.pages[:40], 1):
            for idx_im, img in enumerate(getattr(pag, "images", []) or [], 1):
                raw = getattr(img, "data", b"") or b""
                if len(raw) < 10_000 or len(raw) > 8 * 1024 * 1024:
                    continue
                ext = Path(getattr(img, "name", "captura.png") or "captura.png").suffix.lower()
                mime = _MIMES_IMAGEN.get(ext)
                if not mime:
                    if raw.startswith(b"\x89PNG"):
                        mime = "image/png"
                    elif raw.startswith(b"\xff\xd8"):
                        mime = "image/jpeg"
                    else:
                        continue
                comp, mime_f = _comprimir_imagen_para_ia(raw, mime)
                salida.append(
                    AdjuntoIA(
                        nombre=f"pagina_{num_pag}_{idx_im}.jpg",
                        mime=mime_f,
                        datos_b64=base64.b64encode(comp).decode("ascii"),
                        etiqueta_previa=f"=== PÁGINA {num_pag} DEL PDF ===",
                    )
                )
                if len(salida) >= max_img:
                    return salida
    except Exception:
        return salida
    return salida


def _leer_xfrm_elemento(sp_pr) -> Optional[Tuple[int, int, int, int, bool, bool]]:
    if sp_pr is None:
        return None
    xfrm = sp_pr.find(_NS_A + "xfrm")
    if xfrm is None:
        return None
    off = xfrm.find(_NS_A + "off")
    ext = xfrm.find(_NS_A + "ext")
    if off is None or ext is None:
        return None
    try:
        x = int(off.get("x", "0"))
        y = int(off.get("y", "0"))
        cx = int(ext.get("cx", "0"))
        cy = int(ext.get("cy", "0"))
        flip_h = xfrm.get("flipH") in ("1", "true")
        flip_v = xfrm.get("flipV") in ("1", "true")
        return (x, y, cx, cy, flip_h, flip_v)
    except ValueError:
        return None


def _recorrer_arbol_pptx(
    nodo,
    map_x=lambda v: float(v),
    map_y=lambda v: float(v),
    map_w=lambda v: float(v),
    map_h=lambda v: float(v),
) -> Tuple[List[Dict], List[Dict], List[Tuple[float, float, str]]]:
    """
    Recorre recursivamente <p:spTree> y grupos <p:grpSp> devolviendo:
      - pics: imágenes con sus coordenadas reales en diapositiva y recorte <a:srcRect>
      - marcas: recuadros/flechas/llamadas dibujadas encima de la captura (ej. recuadros rojos)
      - textos: párrafos ordenados espacialmente (y, x, texto)
    """
    pics: List[Dict] = []
    marcas: List[Dict] = []
    textos: List[Tuple[float, float, str]] = []

    for hijo in list(nodo):
        tag = hijo.tag

        if tag == _NS_P + "grpSp":
            grp_pr = hijo.find(_NS_P + "grpSpPr")
            xfrm = grp_pr.find(_NS_A + "xfrm") if grp_pr is not None else None
            if xfrm is not None:
                off = xfrm.find(_NS_A + "off")
                ext = xfrm.find(_NS_A + "ext")
                ch_off = xfrm.find(_NS_A + "chOff")
                ch_ext = xfrm.find(_NS_A + "chExt")
                if None not in (off, ext, ch_off, ch_ext):
                    try:
                        gx, gy = float(off.get("x", "0")), float(off.get("y", "0"))
                        gcx, gcy = float(ext.get("cx", "1")), float(ext.get("cy", "1"))
                        cx0, cy0 = float(ch_off.get("x", "0")), float(ch_off.get("y", "0"))
                        ccx = float(ch_ext.get("cx", "1")) or 1.0
                        ccy = float(ch_ext.get("cy", "1")) or 1.0
                        sub_p, sub_m, sub_t = _recorrer_arbol_pptx(
                            hijo,
                            map_x=lambda v, gx=gx, gcx=gcx, cx0=cx0, ccx=ccx: map_x(gx + (float(v) - cx0) * gcx / ccx),
                            map_y=lambda v, gy=gy, gcy=gcy, cy0=cy0, ccy=ccy: map_y(gy + (float(v) - cy0) * gcy / ccy),
                            map_w=lambda v, gcx=gcx, ccx=ccx: map_w(float(v) * gcx / ccx),
                            map_h=lambda v, gcy=gcy, ccy=ccy: map_h(float(v) * gcy / ccy),
                        )
                        pics.extend(sub_p)
                        marcas.extend(sub_m)
                        textos.extend(sub_t)
                        continue
                    except ValueError:
                        pass
            sub_p, sub_m, sub_t = _recorrer_arbol_pptx(hijo, map_x, map_y, map_w, map_h)
            pics.extend(sub_p)
            marcas.extend(sub_m)
            textos.extend(sub_t)

        elif tag == _NS_P + "pic":
            blip_fill = hijo.find(_NS_P + "blipFill")
            sp_pr = hijo.find(_NS_P + "spPr")
            if blip_fill is None:
                continue
            blip = blip_fill.find(_NS_A + "blip")
            if blip is None:
                continue
            rid = blip.get(_NS_R + "embed", "")
            if not rid:
                continue
            geom = _leer_xfrm_elemento(sp_pr)
            x, y, cx, cy = (geom[0], geom[1], geom[2], geom[3]) if geom else (0, 0, 0, 0)
            src_rect = blip_fill.find(_NS_A + "srcRect")
            crop = (0, 0, 0, 0)
            if src_rect is not None:
                try:
                    crop = (
                        int(src_rect.get("l", "0")),
                        int(src_rect.get("t", "0")),
                        int(src_rect.get("r", "0")),
                        int(src_rect.get("b", "0")),
                    )
                except ValueError:
                    crop = (0, 0, 0, 0)
            pics.append({
                "rid": rid,
                "x": map_x(x),
                "y": map_y(y),
                "cx": map_w(cx),
                "cy": map_h(cy),
                "crop": crop,
            })

        elif tag in (_NS_P + "sp", _NS_P + "cxnSp"):
            sp_pr = hijo.find(_NS_P + "spPr")
            geom = _leer_xfrm_elemento(sp_pr)
            x, y, cx, cy, flip_h, flip_v = geom if geom else (0, 0, 0, 0, False, False)
            rx, ry, rcx, rcy = map_x(x), map_y(y), map_w(cx), map_h(cy)

            lineas_sp: List[str] = []
            for par in hijo.iter(_NS_A + "p"):
                t_par = "".join(tn.text or "" for tn in par.iter(_NS_A + "t")).strip()
                if t_par:
                    lineas_sp.append(t_par)
            texto_sp = " ".join(lineas_sp).strip()
            if texto_sp:
                textos.append((ry, rx, "\n".join(lineas_sp)))

            if sp_pr is not None:
                ln_el = sp_pr.find(_NS_A + "ln")
                tiene_borde = ln_el is not None and ln_el.find(_NS_A + "noFill") is None
                tiene_relleno = sp_pr.find(_NS_A + "solidFill") is not None
                if tag == _NS_P + "cxnSp" and tiene_borde:
                    marcas.append({
                        "tipo": "linea",
                        "x": rx, "y": ry, "cx": rcx, "cy": rcy,
                        "flip_h": flip_h, "flip_v": flip_v,
                        "texto": "",
                    })
                elif tiene_borde and rcx > 0 and rcy > 0:
                    marcas.append({
                        "tipo": "caja",
                        "x": rx, "y": ry, "cx": rcx, "cy": rcy,
                        "relleno": tiene_relleno,
                        "texto": texto_sp,
                    })

    return (pics, marcas, textos)


def _componer_diapositiva_pptx_jpeg(
    num_slide: int,
    sld_cx: float,
    sld_cy: float,
    pics_resueltas: List[Tuple[Dict, bytes, str]],
    marcas: List[Dict],
) -> Optional[Tuple[bytes, str]]:
    """
    Renderiza una diapositiva de PowerPoint en un JPEG limpio de 1152 px de ancho,
    aplicando recortes reales (<a:srcRect>) y dibujando encima los recuadros rojos
    y flechas (<p:sp> / <p:cxnSp>) exactamente donde el autor marcó los menús y botones.
    """
    ancho_canvas = 1152
    alto_canvas = max(648, int(round(ancho_canvas * (sld_cy / max(1.0, sld_cx)))))
    esc_x = ancho_canvas / max(1.0, sld_cx)
    esc_y = alto_canvas / max(1.0, sld_cy)

    # 1) Motor nativo C++ de CineStock (PySide6 QImage + QPainter)
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
        from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

        lienzo = QImage(ancho_canvas, alto_canvas, QImage.Format.Format_RGB32)
        w_check = lienzo.width() if hasattr(lienzo, "width") else 0
        if isinstance(w_check, int) and w_check == ancho_canvas:
            lienzo.fill(QColor("#FFFFFF"))
            pintor = QPainter(lienzo)
            pintor.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pintor.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            alguna_pintada = False
            for info_pic, raw_bytes, _ in pics_resueltas:
                qim = QImage.fromData(raw_bytes)
                if qim.isNull():
                    continue
                cl, ct, cr, cb = info_pic["crop"]
                if any(v > 0 for v in (cl, ct, cr, cb)):
                    iw, ih = qim.width(), qim.height()
                    x0 = max(0, int(round(iw * cl / 100000.0)))
                    y0 = max(0, int(round(ih * ct / 100000.0)))
                    w0 = max(1, int(round(iw * (100000.0 - cl - cr) / 100000.0)))
                    h0 = max(1, int(round(ih * (100000.0 - ct - cb) / 100000.0)))
                    qim = qim.copy(x0, y0, min(w0, iw - x0), min(h0, ih - y0))

                px = int(round(info_pic["x"] * esc_x))
                py = int(round(info_pic["y"] * esc_y))
                pw = max(1, int(round(info_pic["cx"] * esc_x)))
                ph = max(1, int(round(info_pic["cy"] * esc_y)))
                if info_pic["cx"] <= 0 or info_pic["cy"] <= 0:
                    px, py, pw, ph = 0, 0, ancho_canvas, alto_canvas
                pintor.drawImage(QRect(px, py, pw, ph), qim)
                alguna_pintada = True

            if alguna_pintada:
                pluma_roja = QPen(QColor("#DC2626"))
                pluma_roja.setWidth(4)
                pluma_flecha = QPen(QColor("#DC2626"))
                pluma_flecha.setWidth(3)
                fuente_nota = QFont("Segoe UI", 9, QFont.Weight.Bold)

                for m in marcas:
                    mx = int(round(m["x"] * esc_x))
                    my = int(round(m["y"] * esc_y))
                    mw = int(round(m["cx"] * esc_x))
                    mh = int(round(m["cy"] * esc_y))
                    if m["tipo"] == "linea":
                        x1, x2 = (mx + mw, mx) if m.get("flip_h") else (mx, mx + mw)
                        y1, y2 = (my + mh, my) if m.get("flip_v") else (my, my + mh)
                        pintor.setPen(pluma_flecha)
                        pintor.drawLine(x1, y1, x2, y2)
                    elif m["tipo"] == "caja" and 8 <= mw <= int(ancho_canvas * 0.94) and 8 <= mh <= int(alto_canvas * 0.92):
                        txt_m = m.get("texto", "")
                        if m.get("relleno") and 1 <= len(txt_m) <= 130:
                            pintor.fillRect(QRect(mx, my, mw, mh), QColor(255, 251, 235, 235))
                            pintor.setPen(pluma_flecha)
                            pintor.drawRect(QRect(mx, my, mw, mh))
                            pintor.setFont(fuente_nota)
                            pintor.setPen(QColor("#991B1B"))
                            pintor.drawText(
                                QRect(mx + 4, my + 2, max(10, mw - 8), max(10, mh - 4)),
                                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                                txt_m,
                            )
                        elif not txt_m:
                            pintor.setBrush(Qt.BrushStyle.NoBrush)
                            pintor.setPen(pluma_roja)
                            pintor.drawRect(QRect(mx, my, mw, mh))

                pintor.fillRect(QRect(6, 6, 132, 24), QColor("#1E3A8A"))
                pintor.setPen(QColor("#FFFFFF"))
                pintor.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                pintor.drawText(QRect(6, 6, 132, 24), int(Qt.AlignmentFlag.AlignCenter), f"DIAPOSITIVA {num_slide}")
                pintor.end()

                ba = QByteArray()
                buf = QBuffer(ba)
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                if lienzo.save(buf, "JPG", 78):
                    datos_out = bytes(ba)
                    if datos_out:
                        return (datos_out, "image/jpeg")
            else:
                pintor.end()
    except Exception:
        pass

    # 2) Respaldo sin interfaz gráfica: enviar la captura principal de la diapositiva
    if pics_resueltas:
        principal = max(pics_resueltas, key=lambda item: len(item[1]))
        comp, mime_f = _comprimir_imagen_para_ia(principal[1], principal[2], ancho_max=1152)
        return (comp, mime_f)

    return None


def _extraer_diapositivas_pptx_ordenadas(
    ruta_pptx: Path,
    ranuras: Sequence[Tuple[str, ...]],
    max_diapositivas: int = MAX_DIAPOSITIVAS_VISUALES,
) -> List[AdjuntoIA]:
    """
    Inspecciona todas las diapositivas de un .pptx en orden real (1..N):
      - Descarta logos repetidos de plantilla (ej. pie de Yelmo / Cinépolis en >=4 slides).
      - Captura INCLUSO diapositivas sin texto que son 100 % captura de ruta de menú
        (como la Diapositiva 11 de ERP: Menú Principal > Almacenes > Recuentos...).
      - Dibuja encima los recuadros y flechas rojas del PowerPoint y empareja cada
        imagen con las anotaciones de esa diapositiva mediante `etiqueta_previa`.
    """
    try:
        with zipfile.ZipFile(ruta_pptx) as z:
            nombres = set(z.namelist())

            sld_cx, sld_cy = 9144000.0, 6858000.0
            if "ppt/presentation.xml" in nombres:
                try:
                    raiz_pres = ET.fromstring(z.read("ppt/presentation.xml"))
                    sld_sz = raiz_pres.find(_NS_P + "sldSz")
                    if sld_sz is not None:
                        sld_cx = float(sld_sz.get("cx", "9144000"))
                        sld_cy = float(sld_sz.get("cy", "6858000"))
                except Exception:
                    pass

            def _num_slide(nombre_xml: str) -> int:
                m = re.search(r"slide(\d+)\.xml$", nombre_xml)
                return int(m.group(1)) if m else 0

            slides_xml = sorted(
                (n for n in nombres if re.match(r"^ppt/slides/slide\d+\.xml$", n)),
                key=_num_slide,
            )
            if not slides_xml:
                return []

            rels_por_slide: Dict[int, Dict[str, str]] = {}
            frecuencia_media: Dict[str, int] = {}

            for s_xml in slides_xml:
                num_s = _num_slide(s_xml)
                ruta_rels = f"ppt/slides/_rels/slide{num_s}.xml.rels"
                mapa_r: Dict[str, str] = {}
                if ruta_rels in nombres:
                    try:
                        raiz_r = ET.fromstring(z.read(ruta_rels))
                        medios_vistos_slide: Set[str] = set()
                        for rel in raiz_r:
                            rid = rel.get("Id", "")
                            target = rel.get("Target", "")
                            if not rid or not target or target.startswith("http"):
                                continue
                            resuelta = posixpath.normpath(posixpath.join("ppt/slides", target.lstrip("/")))
                            if target.startswith("/"):
                                resuelta = target.lstrip("/")
                            if resuelta in nombres and Path(resuelta).suffix.lower() in _MIMES_IMAGEN:
                                mapa_r[rid] = resuelta
                                medios_vistos_slide.add(resuelta)
                        for med in medios_vistos_slide:
                            frecuencia_media[med] = frecuencia_media.get(med, 0) + 1
                    except Exception:
                        pass
                rels_por_slide[num_s] = mapa_r

            umbral_logo = 4 if len(slides_xml) >= 6 else 999
            candidatas_visuales: List[Tuple[int, float, AdjuntoIA]] = []

            for s_xml in slides_xml:
                num_s = _num_slide(s_xml)
                mapa_r = rels_por_slide.get(num_s, {})
                try:
                    raiz_s = ET.fromstring(z.read(s_xml))
                except Exception:
                    continue

                sp_tree = raiz_s.find(".//" + _NS_P + "spTree")
                if sp_tree is None:
                    continue

                pics, marcas, textos_pos = _recorrer_arbol_pptx(sp_tree)
                textos_pos.sort(key=lambda t: (round(t[0] / 120000.0), t[1]))
                lineas_slide = [t[2] for t in textos_pos if t[2]]

                nota_xml = f"ppt/notesSlides/notesSlide{num_s}.xml"
                if nota_xml in nombres:
                    try:
                        raiz_n = ET.fromstring(z.read(nota_xml))
                        notas_ln = [
                            "".join(tn.text or "" for tn in p.iter(_NS_A + "t")).strip()
                            for p in raiz_n.iter(_NS_A + "p")
                        ]
                        notas_limpias = [x for x in notas_ln if x and not x.isdigit()]
                        if notas_limpias:
                            lineas_slide.append("Notas: " + " ".join(notas_limpias))
                    except Exception:
                        pass

                pics_contenido: List[Tuple[Dict, bytes, str]] = []
                pics_todas: List[Tuple[Dict, bytes, str]] = []
                for info_p in pics:
                    med = mapa_r.get(info_p["rid"])
                    if not med:
                        continue
                    ext_m = Path(med).suffix.lower()
                    mime_m = _MIMES_IMAGEN.get(ext_m)
                    if not mime_m:
                        continue
                    raw_m = z.read(med)
                    pics_todas.append((info_p, raw_m, mime_m))
                    es_logo = frecuencia_media.get(med, 0) >= umbral_logo
                    if not es_logo and len(raw_m) >= 8_500:
                        pics_contenido.append((info_p, raw_m, mime_m))

                if not pics_contenido:
                    continue

                compuesta = _componer_diapositiva_pptx_jpeg(
                    num_s, sld_cx, sld_cy, pics_todas, marcas
                )
                if compuesta is None:
                    continue
                bytes_img, mime_img = compuesta

                resumen_txt = " | ".join(lineas_slide)
                aviso_marcas = (
                    " (contiene recuadros/flechas rojas señalando exactamente dónde pulsar)"
                    if marcas else ""
                )
                if resumen_txt:
                    etiqueta = (
                        f"=== DIAPOSITIVA {num_s}{aviso_marcas} ===\n"
                        f"Texto y llamadas en diapositiva {num_s}: {resumen_txt}"
                    )
                else:
                    etiqueta = (
                        f"=== DIAPOSITIVA {num_s}{aviso_marcas} ===\n"
                        f"(Pantalla visual de navegación/menú en el sistema; lee todos los niveles "
                        f"abiertos del menú y el elemento enmarcado en rojo en la captura):"
                    )

                norm_sl = normalizar(resumen_txt)
                relevancia = 10.0 + sum(12.0 for r in ranuras if _ranura_coincide_en(r, norm_sl))
                if num_s <= 15:
                    relevancia += 6.0

                ext_out = ".jpg" if mime_img == "image/jpeg" else ".png"
                candidatas_visuales.append((
                    num_s,
                    relevancia,
                    AdjuntoIA(
                        nombre=f"diapositiva_{num_s:02d}{ext_out}",
                        mime=mime_img,
                        datos_b64=base64.b64encode(bytes_img).decode("ascii"),
                        etiqueta_previa=etiqueta,
                    ),
                ))

            if len(candidatas_visuales) <= max_diapositivas:
                return [item[2] for item in candidatas_visuales]

            ordenadas_rel = sorted(candidatas_visuales, key=lambda x: (x[1], -x[0]), reverse=True)
            elegidas = sorted(ordenadas_rel[:max_diapositivas], key=lambda x: x[0])
            return [item[2] for item in elegidas]
    except Exception:
        return []


def _extraer_imagenes_docx_zip(ruta: Path, max_img: int = 24) -> List[AdjuntoIA]:
    """Extrae capturas de pantalla internas de un .docx en orden de aparición."""
    salida: List[AdjuntoIA] = []
    try:
        with zipfile.ZipFile(ruta) as z:
            def _num_media(n: str) -> Tuple[int, str]:
                m = re.search(r"(\d+)", Path(n).stem)
                return (int(m.group(1)) if m else 9999, n)

            nombres = sorted(
                (
                    n for n in z.namelist()
                    if ("/media/" in n or n.startswith("media/"))
                    and Path(n).suffix.lower() in _MIMES_IMAGEN
                ),
                key=_num_media,
            )
            for idx, nombre in enumerate(nombres, 1):
                raw = z.read(nombre)
                if len(raw) < 10_000 or len(raw) > 8 * 1024 * 1024:
                    continue
                ext = Path(nombre).suffix.lower()
                comp, mime_f = _comprimir_imagen_para_ia(raw, _MIMES_IMAGEN[ext])
                salida.append(
                    AdjuntoIA(
                        nombre=Path(nombre).name,
                        mime=mime_f,
                        datos_b64=base64.b64encode(comp).decode("ascii"),
                        etiqueta_previa=f"=== CAPTURA {idx} DEL MANUAL WORD ===",
                    )
                )
                if len(salida) >= max_img:
                    break
    except Exception:
        return []
    return salida


def extraer_adjuntos_multimodales_para_ia(
    biblioteca,
    pasajes: Sequence[PasajeManual],
    ranuras: Sequence[Tuple[str, ...]],
) -> List[AdjuntoIA]:
    """
    Prepara las diapositivas compuestas (.pptx), páginas PDF o capturas (.docx / .eml)
    del manual ganador para que la IA lea cada menú, pestaña, campo y botón exacto.
    """
    if biblioteca is None or not pasajes:
        return []

    primero = pasajes[0]
    ruta = _resolver_ruta_documento(biblioteca, primero.doc_id)
    if ruta is None or not ruta.is_file():
        return []

    try:
        tam = ruta.stat().st_size
    except OSError:
        return []
    if tam <= 0:
        return []

    texto_total = "\n".join(p.texto_seccion for p in pasajes if p.doc_id == primero.doc_id)
    sin_ocr = all(p.sin_texto_ocr for p in pasajes if p.doc_id == primero.doc_id)
    ext = ruta.suffix.lower()

    try:
        if ext == ".pptx":
            return _extraer_diapositivas_pptx_ordenadas(ruta, ranuras)

        if ext == ".docx":
            if sin_ocr or len(texto_total) < 3500:
                return _extraer_imagenes_docx_zip(ruta)
            return []

        if ext == ".pdf":
            if tam <= MAX_BYTES_PDF_DIRECTO:
                datos = ruta.read_bytes()
                n_pags = _contar_paginas_pdf_bytes(datos)
                if sin_ocr or n_pags <= MAX_PAGINAS_PDF_ADJUNTO or len(texto_total) < 4000:
                    return [
                        AdjuntoIA(
                            nombre=ruta.name,
                            mime="application/pdf",
                            datos_b64=base64.b64encode(datos).decode("ascii"),
                        )
                    ]
            if tam <= 60 * 1024 * 1024:
                return _extraer_imagenes_de_pdf_bytes(ruta.read_bytes())

        if ext in _MIMES_IMAGEN and tam <= MAX_BYTES_PDF_DIRECTO:
            datos = ruta.read_bytes()
            comp, mime_f = _comprimir_imagen_para_ia(datos, _MIMES_IMAGEN[ext])
            return [
                AdjuntoIA(
                    nombre=ruta.name,
                    mime=mime_f,
                    datos_b64=base64.b64encode(comp).decode("ascii"),
                )
            ]

        if ext == ".eml" and tam <= 40 * 1024 * 1024:
            with ruta.open("rb") as f:
                correo = BytesParser(policy=policy.default).parse(f)
            adjuntos_doc: List[Tuple[int, str, str, bytes]] = []
            imgs_correo: List[AdjuntoIA] = []
            for adj in correo.iter_attachments():
                nom = adj.get_filename() or ""
                ext_a = Path(nom).suffix.lower()
                raw = adj.get_payload(decode=True) or b""
                if not raw:
                    continue
                if ext_a in (".pdf", ".pptx", ".docx"):
                    norm_nom = normalizar(nom)
                    aciertos = sum(1 for r in ranuras if _ranura_coincide_en(r, norm_nom))
                    adjuntos_doc.append((aciertos, ext_a, nom, raw))
                elif ext_a in _MIMES_IMAGEN and len(raw) >= 10_000 and len(imgs_correo) < 12:
                    comp, mime_f = _comprimir_imagen_para_ia(raw, _MIMES_IMAGEN[ext_a])
                    imgs_correo.append(
                        AdjuntoIA(
                            nombre=nom or "captura.jpg",
                            mime=mime_f,
                            datos_b64=base64.b64encode(comp).decode("ascii"),
                        )
                    )
            if adjuntos_doc:
                adjuntos_doc.sort(key=lambda x: x[0], reverse=True)
                _, ext_top, nom_top, raw_top = adjuntos_doc[0]
                if ext_top == ".pdf" and len(raw_top) <= MAX_BYTES_PDF_DIRECTO:
                    if _contar_paginas_pdf_bytes(raw_top) <= MAX_PAGINAS_PDF_ADJUNTO:
                        return [
                            AdjuntoIA(
                                nombre=nom_top,
                                mime="application/pdf",
                                datos_b64=base64.b64encode(raw_top).decode("ascii"),
                            )
                        ]
                elif ext_top == ".pptx":
                    import tempfile as _tmpmod
                    with _tmpmod.TemporaryDirectory() as td:
                        p_tmp = Path(td) / "adj.pptx"
                        p_tmp.write_bytes(raw_top)
                        return _extraer_diapositivas_pptx_ordenadas(p_tmp, ranuras)
            if imgs_correo:
                return imgs_correo
    except Exception:
        return []

    return []


def _construir_clave_cache_ia(
    pregunta: str,
    ranuras: Sequence[Tuple[str, ...]],
    procedimiento: Optional[ProcedimientoCoincidente],
    pasajes: Sequence[PasajeManual],
) -> str:
    if not procedimiento and not pasajes:
        return ""
    firma_preg = normalizar(pregunta)
    firma_ranuras = "|".join(",".join(sorted(r)) for r in ranuras)
    firma_proc = procedimiento.id if procedimiento else ""
    firma_docs = ";".join(
        f"{p.doc_id}:{p.fecha}:{len(p.texto_seccion)}" for p in pasajes[:15]
    )
    semilla = f"{VERSION_CACHE_IA}||{firma_preg}||{firma_ranuras}||{firma_proc}||{firma_docs}||{len(pasajes)}"
    return hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:24]


def leer_cache_manual_ia(biblioteca, clave_cache: str) -> Optional[Tuple[str, str]]:
    if not clave_cache or biblioteca is None:
        return None
    carpeta = getattr(biblioteca, "carpeta", None)
    if not carpeta:
        return None
    ruta = Path(carpeta) / "cache_manuales_ia.json"
    if not ruta.is_file():
        return None
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        item = datos.get(clave_cache)
        if isinstance(item, dict) and item.get("texto_ia"):
            return (str(item["texto_ia"]), str(item.get("proveedor", "IA")))
    except Exception:
        return None
    return None


def guardar_cache_manual_ia(
    biblioteca,
    clave_cache: str,
    texto_ia: str,
    proveedor: str,
) -> None:
    if not clave_cache or not texto_ia or texto_ia == MARCA_NO_DISPONIBLE or biblioteca is None:
        return
    carpeta = getattr(biblioteca, "carpeta", None)
    if not carpeta:
        return
    ruta = Path(carpeta) / "cache_manuales_ia.json"
    try:
        datos: Dict = {}
        if ruta.is_file():
            try:
                datos = json.loads(ruta.read_text(encoding="utf-8"))
                if not isinstance(datos, dict):
                    datos = {}
            except Exception:
                datos = {}
        datos[clave_cache] = {"texto_ia": texto_ia, "proveedor": proveedor}
        if len(datos) > 120:
            claves = list(datos.keys())[-120:]
            datos = {k: datos[k] for k in claves}
        ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ======================================================================
# 5B) Capa conversacional: chat natural SOLO sobre su biblioteca
# ======================================================================
# El asistente saluda, se presenta, cuenta qué documentos/manuales guarda,
# los lista por tema/categoría y responde en primera persona como una IA.
# Nunca usa conocimiento externo: todo sale de biblioteca.db + conocimiento.json.

_PALABRAS_TEMA_VACIAS = frozenset("""
el la los las un una unos unas de del en y o a al por para con sin sobre
documento documentos manual manuales correo correos guia guias informacion
algo algun alguna algunos algunas tienes tiene hay existe existen guardado
guardados guardada guardadas hacer como se me te por favor gracias
""".split())


def _limpiar_tema_chat(texto: str) -> str:
    palabras = [
        p for p in (
            t.strip("¿?¡!.,;:")
            for t in normalizar(texto or "").split()
        )
        if p and p not in _PALABRAS_TEMA_VACIAS and len(p) > 1
    ]
    return " ".join(palabras).strip()


def detectar_intencion_conversacional(pregunta: str) -> Tuple[str, str]:
    """
    Devuelve (intencion, tema). ('', '') si es una consulta de manual normal
    («¿cómo se hace…?»), que debe seguir el flujo estricto de siempre.
    """
    norm = re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;:]+", " ", normalizar(pregunta or ""))).strip()
    if not norm:
        return ("", "")
    palabras = norm.split()

    # --- Saludos / gracias / despedidas: solo si son (casi) solo eso ---
    relleno_saludo = {
        "hola", "buenas", "buenos", "dias", "dia", "tardes", "tarde", "noches",
        "noche", "que", "tal", "estas", "esta", "todo", "bien", "saludos",
        "asistente", "cinestock", "equipo", "hey",
    }
    if palabras[0] in ("hola", "buenas", "buenos", "hey", "saludos") and \
            all(p in relleno_saludo for p in palabras):
        return ("saludo", "")
    if norm.startswith(("gracias", "muchas gracias", "mil gracias")) and len(palabras) <= 5:
        return ("gracias", "")
    if palabras[0] in ("adios", "chao") or norm.startswith(("hasta luego", "hasta pronto", "nos vemos")):
        return ("despedida", "")

    # --- Identidad / ayuda ---
    if any(f in norm for f in (
        "quien eres", "que eres", "como te llamas", "tu nombre",
        "que puedes hacer", "que sabes hacer", "en que puedes ayudar",
        "en que me puedes ayudar", "para que sirves", "que funciones tienes",
        "que sabes", "que conoces",
    )) or norm in ("ayuda", "ayudame", "necesito ayuda"):
        return ("identidad", "")

    # --- Categorías / temas disponibles ---
    if any(f in norm for f in (
        "que categorias", "que temas tienes", "que temas hay",
        "categorias tienes", "temas tienes", "tipos de documentos",
    )):
        return ("categorias", "")

    # --- Más recientes ---
    if any(f in norm for f in (
        "ultimos documentos", "documentos recientes", "mas recientes",
        "documentos mas nuevos", "ultimos manuales", "que hay de nuevo",
        "novedades tienes", "ultimas incorporaciones",
    )):
        return ("recientes", "")

    # --- Cuántos hay ---
    if re.search(r"\bcuantos\b.*\b(documentos|manuales|correos|archivos)\b", norm) or \
            re.search(r"\b(numero|cantidad) de (documentos|manuales|correos)\b", norm):
        return ("conteo", "")

    # --- Documentos sobre un tema concreto ---
    if re.search(r"\b(tienes|hay|existe|existen|guardas|busca|buscame)\b", norm):
        m = re.search(r"\b(?:sobre|acerca de)\s+(.+)$", norm)
        if m:
            return ("tema", _limpiar_tema_chat(m.group(1)))
        m = re.search(r"(?:documentos?|manual(?:es)?|correos?|guias?|algo|algun\w*)\s+(?:de|del)\s+(.+)$", norm)
        if m:
            return ("tema", _limpiar_tema_chat(m.group(1)))
        m = re.search(r"\bdocumentos?\b.*\btienes\b.*\b(?:sobre|de|del)\s+(.+)$", norm)
        if m:
            return ("tema", _limpiar_tema_chat(m.group(1)))

    # --- Inventario general de la biblioteca ---
    if any(f in norm for f in (
        "que documentos tienes", "que documentos hay", "cuales documentos",
        "que manuales tienes", "que manuales hay", "lista de documentos",
        "listame los documentos", "muestrame los documentos",
        "muestrame tu biblioteca", "ensename tu biblioteca",
        "que tienes guardado", "que hay guardado", "que tienes en tu biblioteca",
        "que hay en tu biblioteca", "que hay en conocimiento",
        "que tienes en conocimiento", "que documentos guardas",
        "dime que documentos", "ver tu biblioteca", "tu biblioteca",
    )) or (palabras[0] in ("muestrame", "ensename", "listame", "buscame", "busca")
            and any(p in norm for p in ("documento", "manual", "biblioteca", "correo"))):
        return ("inventario", "")

    return ("", "")


def _obtener_resumenes_biblioteca(biblioteca, limite: int = 500) -> List[DocumentoChatResumen]:
    if biblioteca is None:
        return []
    try:
        resultados = biblioteca.listar("todos", limite)
    except Exception:
        return []
    resumenes: List[DocumentoChatResumen] = []
    for r in resultados or []:
        try:
            resumenes.append(DocumentoChatResumen(
                doc_id=int(getattr(r, "id", 0)),
                titulo=str(getattr(r, "titulo", "") or "(sin título)"),
                tipo=str(getattr(r, "tipo", "") or ""),
                fecha=str(getattr(r, "fecha", "") or ""),
                categoria=str(getattr(r, "categoria", "") or ""),
                estado=str(getattr(r, "estado", "") or ""),
            ))
        except Exception:
            continue
    return resumenes


def _filtrar_resumenes_por_tema(
    resumenes: Sequence[DocumentoChatResumen], tema: str, limite: int = 40
) -> List[DocumentoChatResumen]:
    tema_limpio = _limpiar_tema_chat(tema)
    if not tema_limpio:
        return list(resumenes[:limite])
    _, ranuras = extraer_terminos_consulta(tema_limpio)
    if not ranuras:
        return []
    salida: List[DocumentoChatResumen] = []
    for r in resumenes:
        texto_norm = normalizar(f"{r.titulo} {r.categoria} {r.tipo}")
        if all(_ranura_coincide_en(ranura, texto_norm) for ranura in ranuras):
            salida.append(r)
        if len(salida) >= limite:
            break
    return salida


def _texto_conversacional_local(
    intencion: str,
    tema: str,
    resumenes: Sequence[DocumentoChatResumen],
    filtrados: Sequence[DocumentoChatResumen],
    total: int,
    biblioteca_vacia: bool,
) -> str:
    """Respuesta natural en primera persona, construida solo con datos reales."""
    procs = _cargar_lista_procedimientos()
    titulos_procs = [str(p.get("titulo", "")).strip() for p in procs if p.get("titulo")]

    if intencion == "saludo":
        if biblioteca_vacia:
            return (
                "¡Hola! 👋 Encantado de saludarte. Soy el asistente de CineStock y "
                "trabajo únicamente con los manuales y correos que guardas en "
                "📚 Conocimiento. De momento todavía no tienes documentos cargados, "
                "pero en cuanto añadas tus PDF, presentaciones o correos podré "
                "explicarte cualquier procedimiento paso a paso. ¿Por dónde quieres empezar?"
            )
        return (
            f"¡Hola! 👋 Me alegro de verte. Soy tu asistente del cine y ahora mismo "
            f"tengo a mano **{total} documento(s)** en tu biblioteca de 📚 Conocimiento, "
            "además de los procedimientos confirmados. Pregúntame con tus palabras "
            "cómo se hace cualquier proceso (inventario, mermas, un albarán…) o dime "
            "«¿qué documentos tienes?» y te los enseño. ¿En qué te ayudo?"
        )

    if intencion == "gracias":
        return (
            "¡De nada! 😊 Para eso estoy. Si necesitas otro procedimiento, "
            "quieres que te enseñe qué documentos tengo guardados o que te abra "
            "algún manual original, pídeme lo que sea."
        )

    if intencion == "despedida":
        return (
            "¡Hasta luego! 👋 Aquí me quedo con tu biblioteca preparada para "
            "cuando vuelvas. Que vaya bien el turno."
        )

    if intencion == "identidad":
        lineas = [
            "Soy el asistente de operativa de CineStock 🤖, como un compañero que se ha "
            "leído de cabo a rabo todo lo que guardas en 📚 Conocimiento.",
            "",
            "Esto es lo que puedo hacer por ti, siempre usando **solo** tu biblioteca "
            "(no busco nada fuera ni me invento respuestas):",
            "• Si me preguntas «¿cómo se hace…?», te preparo el manual paso a paso del "
            "proceso exacto, y si no lo tengo te lo digo claramente con un «No disponible».",
            "• Si me dices «¿qué documentos tienes?» o «¿tienes algo sobre mermas?», "
            "te enseño lo que tengo guardado y te lo abro con un clic.",
            "• También entiendo saludos y conversación normal: pregúntame con tus palabras.",
        ]
        if titulos_procs:
            lineas.append("")
            lineas.append("Procedimientos confirmados que domino: " +
                           "; ".join(titulos_procs) + ".")
        if not biblioteca_vacia:
            lineas.append(f"En total consulto {total} documento(s) de tu biblioteca.")
        return "\n".join(lineas)

    if biblioteca_vacia and intencion in ("inventario", "tema", "conteo", "categorias", "recientes"):
        return (
            "Ahora mismo mi biblioteca está vacía: todavía no hay correos ni manuales "
            "en 📚 Conocimiento, así que no tengo documentos que enseñarte. "
            "Añade tus PDF, Word, PowerPoint o correos desde 📚 Conocimiento → "
            "«➕ Añadir correos / documentos…» y enseguida podré listártelos y "
            "explicarte sus procedimientos paso a paso."
        )

    if intencion == "conteo":
        n_correos = sum(1 for r in resumenes if r.tipo == "correo")
        n_docs = len(resumenes) - n_correos
        return (
            f"Tengo guardados **{total} documento(s)** en tu biblioteca: "
            f"{n_docs} documento(s)/manual(es) y {n_correos} correo(s). "
            "Si quieres, dime «¿qué documentos tienes?» y te los enseño todos, "
            "o pregúntame por un tema concreto, por ejemplo «¿tienes algo sobre inventarios?»."
        )

    if intencion == "categorias":
        conteo_cat: Dict[str, int] = {}
        for r in resumenes:
            conteo_cat[r.categoria or "Otros"] = conteo_cat.get(r.categoria or "Otros", 0) + 1
        temas = "; ".join(
            f"{cat} ({n})" for cat, n in sorted(conteo_cat.items(), key=lambda x: -x[1])
        )
        return (
            f"Mis documentos están organizados en estos temas: {temas}. "
            "Dime un tema («¿qué tienes de inventario?») y te enseño sus documentos, "
            "o pregúntame directamente cómo se hace un proceso y te preparo el paso a paso."
        )

    if intencion == "recientes":
        return (
            "Estos son los documentos más recientes que tengo en tu biblioteca, "
            "ordenados de más nuevo a más antiguo. Pulsa en cualquiera para abrirlo. "
            "Si buscas un proceso concreto, pregúntame «¿cómo se hace…?» y te preparo la guía."
        )

    if intencion == "tema":
        tema_mostrar = tema.strip() or "ese tema"
        if not filtrados:
            return (
                f"He mirado bien en mi biblioteca y no tengo ningún documento sobre "
                f"«{tema_mostrar}». No quiero inventarme nada: solo respondo con lo que "
                "tienes guardado en 📚 Conocimiento. Si tienes el manual fuera de CineStock, "
                "añádelo desde 📚 Conocimiento y lo aprendo al instante. "
                "Si quieres, dime «¿qué documentos tienes?» y te enseño todo lo que sí tengo."
            )
        return (
            f"¡Sí! Sobre «{tema_mostrar}» tengo **{len(filtrados)} documento(s)** en tu "
            "biblioteca. Te los dejo aquí debajo y puedes abrir el original con un clic. "
            f"Y si lo que quieres es el paso a paso, pregúntame «¿cómo se hace lo de {tema_mostrar}?» "
            "y te preparo la guía completa."
        )

    # inventario general
    conteo_cat: Dict[str, int] = {}
    for r in resumenes:
        conteo_cat[r.categoria or "Otros"] = conteo_cat.get(r.categoria or "Otros", 0) + 1
    resumen_cat = ""
    if conteo_cat:
        resumen_cat = " Por temas: " + "; ".join(
            f"{cat} ({n})" for cat, n in sorted(conteo_cat.items(), key=lambda x: -x[1])[:6]
        ) + "."
    extra_procs = ""
    if titulos_procs:
        extra_procs = (" Además domino estos procedimientos confirmados: " +
                       "; ".join(titulos_procs) + ".")
    return (
        f"Claro, te enseño mi biblioteca. Ahora mismo tengo **{total} documento(s)** "
        f"guardados en 📚 Conocimiento.{resumen_cat}{extra_procs} "
        "Aquí debajo tienes la lista: abre cualquier documento con un clic, o pregúntame "
        "«¿cómo se hace…?» y te preparo el manual paso a paso de ese proceso."
    )


def construir_contexto_conversacional(respuesta: RespuestaAsistente) -> str:
    """Inventario real (solo datos de la biblioteca) para que la IA lo narre."""
    lineas = [
        f"Total de documentos en la biblioteca: {respuesta.total_documentos}",
    ]
    if respuesta.documentos_chat:
        lineas.append("Documentos relevantes para esta conversación:")
        for d in respuesta.documentos_chat[:40]:
            extra = " · ".join(x for x in (d.tipo, d.categoria, d.fecha) if x)
            lineas.append(f"- (id {d.doc_id}) {d.titulo}" + (f" [{extra}]" if extra else ""))
    procs = _cargar_lista_procedimientos()
    if procs:
        lineas.append("Procedimientos confirmados: " +
                      "; ".join(str(p.get("titulo", "")) for p in procs if p.get("titulo")))
    if respuesta.texto_chat:
        lineas.append("Respuesta ya preparada con esos datos:\n" + respuesta.texto_chat)
    return "\n".join(lineas)


_PROMPT_SISTEMA_CHAT = (
    "Eres el asistente de CineStock, el compañero IA del personal de un cine. "
    "Hablas en español, en primera persona, con naturalidad y cercanía (como una "
    "persona: saluda, responde a lo que te preguntan y ofrece ayuda), pero tu "
    "conocimiento se limita EXCLUSIVAMENTE a los datos de la biblioteca del cine "
    "que recibes en el mensaje (documentos, manuales y procedimientos confirmados). "
    "REGLAS ESTRICTAS: 1) No uses conocimiento externo ni de actualidad (nada de "
    "deportes, noticias, cultura general o datos que no estén en los datos "
    "recibidos). 2) No inventes documentos, títulos, fechas ni procedimientos: "
    "menciona solo los que aparezcan en los datos. 3) Si te preguntan algo que no "
    "está en la biblioteca, dilo con amabilidad y ofrece enseñar los documentos "
    "que sí tienes o explicar un procedimiento guardado. 4) Sé breve y claro: "
    "2-5 frases o una lista corta. Sin bloques de código ni formato de manual "
    "paso a paso (eso ya lo hace otra parte del programa)."
)


def sintetizar_respuesta_conversacional_ia(
    pregunta: str,
    contexto_biblioteca: str,
    historial_turnos: Optional[Sequence[Tuple[str, str]]] = None,
    timeout: float = 45.0,
) -> Tuple[str, str, str]:
    """Narra la respuesta del chat con IA, anclada solo al inventario real."""
    deteccion = detectar_proveedor_ia()
    if not deteccion or not contexto_biblioteca.strip():
        return ("", "", "")
    proveedor, api_key = deteccion

    resumen_historial = ""
    if historial_turnos:
        partes_h = [
            f"Usuario: {q}\nAsistente: {a[:200]}"
            for q, a in list(historial_turnos)[-2:] if q and a
        ]
        if partes_h:
            resumen_historial = "CONVERSACIÓN PREVIA:\n" + "\n".join(partes_h) + "\n\n"

    mensaje_usuario = (
        f"{resumen_historial}"
        f"EL USUARIO DICE:\n{pregunta}\n\n"
        f"DATOS REALES DE SU BIBLIOTECA (tu única fuente):\n{contexto_biblioteca}"
    )

    try:
        if proveedor == "OpenAI":
            modelo = _leer_clave_env_local("OPENAI_MODEL") or "gpt-4.1-mini"
            datos = _peticion_json_https(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                payload={
                    "model": modelo,
                    "temperature": 0.3,
                    "max_tokens": 700,
                    "messages": [
                        {"role": "system", "content": _PROMPT_SISTEMA_CHAT},
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
            return (texto, f"OpenAI · {modelo}", "") if texto else ("", proveedor, "")

        if proveedor == "Gemini":
            modelo = _leer_clave_env_local("GEMINI_MODEL") or "gemini-2.5-flash"
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{modelo}:generateContent"
            )
            datos = _peticion_json_https(
                url,
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                payload={
                    "system_instruction": {"parts": [{"text": _PROMPT_SISTEMA_CHAT}]},
                    "contents": [{"parts": [{"text": mensaje_usuario}]}],
                    "generationConfig": {"temperature": 0.3, "maxOutputTokens": 700},
                },
                timeout=timeout,
            )
            candidatos = datos.get("candidates", [])
            partes = (
                candidatos[0].get("content", {}).get("parts", [])
                if candidatos else []
            )
            texto = "".join(p.get("text", "") for p in partes).strip()
            return (texto, f"Gemini · {modelo}", "") if texto else ("", proveedor, "")
    except urllib.error.HTTPError as err:
        return ("", proveedor, _explicar_http_error(proveedor, err))
    except Exception:
        return ("", proveedor, "IA no disponible ahora mismo.")
    return ("", "", "")


def consultar_base_local(
    pregunta: str,
    biblioteca=None,
    historial_preguntas: Optional[Sequence[str]] = None,
) -> RespuestaAsistente:
    # 0) ¿Es conversación (saludo, «qué documentos tienes»…)? Respuesta natural
    intencion_chat, tema_chat = detectar_intencion_conversacional(pregunta)
    if intencion_chat:
        total_chat = 0
        vacia_chat = True
        error_chat: Optional[str] = None
        resumenes_chat: List[DocumentoChatResumen] = []
        if biblioteca is not None:
            try:
                stats_chat = biblioteca.estadisticas()
                total_chat = int(stats_chat.get("total", 0))
                vacia_chat = total_chat == 0
                if not vacia_chat:
                    resumenes_chat = _obtener_resumenes_biblioteca(biblioteca)
            except Exception as e:
                error_chat = str(e)
        if intencion_chat == "tema":
            documentos_chat = _filtrar_resumenes_por_tema(resumenes_chat, tema_chat)
            if not documentos_chat and biblioteca is not None and tema_chat:
                # Respaldo por contenido completo (FTS): el tema puede estar
                # dentro del manual aunque no figure en su título.
                try:
                    por_id = {r.doc_id: r for r in resumenes_chat}
                    documentos_chat = [
                        por_id[int(r.id)] for r in biblioteca.buscar(tema_chat, "todos", 40)
                        if int(getattr(r, "id", 0)) in por_id
                    ][:40]
                except Exception:
                    documentos_chat = []
        elif intencion_chat in ("inventario", "recientes"):
            documentos_chat = list(resumenes_chat[:40])
        else:
            documentos_chat = []
        return RespuestaAsistente(
            pregunta=pregunta,
            intencion_chat=intencion_chat,
            texto_chat=_texto_conversacional_local(
                intencion_chat, tema_chat, resumenes_chat,
                documentos_chat, total_chat, vacia_chat,
            ),
            documentos_chat=documentos_chat,
            biblioteca_vacia=vacia_chat,
            total_documentos=total_chat,
            error_biblioteca=error_chat,
        )

    terminos, ranuras = extraer_terminos_consulta(pregunta, historial_preguntas)
    proc = buscar_procedimiento_confirmado(pregunta, terminos)

    pasajes: List[PasajeManual] = []
    adjuntos_ia: List[AdjuntoIA] = []
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
                    biblioteca, pregunta, ranuras, max_documentos=2
                )
                if pasajes:
                    adjuntos_ia = extraer_adjuntos_multimodales_para_ia(
                        biblioteca, pasajes, ranuras
                    )
        except Exception as e:
            error_bib = str(e)

    clave_cache = _construir_clave_cache_ia(pregunta, ranuras, proc, pasajes)

    return RespuestaAsistente(
        pregunta=pregunta,
        terminos=terminos,
        procedimiento=proc,
        pasajes=pasajes,
        adjuntos_ia=adjuntos_ia,
        clave_cache_ia=clave_cache,
        biblioteca_vacia=vacia,
        total_documentos=total_docs,
        error_biblioteca=error_bib,
    )


# ======================================================================
# 6) Redactor Multimodal de Hoja de Manual Operativo (OpenAI / Gemini)
# ======================================================================

MARCA_NO_DISPONIBLE = "NO_DISPONIBLE"


def _leer_clave_env_local(nombre: str) -> str:
    valor = (os.environ.get(nombre) or "").strip()
    if valor:
        return valor
    posibles = [
        Path(__file__).resolve().parents[2] / ".env",
        Path.cwd() / ".env",
    ]
    for ruta_env in posibles:
        if not ruta_env.is_file():
            continue
        try:
            for linea in ruta_env.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = linea.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                if k.strip() == nombre:
                    val = v.strip().strip('"').strip("'")
                    if val:
                        return val
        except Exception:
            continue
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

    por_doc: Dict[int, List[PasajeManual]] = {}
    orden_docs: List[int] = []
    for pasaje in respuesta.pasajes:
        if pasaje.doc_id not in por_doc:
            por_doc[pasaje.doc_id] = []
            orden_docs.append(pasaje.doc_id)
        por_doc[pasaje.doc_id].append(pasaje)

    for idx_d, doc_id in enumerate(orden_docs, 1):
        lista = por_doc[doc_id]
        cab = lista[0]
        enc = f"[DOCUMENTO {idx_d}: {cab.titulo_documento} ({cab.tipo_documento.upper()})]"
        partes_doc = [enc]
        for pas in lista:
            if pas.sin_texto_ocr:
                partes_doc.append(
                    "(Contenido visual en el PDF/capturas adjuntas: lee cada pantalla, menú y botón)."
                )
            else:
                etiq_s = f"--- {pas.titulo_seccion} ---\n" if pas.titulo_seccion else ""
                partes_doc.append(f"{etiq_s}{pas.texto_seccion}")
        bloques.append("\n".join(partes_doc)[:38000])

    return "\n\n================\n\n".join(bloques)


_PROMPT_SISTEMA_RAG = (
    "Eres el redactor técnico experto de manuales operativos de CineStock para personal de cine.\n"
    "Tu misión es examinar TODAS las diapositivas, capturas de pantalla de ERP/sistemas, "
    "menús desplegados, recuadros rojos, flechas y textos del documento adjunto y elaborar una "
    "HOJA DE MANUAL PASO A PASO exacta, exhaustiva y súper clara (como una ficha operativa de "
    "Word o Excel), para que cualquier compañero pueda ejecutar el procedimiento clic por clic "
    "delante del ordenador SIN abrir la presentación ni el PDF.\n\n"
    "REGLA 1 — CUÁNDO RESPONDER 'NO_DISPONIBLE':\n"
    "Responde ÚNICAMENTE con la palabra exacta NO_DISPONIBLE si el documento proporcionado "
    "trata en realidad de OTRO trámite ajeno al preguntado (por ejemplo: preguntan por "
    "'solicitud de pedido' y habla de vacaciones; o preguntan por 'conciliación de inventario' "
    "y habla de conciliación familiar o solo lo nombra de pasada sin explicar el proceso).\n\n"
    "REGLA 2 — NIVEL DE DETALLE MICRO-PASO A MICRO-PASO (¡PROHIBIDO RESUMIR PANTALLAS!):\n"
    "No hagas resúmenes genéricos. Desglosa CADA ACCIÓN en orden cronológico leyendo lo que se "
    "ve dentro de cada captura de pantalla y en los recuadros/flechas rojas:\n"
    "- Paso 1: Entrada a la plataforma (ej.: entrar en [ERP Global], escribir [ID Usuario] y "
    "[Contraseña] y pinchar en [Conectar]).\n"
    "- Paso 2: Ruta completa de navegación leyendo todos los menús desplegados en cascada "
    "(ej.: desplazarse a [Menú Principal] ➔ [Almacenes] ➔ [Recuentos de Inventario] ➔ "
    "[Preparación Evento de Recuento] ➔ [Creación de Evento de Recuento]).\n"
    "- Paso 3: Qué pestaña seleccionar (ej.: elegir la pestaña [Añadir un nuevo valor] o "
    "[Buscar un valor existente]).\n"
    "- Paso 4 y siguientes: Cada campo concreto de la pantalla, qué dato/código escribir o "
    "seleccionar en él (ej.: en [ID Evento Ejecución] escribir el código para identificar el "
    "evento y pinchar en [Añadir]), qué opciones marcar, qué botones pulsar ([Guardar], "
    "[Ejecutar], [Actualizar], [Aceptar], etc.) y cómo pasar a la siguiente fase hasta completar "
    "todo el flujo.\n\n"
    "REGLA 3 — FORMATO OBLIGATORIO DE TABLA DE MANUAL (4 COLUMNAS SEPARADAS POR '|'):\n"
    "Usa SIEMPRE esta estructura exacta (sin bloques ```):\n\n"
    "# [Título claro del manual operativo]\n"
    "SISTEMA: [Sistema visto en las capturas, ej.: ERP Global (PeopleSoft) / Cinema Manager]\n"
    "OBJETIVO: [1 frase clara explicando qué se logra con este manual]\n\n"
    "## FASE 1: [Nombre de la primera fase, ej.: Acceso a ERP y Creación del Evento de Recuento]\n"
    "PASO 1 | [Pantalla / Dónde] | [Qué hacer exactamente paso a paso con menús/campos/botones entre corchetes [así] y rutas con ➔] | [Valor a poner o aviso clave]\n"
    "PASO 2 | [Pantalla / Dónde] | [Qué hacer exactamente...] | [Valor / aviso]\n"
    "PASO 3 | [Pantalla / Dónde] | [Qué hacer exactamente...] | [Valor / aviso]\n\n"
    "## FASE 2: [Nombre de la siguiente fase del documento, si tiene varias fases]\n"
    "PASO 4 | [Pantalla / Dónde] | [Qué hacer exactamente...] | [Valor / aviso]\n"
    "(... continúa numerando PASO 5, PASO 6, PASO 7... cubriendo TODAS las fases y pantallas relevantes)\n\n"
    "## ⚠️ NOTAS IMPORTANTES\n"
    "- [Avisos marcados con IMPORTANTE / ATENCIÓN en las diapositivas o errores a evitar]\n\n"
    "## ✅ COMPROBACIÓN FINAL\n"
    "- [Cómo comprobar en el sistema que el inventario/proceso ha quedado correctamente cerrado]"
)


def _crear_contexto_ssl() -> ssl.SSLContext:
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
        return f"Clave de {proveedor} no válida en .env (HTTP 401). Mostrando guía local."
    if err.code == 429:
        if "insufficient_quota" in str(detalle).lower() or "quota" in str(detalle).lower():
            return (
                f"Tu cuenta de {proveedor} no tiene saldo disponible (HTTP 429). "
                "Recarga saldo en OpenAI o usa GEMINI_API_KEY en .env para activar la IA."
            )
        return f"Límite temporal de {proveedor} (HTTP 429). Inténtalo en unos segundos."
    return f"{proveedor} devolvió HTTP {err.code} ({str(detalle)[:90]})."


def _construir_contenido_usuario_openai(
    mensaje_usuario: str,
    adjuntos: Sequence[AdjuntoIA],
    detalle_imagen: str = "high",
) -> object:
    if not adjuntos:
        return mensaje_usuario
    bloques: List[Dict] = [{"type": "text", "text": mensaje_usuario}]
    for adj in adjuntos:
        if adj.etiqueta_previa:
            bloques.append({"type": "text", "text": adj.etiqueta_previa})
        if adj.mime == "application/pdf":
            bloques.append({
                "type": "file",
                "file": {
                    "filename": adj.nombre or "manual.pdf",
                    "file_data": f"data:application/pdf;base64,{adj.datos_b64}",
                },
            })
        elif adj.mime.startswith("image/"):
            bloques.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{adj.mime};base64,{adj.datos_b64}",
                    "detail": detalle_imagen,
                },
            })
    return bloques


def sintetizar_con_ia(
    pregunta: str,
    contexto_rag: str,
    historial_turnos: Optional[Sequence[Tuple[str, str]]] = None,
    adjuntos_ia: Optional[Sequence[AdjuntoIA]] = None,
    timeout: float = 95.0,
) -> Tuple[str, str, str]:
    deteccion = detectar_proveedor_ia()
    lista_adjuntos = list(adjuntos_ia or [])
    if not deteccion or (not contexto_rag.strip() and not lista_adjuntos):
        return ("", "", "")

    proveedor, api_key = deteccion

    resumen_historial = ""
    if historial_turnos:
        ultimos = list(historial_turnos)[-2:]
        partes_h = [f"Usuario: {q}\nAsistente: {a[:240]}" for q, a in ultimos if q and a]
        if partes_h:
            resumen_historial = "CONVERSACIÓN PREVIA:\n" + "\n".join(partes_h) + "\n\n"

    nota_visual = (
        f"\n\nSE ADJUNTAN {len(lista_adjuntos)} DIAPOSITIVAS / PÁGINAS VISUALES EN ORDEN. "
        "Lee con lupa cada menú desplegado, pestaña, nombre de campo, botón y nota roja "
        "en las capturas para detallar TODOS los pasos clic por clic.\n"
        if lista_adjuntos else ""
    )

    mensaje_usuario = (
        f"{resumen_historial}"
        f"PROCESO CONSULTADO POR EL USUARIO:\n{pregunta}\n\n"
        f"TEXTO EXTRAÍDO DEL DOCUMENTO:\n{contexto_rag}"
        f"{nota_visual}"
    )

    timeout_efectivo = max(timeout, 120.0) if lista_adjuntos else timeout

    try:
        if proveedor == "OpenAI":
            modelo_fijo = _leer_clave_env_local("OPENAI_MODEL")
            if modelo_fijo:
                modelos_candidatos = [modelo_fijo]
            elif lista_adjuntos:
                modelos_candidatos = ["gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"]
            else:
                modelos_candidatos = ["gpt-4.1-mini", "gpt-4o-mini"]

            url_oa = "https://api.openai.com/v1/chat/completions"
            cabeceras_oa = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }

            ultimo_err: Optional[Exception] = None
            datos: Optional[Dict] = None
            modelo_usado = modelos_candidatos[0]

            for mod_actual in modelos_candidatos:
                modelo_usado = mod_actual
                # Peticiones gigantes (decenas de diapositivas en alta detalle)
                # provocan HTTP 429 por límite de tokens/minuto: se empieza
                # con detalle alto solo si hay pocas imágenes, y cada reintento
                # con otro modelo reduce imágenes y detalle.
                det_img = "high" if len(lista_adjuntos) <= 10 else "low"
                if mod_actual == "gpt-4o-mini":
                    det_img = "low"
                limite_adj = 28 if mod_actual == modelos_candidatos[0] else 16
                adjs_envio = lista_adjuntos[:limite_adj]
                contenido_usr = _construir_contenido_usuario_openai(
                    mensaje_usuario, adjs_envio, detalle_imagen=det_img
                )
                try:
                    datos = _peticion_json_https(
                        url_oa,
                        headers=cabeceras_oa,
                        payload={
                            "model": mod_actual,
                            "temperature": 0.0,
                            "max_tokens": 7500,
                            "messages": [
                                {"role": "system", "content": _PROMPT_SISTEMA_RAG},
                                {"role": "user", "content": contenido_usr},
                            ],
                        },
                        timeout=timeout_efectivo,
                    )
                    break
                except urllib.error.HTTPError as err_oa:
                    ultimo_err = err_oa
                    if err_oa.code == 401:
                        raise
                    if err_oa.code == 429:
                        # Límite temporal: NO rendirse. Se prueba el siguiente
                        # modelo con menos imágenes/detalle, y si OpenAI sigue
                        # saturado se cae a Gemini (abajo) si hay clave.
                        continue
                    if err_oa.code == 400 and any(a.mime == "application/pdf" for a in adjs_envio):
                        imgs_pdf: List[AdjuntoIA] = []
                        for adj in adjs_envio:
                            if adj.mime == "application/pdf":
                                try:
                                    imgs_pdf.extend(
                                        _extraer_imagenes_de_pdf_bytes(base64.b64decode(adj.datos_b64))
                                    )
                                except Exception:
                                    pass
                            else:
                                imgs_pdf.append(adj)
                        try:
                            datos = _peticion_json_https(
                                url_oa,
                                headers=cabeceras_oa,
                                payload={
                                    "model": mod_actual,
                                    "temperature": 0.0,
                                    "max_tokens": 7500,
                                    "messages": [
                                        {"role": "system", "content": _PROMPT_SISTEMA_RAG},
                                        {
                                            "role": "user",
                                            "content": _construir_contenido_usuario_openai(
                                                mensaje_usuario, imgs_pdf, detalle_imagen=det_img
                                            ),
                                        },
                                    ],
                                },
                                timeout=timeout_efectivo,
                            )
                            break
                        except urllib.error.HTTPError as err_oa2:
                            ultimo_err = err_oa2
                            continue
                    continue

            if datos is None:
                # OpenAI saturado (429 en todos los modelos): si también hay
                # clave de Gemini en .env, se continúa con Gemini en vez de
                # dejar al usuario con el documento en crudo.
                clave_gemini = _leer_clave_env_local("GEMINI_API_KEY")
                if clave_gemini and isinstance(ultimo_err, urllib.error.HTTPError) \
                        and ultimo_err.code == 429:
                    proveedor, api_key = "Gemini", clave_gemini
                elif isinstance(ultimo_err, urllib.error.HTTPError):
                    raise ultimo_err
                elif ultimo_err:
                    raise ultimo_err
                else:
                    return ("", proveedor, "No se obtuvo respuesta de OpenAI.")

            if proveedor == "OpenAI" and datos is not None:
                texto = (
                    datos.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if texto.upper() == MARCA_NO_DISPONIBLE or texto.upper().startswith("NO_DISPONIBLE\n"):
                    return (MARCA_NO_DISPONIBLE, f"OpenAI · {modelo_usado}", "")
                return (texto, f"OpenAI · {modelo_usado}", "")

        if proveedor == "Gemini":
            modelo = _leer_clave_env_local("GEMINI_MODEL") or "gemini-2.5-flash"
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{modelo}:generateContent"
            )
            partes_usr: List[Dict] = [{"text": mensaje_usuario}]
            for adj in lista_adjuntos[:20]:
                if adj.etiqueta_previa:
                    partes_usr.append({"text": adj.etiqueta_previa})
                partes_usr.append({
                    "inline_data": {
                        "mime_type": adj.mime,
                        "data": adj.datos_b64,
                    }
                })

            datos = _peticion_json_https(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                payload={
                    "system_instruction": {"parts": [{"text": _PROMPT_SISTEMA_RAG}]},
                    "contents": [{"role": "user", "parts": partes_usr}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 8000},
                },
                timeout=timeout_efectivo,
            )
            partes = (
                datos.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )
            texto = "".join(p.get("text", "") for p in partes).strip()
            if texto.upper() == MARCA_NO_DISPONIBLE or texto.upper().startswith("NO_DISPONIBLE\n"):
                return (MARCA_NO_DISPONIBLE, f"Gemini · {modelo}", "")
            return (texto, f"Gemini · {modelo}", "")

    except urllib.error.HTTPError as e:
        return ("", proveedor, _explicar_http_error(proveedor, e))
    except Exception as e:
        motivo = "certificado SSL" if "SSL" in str(e).upper() else "tiempo de espera o red"
        return ("", proveedor, f"IA no disponible ({motivo}).")

    return ("", "", "")


# ======================================================================
# 7) Hoja de Manual Estilo Word / Excel en Colores + Exportación Directa
# ======================================================================

FUENTE_UI = "'Segoe UI', 'Calibri', 'Trebuchet MS', Arial, sans-serif"

PALETA_PASOS: Tuple[Tuple[str, str, str, str], ...] = (
    ("#2563EB", "#2563EB", "#1D4ED8", "#F8FAFF"),  # Azul Real
    ("#4F46E5", "#4F46E5", "#4338CA", "#F9F9FF"),  # Índigo
    ("#0284C7", "#0284C7", "#0369A1", "#F7FCFF"),  # Océano
    ("#0D9488", "#0D9488", "#0F766E", "#F7FEFC"),  # Turquesa
)

ICONOS_DOC = {
    "correo": "✉️ Correo",
    "pdf": "📕 PDF",
    "pptx": "📊 Presentación",
    "docx": "📘 Word",
    "xlsx": "📗 Excel",
    "texto": "📄 Manual",
    "zip": "🗜 ZIP",
    "imagen": "🖼 Imagen",
}


@dataclass
class PasoHojaManual:
    numero: int
    pantalla: str
    accion: str
    nota: str = ""
    subpuntos: List[str] = field(default_factory=list)


@dataclass
class FaseHojaManual:
    titulo: str
    pasos: List[PasoHojaManual] = field(default_factory=list)


@dataclass
class ManualEstructuradoIA:
    titulo: str
    sistema: str
    objetivo: str
    fases: List[FaseHojaManual] = field(default_factory=list)
    notas_importantes: List[str] = field(default_factory=list)
    comprobaciones: List[str] = field(default_factory=list)
    bloques_libres: List[str] = field(default_factory=list)


# ======================================================================
# 6B) Hoja de Manual construida LOCALMENTE (sin IA): el mismo formato
# bonito Word/Excel, generado al instante desde el documento original.
# Así, preguntes lo que preguntes, siempre aparece la hoja formateada.
# ======================================================================

def _titulo_bonito_manual(titulo: str) -> str:
    s = str(titulo or "").strip()
    s = re.sub(r"\.(pptx|pdf|docx|xlsx|txt|md|eml|html?)$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^[a-f0-9]{8,}[_\-\s]+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\d{6,8}[_\-\s]+", "", s)
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s or "Manual operativo paso a paso"


def _detectar_sistema_manual(texto: str) -> str:
    norm = normalizar(texto or "")
    sistemas: List[str] = []
    for nombre, claves in (
        ("ERP Global (PeopleSoft)", ("peoplesoft", "erp global", "erp")),
        ("Cinema Manager", ("cinema manager",)),
        ("Vista Cloud", ("vista cloud", "vista")),
        ("Conway", ("conway",)),
        ("TMS", ("tms",)),
    ):
        if any(c in norm for c in claves) and nombre not in sistemas:
            sistemas.append(nombre)
    return " · ".join(sistemas[:3])


def _separar_pantalla_accion(texto: str) -> Tuple[str, str]:
    m = re.match(r"^([^:\[\]]{3,48}):\s+(.+)$", texto or "")
    if m and len(m.group(2).strip()) >= 8:
        candidata = normalizar(m.group(1))
        if any(k in candidata for k in (
            "pantalla", "menu", "modulo", "erp", "cinema", "vista", "acceso",
            "ruta", "pestana", "campo", "conway", "inicio", "paso",
        )):
            return (_limpiar_marcas_texto_plano(m.group(1)), m.group(2).strip())
    return ("", (texto or "").strip())


def construir_manual_desde_respuesta(respuesta: RespuestaAsistente) -> ManualEstructuradoIA:
    """
    Manual estructurado listo para pintar/exportar:
      - si la IA ya redactó el manual, se parsea su texto;
      - si no, se construye LOCALMENTE desde el procedimiento confirmado
        y/o los apartados exactos del documento de la biblioteca.
    """
    if (respuesta.texto_ia or "").strip():
        return parsear_manual_estructurado_ia(
            respuesta.texto_ia,
            titulo_respaldo=f"Manual paso a paso: {respuesta.pregunta.strip()}",
        )

    manual = ManualEstructuradoIA(titulo="", sistema="", objetivo="")
    textos_fuente: List[str] = []

    if respuesta.procedimiento is not None:
        p = respuesta.procedimiento
        manual.titulo = p.titulo
        manual.sistema = p.sistema or ""
        manual.objetivo = p.objetivo or ""
        if p.pasos:
            manual.fases.append(FaseHojaManual(
                titulo="Procedimiento paso a paso",
                pasos=[
                    PasoHojaManual(
                        numero=i,
                        pantalla=_separar_pantalla_accion(paso)[0],
                        accion=_separar_pantalla_accion(paso)[1],
                    )
                    for i, paso in enumerate(p.pasos, 1)
                ],
            ))
        manual.comprobaciones.extend(p.comprobaciones)
        manual.notas_importantes.extend(p.advertencias)
        textos_fuente.append(f"{p.titulo} {p.objetivo} {' '.join(p.pasos)}")

    # La hoja se construye SOLO con el documento ganador (el primer pasaje):
    # mezclar pasajes de un segundo documento metía saludos de correos y
    # criterios de otros procesos en medio del manual pedido.
    pasajes_fuente = list(respuesta.pasajes or [])
    doc_dominante = pasajes_fuente[0].doc_id if pasajes_fuente else None
    if doc_dominante is not None:
        pasajes_fuente = [p for p in pasajes_fuente if p.doc_id == doc_dominante]
    es_manual_visual = any(
        (p.titulo_seccion or "").lower().startswith("diapositiva")
        or p.tipo_documento in ("pptx", "pdf")
        for p in pasajes_fuente
    )

    for pasaje in pasajes_fuente[:14]:
        if pasaje.sin_texto_ocr:
            continue
        texto_sec = (pasaje.texto_seccion or "").strip()
        if not texto_sec:
            continue
        if _seccion_es_correo_sin_pasos(pasaje.titulo_seccion, texto_sec) and len(pasajes_fuente) > 1:
            continue
        textos_fuente.append(f"{pasaje.titulo_documento} {pasaje.titulo_seccion} {texto_sec[:1200]}")
        if not manual.titulo:
            titulo_doc_norm = normalizar(pasaje.titulo_documento)
            es_titulo_correo = (
                pasaje.tipo_documento == "correo"
                or titulo_doc_norm.startswith(("mail", "correo", "aviso", "re ", "rv ", "fw "))
            )
            if es_titulo_correo:
                # El título de la hoja es el proceso, no el asunto del correo
                titulo_adjunto = ""
                if (pasaje.titulo_seccion or "").lower().startswith(("adjunto:", "archivo:")):
                    titulo_adjunto = _titulo_bonito_manual(
                        pasaje.titulo_seccion.split(":", 1)[1]
                    )
                manual.titulo = titulo_adjunto  # si no hay adjunto, manda la pregunta
            else:
                manual.titulo = _titulo_bonito_manual(pasaje.titulo_documento)
        titulo_fase = (pasaje.titulo_seccion or "").strip()
        if not titulo_fase or normalizar(titulo_fase) == normalizar(pasaje.titulo_documento):
            titulo_fase = "Procedimiento paso a paso"
        fase = FaseHojaManual(titulo=titulo_fase)
        es_fase_comprobacion = any(
            k in normalizar(titulo_fase) for k in ("comprobacion", "verificacion")
        )
        vistos_fase: Set[str] = set()

        unidades: List[str] = []
        for cruda in texto_sec.splitlines():
            s = _limpiar_marcas_texto_plano(cruda)
            s = re.sub(r"^[▪▸◆●◦]\s*", "", s).strip()
            if not s or s.startswith("---"):
                continue
            if normalizar(s) in (normalizar(titulo_fase), normalizar(pasaje.titulo_documento)):
                continue
            if normalizar(s) == normalizar(_titulo_bonito_manual(pasaje.titulo_documento)):
                continue  # la portada del manual no es un paso
            if _es_linea_saludo_o_cortesia(s):
                continue
            es_explicita = bool(
                re.match(r"^(\d{1,3})[.)\-]\s+", s) or re.match(r"^[-•*]\s+", s)
            )
            if len(s) > 280 and not es_explicita:
                # Párrafo largo de correo/PDF: solo sobreviven las frases que
                # de verdad ordenan hacer algo; el saludo y la prosa, fuera.
                frases = [
                    f.strip() for f in re.split(r"(?<=[.!?])\s+", s) if f.strip()
                ]
                unidades.extend(f for f in frases if _es_unidad_accionable(f))
            else:
                unidades.append(s)

        for s in unidades:
            norm_s = normalizar(s)
            if re.match(r"^(nota|importante|atencion|ojo|aviso|cuidado)\b", norm_s):
                limpia_nota = re.sub(
                    r"^(nota|importante|atención|atencion|ojo|aviso|cuidado)\s*[:\-–—]?\s*",
                    "", s, flags=re.IGNORECASE,
                ).strip()
                if limpia_nota and limpia_nota not in manual.notas_importantes:
                    manual.notas_importantes.append(limpia_nota)
                continue

            m_num = re.match(r"^(\d{1,3})[.)\-]\s+(.+)$", s)
            m_bullet = re.match(r"^[-•*]\s+(.+)$", s)
            if es_fase_comprobacion:
                item = (m_num.group(2) if m_num else (m_bullet.group(1) if m_bullet else s)).strip()
                if item and item not in manual.comprobaciones:
                    manual.comprobaciones.append(item)
                continue
            if m_num:
                contenido = m_num.group(2).strip()
            elif m_bullet:
                contenido = m_bullet.group(1).strip()
                if fase.pasos:
                    clave_sub = normalizar(contenido)
                    if clave_sub not in vistos_fase:
                        vistos_fase.add(clave_sub)
                        fase.pasos[-1].subpuntos.append(contenido)
                    continue
            else:
                contenido = s
                es_fragmento_diapositiva = (
                    titulo_fase.lower().startswith("diapositiva")
                    or (es_manual_visual and len(contenido) <= 70)
                )
                if not _es_unidad_accionable(contenido) and not es_fragmento_diapositiva:
                    # Prosa suelta (saludos, criterios, contexto): jamás Paso 1.
                    # Como mucho, detalle corto bajo el paso anterior.
                    if fase.pasos and len(contenido) < 110:
                        clave_sub = normalizar(contenido)
                        if clave_sub not in vistos_fase:
                            vistos_fase.add(clave_sub)
                            fase.pasos[-1].subpuntos.append(contenido)
                    continue
                if fase.pasos and contenido[:1].islower():
                    clave_sub = normalizar(contenido)
                    if clave_sub not in vistos_fase:
                        vistos_fase.add(clave_sub)
                        fase.pasos[-1].subpuntos.append(contenido)
                    continue
            clave = normalizar(contenido)
            if not clave or clave in vistos_fase:
                continue
            vistos_fase.add(clave)
            pantalla_p, accion_p = _separar_pantalla_accion(contenido)
            fase.pasos.append(PasoHojaManual(
                numero=len(fase.pasos) + 1, pantalla=pantalla_p, accion=accion_p,
            ))

        if fase.pasos:
            manual.fases.append(fase)

    # GARANTÍA DE HOJA: si el manual es muy visual (diapositivas con rótulos
    # y campos, sin frases completas) y no se ha podido formar ningún paso,
    # la hoja se construye igualmente con el contenido literal del documento
    # ganador. Nunca más el documento «en crudo» como respuesta.
    if not any(f.pasos for f in manual.fases) and pasajes_fuente:
        pasos_respaldo: List[PasoHojaManual] = []
        vistos_respaldo: Set[str] = set()
        pregunta_norm = normalizar(respuesta.pregunta or "")
        for pasaje in pasajes_fuente[:14]:
            if pasaje.sin_texto_ocr:
                continue
            for cruda in (pasaje.texto_seccion or "").splitlines():
                s = _limpiar_marcas_texto_plano(cruda)
                s = re.sub(r"^[▪▸◆●◦]\s*", "", s).strip()
                if not s or s.startswith("---") or _es_linea_saludo_o_cortesia(s):
                    continue
                s = re.sub(r"^(\d{1,3})[.)\-]\s+", "", s)
                s = re.sub(r"^[-•*]\s+", "", s).strip()
                clave = normalizar(s)
                if not clave or clave in vistos_respaldo or clave == pregunta_norm:
                    continue
                if len(s) > 240:
                    frases = [
                        f.strip() for f in re.split(r"(?<=[.!?])\s+", s) if f.strip()
                    ]
                    candidatas = [f for f in frases if _es_unidad_accionable(f) or len(f) <= 90]
                    if not candidatas:
                        continue
                    s = candidatas[0]
                    clave = normalizar(s)
                    if clave in vistos_respaldo:
                        continue
                vistos_respaldo.add(clave)
                pasos_respaldo.append(PasoHojaManual(
                    numero=len(pasos_respaldo) + 1,
                    pantalla=(pasaje.titulo_seccion or "En pantalla").strip(),
                    accion=s,
                ))
                if len(pasos_respaldo) >= 30:
                    break
            if len(pasos_respaldo) >= 30:
                break
        if pasos_respaldo:
            manual.fases.append(FaseHojaManual(
                titulo="Procedimiento paso a paso (extraído del documento)",
                pasos=pasos_respaldo,
            ))

    # Renumeración global continua (PASO 1, 2, 3… aunque haya varias fases)
    contador = 0
    for fase in manual.fases:
        for paso in fase.pasos:
            contador += 1
            paso.numero = contador

    if not manual.titulo:
        manual.titulo = f"Manual paso a paso: {respuesta.pregunta.strip()}" \
            if respuesta.pregunta else "Manual operativo paso a paso"
    if not manual.sistema:
        manual.sistema = _detectar_sistema_manual(" ".join(textos_fuente))
    if not manual.objetivo:
        manual.objetivo = (
            f"Realizar correctamente el proceso «{_titulo_bonito_manual(manual.titulo)}» "
            "siguiendo exactamente los pasos del documento guardado en la biblioteca."
        )
    return manual


def _texto_estructurado_desde_manual(manual: ManualEstructuradoIA) -> str:
    """Serializa el manual al formato que pinta la Hoja (tabla 4 columnas)."""
    lineas = [f"# {manual.titulo}"]
    if manual.sistema:
        lineas.append(f"SISTEMA: {manual.sistema}")
    if manual.objetivo:
        lineas.append(f"OBJETIVO: {manual.objetivo}")
    for bloque in manual.bloques_libres:
        lineas.append(bloque.replace("|", "/"))
    for fase in manual.fases:
        lineas.append("")
        lineas.append(f"## FASE: {fase.titulo or 'Procedimiento paso a paso'}")
        for paso in fase.pasos:
            accion = paso.accion.replace("|", "/")
            if paso.subpuntos:
                accion += " ↳ " + " ↳ ".join(sp.replace("|", "/") for sp in paso.subpuntos)
            lineas.append(
                f"PASO {paso.numero} | {paso.pantalla.replace('|', '/') or 'En pantalla'} "
                f"| {accion} | {paso.nota.replace('|', '/')}"
            )
    if manual.notas_importantes:
        lineas.append("")
        lineas.append("## ⚠️ NOTAS IMPORTANTES")
        lineas.extend(f"- {n.replace('|', '/')}" for n in manual.notas_importantes)
    if manual.comprobaciones:
        lineas.append("")
        lineas.append("## ✅ COMPROBACIÓN FINAL")
        lineas.extend(f"- {c.replace('|', '/')}" for c in manual.comprobaciones)
    return "\n".join(lineas)


def _decorar_linea_rica_ia(texto_crudo: str) -> str:
    """
    Escapa HTML de forma segura y embellece:
      - **negrita** -> texto fuerte oscuro
      - [Menú / Botón / Campo] -> pastilla visual azul estilo botón
      - Flechas '->', '=>', '➔' -> flecha azul destacada '➔'
    """
    esc = html.escape(texto_crudo or "")
    esc = re.sub(r"\*\*(.+?)\*\*", r'<b style="color:#0F172A;">\1</b>', esc)
    esc = re.sub(
        r"\[([^\[\]]{2,85})\]",
        (
            r'<span style="background-color:#DBEAFE; color:#1E40AF; '
            r'font-weight:bold;">&nbsp;\1&nbsp;</span>'
        ),
        esc,
    )
    esc = re.sub(
        r"\s*(?:-&gt;|=&gt;|➔|➜|→)\s*",
        ' <b style="color:#2563EB;">➔</b> ',
        esc,
    )
    return esc


def _limpiar_marcas_texto_plano(texto: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", texto or "")
    return re.sub(r"\s+", " ", s).strip()


def parsear_manual_estructurado_ia(
    texto_ia: str,
    titulo_respaldo: str = "Manual operativo paso a paso",
) -> ManualEstructuradoIA:
    """
    Analiza la respuesta de la IA (tanto en formato tabla de 4 columnas 'PASO N | Pantalla | Acción | Nota'
    como en listas numeradas clásicas) para pintarla como Hoja de Manual Word/Excel y exportarla.
    """
    manual = ManualEstructuradoIA(titulo="", sistema="", objetivo="")
    fase_actual = FaseHojaManual(titulo="")
    seccion = "pasos"
    contador_auto = 0

    for cruda in (texto_ia or "").splitlines():
        s = cruda.strip()
        if not s or s.startswith("```"):
            continue

        if re.match(r"^\|?[\s\-:|]+\|?$", s) and "-" in s:
            continue
        if re.match(r"^\|?\s*(?:n[ºo°]?|orden|paso)\s*\|\s*(?:pantalla|donde|dónde|ubicaci)", s, re.IGNORECASE):
            continue
        if re.match(r"^(?:\*\*)?fuente(?:\*\*)?\s*:", s, re.IGNORECASE) and len(s) < 150:
            continue

        if re.match(r"^#\s+", s) and not manual.titulo and contador_auto == 0:
            limpio_t = re.sub(r"^#\s+", "", s).strip()
            limpio_t = re.sub(r"^[📋📘✨\s]+", "", limpio_t).strip()
            manual.titulo = _limpiar_marcas_texto_plano(limpio_t)
            continue

        m_sis = re.match(r"^(?:\*\*)?sistema(?:\*\*)?\s*:\s*(.+)$", s, re.IGNORECASE)
        if m_sis and not manual.sistema:
            manual.sistema = _limpiar_marcas_texto_plano(m_sis.group(1))
            continue

        m_obj = re.match(r"^(?:\*\*)?objetivo(?:\*\*)?\s*:\s*(.+)$", s, re.IGNORECASE)
        if m_obj and not manual.objetivo:
            manual.objetivo = _limpiar_marcas_texto_plano(m_obj.group(1))
            continue

        if re.match(r"^#{2,3}\s+", s):
            enc_limpio = re.sub(r"^#{2,3}\s+", "", s).strip()
            enc_norm = normalizar(enc_limpio)

            if not manual.titulo and contador_auto == 0 and enc_norm.startswith("manual"):
                manual.titulo = _limpiar_marcas_texto_plano(re.sub(r"^[📋📘✨\s]+", "", enc_limpio))
                continue

            if any(k in enc_norm for k in ("importante", "advertencia", "notas clave", "puntos clave", "atencion", "cuidado")):
                seccion = "importante"
                continue
            if any(k in enc_norm for k in ("comprobacion", "verificacion", "verificar", "resultado final", "cierre final")):
                seccion = "comprobacion"
                continue

            m_h_paso = re.match(r"^paso\s*(\d{1,3})\s*[:\-–—.]?\s*(.*)$", enc_limpio, re.IGNORECASE)
            if m_h_paso:
                seccion = "pasos"
                contador_auto = int(m_h_paso.group(1))
                fase_actual.pasos.append(
                    PasoHojaManual(
                        numero=contador_auto,
                        pantalla=_limpiar_marcas_texto_plano(m_h_paso.group(2)),
                        accion="",
                    )
                )
                continue

            seccion = "pasos"
            if fase_actual.pasos:
                manual.fases.append(fase_actual)
            fase_actual = FaseHojaManual(titulo=_limpiar_marcas_texto_plano(enc_limpio))
            continue

        if "|" in s and seccion == "pasos":
            celdas = [c.strip() for c in s.strip("|").split("|")]
            if len(celdas) >= 2:
                m_pnum = re.match(
                    r"^(?:\*\*)?(?:paso\s*)?(\d{1,3})\s*[ºª°.)\-]*(?:\*\*)?$",
                    celdas[0],
                    re.IGNORECASE,
                )
                if m_pnum:
                    contador_auto = int(m_pnum.group(1))
                    if len(celdas) >= 4:
                        pant, acc, nota = celdas[1], celdas[2], " | ".join(celdas[3:])
                    elif len(celdas) == 3:
                        pant, acc, nota = celdas[1], celdas[2], ""
                    else:
                        pant, acc, nota = "", celdas[1], ""
                    if nota in ("-", "—", "N/A", "n/a", "Ninguna"):
                        nota = ""
                    fase_actual.pasos.append(
                        PasoHojaManual(
                            numero=contador_auto,
                            pantalla=_limpiar_marcas_texto_plano(pant),
                            accion=acc.strip(),
                            nota=nota.strip(),
                        )
                    )
                    continue

        m_num = re.match(
            r"^(?:(?:\*\*)?(?:paso|primero|segundo|tercero|cuarto|quinto)\s+)?(\d{1,3})[ºª°.):\-]\s*(.+)$",
            s,
            re.IGNORECASE,
        )
        if m_num and seccion == "pasos":
            contador_auto = int(m_num.group(1))
            resto = re.sub(r"^\*\*\s*", "", m_num.group(2)) if s.lower().startswith("**paso") else m_num.group(2)
            pant_p, acc_p = "", resto.strip()
            m_bold = re.match(r"^\*\*(.+?)\*\*\s*[:\-–—.]?\s*(.*)$", acc_p)
            if m_bold and m_bold.group(2).strip():
                pant_p = _limpiar_marcas_texto_plano(m_bold.group(1))
                acc_p = m_bold.group(2).strip()
            else:
                m_col = re.match(r"^([^:\[\]]{3,48}):\s+(.+)$", acc_p)
                if m_col:
                    pant_p = _limpiar_marcas_texto_plano(m_col.group(1))
                    acc_p = m_col.group(2).strip()
            fase_actual.pasos.append(
                PasoHojaManual(numero=contador_auto, pantalla=pant_p, accion=acc_p)
            )
            continue

        if re.match(r"^[-•*▪▸]\s+", s):
            limpio_v = re.sub(r"^[-•*▪▸]\s+", "", s).strip()
            if seccion == "importante":
                manual.notas_importantes.append(limpio_v)
            elif seccion == "comprobacion":
                manual.comprobaciones.append(limpio_v)
            elif fase_actual.pasos:
                fase_actual.pasos[-1].subpuntos.append(limpio_v)
            else:
                manual.bloques_libres.append(limpio_v)
            continue

        if seccion == "importante":
            manual.notas_importantes.append(s)
        elif seccion == "comprobacion":
            manual.comprobaciones.append(s)
        elif fase_actual.pasos:
            if not fase_actual.pasos[-1].accion:
                fase_actual.pasos[-1].accion = s
            else:
                fase_actual.pasos[-1].subpuntos.append(s)
        else:
            manual.bloques_libres.append(s)

    if fase_actual.pasos:
        manual.fases.append(fase_actual)

    if not manual.titulo:
        manual.titulo = titulo_respaldo

    return manual


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
        if rx_raices.match(sin_acentos(palabra).lower()):
            return (
                '<span style="background-color:#FEF08A; color:#0F172A; '
                f'font-weight:bold;">{palabra}</span>'
            )
        return palabra

    partes = re.split(r"(&[a-zA-Z0-9#]+;)", escapado)
    for i in range(0, len(partes), 2):
        partes[i] = re.sub(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]{3,}", _reemplazar_palabra, partes[i])
    return "".join(partes)


def formatear_markdown_ia_html(
    texto_ia: str,
    titulo_respaldo: str = "Manual operativo paso a paso",
    proveedor_ia: str = "IA",
    turno_id: int = 0,
) -> str:
    """
    Pinta el manual creado por la IA como una auténtica HOJA DE MANUAL OPERATIVO
    estilo Word / Excel: cabecera corporativa, bloques por fases, tabla cuadriculada
    en colores (Orden | Pantalla / Menú | Qué hacer paso a paso | Valor / Nota clave)
    y botones directos para abrir la hoja en Excel (.xlsx) o Word (.doc).
    """
    manual = parsear_manual_estructurado_ia(texto_ia, titulo_respaldo=titulo_respaldo)
    titulo_esc = html.escape(manual.titulo)
    prov_esc = html.escape(proveedor_ia or "IA")

    partes: List[str] = []

    barra_exportar = ""
    if turno_id > 0:
        barra_exportar = (
            '<div style="margin-top:8px;">'
            f'<a href="excel:{turno_id}" '
            'style="background-color:#15803D; color:#FFFFFF; font-weight:bold; '
            'text-decoration:none; font-size:11.5px;">&nbsp;📗 Abrir en Excel (.xlsx)&nbsp;</a>'
            "&nbsp;&nbsp;"
            f'<a href="word:{turno_id}" '
            'style="background-color:#1D4ED8; color:#FFFFFF; font-weight:bold; '
            'text-decoration:none; font-size:11.5px;">&nbsp;📘 Abrir en Word (.docx)&nbsp;</a>'
            "</div>"
        )

    partes.append(
        '<table width="100%" cellpadding="12" cellspacing="0" bgcolor="#1E3A8A" '
        'style="border:2px solid #1E40AF; margin-bottom:0px;">'
        "<tr><td>"
        f'<div style="font-family:{FUENTE_UI}; font-size:11px; font-weight:bold; '
        'color:#BFDBFE; letter-spacing:0.4px; margin-bottom:3px;">'
        f"✨ HOJA DE MANUAL OPERATIVO PASO A PASO · {prov_esc.upper()}"
        "</div>"
        f'<div style="font-family:{FUENTE_UI}; font-size:17px; font-weight:bold; '
        'color:#FFFFFF; line-height:1.28;">'
        f"📋 {titulo_esc}"
        "</div>"
        f"{barra_exportar}"
        "</td></tr></table>"
    )

    filas_meta: List[str] = []
    if manual.sistema:
        filas_meta.append(
            f'<td width="34%" bgcolor="#EFF6FF" style="border:1px solid #93C5FD; '
            f'font-family:{FUENTE_UI}; font-size:12.5px; color:#1E3A8A;">'
            f'<b>💻 Sistema:</b> {html.escape(manual.sistema)}</td>'
        )
    if manual.objetivo:
        ancho_obj = "66%" if manual.sistema else "100%"
        filas_meta.append(
            f'<td width="{ancho_obj}" bgcolor="#F0F9FF" style="border:1px solid #93C5FD; '
            f'font-family:{FUENTE_UI}; font-size:12.5px; color:#0C4A6E;">'
            f'<b>🎯 Objetivo:</b> {_decorar_linea_rica_ia(manual.objetivo)}</td>'
        )
    if filas_meta:
        partes.append(
            '<table width="100%" cellpadding="8" cellspacing="0" style="margin-bottom:8px;">'
            f'<tr>{"".join(filas_meta)}</tr></table>'
        )
    else:
        partes.append('<div style="height:6px;"></div>')

    if manual.bloques_libres:
        intro = "".join(
            f'<div style="font-family:{FUENTE_UI}; font-size:13px; color:#1E293B; margin:3px 0;">'
            f"{_decorar_linea_rica_ia(ln)}</div>"
            for ln in manual.bloques_libres
        )
        partes.append(
            '<table width="100%" cellpadding="9" cellspacing="0" bgcolor="#FFFFFF" '
            'style="border:1px solid #CBD5E1; margin-bottom:8px;">'
            f"<tr><td>{intro}</td></tr></table>"
        )

    tiene_notas_col = any(
        bool(p.nota.strip())
        for f in manual.fases
        for p in f.pasos
    )

    if manual.fases:
        filas_tabla: List[str] = []

        if tiene_notas_col:
            filas_tabla.append(
                '<tr bgcolor="#1E293B">'
                f'<td width="10%" align="center" style="border:1px solid #334155; color:#FFFFFF; '
                f'font-family:{FUENTE_UI}; font-size:11px; font-weight:bold;">ORDEN</td>'
                f'<td width="22%" style="border:1px solid #334155; color:#FFFFFF; '
                f'font-family:{FUENTE_UI}; font-size:11px; font-weight:bold;">PANTALLA / MENÚ</td>'
                f'<td width="47%" style="border:1px solid #334155; color:#FFFFFF; '
                f'font-family:{FUENTE_UI}; font-size:11px; font-weight:bold;">'
                "PASO A PASO (QUÉ PULSAR Y QUÉ ESCRIBIR)</td>"
                f'<td width="21%" style="border:1px solid #334155; color:#FFFFFF; '
                f'font-family:{FUENTE_UI}; font-size:11px; font-weight:bold;">VALOR / DATO CLAVE</td>'
                "</tr>"
            )
            num_cols = 4
        else:
            filas_tabla.append(
                '<tr bgcolor="#1E293B">'
                f'<td width="12%" align="center" style="border:1px solid #334155; color:#FFFFFF; '
                f'font-family:{FUENTE_UI}; font-size:11px; font-weight:bold;">ORDEN</td>'
                f'<td width="25%" style="border:1px solid #334155; color:#FFFFFF; '
                f'font-family:{FUENTE_UI}; font-size:11px; font-weight:bold;">PANTALLA / CONCEPTO</td>'
                f'<td width="63%" style="border:1px solid #334155; color:#FFFFFF; '
                f'font-family:{FUENTE_UI}; font-size:11px; font-weight:bold;">'
                "PASO A PASO DETALLADO</td>"
                "</tr>"
            )
            num_cols = 3

        indice_global = 0
        for fase in manual.fases:
            if fase.titulo:
                filas_tabla.append(
                    f'<tr bgcolor="#DBEAFE">'
                    f'<td colspan="{num_cols}" style="border:1px solid #93C5FD; '
                    f'font-family:{FUENTE_UI}; font-size:13px; font-weight:bold; color:#1E3A8A;">'
                    f"📂 {html.escape(fase.titulo)}</td></tr>"
                )

            for paso in fase.pasos:
                indice_global += 1
                num_mostrar = paso.numero if paso.numero > 0 else indice_global
                _, badge_bg, color_pant, _ = PALETA_PASOS[(num_mostrar - 1) % len(PALETA_PASOS)]
                bg_fila = "#FFFFFF" if indice_global % 2 == 1 else "#F8FAFC"

                subs_html = ""
                if paso.subpuntos:
                    subs_html = "".join(
                        f'<div style="margin-top:4px; margin-left:6px; font-size:12.5px; color:#334155;">'
                        f'<span style="color:{badge_bg}; font-weight:bold;">↳</span> '
                        f"{_decorar_linea_rica_ia(sp)}</div>"
                        for sp in paso.subpuntos
                    )

                accion_html = _decorar_linea_rica_ia(paso.accion) + subs_html
                pantalla_html = (
                    html.escape(paso.pantalla)
                    if paso.pantalla
                    else f"Paso {num_mostrar}"
                )

                celda_orden = (
                    f'<td align="center" valign="top" bgcolor="{bg_fila}" '
                    'style="border:1px solid #CBD5E1;">'
                    f'<table cellpadding="4" cellspacing="0" bgcolor="{badge_bg}">'
                    "<tr>"
                    f'<td align="center" style="color:#FFFFFF; font-family:{FUENTE_UI}; '
                    'font-size:11px; font-weight:bold; white-space:nowrap;">'
                    f"&nbsp;PASO {num_mostrar}&nbsp;</td></tr></table>"
                    "</td>"
                )
                celda_pantalla = (
                    f'<td valign="top" bgcolor="{bg_fila}" '
                    f'style="border:1px solid #CBD5E1; font-family:{FUENTE_UI}; '
                    f'font-size:13px; font-weight:bold; color:{color_pant}; line-height:1.4;">'
                    f"{pantalla_html}</td>"
                )
                celda_accion = (
                    f'<td valign="top" bgcolor="{bg_fila}" '
                    f'style="border:1px solid #CBD5E1; font-family:{FUENTE_UI}; '
                    'font-size:13px; color:#0F172A; line-height:1.48;">'
                    f"{accion_html}</td>"
                )

                if tiene_notas_col:
                    nota_limpia = paso.nota.strip()
                    es_alerta = any(
                        w in normalizar(nota_limpia)
                        for w in ("importante", "ojo", "atencion", "primera vez", "1a vez", "cuidado", "no ")
                    ) or "⚠" in nota_limpia
                    bg_nota = "#FFFBEB" if (nota_limpia and es_alerta) else bg_fila
                    col_nota = "#92400E" if (nota_limpia and es_alerta) else "#475569"
                    celda_nota = (
                        f'<td valign="top" bgcolor="{bg_nota}" '
                        f'style="border:1px solid #CBD5E1; font-family:{FUENTE_UI}; '
                        f'font-size:12px; color:{col_nota}; line-height:1.42;">'
                        f'{_decorar_linea_rica_ia(nota_limpia) if nota_limpia else "—"}</td>'
                    )
                    filas_tabla.append(
                        f"<tr>{celda_orden}{celda_pantalla}{celda_accion}{celda_nota}</tr>"
                    )
                else:
                    filas_tabla.append(
                        f"<tr>{celda_orden}{celda_pantalla}{celda_accion}</tr>"
                    )

        partes.append(
            '<table width="100%" cellpadding="8" cellspacing="0" '
            'style="border:1px solid #94A3B8; margin-bottom:10px;">'
            f'{"".join(filas_tabla)}</table>'
        )

    if manual.notas_importantes:
        items_imp = "".join(
            f'<div style="font-family:{FUENTE_UI}; font-size:13px; color:#78350F; '
            f'margin:4px 0; line-height:1.45;">'
            f'<span style="color:#D97706; font-weight:bold;">🔸</span> '
            f"{_decorar_linea_rica_ia(ni)}</div>"
            for ni in manual.notas_importantes
        )
        partes.append(
            '<table width="100%" cellpadding="10" cellspacing="0" bgcolor="#FFFBEB" '
            'style="border:1px solid #FCD34D; border-left:5px solid #F59E0B; margin-bottom:8px;">'
            "<tr><td>"
            f'<div style="font-family:{FUENTE_UI}; font-size:13.5px; font-weight:bold; '
            'color:#B45309; margin-bottom:4px;">⚠️ Muy importante a tener en cuenta</div>'
            f"{items_imp}"
            "</td></tr></table>"
        )

    if manual.comprobaciones:
        items_ok = "".join(
            f'<div style="font-family:{FUENTE_UI}; font-size:13px; color:#065F46; '
            f'margin:4px 0; line-height:1.45;">'
            f'<span style="color:#059669; font-weight:bold;">✔</span> '
            f"{_decorar_linea_rica_ia(co)}</div>"
            for co in comprobaciones_limpias(manual.comprobaciones)
        )
        partes.append(
            '<table width="100%" cellpadding="10" cellspacing="0" bgcolor="#ECFDF5" '
            'style="border:1px solid #6EE7B7; border-left:5px solid #10B981; margin-bottom:8px;">'
            "<tr><td>"
            f'<div style="font-family:{FUENTE_UI}; font-size:13.5px; font-weight:bold; '
            'color:#047857; margin-bottom:4px;">✅ Comprobación final</div>'
            f"{items_ok}"
            "</td></tr></table>"
        )

    return "".join(partes)


def comprobaciones_limpias(items: Sequence[str]) -> List[str]:
    return [x for x in items if x and x.strip()]


def exportar_manual_ia_excel(respuesta: RespuestaAsistente, ruta_destino: Path) -> Path:
    """
    Genera un archivo Excel (.xlsx) real con colores, anchos de columna y bordes
    a partir del manual paso a paso creado por la IA.
    """
    ruta_destino = Path(ruta_destino)
    ruta_destino.parent.mkdir(parents=True, exist_ok=True)
    manual = construir_manual_desde_respuesta(respuesta)

    try:
        from openpyxl import Workbook  # type: ignore
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # type: ignore

        wb = Workbook()
        ws = wb.active
        ws.title = "Manual Paso a Paso"

        borde_fino = Border(
            left=Side(style="thin", color="CBD5E1"),
            right=Side(style="thin", color="CBD5E1"),
            top=Side(style="thin", color="CBD5E1"),
            bottom=Side(style="thin", color="CBD5E1"),
        )
        fill_titulo = PatternFill("solid", fgColor="1E3A8A")
        fill_meta = PatternFill("solid", fgColor="EFF6FF")
        fill_cab = PatternFill("solid", fgColor="1E293B")
        fill_fase = PatternFill("solid", fgColor="DBEAFE")
        fill_par = PatternFill("solid", fgColor="F8FAFC")
        fill_impar = PatternFill("solid", fgColor="FFFFFF")
        fill_aviso = PatternFill("solid", fgColor="FFFBEB")
        fill_ok = PatternFill("solid", fgColor="ECFDF5")

        ws.merge_cells("A1:D1")
        ws["A1"] = f"📋 {manual.titulo}"
        ws["A1"].font = Font(name="Segoe UI", size=14, bold=True, color="FFFFFF")
        ws["A1"].fill = fill_titulo
        ws["A1"].alignment = Alignment(vertical="center", indent=1)
        ws.row_dimensions[1].height = 30

        ws.merge_cells("A2:D2")
        sub_meta = "   |   ".join(
            x for x in (
                f"Sistema: {manual.sistema}" if manual.sistema else "",
                f"Objetivo: {manual.objetivo}" if manual.objetivo else "",
            ) if x
        ) or f"Consulta: {respuesta.pregunta}"
        ws["A2"] = sub_meta
        ws["A2"].font = Font(name="Segoe UI", size=10, bold=True, color="1E3A8A")
        ws["A2"].fill = fill_meta
        ws["A2"].alignment = Alignment(vertical="center", wrap_text=True, indent=1)
        ws.row_dimensions[2].height = 24

        fila = 4
        cabeceras = ["ORDEN", "PANTALLA / MENÚ", "PASO A PASO (QUÉ PULSAR Y QUÉ ESCRIBIR)", "VALOR / NOTA CLAVE"]
        for col_i, texto_c in enumerate(cabeceras, 1):
            c = ws.cell(row=fila, column=col_i, value=texto_c)
            c.font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
            c.fill = fill_cab
            c.alignment = Alignment(horizontal="center" if col_i == 1 else "left", vertical="center")
            c.border = borde_fino
        ws.row_dimensions[fila].height = 24
        fila += 1

        idx_g = 0
        for fase in manual.fases:
            if fase.titulo:
                ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=4)
                cf = ws.cell(row=fila, column=1, value=f"📂 {fase.titulo}")
                cf.font = Font(name="Segoe UI", size=11, bold=True, color="1E3A8A")
                cf.fill = fill_fase
                cf.alignment = Alignment(vertical="center", indent=1)
                for ci in range(1, 5):
                    ws.cell(row=fila, column=ci).border = borde_fino
                    ws.cell(row=fila, column=ci).fill = fill_fase
                ws.row_dimensions[fila].height = 22
                fila += 1

            for p in fase.pasos:
                idx_g += 1
                num_m = p.numero if p.numero > 0 else idx_g
                detalle_acc = _limpiar_marcas_texto_plano(p.accion)
                if p.subpuntos:
                    detalle_acc += "\n" + "\n".join(f"  • {_limpiar_marcas_texto_plano(sp)}" for sp in p.subpuntos)
                nota_txt = _limpiar_marcas_texto_plano(p.nota)
                fondo = fill_impar if idx_g % 2 == 1 else fill_par

                valores = [f"PASO {num_m}", p.pantalla or f"Paso {num_m}", detalle_acc, nota_txt]
                for ci, val in enumerate(valores, 1):
                    cel = ws.cell(row=fila, column=ci, value=val)
                    cel.border = borde_fino
                    cel.fill = fondo
                    if ci == 1:
                        cel.font = Font(name="Segoe UI", size=10, bold=True, color="1D4ED8")
                        cel.alignment = Alignment(horizontal="center", vertical="top")
                    elif ci == 2:
                        cel.font = Font(name="Segoe UI", size=10, bold=True, color="1E3A8A")
                        cel.alignment = Alignment(vertical="top", wrap_text=True)
                    elif ci == 3:
                        cel.font = Font(name="Segoe UI", size=10, color="0F172A")
                        cel.alignment = Alignment(vertical="top", wrap_text=True)
                    else:
                        cel.font = Font(name="Segoe UI", size=9.5, color="78350F" if nota_txt else "64748B")
                        if nota_txt:
                            cel.fill = fill_aviso
                        cel.alignment = Alignment(vertical="top", wrap_text=True)
                lineas_est = max(1, len(detalle_acc) // 58 + detalle_acc.count("\n") + 1)
                ws.row_dimensions[fila].height = max(22, lineas_est * 16)
                fila += 1

        if manual.notas_importantes:
            fila += 1
            ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=4)
            ci_imp = ws.cell(
                row=fila,
                column=1,
                value="⚠️ MUY IMPORTANTE:\n" + "\n".join(f"• {_limpiar_marcas_texto_plano(x)}" for x in manual.notas_importantes),
            )
            ci_imp.font = Font(name="Segoe UI", size=10, bold=True, color="92400E")
            ci_imp.alignment = Alignment(vertical="top", wrap_text=True)
            for ci in range(1, 5):
                ws.cell(row=fila, column=ci).fill = fill_aviso
                ws.cell(row=fila, column=ci).border = borde_fino
            ws.row_dimensions[fila].height = 22 + 16 * len(manual.notas_importantes)
            fila += 1

        if manual.comprobaciones:
            fila += 1
            ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=4)
            ci_ok = ws.cell(
                row=fila,
                column=1,
                value="✅ COMPROBACIÓN FINAL:\n" + "\n".join(f"✔ {_limpiar_marcas_texto_plano(x)}" for x in manual.comprobaciones),
            )
            ci_ok.font = Font(name="Segoe UI", size=10, bold=True, color="065F46")
            ci_ok.alignment = Alignment(vertical="top", wrap_text=True)
            for ci in range(1, 5):
                ws.cell(row=fila, column=ci).fill = fill_ok
                ws.cell(row=fila, column=ci).border = borde_fino
            ws.row_dimensions[fila].height = 22 + 16 * len(manual.comprobaciones)

        ws.column_dimensions["A"].width = 13
        ws.column_dimensions["B"].width = 28
        ws.column_dimensions["C"].width = 64
        ws.column_dimensions["D"].width = 28
        ws.freeze_panes = "A5"

        wb.save(str(ruta_destino))
        return ruta_destino
    except ImportError:
        filas_xml: List[List[str]] = [
            [manual.titulo, "", "", ""],
            [f"Sistema: {manual.sistema} | Objetivo: {manual.objetivo}", "", "", ""],
            ["ORDEN", "PANTALLA / MENÚ", "PASO A PASO", "VALOR / NOTA CLAVE"],
        ]
        for fase in manual.fases:
            if fase.titulo:
                filas_xml.append([f"[{fase.titulo}]", "", "", ""])
            for p in fase.pasos:
                filas_xml.append([f"PASO {p.numero}", p.pantalla, _limpiar_marcas_texto_plano(p.accion), p.nota])

        letras = ("A", "B", "C", "D")
        sheet_rows: List[str] = []
        for r_idx, vals in enumerate(filas_xml, 1):
            celdas_xml = "".join(
                f'<c r="{letras[c_idx]}{r_idx}" t="inlineStr"><is><t>{html.escape(v)}</t></is></c>'
                for c_idx, v in enumerate(vals[:4])
            )
            sheet_rows.append(f'<row r="{r_idx}">{celdas_xml}</row>')

        with zipfile.ZipFile(ruta_destino, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                "</Types>",
            )
            zf.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                "</Relationships>",
            )
            zf.writestr(
                "xl/workbook.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Manual" sheetId="1" r:id="rId1"/></sheets></workbook>',
            )
            zf.writestr(
                "xl/_rels/workbook.xml.rels",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                "</Relationships>",
            )
            zf.writestr(
                "xl/worksheets/sheet1.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>',
            )
        return ruta_destino


def exportar_manual_ia_word(respuesta: RespuestaAsistente, ruta_destino: Path) -> Path:
    """
    Genera un documento Word (.doc compatible nativamente con Microsoft Word)
    con diseño de ficha operativa en tabla, colores y tipografía limpia.
    """
    ruta_destino = Path(ruta_destino)
    ruta_destino.parent.mkdir(parents=True, exist_ok=True)
    texto_hoja = (respuesta.texto_ia or "").strip() or _texto_estructurado_desde_manual(
        construir_manual_desde_respuesta(respuesta)
    )
    cuerpo = formatear_markdown_ia_html(
        texto_hoja,
        titulo_respaldo=f"Manual: {respuesta.pregunta}",
        proveedor_ia=respuesta.proveedor_ia or "CineStock",
        turno_id=0,
    )
    doc_html = (
        '<html xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:w="urn:schemas-microsoft-com:office:word" '
        'xmlns="http://www.w3.org/TR/REC-html40">'
        "<head><meta charset='utf-8'>"
        f"<title>{html.escape(respuesta.pregunta)}</title>"
        "<style>"
        "@page { size: 21cm 29.7cm; margin: 1.5cm; }"
        f"body {{ font-family: {FUENTE_UI}; color: #0F172A; }}"
        "table { border-collapse: collapse; width: 100%; }"
        "</style></head>"
        f"<body>{cuerpo}</body></html>"
    )
    ruta_destino.write_bytes(doc_html.encode("utf-8-sig"))
    return ruta_destino


# ======================================================================
# 6C) Documento Word REAL (.docx): portada, tabla de pasos coloreada,
# notas y comprobaciones. Sin librerías externas (OOXML nativo), para
# que funcione igual en el WinPython del cine.
# ======================================================================

_W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx_run(texto: str, *, bold: bool = False, size: int = 21,
              color: str = "0F172A", italic: bool = False) -> str:
    props = (
        "<w:rPr><w:rFonts w:ascii=\"Segoe UI\" w:hAnsi=\"Segoe UI\"/>"
        + ("<w:b/>" if bold else "")
        + ("<w:i/>" if italic else "")
        + f"<w:color w:val=\"{color}\"/><w:sz w:val=\"{size}\"/><w:szCs w:val=\"{size}\"/></w:rPr>"
    )
    return f"<w:r>{props}<w:t xml:space=\"preserve\">{html.escape(str(texto or ''))}</w:t></w:r>"


def _docx_para(contenido: str, *, align: str = "", shade: str = "",
               before: int = 0, after: int = 90, borde_izq: str = "") -> str:
    ppr = "<w:pPr>"
    if borde_izq:
        ppr += (f"<w:pBdr><w:left w:val=\"single\" w:sz=\"18\" w:space=\"8\" "
                f"w:color=\"{borde_izq}\"/></w:pBdr>")
    if shade:
        ppr += f"<w:shd w:val=\"clear\" w:color=\"auto\" w:fill=\"{shade}\"/>"
    ppr += f"<w:spacing w:before=\"{before}\" w:after=\"{after}\" w:line=\"264\" w:lineRule=\"auto\"/>"
    if align:
        ppr += f"<w:jc w:val=\"{align}\"/>"
    ppr += "</w:pPr>"
    return f"<w:p>{ppr}{contenido}</w:p>"


def _docx_celda(parrafos: List[str], *, shade: str = "", ancho: int = 2400) -> str:
    tcpr = (
        f"<w:tcPr><w:tcW w:w=\"{ancho}\" w:type=\"dxa\"/>"
        + (f"<w:shd w:val=\"clear\" w:color=\"auto\" w:fill=\"{shade}\"/>" if shade else "")
        + "<w:tcMar><w:top w:w=\"55\" w:type=\"dxa\"/><w:left w:w=\"90\" w:type=\"dxa\"/>"
        "<w:bottom w:w=\"55\" w:type=\"dxa\"/><w:right w:w=\"90\" w:type=\"dxa\"/></w:tcMar>"
        "<w:vAlign w:val=\"top\"/></w:tcPr>"
    )
    return f"<w:tc>{tcpr}{''.join(parrafos)}</w:tc>"


def _docx_tabla(filas: List[List[str]], anchos: List[int], *, bordes: str = "B6C2D9") -> str:
    grid = "".join(f"<w:gridCol w:w=\"{a}\"/>" for a in anchos)
    filas_xml = "".join(f"<w:tr>{''.join(fila)}</w:tr>" for fila in filas)
    return (
        "<w:tbl><w:tblPr><w:tblW w:w=\"9350\" w:type=\"dxa\"/>"
        f"<w:tblBorders>"
        f"<w:top w:val=\"single\" w:sz=\"6\" w:color=\"{bordes}\"/>"
        f"<w:left w:val=\"single\" w:sz=\"6\" w:color=\"{bordes}\"/>"
        f"<w:bottom w:val=\"single\" w:sz=\"6\" w:color=\"{bordes}\"/>"
        f"<w:right w:val=\"single\" w:sz=\"6\" w:color=\"{bordes}\"/>"
        f"<w:insideH w:val=\"single\" w:sz=\"4\" w:color=\"{bordes}\"/>"
        f"<w:insideV w:val=\"single\" w:sz=\"4\" w:color=\"{bordes}\"/>"
        "</w:tblBorders></w:tblPr>"
        f"<w:tblGrid>{grid}</w:tblGrid>{filas_xml}</w:tbl>"
    )


def exportar_manual_ia_docx(respuesta: RespuestaAsistente, ruta_destino: Path) -> Path:
    """
    Genera un documento Word .docx AUTÉNTICO y formateado por CineStock:
    portada corporativa, sistema/objetivo, fases, tabla de pasos en colores
    (PASO | Pantalla/Menú | Qué hacer | Valor/Nota), notas importantes,
    comprobación final y pie con la fuente de la biblioteca.
    """
    ruta_destino = Path(ruta_destino)
    if ruta_destino.suffix.lower() != ".docx":
        ruta_destino = ruta_destino.with_suffix(".docx")
    ruta_destino.parent.mkdir(parents=True, exist_ok=True)
    manual = construir_manual_desde_respuesta(respuesta)
    fecha_hoy = date.today().strftime("%d/%m/%Y")

    fuentes: List[str] = []
    for p in (respuesta.pasajes or []):
        if p.titulo_documento and p.titulo_documento not in fuentes:
            fuentes.append(p.titulo_documento)
    if respuesta.procedimiento is not None and respuesta.procedimiento.titulo not in fuentes:
        fuentes.append(respuesta.procedimiento.titulo)
    texto_fuentes = " · ".join(fuentes) if fuentes else "Biblioteca de 📚 Conocimiento"

    cuerpo: List[str] = []
    # Portada
    cuerpo.append(_docx_tabla([[
        _docx_celda([
            _docx_para(_docx_run("CINESTOCK  ·  HOJA DE MANUAL OPERATIVO", bold=True, size=17, color="BFDBFE"), after=60),
            _docx_para(_docx_run(f"📋 {manual.titulo}", bold=True, size=34, color="FFFFFF"), after=60),
            _docx_para(_docx_run("Manual paso a paso generado desde tu biblioteca de Conocimiento", size=20, color="DBEAFE", italic=True), after=20),
        ], shade="1E3A8A", ancho=9350),
    ]], [9350], bordes="1E3A8A"))
    cuerpo.append(_docx_para("", after=70))
    # Metadatos
    filas_meta: List[List[str]] = []
    if manual.sistema:
        filas_meta.append([
            _docx_celda([_docx_para(_docx_run("💻 Sistema", bold=True, size=20, color="1E3A8A"), after=20)], shade="EFF6FF", ancho=2100),
            _docx_celda([_docx_para(_docx_run(manual.sistema, size=21), after=20)], ancho=7250),
        ])
    filas_meta.append([
        _docx_celda([_docx_para(_docx_run("🎯 Objetivo", bold=True, size=20, color="1E3A8A"), after=20)], shade="EFF6FF", ancho=2100),
        _docx_celda([_docx_para(_docx_run(manual.objetivo, size=21), after=20)], ancho=7250),
    ])
    filas_meta.append([
        _docx_celda([_docx_para(_docx_run("📎 Fuente", bold=True, size=20, color="1E3A8A"), after=20)], shade="EFF6FF", ancho=2100),
        _docx_celda([_docx_para(_docx_run(texto_fuentes, size=19, color="475569"), after=20)], ancho=7250),
    ])
    filas_meta.append([
        _docx_celda([_docx_para(_docx_run("🗓 Hoja creada", bold=True, size=20, color="1E3A8A"), after=20)], shade="EFF6FF", ancho=2100),
        _docx_celda([_docx_para(_docx_run(f"{fecha_hoy} · CineStock (solo con documentos de tu biblioteca)", size=19, color="475569"), after=20)], ancho=7250),
    ])
    cuerpo.append(_docx_tabla(filas_meta, [2100, 7250]))
    cuerpo.append(_docx_para("", after=70))
    cuerpo.append(_docx_para(
        _docx_run("▶ Cómo usar esta hoja:  ", bold=True, size=21, color="0369A1")
        + _docx_run("sigue los pasos en orden, sin saltarte ninguno. En cada paso tienes la pantalla o menú, "
                    "exactamente qué pulsar o escribir y, a la derecha, el valor o aviso clave.", size=21, color="0C4A6E"),
        shade="F0F9FF", borde_izq="0284C7", after=160,
    ))

    for bloque in manual.bloques_libres:
        cuerpo.append(_docx_para(_docx_run(bloque, size=21), after=90))

    anchos = [1050, 2050, 4300, 1950]
    for fase in manual.fases:
        cuerpo.append(_docx_para(
            _docx_run(f"📂  {fase.titulo or 'Procedimiento paso a paso'}", bold=True, size=25, color="1E3A8A"),
            shade="DBEAFE", before=170, after=110,
        ))
        filas: List[List[str]] = [[
            _docx_celda([_docx_para(_docx_run("ORDEN", bold=True, size=18, color="FFFFFF"), align="center", after=20)], shade="1E293B", ancho=anchos[0]),
            _docx_celda([_docx_para(_docx_run("PANTALLA / MENÚ", bold=True, size=18, color="FFFFFF"), after=20)], shade="1E293B", ancho=anchos[1]),
            _docx_celda([_docx_para(_docx_run("PASO A PASO — QUÉ PULSAR Y QUÉ ESCRIBIR", bold=True, size=18, color="FFFFFF"), after=20)], shade="1E293B", ancho=anchos[2]),
            _docx_celda([_docx_para(_docx_run("VALOR / NOTA CLAVE", bold=True, size=18, color="FFFFFF"), after=20)], shade="1E293B", ancho=anchos[3]),
        ]]
        for i, paso in enumerate(fase.pasos):
            fondo = "FFFFFF" if i % 2 == 0 else "F1F5F9"
            paras_accion = [_docx_para(_docx_run(_limpiar_marcas_texto_plano(paso.accion), size=21), after=45)]
            for sub in paso.subpuntos:
                paras_accion.append(_docx_para(
                    _docx_run("↳ ", bold=True, size=20, color="2563EB")
                    + _docx_run(_limpiar_marcas_texto_plano(sub), size=20, color="334155"),
                    after=35,
                ))
            nota_txt = _limpiar_marcas_texto_plano(paso.nota)
            es_aviso_nota = any(k in normalizar(nota_txt) for k in ("importante", "ojo", "atencion", "cuidado")) or "⚠" in nota_txt
            filas.append([
                _docx_celda([_docx_para(_docx_run(f"PASO {paso.numero}", bold=True, size=19, color="FFFFFF"), align="center", after=20)], shade="2563EB", ancho=anchos[0]),
                _docx_celda([_docx_para(_docx_run(_limpiar_marcas_texto_plano(paso.pantalla) or f"Paso {paso.numero}", bold=True, size=20, color="1D4ED8"), after=20)], shade=fondo, ancho=anchos[1]),
                _docx_celda(paras_accion, shade=fondo, ancho=anchos[2]),
                _docx_celda([_docx_para(_docx_run(nota_txt or "—", size=19, color="92400E" if es_aviso_nota else "475569"), after=20)], shade="FFFBEB" if (nota_txt and es_aviso_nota) else fondo, ancho=anchos[3]),
            ])
        cuerpo.append(_docx_tabla(filas, anchos))
        cuerpo.append(_docx_para("", after=80))

    if manual.notas_importantes:
        cuerpo.append(_docx_para(
            _docx_run("⚠️  MUY IMPORTANTE A TENER EN CUENTA", bold=True, size=24, color="B45309"),
            shade="FFFBEB", before=150, after=90, borde_izq="F59E0B",
        ))
        for nota in manual.notas_importantes:
            cuerpo.append(_docx_para(
                _docx_run("🔸 ", bold=True, size=21, color="D97706")
                + _docx_run(_limpiar_marcas_texto_plano(nota), size=21, color="78350F"),
                after=60,
            ))

    if manual.comprobaciones:
        cuerpo.append(_docx_para(
            _docx_run("✅  COMPROBACIÓN FINAL", bold=True, size=24, color="047857"),
            shade="ECFDF5", before=150, after=90, borde_izq="10B981",
        ))
        for comp in comprobaciones_limpias(manual.comprobaciones):
            cuerpo.append(_docx_para(
                _docx_run("✔ ", bold=True, size=21, color="059669")
                + _docx_run(_limpiar_marcas_texto_plano(comp), size=21, color="065F46"),
                after=60,
            ))

    cuerpo.append(_docx_para(
        _docx_run("Hoja de manual generada por CineStock a partir de los documentos de tu biblioteca de "
                  "📚 Conocimiento. Consulta siempre el archivo original si necesitas ver las capturas.", size=17, color="64748B", italic=True),
        before=220, after=40,
    ))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_W_NS} "
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<w:body>" + "".join(cuerpo) +
        "<w:sectPr><w:headerReference w:type=\"default\" r:id=\"rId3\"/>"
        "<w:footerReference w:type=\"default\" r:id=\"rId4\"/>"
        "<w:pgSz w:w=\"11906\" w:h=\"16838\"/>"
        "<w:pgMar w:top=\"850\" w:right=\"850\" w:bottom=\"850\" w:left=\"850\" "
        "w:header=\"450\" w:footer=\"450\"/></w:sectPr></w:body></w:document>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:styles {_W_NS}>"
        "<w:docDefaults><w:rPrDefault><w:rPr>"
        "<w:rFonts w:ascii=\"Segoe UI\" w:hAnsi=\"Segoe UI\"/>"
        "<w:sz w:val=\"21\"/><w:szCs w:val=\"21\"/></w:rPr></w:rPrDefault></w:docDefaults>"
        "<w:style w:type=\"paragraph\" w:default=\"1\" w:styleId=\"Normal\">"
        "<w:name w:val=\"Normal\"/></w:style></w:styles>"
    )
    header_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:hdr {_W_NS}>" +
        _docx_para(_docx_run("CineStock · Hoja de Manual Operativo", bold=True, size=16, color="64748B"), align="right", after=30) +
        "</w:hdr>"
    )
    footer_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:ftr {_W_NS}><w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr>"
        + _docx_run("CineStock · Manual paso a paso · Página ", size=16, color="64748B")
        + "<w:r><w:rPr><w:rFonts w:ascii=\"Segoe UI\" w:hAnsi=\"Segoe UI\"/>"
          "<w:color w:val=\"64748B\"/><w:sz w:val=\"16\"/></w:rPr>"
          "<w:fldChar w:fldCharType=\"begin\"/></w:r>"
          "<w:r><w:instrText xml:space=\"preserve\"> PAGE </w:instrText></w:r>"
          "<w:r><w:fldChar w:fldCharType=\"end\"/></w:r>"
        + "</w:p></w:ftr>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
        '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )
    rels_root = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    )
    rels_doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
        '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
        "</Relationships>"
    )
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{html.escape(manual.titulo)}</dc:title>"
        "<dc:creator>CineStock</dc:creator>"
        f"<dc:description>{html.escape('Hoja de manual operativo paso a paso generada desde la biblioteca de Conocimiento')}</dc:description>"
        "</cp:coreProperties>"
    )

    with zipfile.ZipFile(ruta_destino, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels_root)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/header1.xml", header_xml)
        zf.writestr("word/footer1.xml", footer_xml)
        zf.writestr("word/_rels/document.xml.rels", rels_doc)
        zf.writestr("docProps/core.xml", core_xml)
    return ruta_destino


@dataclass
class HojaWordGenerada:
    doc_id: int
    titulo: str
    ruta: Path
    n_pasos: int


@dataclass
class ResultadoHojasWord:
    carpeta: Path
    generadas: List[HojaWordGenerada] = field(default_factory=list)
    omitidas: int = 0
    indice: Optional[Path] = None


def generar_todas_las_hojas_word(
    biblioteca,
    carpeta_destino: Optional[Path] = None,
) -> ResultadoHojasWord:
    """
    Convierte TODOS los manuales instructivos de la biblioteca en Hojas Word
    (.docx) formateadas por CineStock, una por manual (sin duplicar temas),
    en data/manuales_ia/hojas_word/ con un índice. No usa IA ni internet.
    """
    if carpeta_destino is None:
        carpeta_base = getattr(biblioteca, "carpeta", None)
        carpeta_destino = (
            Path(carpeta_base).parent / "manuales_ia" / "hojas_word"
            if carpeta_base else Path("data") / "manuales_ia" / "hojas_word"
        )
    carpeta_destino = Path(carpeta_destino)
    carpeta_destino.mkdir(parents=True, exist_ok=True)

    fichas = _obtener_todas_las_fichas(biblioteca) if biblioteca is not None else []
    prioridad_tipo = {"pptx": 5, "pdf": 5, "docx": 4, "texto": 3, "xlsx": 2, "correo": 1}
    candidatos: Dict[str, Tuple[Tuple[int, int, int], RespuestaAsistente, ManualEstructuradoIA]] = {}
    omitidas = 0

    for ficha in fichas:
        texto = (ficha.get("texto") or "").strip()
        tipo = (ficha.get("tipo") or "texto").strip()
        titulo_doc = (ficha.get("titulo") or "").strip() or "Manual"
        if len(texto) < 180 or not _es_contenido_instructivo(texto, tipo):
            omitidas += 1
            continue
        secciones = dividir_documento_en_secciones(texto, titulo_doc)
        pasajes = [
            PasajeManual(
                doc_id=int(ficha["id"]), titulo_documento=titulo_doc,
                tipo_documento=tipo, fecha=ficha.get("fecha_doc") or "",
                categoria=ficha.get("categoria") or "",
                remitente=ficha.get("remitente") or "",
                estado=ficha.get("estado") or "NORMAL",
                titulo_seccion=tit, texto_seccion=txt, puntuacion=60.0,
            )
            for tit, txt in secciones[:15] if (txt or "").strip()
        ]
        if not pasajes:
            omitidas += 1
            continue
        resp_tmp = RespuestaAsistente(
            pregunta=_titulo_bonito_manual(titulo_doc), pasajes=pasajes,
        )
        manual_tmp = construir_manual_desde_respuesta(resp_tmp)
        n_pasos = sum(len(f.pasos) for f in manual_tmp.fases)
        if n_pasos < 2:
            omitidas += 1
            continue
        clave_tema = re.sub(
            r"\b(20\d{2}|19\d{2}|v\d+|version \d+)\b", "",
            normalizar(_titulo_bonito_manual(titulo_doc)),
        )
        clave_tema = re.sub(r"\s+", " ", clave_tema).strip() or normalizar(titulo_doc)
        rango = (n_pasos, prioridad_tipo.get(tipo, 1), len(texto))
        if clave_tema not in candidatos or rango > candidatos[clave_tema][0]:
            candidatos[clave_tema] = (rango, resp_tmp, manual_tmp)

    generadas: List[HojaWordGenerada] = []
    for _clave, (_rango, resp_tmp, manual_tmp) in sorted(
        candidatos.items(), key=lambda kv: normalizar(kv[1][2].titulo)
    ):
        slug = "_".join(normalizar(manual_tmp.titulo).split())[:70] or "manual"
        destino = carpeta_destino / f"Hoja_Manual_{slug}.docx"
        exportar_manual_ia_docx(resp_tmp, destino)
        generadas.append(HojaWordGenerada(
            doc_id=resp_tmp.pasajes[0].doc_id if resp_tmp.pasajes else 0,
            titulo=manual_tmp.titulo,
            ruta=destino,
            n_pasos=sum(len(f.pasos) for f in manual_tmp.fases),
        ))

    ruta_indice = carpeta_destino / "INDICE_Hojas_de_Manuales.txt"
    lineas_indice = [
        "CINESTOCK · ÍNDICE DE HOJAS DE MANUALES WORD",
        f"Generadas el {date.today().strftime('%d/%m/%Y')} desde tu biblioteca de Conocimiento.",
        f"Hojas creadas: {len(generadas)} · Documentos omitidos (no son manuales paso a paso): {omitidas}",
        "",
    ]
    lineas_indice.extend(
        f"{i}. {g.titulo}  ({g.n_pasos} pasos)  ->  {g.ruta.name}"
        for i, g in enumerate(generadas, 1)
    )
    if not generadas:
        lineas_indice.append("Todavía no hay manuales instructivos suficientes en la biblioteca.")
    ruta_indice.write_text("\n".join(lineas_indice), encoding="utf-8")

    return ResultadoHojasWord(
        carpeta=carpeta_destino, generadas=generadas,
        omitidas=omitidas, indice=ruta_indice,
    )


def _html_tarjeta_paso_local(num_paso: int, cuerpo_html: str) -> str:
    borde, badge_bg, _, fondo_card = PALETA_PASOS[(max(1, num_paso) - 1) % len(PALETA_PASOS)]
    return (
        f'<table width="100%" cellpadding="9" cellspacing="0" bgcolor="{fondo_card}" '
        f'style="border:1px solid #DBEAFE; border-left:5px solid {borde}; margin-bottom:6px;">'
        "<tr>"
        '<td width="76" valign="top">'
        f'<table cellpadding="4" cellspacing="0" bgcolor="{badge_bg}">'
        f'<tr><td style="color:#FFFFFF; font-family:{FUENTE_UI}; font-size:11px; '
        f'font-weight:bold;">&nbsp;PASO {num_paso}&nbsp;</td></tr></table>'
        "</td>"
        f'<td valign="top" style="font-family:{FUENTE_UI}; font-size:13px; '
        f'color:#1E293B; line-height:1.48;">{cuerpo_html}</td>'
        "</tr></table>"
    )


def formatear_bloque_manual_html(texto: str, terminos: Sequence[str]) -> str:
    lineas_html: List[str] = []
    for linea in (texto or "").splitlines():
        s = linea.strip()
        if not s:
            lineas_html.append('<div style="height:4px;"></div>')
            continue

        m_num = re.match(r"^(\d{1,2})[.)\-]\s+(.*)$", s)
        if m_num:
            lineas_html.append(
                _html_tarjeta_paso_local(
                    int(m_num.group(1)),
                    resaltar_terminos_html(m_num.group(2), terminos),
                )
            )
        elif re.match(r"^(nota|importante|atenci[oó]n|ojo|aviso)\s*:", s, re.IGNORECASE):
            lineas_html.append(
                f'<table width="100%" cellpadding="8" cellspacing="0" bgcolor="#FFFBEB" '
                f'style="border:1px solid #FCD34D; border-left:4px solid #F59E0B; margin:5px 0;">'
                f'<tr><td style="font-family:{FUENTE_UI}; font-size:13px; color:#92400E;">'
                f"⚠️ {resaltar_terminos_html(s, terminos)}</td></tr></table>"
            )
        elif re.match(r"^[-•*▪▸]\s+", s):
            cuerpo_v = resaltar_terminos_html(re.sub(r"^[-•*▪▸]\s+", "", s), terminos)
            lineas_html.append(
                f'<div style="margin:4px 0 4px 8px; font-family:{FUENTE_UI}; '
                f'font-size:13px; color:#1E293B; line-height:1.45;">'
                f'<span style="color:#2563EB; font-weight:bold;">➔</span> {cuerpo_v}</div>'
            )
        else:
            lineas_html.append(
                f'<div style="margin:3px 0; font-family:{FUENTE_UI}; font-size:13px; '
                f'color:#1E293B; line-height:1.45;">{resaltar_terminos_html(s, terminos)}</div>'
            )

    return "".join(lineas_html)


def _html_cartel_no_disponible(respuesta: RespuestaAsistente) -> str:
    consulta_esc = html.escape(respuesta.pregunta.strip())
    if respuesta.error_biblioteca:
        sub = "La biblioteca de Conocimiento no está accesible en este momento."
    elif respuesta.biblioteca_vacia:
        sub = (
            "Todavía no hay manuales cargados en <b>📚 Conocimiento</b>. "
            "Añade el PDF, Word o presentación del proceso para habilitarlo."
        )
    else:
        sub = (
            "He buscado en toda mi biblioteca y no tengo ningún manual paso a paso sobre "
            "eso en <b>📚 Conocimiento</b>. Solo respondo con lo que tienes guardado, así "
            "que prefiero decírtelo claramente a inventarme algo. Si tienes el archivo "
            "fuera de CineStock, incorpóralo desde <b>📚 Conocimiento → "
            "➕ Añadir correos / documentos…</b> y lo aprendo al momento. "
            "Puedes decirme también «¿qué documentos tienes?» para ver todo lo que sí domino."
        )

    return (
        '<table width="100%" cellpadding="14" cellspacing="0" bgcolor="#FEF2F2" '
        'style="border:2px solid #F87171; margin-bottom:6px;">'
        "<tr><td>"
        f'<div style="font-family:{FUENTE_UI}; font-size:14.5px; font-weight:bold; '
        'color:#991B1B; margin-bottom:4px;">'
        "🚫 PROCEDIMIENTO NO DISPONIBLE"
        "</div>"
        f'<div style="font-family:{FUENTE_UI}; font-size:13px; color:#7F1D1D; margin-bottom:5px;">'
        f"Sin manual disponible para: <b>«{consulta_esc}»</b>."
        "</div>"
        f'<div style="font-family:{FUENTE_UI}; font-size:12.5px; color:#991B1B;">{sub}</div>'
        "</td></tr></table>"
    )


def _formatear_texto_chat_html(texto: str) -> str:
    esc = html.escape(texto or "")
    esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
    return esc.replace("\n", "<br>")


def _html_respuesta_chat(respuesta: RespuestaAsistente, pensando_ia: bool = False) -> str:
    if pensando_ia:
        return (
            '<table width="100%" cellpadding="12" cellspacing="0" bgcolor="#EFF6FF" '
            'style="border:1px solid #BFDBFE; margin-bottom:8px;">'
            "<tr><td>"
            f'<div style="font-family:{FUENTE_UI}; color:#1E3A8A; font-size:13px;">'
            "💬 Un momento, estoy consultando tu biblioteca…"
            "</div></td></tr></table>"
        )
    partes = [
        '<table width="100%" cellpadding="13" cellspacing="0" bgcolor="#F0F9FF" '
        'style="border:1px solid #7DD3FC; border-left:5px solid #0284C7; margin-bottom:8px;">'
        "<tr><td>"
        f'<div style="font-family:{FUENTE_UI}; font-size:11px; font-weight:bold; '
        'color:#0369A1; margin-bottom:4px;">🤖 TU ASISTENTE DE CINESTOCK</div>'
        f'<div style="font-family:{FUENTE_UI}; font-size:13.5px; color:#0F172A; '
        f'line-height:1.55;">{_formatear_texto_chat_html(respuesta.texto_chat)}</div>'
    ]
    if respuesta.proveedor_ia:
        partes.append(
            f'<div style="font-family:{FUENTE_UI}; font-size:11px; color:#64748B; '
            f'margin-top:6px;">Respondido con {html.escape(respuesta.proveedor_ia)} '
            "usando solo tu biblioteca</div>"
        )
    partes.append("</td></tr></table>")

    if respuesta.documentos_chat:
        filas: List[str] = []
        for d in respuesta.documentos_chat:
            icono = ICONOS_DOC.get(d.tipo, "📄 Manual")
            meta = " · ".join(x for x in (d.categoria, d.fecha) if x)
            estrella = " ⭐" if d.estado == "DESTACADO" else ""
            filas.append(
                '<tr><td style="padding:7px 9px; border-bottom:1px solid #E2E8F0;">'
                f'<span style="font-family:{FUENTE_UI}; font-size:12.5px;">{icono}{estrella} </span>'
                f'<a href="abrir:{d.doc_id}" style="font-family:{FUENTE_UI}; '
                f'font-size:13px; color:#1D4ED8; font-weight:bold; text-decoration:none;">'
                f"{html.escape(d.titulo)}</a>"
                + (f'<br><span style="font-family:{FUENTE_UI}; font-size:11.5px; '
                   f'color:#64748B; margin-left:4px;">{html.escape(meta)}</span>' if meta else "")
                + "</td></tr>"
            )
        partes.append(
            '<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#FFFFFF" '
            'style="border:1px solid #CBD5E1; margin-bottom:8px;">'
            + "".join(filas) + "</table>"
        )
        if respuesta.total_documentos > len(respuesta.documentos_chat) and \
                respuesta.intencion_chat in ("inventario", "recientes"):
            partes.append(
                f'<div style="font-family:{FUENTE_UI}; font-size:11.5px; color:#64748B; '
                f'margin-bottom:6px;">Te muestro {len(respuesta.documentos_chat)} de '
                f"{respuesta.total_documentos}; pídeme un tema concreto y te lo afino.</div>"
            )
    return "".join(partes)


def renderizar_respuesta_asistente_html(
    respuesta: RespuestaAsistente,
    pensando_ia: bool = False,
    turno_id: int = 0,
) -> str:
    if respuesta.es_chat:
        return _html_respuesta_chat(respuesta, pensando_ia=pensando_ia)

    if not respuesta.tiene_contenido:
        return _html_cartel_no_disponible(respuesta)

    if pensando_ia:
        n_vis = len(respuesta.adjuntos_ia)
        detalle_carga = (
            f"Examinando {n_vis} diapositivas/capturas en alta definición (menús, campos y recuadros rojos) "
            "para construir tu hoja de manual clic por clic…"
            if n_vis > 1
            else (
                "Leyendo las páginas y capturas del documento para redactar cada paso…"
                if n_vis == 1
                else "Estructurando los pasos del procedimiento en formato hoja de manual…"
            )
        )
        return (
            '<table width="100%" cellpadding="14" cellspacing="0" bgcolor="#EFF6FF" '
            'style="border:2px solid #3B82F6; margin-bottom:8px;">'
            "<tr><td>"
            f'<div style="font-family:{FUENTE_UI}; color:#1E3A8A; font-size:14px; '
            'font-weight:bold; margin-bottom:4px;">'
            "📋 Elaborando tu Hoja de Manual Paso a Paso con IA…"
            "</div>"
            f'<div style="font-family:{FUENTE_UI}; color:#1D4ED8; font-size:12.5px;">'
            f"{detalle_carga}"
            "</div>"
            "</td></tr></table>"
        )

    if respuesta.texto_ia:
        titulo_def = f"Manual paso a paso: {respuesta.pregunta.strip()}"
        hoja_html = formatear_markdown_ia_html(
            respuesta.texto_ia,
            titulo_respaldo=titulo_def,
            proveedor_ia=respuesta.proveedor_ia or "IA",
            turno_id=turno_id,
        )
        bloques_ia = [hoja_html]

        if respuesta.pasajes:
            vistos = set()
            enlaces_discretos: List[str] = []
            for pasaje in respuesta.pasajes:
                if pasaje.doc_id in vistos:
                    continue
                vistos.add(pasaje.doc_id)
                enlaces_discretos.append(
                    f'<span style="color:#475569;">{html.escape(pasaje.titulo_documento)}</span> '
                    f'(<a href="abrir:{pasaje.doc_id}" '
                    f'style="color:#2563EB; text-decoration:none; font-weight:bold;">'
                    f"ver archivo original</a>)"
                )
            bloques_ia.append(
                '<table width="100%" cellpadding="7" cellspacing="0" bgcolor="#F8FAFC" '
                'style="border:1px solid #E2E8F0; margin-top:2px;">'
                f'<tr><td style="font-family:{FUENTE_UI}; font-size:11.5px; color:#64748B;">'
                f'📎 Documento origen: {" · ".join(enlaces_discretos)}'
                "</td></tr></table>"
            )
        return "".join(bloques_ia)

    bloques: List[str] = []

    if respuesta.aviso_ia:
        bloques.append(
            '<table width="100%" cellpadding="9" cellspacing="0" bgcolor="#FFFBEB" '
            'style="border:1px solid #FCD34D; border-left:4px solid #F59E0B; margin-bottom:8px;">'
            f'<tr><td style="font-family:{FUENTE_UI}; color:#92400E; font-size:12px;">'
            f"ℹ️ {html.escape(respuesta.aviso_ia)}</td></tr></table>"
        )

    if respuesta.solo_documento_sin_ocr:
        for pasaje in respuesta.pasajes:
            icono = ICONOS_DOC.get(pasaje.tipo_documento, "📕 PDF")
            bloques.append(
                '<table width="100%" cellpadding="12" cellspacing="0" bgcolor="#FFFBEB" '
                'style="border:2px solid #F59E0B; margin-bottom:8px;">'
                "<tr><td>"
                f'<div style="font-family:{FUENTE_UI}; font-size:14px; font-weight:bold; '
                'color:#92400E; margin-bottom:4px;">'
                f"📄 Manual localizado: {html.escape(pasaje.titulo_documento)}"
                "</div>"
                f'<div style="font-family:{FUENTE_UI}; font-size:12.5px; color:#78350F; '
                'line-height:1.45; margin-bottom:8px;">'
                f"Este archivo ({icono}) contiene los pasos en <b>imágenes/capturas</b>. "
                "Activa <b>✨ Redactar con IA</b> (con clave activa en <code>.env</code>) "
                "para que la IA lea las capturas y cree la hoja de manual paso a paso, "
                "o ábrelo aquí:"
                "</div>"
                f'<a href="abrir:{pasaje.doc_id}" '
                f'style="font-family:{FUENTE_UI}; color:#1D4ED8; font-weight:bold; '
                'text-decoration:none; font-size:13px;">'
                "📂 Abrir manual original</a>"
                "</td></tr></table>"
            )
        return "".join(bloques)

    # Sin IA (o mientras llega): la Hoja de Manual formateada por CineStock
    # se construye al instante desde el documento exacto de la biblioteca.
    if respuesta.procedimiento is not None or respuesta.pasajes:
        manual_local = construir_manual_desde_respuesta(respuesta)
        if any(f.pasos for f in manual_local.fases):
            bloques.append(formatear_markdown_ia_html(
                _texto_estructurado_desde_manual(manual_local),
                titulo_respaldo=f"Manual paso a paso: {respuesta.pregunta.strip()}",
                proveedor_ia="CineStock · Hoja de Manual v4",
                turno_id=turno_id,
            ))
            if respuesta.pasajes:
                vistos_l: set = set()
                enlaces_l: List[str] = []
                for pasaje in respuesta.pasajes:
                    if pasaje.doc_id in vistos_l:
                        continue
                    vistos_l.add(pasaje.doc_id)
                    enlaces_l.append(
                        f'<span style="color:#475569;">{html.escape(pasaje.titulo_documento)}</span> '
                        f'(<a href="abrir:{pasaje.doc_id}" '
                        f'style="color:#2563EB; text-decoration:none; font-weight:bold;">'
                        f"ver archivo original</a>)"
                    )
                bloques.append(
                    '<table width="100%" cellpadding="7" cellspacing="0" bgcolor="#F8FAFC" '
                    'style="border:1px solid #E2E8F0; margin-top:2px;">'
                    f'<tr><td style="font-family:{FUENTE_UI}; font-size:11.5px; color:#64748B;">'
                    f'📎 Documento origen: {" · ".join(enlaces_l)}'
                    "</td></tr></table>"
                )
            return "".join(bloques)

    if respuesta.procedimiento is not None:
        p = respuesta.procedimiento
        bloques.append(
            '<table width="100%" cellpadding="12" cellspacing="0" bgcolor="#15803D" '
            'style="border:2px solid #16A34A; margin-bottom:8px;">'
            "<tr><td>"
            f'<div style="font-family:{FUENTE_UI}; color:#DCFCE7; font-size:11px; '
            'font-weight:bold; margin-bottom:3px;">✅ PROCEDIMIENTO CONFIRMADO</div>'
            f'<div style="font-family:{FUENTE_UI}; font-size:16px; font-weight:bold; '
            f'color:#FFFFFF;">{html.escape(p.titulo)}</div>'
            "</td></tr></table>"
        )
        if p.objetivo:
            bloques.append(
                '<table width="100%" cellpadding="9" cellspacing="0" bgcolor="#F0FDF4" '
                'style="border:1px solid #86EFAC; border-left:4px solid #16A34A; margin-bottom:8px;">'
                f'<tr><td style="font-family:{FUENTE_UI}; font-size:13px; color:#14532D;">'
                f"<b>🎯 Objetivo:</b> {html.escape(p.objetivo)}</td></tr></table>"
            )
        for i, paso in enumerate(p.pasos, 1):
            bloques.append(
                _html_tarjeta_paso_local(
                    i,
                    resaltar_terminos_html(paso, respuesta.terminos),
                )
            )
        if p.advertencias:
            adv_html = "".join(
                f'<div style="font-family:{FUENTE_UI}; font-size:13px; color:#78350F; margin:3px 0;">'
                f"🔸 {resaltar_terminos_html(adv, respuesta.terminos)}</div>"
                for adv in p.advertencias
            )
            bloques.append(
                '<table width="100%" cellpadding="10" cellspacing="0" bgcolor="#FFFBEB" '
                'style="border:1px solid #FCD34D; border-left:5px solid #F59E0B; margin-bottom:8px;">'
                f'<tr><td><div style="font-family:{FUENTE_UI}; font-weight:bold; color:#B45309; '
                f'margin-bottom:4px;">⚠️ Muy importante</div>{adv_html}</td></tr></table>'
            )

    if respuesta.pasajes:
        titulo_cab = html.escape(respuesta.pasajes[0].titulo_documento)
        bloques.append(
            '<table width="100%" cellpadding="12" cellspacing="0" bgcolor="#1E3A8A" '
            'style="border:2px solid #2563EB; margin-bottom:8px;">'
            "<tr><td>"
            f'<div style="font-family:{FUENTE_UI}; color:#BFDBFE; font-size:11px; '
            'font-weight:bold; margin-bottom:3px;">📘 GUÍA EXTRAÍDA DE TU BIBLIOTECA</div>'
            f'<div style="font-family:{FUENTE_UI}; font-size:16px; font-weight:bold; '
            f'color:#FFFFFF;">{titulo_cab}</div>'
            "</td></tr></table>"
        )
        for pasaje in respuesta.pasajes[:8]:
            sub_sec = (
                f'<div style="font-family:{FUENTE_UI}; color:#1D4ED8; font-weight:bold; '
                f'font-size:13.5px; margin:6px 0 4px 0;">📌 {html.escape(pasaje.titulo_seccion)}</div>'
                if pasaje.titulo_seccion
                else ""
            )
            cuerpo_html = formatear_bloque_manual_html(pasaje.texto_seccion, respuesta.terminos)
            bloques.append(
                '<table width="100%" cellpadding="10" cellspacing="0" bgcolor="#FFFFFF" '
                'style="border:1px solid #DBEAFE; margin-bottom:8px;">'
                f"<tr><td>{sub_sec}{cuerpo_html}"
                f'<div style="margin-top:6px; font-family:{FUENTE_UI}; font-size:11.5px;">'
                f'<a href="abrir:{pasaje.doc_id}" '
                f'style="color:#2563EB; font-weight:bold; text-decoration:none;">'
                f"📎 Ver archivo original</a></div>"
                "</td></tr></table>"
            )

    return "".join(bloques)
