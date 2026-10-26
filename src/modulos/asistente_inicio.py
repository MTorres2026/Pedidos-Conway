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
import re
import ssl
import unicodedata
import urllib.error
import urllib.request
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

    @property
    def tiene_contenido(self) -> bool:
        if self.rechazado_por_ia:
            return False
        return self.procedimiento is not None or bool(self.pasajes)

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

    candidatos_doc: List[Tuple[float, List[PasajeManual]]] = []

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
            ))
            continue

        secciones = dividir_documento_en_secciones(texto_bruto, titulo_doc)
        if not secciones:
            continue

        # ¿Es un MANUAL DEDICADO específicamente al tema preguntado por su título?
        # (Ej. "Manual_Conciliacion_Inventarios.pdf" al preguntar "conciliación de inventarios")
        es_manual_dedicado = (
            ranuras_en_cabecera_doc == n_ranuras
            and especificas_en_cabecera_doc == len(ranuras_especificas)
        )

        if es_manual_dedicado:
            # En un manual dedicado por completo al proceso buscado, no descartamos sus páginas
            # internas aunque en el paso 3 no repita el título del PDF. Reunimos sus secciones
            # operativas principales en orden para que la IA redacte el manual completo.
            texto_unido = "\n\n".join(txt for _, txt in secciones)
            if not _es_contenido_instructivo(texto_unido, tipo_doc):
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

            con_chicha = [s for s in secciones_puntuadas if s[0] >= 55.0] or secciones_puntuadas
            con_chicha.sort(key=lambda x: x[0], reverse=True)
            mejor_sec_pts = con_chicha[0][0]
            # Si el manual tiene varios capítulos distintos y uno clava los conceptos (ej. Conciliación),
            # solo incluimos las secciones que estén al nivel de ese capítulo (>= 80 % de su puntuación),
            # evitando meter "Apertura de bar" o "Mermas" por el mero nombre del archivo.
            filtradas_doc = [s for s in con_chicha if s[0] >= mejor_sec_pts * 0.80]
            elegidas = sorted(filtradas_doc[:4], key=lambda x: x[1])

            pts_doc = elegidas[0][0] + 35.0 + _bono_recencia(fecha_doc)
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
            candidatos_doc.append((pts_doc, pasajes_doc))
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
        candidatos_doc.append((mejor_pts, pasajes_doc))

    if not candidatos_doc:
        return []

    candidatos_doc.sort(key=lambda par: par[0], reverse=True)
    tope = candidatos_doc[0][0]
    candidatos_doc = [c for c in candidatos_doc if c[0] >= tope * 0.65]

    salida: List[PasajeManual] = []
    for pos, (_, lista_p) in enumerate(candidatos_doc[:max_documentos]):
        if pos == 0:
            salida.extend(lista_p[:4])
        else:
            salida.append(lista_p[0])

    return salida


# ======================================================================
# 5) Consulta completa local + Preparación Multimodal (PDF / Capturas)
# ======================================================================

MAX_BYTES_ADJUNTO_IA = 10 * 1024 * 1024  # 10 MB máx para envío directo a IA
MAX_PAGINAS_PDF_ADJUNTO = 18
VERSION_CACHE_IA = "v2_manual_colores"

_MIMES_IMAGEN = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


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


def _extraer_imagenes_de_pdf_bytes(datos_pdf: bytes, max_img: int = 8) -> List[AdjuntoIA]:
    """Respaldo Vision: extrae capturas incrustadas en un PDF usando pypdf."""
    salida: List[AdjuntoIA] = []
    try:
        from pypdf import PdfReader  # type: ignore
        lector = PdfReader(BytesIO(datos_pdf))
        for num_pag, pag in enumerate(lector.pages[:12], 1):
            for idx_im, img in enumerate(getattr(pag, "images", []) or [], 1):
                raw = getattr(img, "data", b"") or b""
                if len(raw) < 12_000 or len(raw) > 4 * 1024 * 1024:
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
                salida.append(
                    AdjuntoIA(
                        nombre=f"pagina_{num_pag}_{idx_im}{ext or '.png'}",
                        mime=mime,
                        datos_b64=base64.b64encode(raw).decode("ascii"),
                    )
                )
                if len(salida) >= max_img:
                    return salida
    except Exception:
        return salida
    return salida


