"""
Motor de Alta Precisión del Asistente de Inicio para CineStock (sin dependencias de Qt).

Filosofía ("Manual exacto o No disponible"):
  - El usuario busca MANUALES OPERATIVOS PASO A PASO para ejecutar procesos
    concretos del cine, NO menciones sueltas, correos informativos ni procesos
    parecidos que compartan una palabra ("solicitud de vacaciones" != "solicitud
    de pedido"; "conciliación bancaria/laboral" != "conciliación de inventario";
    "10. Cierre de Día" != "cómo hacer el inventario").
  - Etapa 1 (Local estricta):
      * Agrupa la pregunta en conceptos obligatorios con equivalentes estrictos.
        TODOS los conceptos de la consulta (100 % hasta 3 conceptos) deben estar
        presentes en el apartado.
      * Exige evidencia real de MANUAL / PROCEDIMIENTO (pasos numerados, viñetas
        operativas o secuencia clara de instrucciones) y autoridad temática en el
        título/epígrafe (descarta menciones laterales en otros procesos).
  - Etapa 2 (Verificador + Redactor IA con OpenAI / Gemini):
      * Verifica semánticamente que el fragmento explique de verdad cómo hacer
        EXACTAMENTE el proceso preguntado. Si no lo explica, devuelve
        NO_DISPONIBLE y se muestra el cartel limpio de "No disponible".
      * Si sí lo explica, redacta una guía tipo manual paso a paso limpia.
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
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .conocimiento import cargar_conocimiento


# ======================================================================
# 1) Normalización y Conceptos Estrictos (cero sinónimos laxos)
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

# Sustantivos "genéricos/calificables" que por sí solos son ambiguos si el usuario
# especificó un complemento ("solicitud DE pedido", "conciliación DE inventario").
SUSTANTIVOS_CALIFICABLES = frozenset({
    "solicitud", "conciliacion", "cierre", "apertura", "cambio", "alta",
    "baja", "registro", "control", "gestion", "envio", "informe", "estado",
    "proceso", "parte", "hoja", "ficha", "consulta", "autorizacion",
})

# Equivalencias ESTRICTAS: solo términos que significan exactamente lo mismo en el cine.
# NUNCA mezclar aquí "solicitud" con "pedido", ni "conciliación" con "diferencia".
CLASES_EQUIVALENCIA: Tuple[Tuple[str, ...], ...] = (
    ("inventario", "inventariar", "stock", "conteo", "recuento"),
    ("conciliacion", "conciliar"),
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


def _equivalentes_de(termino: str) -> Tuple[str, ...]:
    """Devuelve las variantes estrictamente equivalentes a un término."""
    t = _simplificar_termino(termino)
    raiz_t = _raiz_busqueda(t)
    for clase in CLASES_EQUIVALENCIA:
        clase_norm = tuple(_simplificar_termino(x) for x in clase)
        if t in clase_norm or any(_raiz_busqueda(x) == raiz_t for x in clase_norm):
            return clase_norm
    return (t,)


def extraer_terminos_consulta(
    pregunta: str,
    historial_preguntas: Optional[Sequence[str]] = None,
) -> Tuple[List[str], List[Tuple[str, ...]]]:
    """
    Devuelve:
      - terminos_principales: lista de palabras clave limpias de la pregunta.
      - ranuras_concepto: lista de tuplas de equivalentes estrictos; CADA tupla
        representa un concepto obligatorio distinto de la consulta.
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

    texto_norm = normalizar(pregunta or "").strip(" ¿?¡!.,;:")
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

    # Agrupar en ranuras de concepto obligatorias (para que "mermas o bajas" sea 1 ranura,
    # pero "conciliación de inventario" o "solicitud de pedido" sean 2 ranuras obligatorias).
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
    rechazado_por_ia: bool = False
    biblioteca_vacia: bool = False
    total_documentos: int = 0
    error_biblioteca: Optional[str] = None

    @property
    def tiene_contenido(self) -> bool:
        if self.rechazado_por_ia:
            return False
        return self.procedimiento is not None or bool(self.pasajes)


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


def _ranura_coincide_en(ranura: Sequence[str], texto_norm: str) -> bool:
    for variante in ranura:
        raiz = re.escape(_raiz_busqueda(variante))
        if re.search(r"(?<!\w)" + raiz, texto_norm):
            return True
    return False


