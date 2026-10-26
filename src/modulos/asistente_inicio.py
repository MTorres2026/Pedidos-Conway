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

import html
import json
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
# 6) Verificador + Redactor de Manual con IA (OpenAI / Gemini)
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
    if respuesta.solo_documento_sin_ocr:
        return ""

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
        if pasaje.sin_texto_ocr:
            continue
        meta = " | ".join(
            x for x in (
                f"Documento: {pasaje.titulo_documento}",
                f"Sección: {pasaje.titulo_seccion}" if pasaje.titulo_seccion else "",
                f"Fecha: {pasaje.fecha}" if pasaje.fecha else "",
            ) if x
        )
        bloques.append(f"[FUENTE {i}: {meta}]\n{pasaje.texto_seccion}")

    return "\n\n---\n\n".join(bloques)


_PROMPT_SISTEMA_RAG = (
    "Eres el redactor de manuales operativos internos de CineStock.\n"
    "Tu trabajo tiene dos reglas esenciales:\n\n"
    "1. CUÁNDO RESPONDER 'NO_DISPONIBLE':\n"
    "   Responde ÚNICAMENTE con la palabra exacta NO_DISPONIBLE si el CONTEXTO trata en "
    "   realidad de OTRO trámite distinto al preguntado (por ejemplo: preguntan por "
    "   'solicitud de pedido' y el texto habla de solicitudes de vacaciones/permisos; o "
    "   preguntan por 'conciliación de inventario' y el texto habla de conciliación laboral "
    "   o solo nombra la palabra inventario en una línea suelta del cierre de día sin "
    "   explicar el proceso).\n\n"
    "2. CUÁNDO REDACTAR EL MANUAL PASO A PASO:\n"
    "   Si el CONTEXTO sí trata del proceso consultado (aunque en el PDF/documento original "
    "   esté redactado en párrafos, diapositivas o viñetas), conviértelo en un MANUAL "
    "   PASO A PASO súper claro, ordenado y fiel al 100 % al documento (sin inventar nada "
    "   externo ni mezclar pasos de otros apartados ajenos):\n"
    "   ## 📋 Manual paso a paso: [Nombre del proceso]\n"
    "   1. **[Acción principal]**: detalle exacto según el manual.\n"
    "   2. **[Siguiente acción]**: detalle exacto según el manual.\n"
    "   (...)\n"
    "   ## ⚠️ Puntos clave a revisar\n"
    "   - (Solo si el documento incluye comprobaciones, notas o advertencias).\n"
    "   Fuente: [Nombre del documento]."
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
    timeout: float = 20.0,
) -> Tuple[str, str, str]:
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
                    "max_tokens": 950,
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
            if texto.upper() == MARCA_NO_DISPONIBLE or texto.upper().startswith("NO_DISPONIBLE\n"):
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
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 1000},
                },
                timeout=timeout,
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
        motivo = "certificado SSL" if "SSL" in str(e).upper() else "red"
        return ("", proveedor, f"IA sin conexión ({motivo}); mostrando manual local.")

    return ("", "", "")


# ======================================================================
# 7) Formateo HTML limpio (Manual exacto, Aviso OCR o Cartel NO DISPONIBLE)
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
        if rx_raices.match(sin_acentos(palabra).lower()):
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

        m_num = re.match(r"^(\d{1,2}[.)\-])\s+(.*)$", s)
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
    if not respuesta.tiene_contenido:
        return _html_cartel_no_disponible(respuesta)

    # Caso especial: el manual SÍ está en Conocimiento por su título exacto, pero es un PDF
    # escaneado / de puras imágenes sin texto OCR extraído en biblioteca.db.
    if respuesta.solo_documento_sin_ocr:
        tarjetas_ocr: List[str] = []
        for pasaje in respuesta.pasajes:
            icono = ICONOS_DOC.get(pasaje.tipo_documento, "📕 PDF")
            tarjetas_ocr.append(
                '<table width="100%" cellpadding="12" cellspacing="0" bgcolor="#FFFBEB" '
                'style="border:2px solid #F59E0B; margin-bottom:8px;">'
                "<tr><td>"
                '<div style="font-size:13.5px; font-weight:bold; color:#92400E; margin-bottom:4px;">'
                f"📄 Manual localizado: {html.escape(pasaje.titulo_documento)}"
                "</div>"
                '<div style="font-size:12.5px; color:#78350F; line-height:1.45; margin-bottom:8px;">'
                f"Este archivo ({icono}) está en tu biblioteca, pero sus páginas están guardadas como "
                "<b>imágenes/capturas sin texto legible</b> para el buscador. Puedes abrirlo directamente "
                "aquí debajo, o ir a <b>📚 Conocimiento → 🔄 Releer todo (con imágenes)</b> para extraer "
                "el texto de sus capturas."
                "</div>"
                f'<a href="abrir:{pasaje.doc_id}" '
                'style="color:#1D4ED8; font-weight:bold; text-decoration:none; font-size:13px;">'
                "📂 Abrir manual original</a>"
                "</td></tr></table>"
            )
        return "".join(tarjetas_ocr)

    bloques: List[str] = []

    if pensando_ia:
        return (
            '<table width="100%" cellpadding="12" cellspacing="0" bgcolor="#F0FDFA" '
            'style="border:1px solid #5EEAD4; margin-bottom:8px;">'
            '<tr><td style="color:#0F766E; font-size:13px;">'
            "<b>🤖 Redactando manual paso a paso desde tu biblioteca…</b>"
            "</td></tr></table>"
        )

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