def _extraer_imagenes_office_zip(ruta: Path, max_img: int = 8) -> List[AdjuntoIA]:
    """Extrae capturas de pantalla internas de un .pptx o .docx."""
    salida: List[AdjuntoIA] = []
    try:
        with zipfile.ZipFile(ruta) as z:
            nombres = sorted(
                n for n in z.namelist()
                if ("/media/" in n or n.startswith("media/"))
                and Path(n).suffix.lower() in _MIMES_IMAGEN
            )
            for nombre in nombres:
                raw = z.read(nombre)
                if len(raw) < 12_000 or len(raw) > 4 * 1024 * 1024:
                    continue
                ext = Path(nombre).suffix.lower()
                salida.append(
                    AdjuntoIA(
                        nombre=Path(nombre).name,
                        mime=_MIMES_IMAGEN[ext],
                        datos_b64=base64.b64encode(raw).decode("ascii"),
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
    Prepara el PDF o las capturas del documento ganador para que OpenAI / Gemini
    puedan leer directamente páginas escaneadas, capturas de pantalla, botones y flechas
    del manual y redactar una guía paso a paso completa sin enseñar el PDF crudo.
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
    if tam <= 0 or tam > MAX_BYTES_ADJUNTO_IA:
        return []

    texto_total = "\n".join(p.texto_seccion for p in pasajes if p.doc_id == primero.doc_id)
    sin_ocr = all(p.sin_texto_ocr for p in pasajes if p.doc_id == primero.doc_id)
    ext = ruta.suffix.lower()

    try:
        if ext == ".pdf":
            datos = ruta.read_bytes()
            n_pags = _contar_paginas_pdf_bytes(datos)
            if sin_ocr or n_pags <= MAX_PAGINAS_PDF_ADJUNTO or len(texto_total) < 2400:
                return [
                    AdjuntoIA(
                        nombre=ruta.name,
                        mime="application/pdf",
                        datos_b64=base64.b64encode(datos).decode("ascii"),
                    )
                ]

        elif ext in _MIMES_IMAGEN:
            datos = ruta.read_bytes()
            return [
                AdjuntoIA(
                    nombre=ruta.name,
                    mime=_MIMES_IMAGEN[ext],
                    datos_b64=base64.b64encode(datos).decode("ascii"),
                )
            ]

        elif ext in (".pptx", ".docx") and (sin_ocr or len(texto_total) < 900):
            return _extraer_imagenes_office_zip(ruta)

        elif ext == ".eml":
            with ruta.open("rb") as f:
                correo = BytesParser(policy=policy.default).parse(f)
            pdfs_correo: List[Tuple[int, str, bytes]] = []
            imgs_correo: List[AdjuntoIA] = []
            for adj in correo.iter_attachments():
                nom = adj.get_filename() or ""
                ext_a = Path(nom).suffix.lower()
                raw = adj.get_payload(decode=True) or b""
                if not raw or len(raw) > MAX_BYTES_ADJUNTO_IA:
                    continue
                if ext_a == ".pdf":
                    norm_nom = normalizar(nom)
                    aciertos = sum(1 for r in ranuras if _ranura_coincide_en(r, norm_nom))
                    pdfs_correo.append((aciertos, nom, raw))
                elif ext_a in _MIMES_IMAGEN and len(raw) >= 12_000 and len(imgs_correo) < 6:
                    imgs_correo.append(
                        AdjuntoIA(
                            nombre=nom or "captura.png",
                            mime=_MIMES_IMAGEN[ext_a],
                            datos_b64=base64.b64encode(raw).decode("ascii"),
                        )
                    )
            if pdfs_correo:
                pdfs_correo.sort(key=lambda x: x[0], reverse=True)
                _, nom_pdf, raw_pdf = pdfs_correo[0]
                if _contar_paginas_pdf_bytes(raw_pdf) <= MAX_PAGINAS_PDF_ADJUNTO:
                    return [
                        AdjuntoIA(
                            nombre=nom_pdf,
                            mime="application/pdf",
                            datos_b64=base64.b64encode(raw_pdf).decode("ascii"),
                        )
                    ]
            if sin_ocr and imgs_correo:
                return imgs_correo
    except Exception:
        return []

    return []


def _construir_clave_cache_ia(
    ranuras: Sequence[Tuple[str, ...]],
    procedimiento: Optional[ProcedimientoCoincidente],
    pasajes: Sequence[PasajeManual],
) -> str:
    if not procedimiento and not pasajes:
        return ""
    firma_ranuras = "|".join(",".join(sorted(r)) for r in ranuras)
    firma_proc = procedimiento.id if procedimiento else ""
    firma_docs = ";".join(
        f"{p.doc_id}:{p.fecha}:{len(p.texto_seccion)}" for p in pasajes
    )
    semilla = f"{VERSION_CACHE_IA}||{firma_ranuras}||{firma_proc}||{firma_docs}"
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
            # Conservar las últimas 120 entradas
            claves = list(datos.keys())[-120:]
            datos = {k: datos[k] for k in claves}
        ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def consultar_base_local(
    pregunta: str,
    biblioteca=None,
    historial_preguntas: Optional[Sequence[str]] = None,
) -> RespuestaAsistente:
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

    clave_cache = _construir_clave_cache_ia(ranuras, proc, pasajes)

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
# 6) Verificador + Redactor Multimodal de Manual Paso a Paso con IA
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

    for i, pasaje in enumerate(respuesta.pasajes, 1):
        meta = " | ".join(
            x for x in (
                f"Documento: {pasaje.titulo_documento}",
                f"Sección: {pasaje.titulo_seccion}" if pasaje.titulo_seccion else "",
                f"Fecha: {pasaje.fecha}" if pasaje.fecha else "",
            ) if x
        )
        if pasaje.sin_texto_ocr:
            bloques.append(
                f"[FUENTE {i}: {meta}]\n"
                "(El contenido de este manual viene en el archivo PDF/imágenes adjunto; "
                "lee todas sus páginas y capturas de pantalla para redactar cada paso)."
            )
        else:
            bloques.append(f"[FUENTE {i}: {meta}]\n{pasaje.texto_seccion}")

    return "\n\n---\n\n".join(bloques)


_PROMPT_SISTEMA_RAG = (
    "Eres el redactor experto de manuales operativos de CineStock para personal de cine.\n"
    "Tu misión es leer el documento/PDF/capturas proporcionado y escribir un MANUAL PASO A PASO "
    "precioso, clarísimo, autónomo y 100 % fiel al documento para que el compañero pueda "
    "realizar el proceso de principio a fin SIN tener que abrir el PDF.\n\n"
    "REGLA 1 — CUÁNDO RESPONDER 'NO_DISPONIBLE':\n"
    "Responde ÚNICAMENTE con la palabra exacta NO_DISPONIBLE si el material proporcionado "
    "trata en realidad de OTRO trámite distinto al preguntado (por ejemplo: preguntan por "
    "'solicitud de pedido' y el documento es de vacaciones/RRHH; o preguntan por "
    "'conciliación de inventario' y trata de conciliación familiar o solo lo menciona "
    "de pasada en una línea sin explicar cómo hacerlo).\n\n"
    "REGLA 2 — FORMATO OBLIGATORIO DEL MANUAL PASO A PASO:\n"
    "Cuando el documento SÍ explique el proceso (leyendo tanto el texto como todos los "
    "botones, pestañas, campos y notas que aparezcan en las capturas del PDF/imágenes), "
    "redáctalo SIEMPRE con esta estructura exacta:\n\n"
    "# Manual paso a paso: [Nombre claro del proceso]\n"
    "OBJETIVO: [1 frase clara y directa explicando qué conseguimos con este proceso]\n\n"
    "1. **[Título breve del paso 1]**: Explicación clara y detallada. Escribe siempre los "
    "nombres de programas, menús, pestañas, campos o botones entre corchetes, por ejemplo "
    "[Cinema Manager], [Inventario], [Conciliación] o [Guardar].\n"
    "   - Si dentro del paso hay subdetalles o datos a rellenar, añádelos con guion debajo.\n"
    "2. **[Título breve del paso 2]**: Explicación detallada del siguiente paso...\n"
    "3. **[Título breve del paso 3]**: (... incluye TODOS los pasos del documento en orden, "
    "sin saltarte ninguno ni inventar pasos externos).\n\n"
    "## ⚠️ Muy importante\n"
    "- [Advertencias, errores frecuentes a evitar o condiciones clave indicadas en el manual]\n\n"
    "## ✅ Comprobación final\n"
    "- [Cómo verificar en pantalla que el proceso ha quedado correctamente grabado/cerrado]"
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
) -> object:
    if not adjuntos:
        return mensaje_usuario
    bloques: List[Dict] = []
    for adj in adjuntos:
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
                "image_url": {"url": f"data:{adj.mime};base64,{adj.datos_b64}"},
            })
    bloques.append({"type": "text", "text": mensaje_usuario})
    return bloques