def buscar_procedimiento_confirmado(
    pregunta: str,
    terminos_principales: Optional[Sequence[str]] = None,
) -> Optional[ProcedimientoCoincidente]:
    """
    Solo devuelve un procedimiento confirmado si cubre TODOS los conceptos clave
    de la consulta (o coincide directamente con una de sus preguntas clave).
    """
    if not pregunta or not pregunta.strip():
        return None

    procedimientos = _cargar_lista_procedimientos()
    if not procedimientos:
        return None

    limpia = re.sub(r"[¿?¡!.,;:]", " ", normalizar(pregunta))
    limpia = re.sub(r"\s+", " ", limpia).strip()

    _, ranuras = extraer_terminos_consulta(pregunta)

    mejor_proc: Optional[Dict] = None
    mejor_pts = 0.0

    for proc in procedimientos:
        pts = 0.0
        preguntas_clave = [
            re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;:]", " ", normalizar(pk))).strip()
            for pk in proc.get("preguntas_clave", [])
        ]

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
            # Exigir el 100 % de las ranuras de concepto (hasta 3 conceptos)
            min_ranuras = len(ranuras) if len(ranuras) <= 3 else len(ranuras) - 1
            ranuras_en_todo = sum(1 for r in ranuras if _ranura_coincide_en(r, todo_norm))
            ranuras_en_cab = sum(1 for r in ranuras if _ranura_coincide_en(r, cabecera_norm))

            if ranuras_en_todo < min_ranuras or ranuras_en_cab == 0:
                continue

            # Si hay sustantivo calificable ("cambio", "solicitud"), exigir que el
            # concepto específico (ej. "pelicula"/"sala") esté en la cabecera
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
    r"^\s*(?:[-•*▪▸]\s+\S|\d{1,2}[.)]\s+\S|paso\s+\d+\b)",
    re.IGNORECASE,
)
_RX_PASO_O_NOTA = re.compile(
    r"^\s*(?:[-•*▪▸]\s+\S|\d{1,2}[.)]\s+\S|paso\s+\d+\b|nota\s*:|importante\s*:|atenci[oó]n\s*:)",
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
confirma esperar espera repetir repite asegurar asegurate
""".split())

_RX_VERBOS_OPERATIVOS = re.compile(
    r"\b(entrar|entra|acceder|accede|pulsar|pulsa|presionar|presiona|"
    r"seleccionar|selecciona|ingresar|ingresa|introducir|introduce|"
    r"guardar|guarda|comprobar|comprueba|revisar|revisa|verificar|verifica|"
    r"imprimir|imprime|contar|cuenta|validar|valida|hacer clic|haz clic|"
    r"pestana|modulo|boton|desplegable|menu)\b"
)

MAX_CHARS_SECCION = 1450
MIN_CHARS_FUSION = 90


def limpiar_texto_manual(texto: str) -> str:
    if not texto:
        return ""

    t = texto.replace("\r\n", "\n").replace("\r", "\n")
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
# 4) Filtro y Puntuación ULTRA-ESTRICTA de Manuales Operativos
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


def _tiene_estructura_manual(texto_seccion: str, tipo_doc: str = "") -> bool:
    """
    Exige que el bloque sea realmente instructivo/operativo (pasos o acciones de cómo
    hacer algo), descartando simples correos de aviso, tablas sueltas o menciones breves.
    """
    if tipo_doc == "xlsx":
        # Una hoja de cálculo de planificación/números no es un manual paso a paso
        lineas_paso_xlsx = sum(1 for ln in texto_seccion.splitlines() if _RX_PASO_ESTRICTO.match(ln))
        return lineas_paso_xlsx >= 3

    lineas = [ln.strip() for ln in texto_seccion.splitlines() if ln.strip()]
    n_pasos = sum(1 for ln in lineas if _RX_PASO_ESTRICTO.match(ln))
    if n_pasos >= 2:
        return True

    norm = normalizar(texto_seccion)
    verbos = len(_RX_VERBOS_OPERATIVOS.findall(norm))
    if n_pasos >= 1 and verbos >= 2:
        return True
    if verbos >= 3 and len(texto_seccion) >= 140:
        return True

    return False


def _evaluar_seccion_estricta(
    titulo_doc: str,
    titulo_seccion: str,
    texto_seccion: str,
    tipo_doc: str,
    ranuras: Sequence[Tuple[str, ...]],
    estado_doc: str = "NORMAL",
    importancia_doc: float = 0.0,
    fecha_doc: str = "",
) -> float:
    """
    Evalúa con criterio estricto si una sección es un MANUAL REAL del proceso pedido.
    Devuelve 0.0 si falta algún concepto obligatorio, si es una mención lateral en otro
    proceso o si carece de instrucciones paso a paso.
    """
    if not ranuras:
        return 0.0

    # 1) Debe tener estructura real de procedimiento/manual
    if not _tiene_estructura_manual(texto_seccion, tipo_doc):
        return 0.0

    norm_tit_doc = normalizar(titulo_doc)
    norm_tit_sec = normalizar(titulo_seccion)
    norm_cuerpo = normalizar(texto_seccion)
    norm_contexto_seccion = f"{norm_tit_sec} {norm_cuerpo}"
    norm_completo = f"{norm_tit_doc} {norm_tit_sec} {norm_cuerpo}"

    n_ranuras = len(ranuras)
    min_ranuras = n_ranuras if n_ranuras <= 3 else n_ranuras - 1

    # 2) Cobertura obligatoria de TODOS los conceptos de la pregunta:
    #    - Todos deben estar en (título_doc + título_sección + cuerpo)
    #    - Y al menos todos menos uno (o todos si <=2) dentro de la propia sección
    ranuras_en_completo = 0
    ranuras_en_seccion = 0
    ranuras_en_titulos = 0
    lineas_norm = [normalizar(ln) for ln in texto_seccion.splitlines() if ln.strip()]

    # Ranuras "específicas" (ej. 'inventario' en 'conciliación de inventario',
    # 'pedido' en 'solicitud de pedido')
    ranuras_especificas = [
        r for r in ranuras if not any(v in SUSTANTIVOS_CALIFICABLES for v in r)
    ] or list(ranuras)

    pts = 0.0

    for ranura in ranuras:
        en_sec = _ranura_coincide_en(ranura, norm_contexto_seccion)
        en_td = _ranura_coincide_en(ranura, norm_tit_doc)
        en_ts = _ranura_coincide_en(ranura, norm_tit_sec)

        if en_sec or en_td:
            ranuras_en_completo += 1
        if en_sec:
            ranuras_en_seccion += 1
            pts += 15.0
        if en_ts:
            ranuras_en_titulos += 1
            pts += 26.0
        elif en_td and en_sec:
            ranuras_en_titulos += 1
            pts += 16.0

    if ranuras_en_completo < min_ranuras:
        return 0.0

    # Para consultas de 1 o 2 conceptos ("solicitud de pedido", "conciliación de inventario"),
    # exigimos que AMBOS conceptos estén presentes dentro de la sección (o su título + cuerpo),
    # salvo que uno sea el título general del manual y el otro el título de la sección.
    if n_ranuras <= 2 and ranuras_en_seccion < n_ranuras:
        if not (ranuras_en_completo == n_ranuras and ranuras_en_titulos == n_ranuras):
            return 0.0

    # 3) Guardia contra menciones laterales (ej. "10. Cierre de Día" mencionando "inventario" en 1 línea):
    es_titulo_generico_contenedor = (
        not titulo_seccion
        or titulo_seccion.lower().startswith(("diapositiva ", "adjunto:", "archivo:", "imagen:"))
    )

    especificas_en_tit_sec = sum(1 for r in ranuras_especificas if _ranura_coincide_en(r, norm_tit_sec))
    especificas_en_tit_doc = sum(1 for r in ranuras_especificas if _ranura_coincide_en(r, norm_tit_doc))

    # Contar en cuántas líneas distintas del cuerpo aparece el tema específico
    lineas_con_tema = 0
    for ln in lineas_norm:
        if any(_ranura_coincide_en(r, ln) for r in ranuras_especificas):
            lineas_con_tema += 1

    if not es_titulo_generico_contenedor:
        # La sección tiene un epígrafe explícito (p. ej. "10. Cierre de Día" o "CONCILIACIÓN DE STOCK").
        # Si el epígrafe NO contiene ninguno de los conceptos específicos preguntados,
        # rechazamos salvo que el título del documento SÍ sea del tema Y el cuerpo trate el tema a fondo.
        if especificas_en_tit_sec == 0:
            if especificas_en_tit_doc == 0 or lineas_con_tema < 2:
                return 0.0
    else:
        # Sin epígrafe de sección (ej. diapositiva o texto corrido):
        # si el título del documento tampoco es del tema, exigimos presencia reiterada (>=2 líneas)
        if especificas_en_tit_doc == 0 and lineas_con_tema < 2:
            return 0.0

    # 4) Bono por proximidad cuando hay 2+ conceptos ("solicitud de pedido", "conciliación de inventario")
    if len(ranuras) >= 2:
        # Comprobar si ambos conceptos aparecen en la misma frase/línea o título
        en_misma_linea = any(
            all(_ranura_coincide_en(r, ln) for r in ranuras[:2])
            for ln in ([norm_tit_sec, norm_tit_doc] + lineas_norm)
            if ln
        )
        if en_misma_linea:
            pts += 18.0
        elif especificas_en_tit_sec == 0 and especificas_en_tit_doc == 0:
            # Dos palabras sueltas en párrafos lejanos de un documento sin título del tema -> descartar
            return 0.0

    n_pasos = sum(1 for ln in texto_seccion.splitlines() if _RX_PASO_ESTRICTO.match(ln))
    pts += min(n_pasos * 2.0, 10.0)

    if estado_doc == "DESTACADO":
        pts += 5.0
    pts += min(max(importancia_doc, 0.0) * 0.10, 4.0)
    pts += _bono_recencia(fecha_doc)

    return pts


def buscar_pasajes_biblioteca(
    biblioteca,
    pregunta: str,
    ranuras: Sequence[Tuple[str, ...]],
    max_documentos: int = 3,
) -> List[PasajeManual]:
    if biblioteca is None or not ranuras:
        return []

    candidatos_por_id: Dict[int, object] = {}

    # Buscar candidatos por cada variante equivalente de cada concepto
    terminos_fts: List[str] = []
    for ranura in ranuras:
        for variante in ranura:
            if variante not in terminos_fts:
                terminos_fts.append(variante)
            raiz = _raiz_busqueda(variante)
            if len(raiz) >= 4 and raiz not in terminos_fts:
                terminos_fts.append(raiz)

    for q in terminos_fts:
        try:
            for r in biblioteca.buscar(q, "todos", 40):
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
        tipo_doc = ficha.get("tipo") or getattr(resumen, "tipo", "texto")
        if not texto_completo:
            continue

        secciones = dividir_documento_en_secciones(texto_completo, titulo_doc)
        if not secciones:
            continue

        evaluadas: List[Tuple[float, int, str, str]] = []
        for idx, (tit_sec, txt_sec) in enumerate(secciones):
            pts = _evaluar_seccion_estricta(
                titulo_doc=titulo_doc,
                titulo_seccion=tit_sec,
                texto_seccion=txt_sec,
                tipo_doc=tipo_doc,
                ranuras=ranuras,
                estado_doc=ficha.get("estado") or "NORMAL",
                importancia_doc=float(ficha.get("importancia") or 0.0),
                fecha_doc=ficha.get("fecha") or "",
            )
            if pts >= 22.0:
                evaluadas.append((pts, idx, tit_sec, txt_sec))

        if not evaluadas:
            continue

        evaluadas.sort(key=lambda x: x[0], reverse=True)
        mejor_pts, mejor_idx, mejor_tit, mejor_txt = evaluadas[0]

        def _crear_pasaje(t_sec: str, c_sec: str, p_sec: float) -> PasajeManual:
            return PasajeManual(
                doc_id=doc_id,
                titulo_documento=titulo_doc,
                tipo_documento=tipo_doc,
                fecha=ficha.get("fecha") or "",
                categoria=ficha.get("categoria") or getattr(resumen, "categoria", ""),
                remitente=ficha.get("remitente") or "",
                estado=ficha.get("estado") or "NORMAL",
                titulo_seccion=t_sec,
                texto_seccion=c_sec,
                puntuacion=p_sec,
            )

        seleccion_doc = [_crear_pasaje(mejor_tit, mejor_txt, mejor_pts)]

        # Si el mismo manual tiene una segunda parte directa de ese mismo procedimiento
        if len(evaluadas) > 1:
            seg_pts, seg_idx, seg_tit, seg_txt = evaluadas[1]
            if seg_pts >= mejor_pts * 0.70 and abs(seg_idx - mejor_idx) == 1:
                par = [(mejor_idx, seleccion_doc[0]), (seg_idx, _crear_pasaje(seg_tit, seg_txt, seg_pts))]
                par.sort(key=lambda x: x[0])
                seleccion_doc = [p for _, p in par]

        mejores_por_doc.append((mejor_pts, seleccion_doc))

    mejores_por_doc.sort(key=lambda item: item[0], reverse=True)

    # Si el documento #1 destaca claramente sobre el resto, no mezclamos manuales secundarios flojos
    if mejores_por_doc:
        tope = mejores_por_doc[0][0]
        mejores_por_doc = [m for m in mejores_por_doc if m[0] >= tope * 0.68]

    salida: List[PasajeManual] = []
    for pos, (_, lista_pasajes) in enumerate(mejores_por_doc[:max_documentos]):
        if pos == 0:
            salida.extend(lista_pasajes[:2])
        else:
            salida.append(lista_pasajes[0])

    return salida


# ======================================================================
# 5) Consulta completa local
# ======================================================================

def consultar_base_local(
    pregunta: str,
    biblioteca=None,
    historial_preguntas: Optional[Sequence[str]] = None,
) -> RespuestaAsistente:
    terminos, ranuras = extraer_terminos_consulta(pregunta, historial_preguntas)
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
                    biblioteca, pregunta, ranuras, max_documentos=2
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
# 6) Verificador Estricto + Generador de Manual con IA (OpenAI / Gemini)
# ======================================================================

MARCA_NO_DISPONIBLE = "NO_DISPONIBLE"


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
        meta = " | ".join(
            x for x in (
                pasaje.titulo_documento,
                f"Sección: {pasaje.titulo_seccion}" if pasaje.titulo_seccion else "",
                f"Fecha: {pasaje.fecha}" if pasaje.fecha else "",
            ) if x
        )
        bloques.append(f"[FUENTE {i}: {meta}]\n{pasaje.texto_seccion}")

    return "\n\n---\n\n".join(bloques)


_PROMPT_SISTEMA_RAG = (
    "Eres un verificador y redactor de manuales operativos de máxima precisión para CineStock.\n"
    "El usuario SOLO quiere el MANUAL PASO A PASO EXACTO del proceso que pregunta. "
    "NO quiere información aproximada, correos generales ni procesos parecidos.\n\n"
    "PASO 1 - VERIFICACIÓN ESTRICTA DE CORRESPONDENCIA:\n"
    "Comprueba si el CONTEXTO explica realmente los pasos concretos para ejecutar "
    "EXACTAMENTE el proceso por el que pregunta el usuario:\n"
    "- Si preguntan por 'conciliación de inventario' y el texto habla de otro tipo de "
    "conciliación (laboral, bancaria, caja) o solo menciona inventario de pasada -> NO SIRVE.\n"
    "- Si preguntan por 'solicitud de pedido' y el texto habla de otro tipo de solicitudes "
    "(permisos, vacaciones, matinales, tickets de soporte) -> NO SIRVE.\n"
    "- Si el texto es solo un aviso informativo sin instrucciones operativas de cómo hacerlo -> NO SIRVE.\n"
    "En TODOS esos casos donde el contexto NO contenga el manual real de ese proceso exacto, "
    "responde ÚNICAMENTE con la palabra exacta:\n"
    "NO_DISPONIBLE\n"
    "(sin añadir ninguna otra palabra ni explicación).\n\n"
    "PASO 2 - SI Y SOLO SI EL CONTEXTO EXPLICA REALMENTE ESE PROCESO EXACTO:\n"
    "Redacta una ficha de manual limpia, directa y accionable usando SOLO datos del CONTEXTO "
    "(prohibido inventar pasos, botones o rutas que no estén en el texto):\n"
    "## 📋 Manual paso a paso: [Nombre exacto del proceso]\n"
    "1. **Paso 1**: instrucción clara y exacta.\n"
    "2. **Paso 2**: instrucción clara y exacta.\n"
    "(...)\n"
    "## ⚠️ Comprobaciones clave\n"
    "- (Solo si el manual original menciona validaciones o notas importantes).\n"
    "Fuente: [Título del documento]."
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
        return f"Clave de {proveedor} no válida en .env (HTTP 401). Mostrando manual local."
    if err.code == 429:
        if "insufficient_quota" in str(detalle).lower() or "quota" in str(detalle).lower():
            return (
                f"Tu cuenta de {proveedor} no tiene saldo disponible (HTTP 429). "
                "Mostrando manual local."
            )
        return f"Límite temporal de {proveedor} (HTTP 429). Mostrando manual local."
    return f"{proveedor} devolvió HTTP {err.code}. Mostrando manual local."


def sintetizar_con_ia(
    pregunta: str,
    contexto_rag: str,
    historial_turnos: Optional[Sequence[Tuple[str, str]]] = None,
    timeout: float = 18.0,
) -> Tuple[str, str, str]:
    """
    Devuelve (texto_generado_o_NO_DISPONIBLE, nombre_proveedor, aviso_si_falla).
    Si la IA dictamina que el contexto no responde exactamente al proceso pedido,
    devuelve texto_generado == 'NO_DISPONIBLE'.
    """
    deteccion = detectar_proveedor_ia()
    if not deteccion or not contexto_rag.strip():
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
                    "temperature": 0.0,
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
            if texto.upper().startswith(MARCA_NO_DISPONIBLE):
                return (MARCA_NO_DISPONIBLE, f"OpenAI · {modelo}", "")
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
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 900},
                },
                timeout=timeout,
            )
            partes = (
                datos.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )
            texto = "".join(p.get("text", "") for p in partes).strip()
            if texto.upper().startswith(MARCA_NO_DISPONIBLE):
                return (MARCA_NO_DISPONIBLE, f"Gemini · {modelo}", "")
            return (texto, f"Gemini · {modelo}", "")

    except urllib.error.HTTPError as e:
        return ("", proveedor, _explicar_http_error(proveedor, e))
    except Exception as e:
        motivo = "certificado SSL" if "SSL" in str(e).upper() else "red"
        return ("", proveedor, f"IA sin conexión ({motivo}); mostrando manual local.")

    return ("", "", "")


# ======================================================================
# 7) Formateo HTML limpio (Manual exacto o Cartel "NO DISPONIBLE")
# ======================================================================

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


def _html_cartel_no_disponible(respuesta: RespuestaAsistente) -> str:
    """Cartel limpio y directo cuando no existe un manual exacto del proceso consultado."""
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
            "No hay ningún manual paso a paso cargado en <b>📚 Conocimiento</b> que explique "
            "específicamente este proceso. Cuando añadas su manual oficial, aparecerá aquí."
        )

    return (
        '<table width="100%" cellpadding="14" cellspacing="0" bgcolor="#FEF2F2" '
        'style="border:2px solid #F87171; margin-bottom:6px;">'
        "<tr><td>"
        '<div style="font-size:14px; font-weight:bold; color:#991B1B; margin-bottom:4px;">'
        "🚫 PROCEDIMIENTO NO DISPONIBLE"
        "</div>"
        f'<div style="font-size:13px; color:#7F1D1D; margin-bottom:5px;">'
        f"Sin manual disponible para: <b>«{consulta_esc}»</b>."
        "</div>"
        f'<div style="font-size:12px; color:#991B1B;">{sub}</div>'
        "</td></tr></table>"
    )


def renderizar_respuesta_asistente_html(
    respuesta: RespuestaAsistente,
    pensando_ia: bool = False,
) -> str:
    # Si no hay coincidencias estrictas o la IA verificó que no corresponde al proceso -> Cartel directo
    if not respuesta.tiene_contenido:
        return _html_cartel_no_disponible(respuesta)

    bloques: List[str] = []

    if pensando_ia:
        return (
            '<table width="100%" cellpadding="12" cellspacing="0" bgcolor="#F0FDFA" '
            'style="border:1px solid #5EEAD4; margin-bottom:8px;">'
            '<tr><td style="color:#0F766E; font-size:13px;">'
            "<b>🤖 Verificando manual exacto y redactando pasos…</b>"
            "</td></tr></table>"
        )

    # 1) Si la IA validó y redactó el manual paso a paso
    if respuesta.texto_ia:
        cuerpo_ia = formatear_markdown_ia_html(respuesta.texto_ia)
        etiqueta_prov = html.escape(respuesta.proveedor_ia or "IA")
        bloques.append(
            '<table width="100%" cellpadding="12" cellspacing="0" bgcolor="#F8FAFC" '
            'style="border:1px solid #99F6E4; margin-bottom:8px;">'
            "<tr><td>"
            '<div style="color:#0F766E; font-size:11.5px; font-weight:bold; '
            'letter-spacing:0.3px; margin-bottom:6px;">'
            f"📘 MANUAL OPERATIVO PASO A PASO ({etiqueta_prov})"
            "</div>"
            f'<div style="color:#0F172A; font-size:13px; line-height:1.48; '
            f'background-color:#FFFFFF; padding:10px 12px; border:1px solid #E2E8F0;">'
            f"{cuerpo_ia}</div>"
            "</td></tr></table>"
        )

        if respuesta.pasajes:
            vistos = set()
            enlaces_fuentes: List[str] = []
            for pasaje in respuesta.pasajes:
                if pasaje.doc_id in vistos:
                    continue
                vistos.add(pasaje.doc_id)
                icono = ICONOS_DOC.get(pasaje.tipo_documento, "📄")
                sec = f" ({html.escape(pasaje.titulo_seccion)})" if pasaje.titulo_seccion else ""
                enlaces_fuentes.append(
                    f'<div style="margin:2px 0; font-size:12px; color:#334155;">'
                    f'{icono}: <b>{html.escape(pasaje.titulo_documento)}</b>{sec} — '
                    f'<a href="abrir:{pasaje.doc_id}" '
                    f'style="color:#1D4ED8; font-weight:bold; text-decoration:none;">'
                    f"📂 Abrir manual original</a></div>"
                )
            bloques.append(
                '<table width="100%" cellpadding="8" cellspacing="0" bgcolor="#F1F5F9" '
                'style="border:1px solid #CBD5E1; margin-bottom:6px;">'
                "<tr><td>"
                f'{"".join(enlaces_fuentes)}'
                "</td></tr></table>"
            )
        return "".join(bloques)

    if respuesta.aviso_ia:
        bloques.append(
            '<table width="100%" cellpadding="7" cellspacing="0" bgcolor="#FFFBEB" '
            'style="border:1px solid #FDE68A; margin-bottom:8px;">'
            f'<tr><td style="color:#92400E; font-size:11.5px;">'
            f"ℹ️ {html.escape(respuesta.aviso_ia)}</td></tr></table>"
        )

    # 2) Procedimiento confirmado oficial (conocimiento.json)
    if respuesta.procedimiento is not None:
        p = respuesta.procedimiento
        partes_p = [
            '<div style="color:#15803D; font-size:12px; font-weight:bold; margin-bottom:4px;">'
            "✅ PROCEDIMIENTO CONFIRMADO</div>",
            f'<div style="font-size:14px; font-weight:bold; color:#0F172A; margin-bottom:4px;">'
            f"{html.escape(p.titulo)}</div>",
        ]
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

    # 3) Apartados de manual (solo los que han superado el filtro estricto de procedimiento)
    if respuesta.pasajes:
        for pasaje in respuesta.pasajes:
            icono = ICONOS_DOC.get(pasaje.tipo_documento, "📄 Manual")
            detalles = [icono]
            if pasaje.fecha:
                detalles.append(f"Fecha: {html.escape(pasaje.fecha)}")
            if pasaje.categoria:
                detalles.append(html.escape(pasaje.categoria))
            linea_meta = " · ".join(detalles)
            sub_sec = (
                f'<div style="color:#1D4ED8; font-weight:bold; font-size:12.5px; margin:4px 0;">'
                f"📌 {html.escape(pasaje.titulo_seccion)}</div>"
                if pasaje.titulo_seccion
                else ""
            )
            cuerpo_html = formatear_bloque_manual_html(pasaje.texto_seccion, respuesta.terminos)

            bloques.append(
                '<table width="100%" cellpadding="10" cellspacing="0" bgcolor="#F8FAFC" '
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
                f"📂 Abrir manual original</a></div>"
                "</td></tr></table>"
            )

    return "".join(bloques)