def sintetizar_con_ia(
    pregunta: str,
    contexto_rag: str,
    historial_turnos: Optional[Sequence[Tuple[str, str]]] = None,
    adjuntos_ia: Optional[Sequence[AdjuntoIA]] = None,
    timeout: float = 55.0,
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

    mensaje_usuario = (
        f"{resumen_historial}"
        f"CONTEXTO DE DOCUMENTOS:\n{contexto_rag}\n\n"
        f"PROCESO CONSULTADO POR EL USUARIO:\n{pregunta}"
    )

    timeout_efectivo = max(timeout, 60.0) if lista_adjuntos else timeout

    try:
        if proveedor == "OpenAI":
            modelo = _leer_clave_env_local("OPENAI_MODEL") or "gpt-4o-mini"
            url_oa = "https://api.openai.com/v1/chat/completions"
            cabeceras_oa = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }

            contenido_usr = _construir_contenido_usuario_openai(mensaje_usuario, lista_adjuntos)
            try:
                datos = _peticion_json_https(
                    url_oa,
                    headers=cabeceras_oa,
                    payload={
                        "model": modelo,
                        "temperature": 0.0,
                        "max_tokens": 1800,
                        "messages": [
                            {"role": "system", "content": _PROMPT_SISTEMA_RAG},
                            {"role": "user", "content": contenido_usr},
                        ],
                    },
                    timeout=timeout_efectivo,
                )
            except urllib.error.HTTPError as err_oa:
                if err_oa.code == 400 and lista_adjuntos:
                    # Respaldo: si el modelo configurado no soporta 'type: file',
                    # extraemos las capturas del PDF como imágenes Vision o usamos el texto
                    imgs_respaldo: List[AdjuntoIA] = []
                    for adj in lista_adjuntos:
                        if adj.mime == "application/pdf":
                            try:
                                raw_pdf = base64.b64decode(adj.datos_b64)
                                imgs_respaldo.extend(_extraer_imagenes_de_pdf_bytes(raw_pdf))
                            except Exception:
                                pass
                        elif adj.mime.startswith("image/"):
                            imgs_respaldo.append(adj)
                    contenido_alt = _construir_contenido_usuario_openai(
                        mensaje_usuario, imgs_respaldo
                    )
                    datos = _peticion_json_https(
                        url_oa,
                        headers=cabeceras_oa,
                        payload={
                            "model": modelo,
                            "temperature": 0.0,
                            "max_tokens": 1800,
                            "messages": [
                                {"role": "system", "content": _PROMPT_SISTEMA_RAG},
                                {"role": "user", "content": contenido_alt},
                            ],
                        },
                        timeout=timeout_efectivo,
                    )
                else:
                    raise

            texto = (
                datos.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if texto.upper() == MARCA_NO_DISPONIBLE or texto.upper().startswith("NO_DISPONIBLE\n"):
                return (MARCA_NO_DISPONIBLE, f"OpenAI · {modelo}", "")
            return (texto, f"OpenAI · {modelo}", "")

        if proveedor == "Gemini":
            modelo = _leer_clave_env_local("GEMINI_MODEL") or "gemini-2.5-flash"
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{modelo}:generateContent"
            )
            partes_usr: List[Dict] = []
            for adj in lista_adjuntos:
                partes_usr.append({
                    "inline_data": {
                        "mime_type": adj.mime,
                        "data": adj.datos_b64,
                    }
                })
            partes_usr.append({"text": mensaje_usuario})

            datos = _peticion_json_https(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                payload={
                    "system_instruction": {"parts": [{"text": _PROMPT_SISTEMA_RAG}]},
                    "contents": [{"role": "user", "parts": partes_usr}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2000},
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
# 7) Diseño Visual en Colores y Tipografía Limpia (Qt Rich Text HTML4)
# ======================================================================

FUENTE_UI = "'Segoe UI', 'Calibri', 'Trebuchet MS', Arial, sans-serif"

# Paleta armónica para las tarjetas numeradas (borde / insignia / título / fondo suave)
PALETA_PASOS: Tuple[Tuple[str, str, str, str], ...] = (
    ("#2563EB", "#2563EB", "#1D4ED8", "#F8FAFF"),  # Azul Real
    ("#4F46E5", "#4F46E5", "#4338CA", "#F9F9FF"),  # Índigo
    ("#0284C7", "#0284C7", "#0369A1", "#F7FCFF"),  # Océano
    ("#0D9488", "#0D9488", "#0F766E", "#F7FEFC"),  # Esmeralda Turquesa
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


def _decorar_linea_rica_ia(texto_crudo: str) -> str:
    """
    Escapa HTML de forma segura y aplica formato visual bonito:
      - **negrita** -> texto destacado oscuro
      - [Botón / Menú] -> pastilla visual estilo botón azul suave
    """
    esc = html.escape(texto_crudo or "")
    esc = re.sub(
        r"\*\*(.+?)\*\*",
        r'<b style="color:#0F172A;">\1</b>',
        esc,
    )
    esc = re.sub(
        r"\[([^\[\]]{2,80})\]",
        (
            r'<span style="background-color:#DBEAFE; color:#1E40AF; '
            r'font-weight:bold;">&nbsp;\1&nbsp;</span>'
        ),
        esc,
    )
    return esc


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


def _html_tarjeta_paso(
    num_paso: int,
    titulo_paso: str,
    cuerpo_html: str,
    subpuntos_html: Sequence[str],
) -> str:
    borde, badge_bg, color_tit, fondo_card = PALETA_PASOS[(max(1, num_paso) - 1) % len(PALETA_PASOS)]

    bloque_tit = (
        f'<div style="font-family:{FUENTE_UI}; font-size:14px; font-weight:bold; '
        f'color:{color_tit}; margin-bottom:4px;">{titulo_paso}</div>'
        if titulo_paso
        else ""
    )
    bloque_cuerpo = (
        f'<div style="font-family:{FUENTE_UI}; font-size:13.5px; color:#1E293B; '
        f'line-height:1.5;">{cuerpo_html}</div>'
        if cuerpo_html
        else ""
    )
    bloque_subs = ""
    if subpuntos_html:
        items = "".join(
            f'<div style="margin:4px 0 2px 6px; font-family:{FUENTE_UI}; '
            f'font-size:13px; color:#334155; line-height:1.45;">'
            f'<span style="color:{badge_bg}; font-weight:bold;">➜</span> {sp}</div>'
            for sp in subpuntos_html
        )
        bloque_subs = f'<div style="margin-top:4px;">{items}</div>'

    return (
        f'<table width="100%" cellpadding="10" cellspacing="0" bgcolor="{fondo_card}" '
        f'style="border:1px solid #DBEAFE; border-left:5px solid {borde}; margin-bottom:8px;">'
        "<tr>"
        '<td width="76" valign="top" style="padding-right:6px;">'
        f'<table cellpadding="4" cellspacing="0" bgcolor="{badge_bg}">'
        "<tr>"
        f'<td style="color:#FFFFFF; font-family:{FUENTE_UI}; font-size:11px; '
        'font-weight:bold; white-space:nowrap;">'
        f"&nbsp;PASO {num_paso}&nbsp;"
        "</td></tr></table>"
        "</td>"
        f'<td valign="top">{bloque_tit}{bloque_cuerpo}{bloque_subs}</td>'
        "</tr></table>"
    )


def _separar_titulo_y_cuerpo_paso(texto_paso: str) -> Tuple[str, str]:
    """
    Si el paso empieza por '**Título**: explicación' o 'Título: explicación',
    separa el título para pintarlo con jerarquía de color propia.
    """
    s = texto_paso.strip()
    m_bold = re.match(r"^\*\*(.+?)\*\*\s*[:\-–—.]?\s*(.*)$", s)
    if m_bold:
        return (html.escape(m_bold.group(1).strip()), _decorar_linea_rica_ia(m_bold.group(2).strip()))

    m_colon = re.match(r"^([^:]{3,55}):\s+(.+)$", s)
    if m_colon and "[" not in m_colon.group(1):
        return (html.escape(m_colon.group(1).strip()), _decorar_linea_rica_ia(m_colon.group(2).strip()))

    return ("", _decorar_linea_rica_ia(s))


def formatear_markdown_ia_html(
    texto_ia: str,
    titulo_respaldo: str = "Manual operativo paso a paso",
    proveedor_ia: str = "IA",
) -> str:
    """
    Convierte la respuesta estructurada de la IA en un manual visual con colores,
    tipografía limpia ('Segoe UI'), tarjetas numeradas PASO 1..N y cajas destacadas.
    """
    titulo_manual = ""
    objetivo = ""
    pasos: List[Dict] = []  # {"num": int, "titulo": str, "cuerpo": str, "subs": List[str]}
    notas_importantes: List[str] = []
    comprobaciones: List[str] = []
    bloques_libres: List[str] = []

    seccion_actual = "pasos"  # "pasos" | "importante" | "comprobacion"

    for cruda in (texto_ia or "").splitlines():
        s = cruda.strip()
        if not s:
            continue

        # Ignorar pie "Fuente: ..." suelto porque ya pintamos un pie discreto interactivo
        if re.match(r"^(?:\*\*)?fuente(?:\*\*)?\s*:", s, re.IGNORECASE) and len(s) < 140:
            continue

        # Título principal (# ...)
        if re.match(r"^#{1,2}\s+", s) and not titulo_manual and not pasos:
            limpio_t = re.sub(r"^#{1,2}\s+", "", s).strip()
            limpio_t = re.sub(r"^[📋📘✨\s]+", "", limpio_t).strip()
            limpio_t = re.sub(r"\*\*(.+?)\*\*", r"\1", limpio_t)
            if not re.search(r"(importante|puntos clave|advertencia|comprobaci|resultado)", normalizar(limpio_t)):
                titulo_manual = limpio_t
                continue

        # Objetivo inicial
        m_obj = re.match(r"^(?:\*\*)?objetivo(?:\*\*)?\s*:\s*(.+)$", s, re.IGNORECASE)
        if m_obj and not objetivo:
            objetivo = m_obj.group(1).strip()
            continue

        # Encabezados de sección secundaria (## ⚠️ Muy importante / ## ✅ Comprobación final)
        if re.match(r"^#{2,3}\s+", s):
            enc_norm = normalizar(re.sub(r"^#{2,3}\s+", "", s))
            if any(k in enc_norm for k in ("importante", "advertencia", "clave", "atencion", "nota", "cuidado")):
                seccion_actual = "importante"
                continue
            if any(k in enc_norm for k in ("comprobacion", "verificar", "verificacion", "resultado", "cierre final")):
                seccion_actual = "comprobacion"
                continue

            # ¿Es un encabezado tipo "### Paso 1: Acceso"?
            m_h_paso = re.match(r"^#{2,3}\s*paso\s*(\d{1,2})\s*[:\-–—.]?\s*(.*)$", s, re.IGNORECASE)
            if m_h_paso:
                seccion_actual = "pasos"
                pasos.append({
                    "num": int(m_h_paso.group(1)),
                    "titulo": html.escape(re.sub(r"\*\*(.+?)\*\*", r"\1", m_h_paso.group(2).strip())),
                    "cuerpo": "",
                    "subs": [],
                })
                continue

        # Paso numerado: "1. ...", "1) ...", "**Paso 1:** ..."
        m_num = re.match(r"^(?:(?:\*\*)?paso\s+)?(\d{1,2})[.):\-]\s*(.+)$", s, re.IGNORECASE)
        if m_num and seccion_actual == "pasos":
            num_p = int(m_num.group(1))
            resto_p = re.sub(r"^\*\*\s*", "", m_num.group(2)) if s.lower().startswith("**paso") else m_num.group(2)
            tit_p, cuerpo_p = _separar_titulo_y_cuerpo_paso(resto_p)
            pasos.append({
                "num": num_p,
                "titulo": tit_p,
                "cuerpo": cuerpo_p,
                "subs": [],
            })
            continue

        # Viñetas (- ..., • ..., * ...)
        if re.match(r"^[-•*▪▸]\s+", s):
            item_limpio = re.sub(r"^[-•*▪▸]\s+", "", s).strip()
            decorado = _decorar_linea_rica_ia(item_limpio)
            if seccion_actual == "importante":
                notas_importantes.append(decorado)
            elif seccion_actual == "comprobacion":
                comprobaciones.append(decorado)
            elif pasos:
                pasos[-1]["subs"].append(decorado)
            else:
                bloques_libres.append(decorado)
            continue

        # Texto adicional perteneciente a la sección activa
        decorado_ln = _decorar_linea_rica_ia(s)
        if seccion_actual == "importante":
            notas_importantes.append(decorado_ln)
        elif seccion_actual == "comprobacion":
            comprobaciones.append(decorado_ln)
        elif pasos:
            if not pasos[-1]["cuerpo"]:
                pasos[-1]["cuerpo"] = decorado_ln
            else:
                pasos[-1]["subs"].append(decorado_ln)
        else:
            bloques_libres.append(decorado_ln)

    titulo_final = html.escape(titulo_manual or titulo_respaldo)
    etiqueta_prov = html.escape(proveedor_ia or "IA")

    partes_html: List[str] = []

    # 1. Cabecera Azul Real / Índigo con tipografía grande y limpia
    partes_html.append(
        '<table width="100%" cellpadding="14" cellspacing="0" bgcolor="#1E3A8A" '
        'style="border:2px solid #2563EB; margin-bottom:8px;">'
        "<tr><td>"
        f'<div style="font-family:{FUENTE_UI}; font-size:11px; font-weight:bold; '
        'color:#BFDBFE; letter-spacing:0.4px; margin-bottom:4px;">'
        f"✨ GUÍA PASO A PASO · ELABORADA CON {etiqueta_prov.upper()}"
        "</div>"
        f'<div style="font-family:{FUENTE_UI}; font-size:17px; font-weight:bold; '
        'color:#FFFFFF; line-height:1.3;">'
        f"📘 {titulo_final}"
        "</div>"
        "</td></tr></table>"
    )

    # 2. Franja Azul Cielo con el Objetivo
    if objetivo:
        partes_html.append(
            '<table width="100%" cellpadding="10" cellspacing="0" bgcolor="#EFF6FF" '
            'style="border:1px solid #93C5FD; border-left:5px solid #0284C7; margin-bottom:10px;">'
            "<tr><td>"
            f'<div style="font-family:{FUENTE_UI}; font-size:13px; color:#0C4A6E; line-height:1.45;">'
            f'<b style="color:#0369A1;">🎯 Objetivo:</b> {_decorar_linea_rica_ia(objetivo)}'
            "</div>"
            "</td></tr></table>"
        )

    # Texto introductorio libre (si lo hubiera)
    if bloques_libres:
        intro_html = "".join(
            f'<div style="font-family:{FUENTE_UI}; font-size:13.5px; color:#1E293B; '
            f'margin:4px 0; line-height:1.5;">{ln}</div>'
            for ln in bloques_libres
        )
        partes_html.append(
            '<table width="100%" cellpadding="10" cellspacing="0" bgcolor="#FFFFFF" '
            'style="border:1px solid #E2E8F0; margin-bottom:8px;">'
            f"<tr><td>{intro_html}</td></tr></table>"
        )

    # 3. Tarjetas numeradas en colores (PASO 1, PASO 2, PASO 3...)
    for idx_p, p in enumerate(pasos, 1):
        num_mostrar = p["num"] if p["num"] > 0 else idx_p
        partes_html.append(
            _html_tarjeta_paso(
                num_paso=num_mostrar,
                titulo_paso=p["titulo"],
                cuerpo_html=p["cuerpo"],
                subpuntos_html=p["subs"],
            )
        )

    # 4. Caja Ámbar Dorada para Puntos Importantes / Advertencias
    if notas_importantes:
        items_imp = "".join(
            f'<div style="font-family:{FUENTE_UI}; font-size:13px; color:#78350F; '
            f'margin:4px 0; line-height:1.45;">'
            f'<span style="color:#D97706; font-weight:bold;">🔸</span> {ni}</div>'
            for ni in notas_importantes
        )
        partes_html.append(
            '<table width="100%" cellpadding="11" cellspacing="0" bgcolor="#FFFBEB" '
            'style="border:1px solid #FCD34D; border-left:5px solid #F59E0B; margin-bottom:8px;">'
            "<tr><td>"
            f'<div style="font-family:{FUENTE_UI}; font-size:13.5px; font-weight:bold; '
            'color:#B45309; margin-bottom:4px;">⚠️ Muy importante a tener en cuenta</div>'
            f"{items_imp}"
            "</td></tr></table>"
        )

    # 5. Caja Verde Esmeralda para Comprobación Final
    if comprobaciones:
        items_ok = "".join(
            f'<div style="font-family:{FUENTE_UI}; font-size:13px; color:#065F46; '
            f'margin:4px 0; line-height:1.45;">'
            f'<span style="color:#059669; font-weight:bold;">✔</span> {co}</div>'
            for co in comprobaciones
        )
        partes_html.append(
            '<table width="100%" cellpadding="11" cellspacing="0" bgcolor="#ECFDF5" '
            'style="border:1px solid #6EE7B7; border-left:5px solid #10B981; margin-bottom:8px;">'
            "<tr><td>"
            f'<div style="font-family:{FUENTE_UI}; font-size:13.5px; font-weight:bold; '
            'color:#047857; margin-bottom:4px;">✅ Comprobación final</div>'
            f"{items_ok}"
            "</td></tr></table>"
        )

    return "".join(partes_html)


def formatear_bloque_manual_html(texto: str, terminos: Sequence[str]) -> str:
    """Formateador visual en colores también para el modo local (sin IA)."""
    lineas_html: List[str] = []
    contador_auto = 0
    for linea in (texto or "").splitlines():
        s = linea.strip()
        if not s:
            lineas_html.append('<div style="height:4px;"></div>')
            continue

        m_num = re.match(r"^(\d{1,2})[.)\-]\s+(.*)$", s)
        if m_num:
            num_p = int(m_num.group(1))
            resto = resaltar_terminos_html(m_num.group(2), terminos)
            lineas_html.append(
                _html_tarjeta_paso(
                    num_paso=num_p,
                    titulo_paso="",
                    cuerpo_html=resto,
                    subpuntos_html=[],
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
            contador_auto += 1
            cuerpo_v = resaltar_terminos_html(re.sub(r"^[-•*▪▸]\s+", "", s), terminos)
            lineas_html.append(
                f'<div style="margin:4px 0 4px 8px; font-family:{FUENTE_UI}; '
                f'font-size:13.5px; color:#1E293B; line-height:1.48;">'
                f'<span style="color:#2563EB; font-weight:bold;">➜</span> {cuerpo_v}</div>'
            )
        else:
            lineas_html.append(
                f'<div style="margin:3px 0; font-family:{FUENTE_UI}; font-size:13.5px; '
                f'color:#1E293B; line-height:1.48;">{resaltar_terminos_html(s, terminos)}</div>'
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
            "No figura ningún manual paso a paso en <b>📚 Conocimiento</b> para este proceso. "
            "Si tienes el archivo fuera de CineStock, incorpóralo desde <b>📚 Conocimiento → "
            "➕ Añadir correos / documentos…</b>"
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


def renderizar_respuesta_asistente_html(
    respuesta: RespuestaAsistente,
    pensando_ia: bool = False,
) -> str:
    if not respuesta.tiene_contenido:
        return _html_cartel_no_disponible(respuesta)

    # Mientras la IA lee el PDF/capturas y redacta la guía paso a paso en colores
    if pensando_ia:
        leyendo_visual = bool(respuesta.adjuntos_ia)
        detalle_carga = (
            "Analizando las páginas y capturas del documento para escribirte todos los pasos claros…"
            if leyendo_visual
            else "Transformando el documento de tu biblioteca en una guía clara y ordenada…"
        )
        return (
            '<table width="100%" cellpadding="14" cellspacing="0" bgcolor="#EFF6FF" '
            'style="border:2px solid #60A5FA; margin-bottom:8px;">'
            "<tr><td>"
            f'<div style="font-family:{FUENTE_UI}; color:#1E3A8A; font-size:14px; '
            'font-weight:bold; margin-bottom:3px;">'
            "🎨 Creando tu manual paso a paso en colores con IA…"
            "</div>"
            f'<div style="font-family:{FUENTE_UI}; color:#1D4ED8; font-size:12.5px;">'
            f"{detalle_carga}"
            "</div>"
            "</td></tr></table>"
        )

    # 1) MODO PROTAGONISTA: Manual paso a paso escrito con IA (letra bonita y colores)
    if respuesta.texto_ia:
        titulo_def = f"Manual paso a paso: {respuesta.pregunta.strip()}"
        manual_colores = formatear_markdown_ia_html(
            respuesta.texto_ia,
            titulo_respaldo=titulo_def,
            proveedor_ia=respuesta.proveedor_ia or "IA",
        )
        bloques_ia = [manual_colores]

        # Pie discreto de una sola línea por si algún día se quiere consultar el archivo raíz
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
                    f"ver archivo base</a>)"
                )
            bloques_ia.append(
                '<table width="100%" cellpadding="7" cellspacing="0" bgcolor="#F8FAFC" '
                'style="border:1px solid #E2E8F0; margin-top:2px;">'
                f'<tr><td style="font-family:{FUENTE_UI}; font-size:11.5px; color:#64748B;">'
                f'📎 Basado en: {" · ".join(enlaces_discretos)}'
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

    # Caso sin IA disponible cuando el PDF es 100 % imagen sin texto OCR
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
                "Activa la casilla <b>✨ Redactar con IA</b> (con clave activa en <code>.env</code>) "
                "para que la IA lea las capturas y te escriba aquí el manual paso a paso en colores, "
                "o ábrelo directamente:"
                "</div>"
                f'<a href="abrir:{pasaje.doc_id}" '
                f'style="font-family:{FUENTE_UI}; color:#1D4ED8; font-weight:bold; '
                'text-decoration:none; font-size:13px;">'
                "📂 Abrir manual original</a>"
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
                _html_tarjeta_paso(
                    num_paso=i,
                    titulo_paso="",
                    cuerpo_html=resaltar_terminos_html(paso, respuesta.terminos),
                    subpuntos_html=[],
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
        for pasaje in respuesta.pasajes:
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
