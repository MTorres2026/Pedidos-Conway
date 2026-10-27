"""
Motor de Estudio Profundo, Deduplicación y Compactación de la Biblioteca de CineStock.

Objetivo:
  Dejar la base de Conocimiento (biblioteca.db + originales/) 100 % compacta, rápida
  y libre de ruido, conservando ÚNICAMENTE manuales operativos, guías paso a paso y
  documentación vigente (la versión más actual de cada tema), y eliminando:
    1) Archivos archivados, rotos, vacíos o temporales (~$).
    2) Duplicados exactos o cuasi-duplicados por contenido o título canónico.
    3) Hilos de correo repetidos (RE:/RV:/FW: antiguos superados por el último mensaje).
    4) Correos envoltorio cuyo adjunto manual ya existe guardado como documento suelto.
    5) Correos y archivos efímeros que NO son manuales (reportes diarios de taquilla,
       confirmaciones de pedidos puntuales, albaranes concretos, claves KDM, cuadrantes
       de horarios, campañas/combos de películas pasadas, avisos breves o saludos).
    6) Versiones antiguas de un manual cuando existe otro documento más reciente del
       mismo procedimiento.
    7) Auditoría semántica opcional con IA (OpenAI / Gemini) para detectar redundancias
       sutiles entre documentos con distinto nombre.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

if __package__ in (None, ""):
    _RAIZ_SRC = Path(__file__).resolve().parents[1]
    if str(_RAIZ_SRC) not in sys.path:
        sys.path.insert(0, str(_RAIZ_SRC))
    try:
        from dotenv import load_dotenv
        load_dotenv(_RAIZ_SRC.parent / ".env")
    except Exception:
        pass
    from modulos.asistente_inicio import (
        _peticion_json_https,
        detectar_proveedor_ia,
        normalizar,
        sin_acentos,
    )
    from modulos.biblioteca import MARCAS_AUTOMATICO, Biblioteca
else:
    from .asistente_inicio import (
        _peticion_json_https,
        detectar_proveedor_ia,
        normalizar,
        sin_acentos,
    )
    from .biblioteca import MARCAS_AUTOMATICO, Biblioteca


# ======================================================================
# 1) Modelos de datos del Estudio de Biblioteca
# ======================================================================

@dataclass
class DecisionDocumento:
    doc_id: int
    titulo: str
    tipo: str
    archivo: str
    fecha: str
    categoria: str
    remitente: str
    estado: str
    tamano_bytes: int
    accion: str            # "CONSERVAR" | "ELIMINAR"
    codigo_motivo: str     # "MANUAL_VIGENTE", "DESTACADO", "DUPLICADO", "OBSOLETO", "NO_MANUAL", etc.
    motivo: str
    puntuacion_manual: float = 0.0
    reemplazado_por_id: Optional[int] = None


@dataclass
class InformeEstudioBiblioteca:
    fecha_estudio: str
    total_analizados: int
    conservar: List[DecisionDocumento] = field(default_factory=list)
    eliminar: List[DecisionDocumento] = field(default_factory=list)
    archivos_huerfanos: List[Path] = field(default_factory=list)
    bytes_totales_antes: int = 0
    bytes_estimados_liberar: int = 0
    usado_ia: bool = False
    proveedor_ia: str = ""
    aviso_ia: str = ""


@dataclass
class ResultadoLimpiezaBiblioteca:
    eliminados_db: int
    conservados_db: int
    huerfanos_borrados: int
    bytes_antes: int
    bytes_despues: int

    @property
    def bytes_liberados(self) -> int:
        return max(0, self.bytes_antes - self.bytes_despues)


# ======================================================================
# 2) Señales de Manual Operativo vs. Contenido Efímero / Obsoleto
# ======================================================================

_PALABRAS_TITULO_MANUAL_EXPLICITO = (
    "manual", "guia", "procedimiento", "procedimientos", "protocolo",
    "instructivo", "instrucciones", "paso a paso", "tutorial",
    "formacion", "capacitacion", "operativa",
)

_PALABRAS_TITULO_MANUAL = _PALABRAS_TITULO_MANUAL_EXPLICITO + (
    "conciliacion", "recuento", "inventario", "inventarios",
    "merma", "mermas", "peoplesoft", "cinema manager",
    "merrychef", "horno", "limpieza", "recepcion", "devolucion", "traspaso",
    "matinales", "apertura", "cierre", "supermaestro", "mapeo", "catalogo",
    "freshservice", "tms", "rosetta",
)

# Patrones claros de archivos/correos puntuales, transaccionales o efímeros (NO manuales)
_RX_EFIMERO_FUERTE = re.compile(
    r"(?<![a-z0-9])("
    r"performancereport|performance report|recaudacion diaria|taquilla del dia|"
    r"cierre de taquilla del|ranking semanal|ventas fin de semana|resumen de ventas|"
    r"asistencia semanal|sph semanal|"
    r"kdm|claves kdm|envio dcp|tracking dcp|cartelera semana|programacion semana|"
    r"parrilla semanal|hoja de sesiones|"
    r"cuadrante|horarios semana|turnos semana|solicitud de vacaciones|"
    r"parte de baja|justificante medico|nomina|finiquito|"
    r"feliz navidad|felices fiestas|bienvenido al equipo|aniversario|"
    r"out of office|respuesta automatica|fuera de la oficina|"
    r"confirmacion de pedido|tu pedido ha sido|rotura de stock|roturas de stock|"
    r"adjunto albaran|albaran\s+[0-9a-z]{3,}|factura\s+[0-9a-z]{2,}|"
    r"reserva colegio|confirmacion colegio|presupuesto grupo|"
    r"invitacion a reunion|enlace teams|microsoft teams"
    r")(?![a-z0-9])"
)

# Campañas promocionales o eventos con caducidad temporal
_RX_CAMPANA_CADUCABLE = re.compile(
    r"(?<![a-z0-9])("
    r"combo promocional|menu promocional|vaso promocional|palomitero|"
    r"preventa|estreno de la semana|fiesta del cine|campana verano|campana navidad|"
    r"black friday|san valentin|halloween"
    r")(?![a-z0-9])"
)

_RX_PASOS_INSTRUCTIVOS = re.compile(
    r"(?m)^\s*(?:paso\s+\d+|\d{1,2}[.)\-]\s+[A-ZÁÉÍÓÚÑa-záéíóúñ]|[-•▪▸]\s+\S+)"
)

_RX_VERBOS_MANUAL = re.compile(
    r"(?<![a-z0-9])("
    r"entrar|acceder|pulsar|pinchar|clic|seleccionar|desplazarse|escribir|"
    r"rellenar|introducir|guardar|validar|comprobar|verificar|desplegable|"
    r"pestana|menu principal|boton|campo|pantalla|modulo|configurar"
    r")(?![a-z0-9])"
)

_STOPWORDS_HUELLA = frozenset("""
a al algo ante bajo cabe con contra de del desde durante el ella ellas ellos
en entre era es esta estas este esto estos fue ha hasta la las le les lo los
mas me mi mis muy no nos o para pero por porque que se sin sobre su sus te
tiene tu tus un una uno unos y ya yelmo cines cinepolis confidencial mensaje
correo enviado asunto fecha destinatario adjunto archivo imagen diapositiva
""".split())


def _tamano_copia_ficha(biblioteca: Biblioteca, ficha: Dict) -> int:
    guardada = str(ficha.get("ruta_copia") or "")
    candidatas: List[Path] = []
    if guardada:
        nombre = guardada.replace("\\", "/").rsplit("/", 1)[-1]
        if nombre:
            candidatas.append(biblioteca.originales / nombre)
        candidatas.append(Path(guardada))
    for c in candidatas:
        try:
            if c.is_file():
                return c.stat().st_size
        except OSError:
            continue
    return len((ficha.get("texto") or "").encode("utf-8", errors="ignore"))


def _titulo_canonico_tema(titulo: str, archivo: str = "") -> str:
    """
    Limpia hashes ('04c654d25d_'), fechas ('170608', '20260918'), prefijos RE:/RV:/FW:
    y sufijos de copia ('(1)', 'copia', 'v2', 'final') para detectar documentos del mismo tema.
    """
    base = sin_acentos(titulo or Path(archivo or "").stem).lower()
    # Quitar extensión si quedó en el título
    base = re.sub(r"\.(pdf|pptx|ppt|docx|doc|xlsx|xls|eml|txt|md|zip|png|jpg)$", "", base)
    # Quitar prefijos de respuesta de correo
    base = re.sub(r"^\s*(?:(?:re|rv|fw|fwd|enc)\s*:\s*)+", "", base)
    # Quitar hashes hexadecimales iniciales (ej. 04c654d25d_)
    base = re.sub(r"^[0-9a-f]{7,32}[_\-\s]+", "", base)
    # Quitar fechas numéricas y años de versión en cualquier posición (ej. 20260918, 170608, 2023, 2026, v2)
    base = re.sub(r"\b(?:20\d{6}|\d{6}|20[12]\d|v\d+|version\s*\d+|rev\s*\d*)\b", " ", base)
    # Quitar marcas de copia repetida
    base = re.sub(r"\(\s*\d+\s*\)", " ", base)
    base = re.sub(r"\b(copia|copy|final|definitivo|def|nuevo|actualizado|actualizada|ok)\b", " ", base)
    base = re.sub(r"[_\-./\\():;,\"'«»\[\]{}]+", " ", base)
    palabras = [p for p in base.split() if len(p) >= 2]
    # Unificar plurales simples para agrupar "inventario" == "inventarios"
    norm_pals: List[str] = []
    for p in palabras:
        if len(p) > 5 and p.endswith("es") and p[-3] not in "aeiou":
            p = p[:-2]
        elif len(p) > 4 and p.endswith("s") and not p.endswith(("ss", "us", "is")):
            p = p[:-1]
        norm_pals.append(p)
    return " ".join(norm_pals).strip()


def _extraer_fecha_efectiva(ficha: Dict) -> str:
    """
    Obtiene una fecha ISO comparable YYYY-MM-DD a partir de fecha_doc o de patrones
    de año/fecha en el título o nombre de archivo, para saber cuál es más actual.
    """
    f_doc = str(ficha.get("fecha_doc") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", f_doc):
        return f_doc

    fuente = f"{ficha.get('titulo', '')} {ficha.get('archivo', '')}"
    # YYYYMMDD o YYYY-MM-DD
    m8 = re.search(r"(20[12]\d)[_\-]?([01]\d)[_\-]?([0-3]\d)", fuente)
    if m8:
        y, mo, da = int(m8.group(1)), int(m8.group(2)), int(m8.group(3))
        if 1 <= mo <= 12 and 1 <= da <= 31:
            return f"{y:04d}-{mo:02d}-{da:02d}"

    # YYMMDD al inicio (como '170608 Conciliación de inventarios' -> 2017-06-08)
    m6 = re.search(r"(?:^|[_\s])([12]\d)([01]\d)([0-3]\d)(?:[_\s]|$)", fuente)
    if m6:
        yy, mo, da = int(m6.group(1)), int(m6.group(2)), int(m6.group(3))
        if 1 <= mo <= 12 and 1 <= da <= 31:
            return f"{2000 + yy:04d}-{mo:02d}-{da:02d}"

    # Año suelto 2018..2029
    m_anio = re.search(r"\b(20[12]\d)\b", fuente)
    if m_anio:
        return f"{m_anio.group(1)}-06-01"

    return ""


def _huella_palabras(texto: str) -> Set[str]:
    norm = normalizar((texto or "")[:25000])
    tokens = [
        t for t in norm.split()
        if len(t) >= 4 and not t.isdigit() and t not in _STOPWORDS_HUELLA
    ]
    if len(tokens) < 12:
        return set(tokens)
    # Bigramas léxicos para medir solapamiento real de contenido
    shingles = {f"{tokens[i]}_{tokens[i + 1]}" for i in range(len(tokens) - 1)}
    return shingles if len(shingles) >= 8 else set(tokens)


def _similitud_textos(h1: Set[str], h2: Set[str]) -> float:
    if not h1 or not h2:
        return 0.0
    inter = len(h1 & h2)
    if inter == 0:
        return 0.0
    union = len(h1 | h2)
    jaccard = inter / union
    contencion = inter / min(len(h1), len(h2))
    # Si un documento grande contiene al 90 % otro corto (ej. correo vs su extracto)
    if min(len(h1), len(h2)) >= 20 and contencion >= 0.88:
        return max(jaccard, 0.84)
    return jaccard


def _evaluar_calidad_manual(ficha: Dict, hoy: date) -> Tuple[float, bool, str]:
    """
    Calcula (puntuación_manual 0..100, es_candidato_a_conservar, motivo_descarte_si_no).
    Filtra con rigor todo lo que NO sea un manual, guía operativa o tabla maestra vigente.
    """
    tipo = (ficha.get("tipo") or "").strip().lower()
    titulo = (ficha.get("titulo") or "").strip()
    archivo = (ficha.get("archivo") or "").strip()
    remitente = (ficha.get("remitente") or "").strip()
    texto = (ficha.get("texto") or "").strip()

    # Archivos temporales de Office (~$...)
    if archivo.startswith("~$") or titulo.startswith("~$"):
        return (0.0, False, "Archivo temporal de bloqueo de Office (~$)")

    cab_norm = normalizar(f"{titulo} {archivo}")
    muestra_norm = normalizar(f"{titulo} {archivo} {texto[:4500]}")

    # Respuestas automáticas / notificaciones robotizadas
    cab_correo = normalizar(f"{remitente} {titulo} {texto[:400]}")
    if any(m in cab_correo for m in MARCAS_AUTOMATICO):
        return (0.0, False, "Notificación o respuesta automática sin valor de manual")

    es_manual_explicito = any(
        re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", cab_norm)
        for p in _PALABRAS_TITULO_MANUAL_EXPLICITO
    )

    # Reportes diarios de taquilla, KDMs, nóminas, horarios puntuales, albaranes sueltos...
    if not es_manual_explicito:
        m_efim = _RX_EFIMERO_FUERTE.search(cab_norm) or (
            tipo == "correo" and _RX_EFIMERO_FUERTE.search(muestra_norm)
        )
        if m_efim:
            return (
                5.0,
                False,
                f"Contenido puntual/administrativo («{m_efim.group(1)}»), no es un manual operativo",
            )

    # Campañas o promociones caducadas (> 180 días)
    fecha_ef = _extraer_fecha_efectiva(ficha)
    edad_dias = 0
    if fecha_ef:
        try:
            edad_dias = max(0, (hoy - date.fromisoformat(fecha_ef)).days)
        except ValueError:
            edad_dias = 0

    m_camp = _RX_CAMPANA_CADUCABLE.search(cab_norm)
    if m_camp and not es_manual_explicito and (edad_dias > 180 or tipo == "correo"):
        return (
            10.0,
            False,
            f"Campaña o promoción temporal caducada («{m_camp.group(1)}»)",
        )

    # Contar señales instructivas reales
    tiene_palabra_manual_titulo = any(
        re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", cab_norm)
        for p in _PALABRAS_TITULO_MANUAL
    )
    n_pasos = len(_RX_PASOS_INSTRUCTIVOS.findall(texto))
    n_verbos = len(_RX_VERBOS_MANUAL.findall(muestra_norm))
    n_diapositivas = texto.count("--- Diapositiva ")
    adjuntos_brutos = re.findall(
        r"\[Adjunto:\s*([^\]]+\.(?:pdf|pptx|docx))\]",
        texto,
        flags=re.IGNORECASE,
    )
    adjuntos_manual = [
        a for a in adjuntos_brutos
        if not _RX_EFIMERO_FUERTE.search(normalizar(a))
    ]

    pts = 0.0
    if tipo in ("pptx", "pdf", "docx"):
        pts += 34.0
    elif tipo in ("xlsx", "texto"):
        pts += 22.0
    elif tipo == "zip":
        pts += 15.0
    elif tipo == "imagen":
        pts += 8.0
    elif tipo == "correo":
        pts += 6.0

    if tiene_palabra_manual_titulo:
        pts += 26.0
    pts += min(22.0, n_pasos * 2.5)
    pts += min(16.0, n_verbos * 2.0)
    if n_diapositivas >= 3:
        pts += 18.0
    if adjuntos_manual:
        pts += 16.0
    if len(texto) >= 800:
        pts += 8.0

    # Reglas específicas por tipo:
    if tipo == "correo":
        # Un correo solo merece quedarse en Conocimiento si contiene un procedimiento
        # paso a paso reutilizable o porta un manual adjunto que no esté suelto.
        es_instructivo_correo = (
            bool(adjuntos_manual)
            or (n_pasos >= 3 and n_verbos >= 2)
            or (tiene_palabra_manual_titulo and n_pasos >= 2 and len(texto) >= 350)
            or (n_verbos >= 5 and len(texto) >= 700 and tiene_palabra_manual_titulo)
        )
        if not es_instructivo_correo:
            return (
                pts,
                False,
                "Correo conversacional o aviso puntual sin manual paso a paso",
            )
        # Correos antiguos (> 18 meses) sin adjunto formal suelen estar desfasados
        if edad_dias > 540 and not adjuntos_manual and n_pasos < 5:
            return (
                pts,
                False,
                f"Correo antiguo ({edad_dias // 30} meses) sin manual adjunto formal",
            )

    elif tipo == "imagen":
        if len(texto) < 80 and not tiene_palabra_manual_titulo:
            return (pts, False, "Imagen suelta sin estructura de manual")

    elif tipo in ("texto", "xlsx"):
        if len(texto) < 80 and not tiene_palabra_manual_titulo:
            return (pts, False, "Archivo demasiado breve o vacío sin pasos operativos")
        if tipo == "xlsx" and not tiene_palabra_manual_titulo and n_pasos < 2:
            # Excel numérico puntual (ej. conteo de una semana concreta o listado suelto)
            if edad_dias > 180 or re.search(r"\b202[0-5]\b", cab_norm):
                return (pts, False, "Hoja Excel antigua o puntual (no es plantilla/manual vigente)")

    elif tipo in ("pdf", "pptx", "docx"):
        # Si un PDF/Word no tiene casi texto Y su título tampoco parece un manual/guía/tema operativo
        if len(texto) < 50 and not tiene_palabra_manual_titulo and n_diapositivas == 0:
            return (pts, False, "Documento sin texto legible ni título de manual identificable")

    return (min(100.0, pts), True, "Manual / documentación operativa útil")


def _orden_preferencia_ejemplar(ficha: Dict, pts_manual: float) -> Tuple:
    """Clave de ordenación para elegir el MEJOR y MÁS ACTUAL ejemplar de un mismo tema."""
    es_destacado = 1 if ficha.get("estado") == "DESTACADO" else 0
    tipo = (ficha.get("tipo") or "").lower()
    # Preferimos documento nativo suelto frente a correo envoltorio
    rango_tipo = {
        "pptx": 5,
        "pdf": 5,
        "docx": 5,
        "xlsx": 4,
        "texto": 3,
        "zip": 2,
        "imagen": 1,
        "correo": 0,
    }.get(tipo, 1)
    fecha_ef = _extraer_fecha_efectiva(ficha) or "0000-00-00"
    nom_bruto = sin_acentos(f"{ficha.get('titulo', '')} {ficha.get('archivo', '')}").lower()
    es_nombre_limpio = 0 if re.search(r"\(\s*\d+\s*\)|\bcopia\b|\bcopy\b", nom_bruto) else 1
    largo_txt = min(len(ficha.get("texto") or ""), 50000)
    return (
        es_destacado,
        rango_tipo,
        fecha_ef,
        es_nombre_limpio,
        round(pts_manual, 1),
        largo_txt,
        -int(ficha.get("id") or 0),
    )


# ======================================================================
# 3) Auditoría Semántica Opcional con IA (OpenAI / Gemini)
# ======================================================================

_PROMPT_CURADOR_BIBLIOTECA = (
    "Eres el auditor jefe de calidad documental de CineStock (aplicación de gestión de cine).\n"
    "El gerente quiere compactar su Biblioteca de Conocimiento dejando ÚNICAMENTE:\n"
    "  - Manuales operativos paso a paso (ERP PeopleSoft, Cinema Manager, Vista, Conway, "
    "    inventarios, conciliaciones, mermas, pedidos, recepciones, matinales, cocina/hornos, "
    "    TMS/proyección, soporte técnico).\n"
    "  - Protocolos vigentes y tablas maestras de referencia actual.\n\n"
    "Debes marcar para ELIMINAR sin dudar:\n"
    "  1. Documentos o correos que traten el MISMO procedimiento que otro documento más actual "
    "     o más completo de la lista (deja SOLO el mejor/más reciente).\n"
    "  2. Correos puntuales, avisos temporales caducados, campañas de películas concretas, "
    "     comunicados corporativos genéricos que no explican cómo hacer una tarea operativa, "
    "     albaranes/facturas/reportes de un día concreto o recordatorios informales.\n"
    "  NOTA CRÍTICA: NUNCA elimines un manual paso a paso de ERP o del cine (aunque sea de un "
    "  año anterior como 2017-2024) si es el ÚNICO manual que explica ese procedimiento.\n\n"
    "Responde EXCLUSIVAMENTE con un objeto JSON válido con esta forma exacta (sin bloques ```):\n"
    '{"eliminar": [{"id": 12, "motivo": "Explicación breve en español de por qué sobra"}]}'
)


def _auditar_supervivientes_con_ia(
    candidatos_conservar: List[Tuple[Dict, float]],
    timeout: float = 45.0,
) -> Tuple[Dict[int, str], str, str]:
    """
    Revisa con IA la lista de documentos preseleccionados para detectar duplicados
    semánticos con distinto título o documentos obsoletos/no-manuales residuales.
    Devuelve ({doc_id: motivo_ia}, proveedor, aviso).
    """
    deteccion = detectar_proveedor_ia()
    if not deteccion or len(candidatos_conservar) <= 1:
        return ({}, "", "")

    proveedor, api_key = deteccion

    resumen_docs = []
    for ficha, pts in candidatos_conservar[:90]:
        if ficha.get("estado") == "DESTACADO":
            continue
        extracto = re.sub(r"\s+", " ", (ficha.get("texto") or "")[:380]).strip()
        resumen_docs.append({
            "id": int(ficha["id"]),
            "tipo": ficha.get("tipo", ""),
            "titulo": ficha.get("titulo", ""),
            "archivo": ficha.get("archivo", ""),
            "fecha": _extraer_fecha_efectiva(ficha) or "sin_fecha",
            "categoria": ficha.get("categoria", ""),
            "extracto": extracto,
        })

    if not resumen_docs:
        return ({}, "", "")

    cuerpo_usuario = (
        f"FECHA ACTUAL: {date.today().isoformat()}\n"
        "CATÁLOGO DE DOCUMENTOS EN LA BIBLIOTECA:\n"
        + json.dumps(resumen_docs, ensure_ascii=False, indent=2)
    )

    try:
        texto_json = ""
        etiqueta = proveedor

        if proveedor == "OpenAI":
            modelo = (os.environ.get("OPENAI_MODEL") or "").strip() or "gpt-4o-mini"
            datos = _peticion_json_https(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                payload={
                    "model": modelo,
                    "temperature": 0.0,
                    "max_tokens": 2000,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _PROMPT_CURADOR_BIBLIOTECA},
                        {"role": "user", "content": cuerpo_usuario},
                    ],
                },
                timeout=timeout,
            )
            texto_json = (
                datos.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            etiqueta = f"OpenAI · {modelo}"

        elif proveedor == "Gemini":
            modelo = (os.environ.get("GEMINI_MODEL") or "").strip() or "gemini-2.5-flash"
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
                    "system_instruction": {"parts": [{"text": _PROMPT_CURADOR_BIBLIOTECA}]},
                    "contents": [{"role": "user", "parts": [{"text": cuerpo_usuario}]}],
                    "generationConfig": {
                        "temperature": 0.0,
                        "maxOutputTokens": 2000,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=timeout,
            )
            partes = datos.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            texto_json = "".join(p.get("text", "") for p in partes).strip()
            etiqueta = f"Gemini · {modelo}"

        limpio = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto_json, flags=re.IGNORECASE).strip()
        obj = json.loads(limpio)
        mapa_elim: Dict[int, str] = {}
        for item in obj.get("eliminar", []) or []:
            if isinstance(item, dict) and "id" in item:
                try:
                    mapa_elim[int(item["id"])] = str(item.get("motivo") or "Descartado por auditoría IA").strip()
                except (TypeError, ValueError):
                    continue
        return (mapa_elim, etiqueta, "")

    except Exception as e:
        return ({}, proveedor, f"Auditoría local completada (IA no disponible: {str(e)[:70]}).")


# ======================================================================
# 4) Estudio Profundo Completo + Ejecución de Limpieza y Compactación
# ======================================================================

def detectar_archivos_huerfanos(biblioteca: Biblioteca, fichas: Sequence[Dict]) -> List[Path]:
    """Localiza archivos físicos en data/conocimiento/originales/ que ya no pertenecen a ninguna ficha."""
    if not biblioteca.originales.is_dir():
        return []
    referenciados: Set[str] = set()
    for f in fichas:
        guardada = str(f.get("ruta_copia") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if guardada:
            referenciados.add(guardada.lower())
    huerfanos: List[Path] = []
    for archivo in sorted(biblioteca.originales.iterdir()):
        if archivo.is_file() and archivo.name.lower() not in referenciados:
            huerfanos.append(archivo)
    return huerfanos


def analizar_biblioteca_en_profundidad(
    biblioteca: Biblioteca,
    usar_ia: bool = True,
    hoy: Optional[date] = None,
) -> InformeEstudioBiblioteca:
    """
    Realiza el estudio integral de la biblioteca sin borrar nada todavía, devolviendo
    qué manuales conservar y qué elementos obsoletos/repetidos/ruido eliminar.
    """
    hoy = hoy or date.today()
    filas = biblioteca._con.execute(
        "SELECT * FROM docs ORDER BY id ASC"
    ).fetchall()
    fichas = [dict(f) for f in filas]

    decisiones: Dict[int, DecisionDocumento] = {}
    tamanos: Dict[int, int] = {int(f["id"]): _tamano_copia_ficha(biblioteca, f) for f in fichas}

    # --- FASE 1: Archivados previos + evaluación de calidad de manual ---
    puntuaciones: Dict[int, float] = {}
    supervivientes: List[Dict] = []

    for f in fichas:
        did = int(f["id"])
        estado = (f.get("estado") or "NORMAL").upper()
        pts, es_manual, motivo_eval = _evaluar_calidad_manual(f, hoy)
        puntuaciones[did] = pts

        if estado == "DESTACADO":
            supervivientes.append(f)
            continue

        if estado == "ARCHIVADO":
            decisiones[did] = DecisionDocumento(
                doc_id=did,
                titulo=f["titulo"],
                tipo=f["tipo"],
                archivo=f["archivo"],
                fecha=_extraer_fecha_efectiva(f) or f.get("fecha_doc", ""),
                categoria=f.get("categoria", ""),
                remitente=f.get("remitente", ""),
                estado=estado,
                tamano_bytes=tamanos[did],
                accion="ELIMINAR",
                codigo_motivo="ARCHIVADO",
                motivo="Estaba marcado como Archivado; se elimina para compactar la biblioteca.",
                puntuacion_manual=pts,
            )
            continue

        if not es_manual:
            decisiones[did] = DecisionDocumento(
                doc_id=did,
                titulo=f["titulo"],
                tipo=f["tipo"],
                archivo=f["archivo"],
                fecha=_extraer_fecha_efectiva(f) or f.get("fecha_doc", ""),
                categoria=f.get("categoria", ""),
                remitente=f.get("remitente", ""),
                estado=estado,
                tamano_bytes=tamanos[did],
                accion="ELIMINAR",
                codigo_motivo="NO_ES_MANUAL",
                motivo=motivo_eval,
                puntuacion_manual=pts,
            )
            continue

        supervivientes.append(f)

    # --- FASE 2: Hilos de correo repetidos (conservar solo el último del hilo) ---
    por_hilo: Dict[str, List[Dict]] = {}
    for f in supervivientes:
        if f.get("tipo") == "correo" and f.get("hilo"):
            por_hilo.setdefault(f["hilo"], []).append(f)

    ids_descartados_hilo: Set[int] = set()
    for hilo, lista_h in por_hilo.items():
        if len(lista_h) <= 1:
            continue
        ordenados_h = sorted(
            lista_h,
            key=lambda x: _orden_preferencia_ejemplar(x, puntuaciones[int(x["id"])]),
            reverse=True,
        )
        ganador_h = ordenados_h[0]
        for ant in ordenados_h[1:]:
            if ant.get("estado") == "DESTACADO":
                continue
            aid = int(ant["id"])
            ids_descartados_hilo.add(aid)
            decisiones[aid] = DecisionDocumento(
                doc_id=aid,
                titulo=ant["titulo"],
                tipo=ant["tipo"],
                archivo=ant["archivo"],
                fecha=_extraer_fecha_efectiva(ant) or ant.get("fecha_doc", ""),
                categoria=ant.get("categoria", ""),
                remitente=ant.get("remitente", ""),
                estado=ant.get("estado", "NORMAL"),
                tamano_bytes=tamanos[aid],
                accion="ELIMINAR",
                codigo_motivo="HILO_REPETIDO",
                motivo=f"Mensaje anterior del mismo hilo de correo (superado por «{ganador_h['titulo']}»).",
                puntuacion_manual=puntuaciones[aid],
                reemplazado_por_id=int(ganador_h["id"]),
            )

    supervivientes = [f for f in supervivientes if int(f["id"]) not in ids_descartados_hilo]

    # --- FASE 3: Correos cuyo adjunto manual ya existe como documento suelto ---
    temas_sueltos: Dict[str, Dict] = {}
    for f in supervivientes:
        if f.get("tipo") != "correo":
            for cand_t in (
                _titulo_canonico_tema(f.get("titulo", ""), f.get("archivo", "")),
                _titulo_canonico_tema(Path(f.get("archivo", "")).stem, f.get("archivo", "")),
            ):
                if cand_t and len(cand_t) >= 5:
                    temas_sueltos[cand_t] = f

    ids_correo_redundante: Set[int] = set()
    for f in supervivientes:
        if f.get("tipo") != "correo" or f.get("estado") == "DESTACADO":
            continue
        adjs = re.findall(r"\[Adjunto:\s*([^\]]+)\]", f.get("texto") or "")
        if not adjs:
            continue
        coincidencias_sueltas = []
        for nom_adj in adjs:
            canon_adj = _titulo_canonico_tema(Path(nom_adj).stem, nom_adj)
            if canon_adj in temas_sueltos:
                coincidencias_sueltas.append(temas_sueltos[canon_adj])
        if coincidencias_sueltas and len(coincidencias_sueltas) == len(adjs):
            did = int(f["id"])
            ref = coincidencias_sueltas[0]
            ids_correo_redundante.add(did)
            decisiones[did] = DecisionDocumento(
                doc_id=did,
                titulo=f["titulo"],
                tipo=f["tipo"],
                archivo=f["archivo"],
                fecha=_extraer_fecha_efectiva(f) or f.get("fecha_doc", ""),
                categoria=f.get("categoria", ""),
                remitente=f.get("remitente", ""),
                estado=f.get("estado", "NORMAL"),
                tamano_bytes=tamanos[did],
                accion="ELIMINAR",
                codigo_motivo="ADJUNTO_YA_SUELTO",
                motivo=f"Correo redundante: su manual adjunto ya está guardado suelto («{ref['titulo']}»).",
                puntuacion_manual=puntuaciones[did],
                reemplazado_por_id=int(ref["id"]),
            )

    supervivientes = [f for f in supervivientes if int(f["id"]) not in ids_correo_redundante]

    # --- FASE 4: Deduplicación y versiones obsoletas por título canónico o contenido (>=78 %) ---
    huellas = {int(f["id"]): _huella_palabras(f.get("texto", "")) for f in supervivientes}
    canonicos = {
        int(f["id"]): _titulo_canonico_tema(f.get("titulo", ""), f.get("archivo", ""))
        for f in supervivientes
    }

    # Agrupar mediante Union-Find los documentos equivalentes o versiones del mismo manual
    padre = {int(f["id"]): int(f["id"]) for f in supervivientes}

    def _raiz(x: int) -> int:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def _unir(a: int, b: int) -> None:
        ra, rb = _raiz(a), _raiz(b)
        if ra != rb:
            padre[rb] = ra

    n_sup = len(supervivientes)
    for i in range(n_sup):
        id_a = int(supervivientes[i]["id"])
        can_a = canonicos[id_a]
        for j in range(i + 1, n_sup):
            id_b = int(supervivientes[j]["id"])
            can_b = canonicos[id_b]
            mismo_canon = bool(can_a and can_b and len(can_a) >= 6 and can_a == can_b)
            sim = _similitud_textos(huellas[id_a], huellas[id_b])
            if mismo_canon or sim >= 0.78:
                _unir(id_a, id_b)

    grupos: Dict[int, List[Dict]] = {}
    for f in supervivientes:
        grupos.setdefault(_raiz(int(f["id"])), []).append(f)

    finalistas: List[Dict] = []
    for _, miembros in grupos.items():
        if len(miembros) == 1:
            finalistas.append(miembros[0])
            continue

        ordenados = sorted(
            miembros,
            key=lambda x: _orden_preferencia_ejemplar(x, puntuaciones[int(x["id"])]),
            reverse=True,
        )
        lider = ordenados[0]
        finalistas.append(lider)
        fecha_lider = _extraer_fecha_efectiva(lider)

        for rep in ordenados[1:]:
            if rep.get("estado") == "DESTACADO":
                finalistas.append(rep)
                continue
            rid = int(rep["id"])
            fecha_rep = _extraer_fecha_efectiva(rep)
            es_version_vieja = bool(fecha_lider and fecha_rep and fecha_lider > fecha_rep)
            cod = "VERSION_OBSOLETA" if es_version_vieja else "DUPLICADO"
            razon = (
                f"Versión anterior ({fecha_rep}) superada por la versión más actual «{lider['titulo']}» ({fecha_lider})."
                if es_version_vieja
                else f"Contenido repetido / duplicado de «{lider['titulo']}»."
            )
            decisiones[rid] = DecisionDocumento(
                doc_id=rid,
                titulo=rep["titulo"],
                tipo=rep["tipo"],
                archivo=rep["archivo"],
                fecha=fecha_rep or rep.get("fecha_doc", ""),
                categoria=rep.get("categoria", ""),
                remitente=rep.get("remitente", ""),
                estado=rep.get("estado", "NORMAL"),
                tamano_bytes=tamanos[rid],
                accion="ELIMINAR",
                codigo_motivo=cod,
                motivo=razon,
                puntuacion_manual=puntuaciones[rid],
                reemplazado_por_id=int(lider["id"]),
            )

    # --- FASE 5: Auditoría profunda semántica con IA sobre los supervivientes ---
    usado_ia = False
    prov_ia = ""
    aviso_ia = ""
    descartes_ia: Dict[int, str] = {}

    if usar_ia and detectar_proveedor_ia() is not None and len(finalistas) > 1:
        descartes_ia, prov_ia, aviso_ia = _auditar_supervivientes_con_ia(
            [(f, puntuaciones[int(f["id"])]) for f in finalistas]
        )
        if prov_ia and not aviso_ia:
            usado_ia = True

    for f in finalistas:
        did = int(f["id"])
        estado = (f.get("estado") or "NORMAL").upper()
        fecha_m = _extraer_fecha_efectiva(f) or f.get("fecha_doc", "")

        if estado != "DESTACADO" and did in descartes_ia:
            decisiones[did] = DecisionDocumento(
                doc_id=did,
                titulo=f["titulo"],
                tipo=f["tipo"],
                archivo=f["archivo"],
                fecha=fecha_m,
                categoria=f.get("categoria", ""),
                remitente=f.get("remitente", ""),
                estado=estado,
                tamano_bytes=tamanos[did],
                accion="ELIMINAR",
                codigo_motivo="AUDITORIA_IA",
                motivo=f"IA: {descartes_ia[did]}",
                puntuacion_manual=puntuaciones[did],
            )
        else:
            motivo_ok = (
                "Protegido por estar marcado como ⭐ Destacado."
                if estado == "DESTACADO"
                else "Manual / guía operativa vigente y sin duplicados más recientes."
            )
            decisiones[did] = DecisionDocumento(
                doc_id=did,
                titulo=f["titulo"],
                tipo=f["tipo"],
                archivo=f["archivo"],
                fecha=fecha_m,
                categoria=f.get("categoria", ""),
                remitente=f.get("remitente", ""),
                estado=estado,
                tamano_bytes=tamanos[did],
                accion="CONSERVAR",
                codigo_motivo="DESTACADO" if estado == "DESTACADO" else "MANUAL_VIGENTE",
                motivo=motivo_ok,
                puntuacion_manual=puntuaciones[did],
            )

    conservar = sorted(
        (d for d in decisiones.values() if d.accion == "CONSERVAR"),
        key=lambda d: (d.categoria or "ZZZ", d.titulo.lower()),
    )
    eliminar = sorted(
        (d for d in decisiones.values() if d.accion == "ELIMINAR"),
        key=lambda d: (d.codigo_motivo, d.titulo.lower()),
    )

    huerfanos = detectar_archivos_huerfanos(biblioteca, fichas)
    bytes_huerfanos = sum(p.stat().st_size for p in huerfanos if p.is_file())
    ruta_db = biblioteca.carpeta / "biblioteca.db"
    bytes_db = ruta_db.stat().st_size if ruta_db.is_file() else 0
    bytes_orig = sum(
        p.stat().st_size for p in biblioteca.originales.iterdir() if p.is_file()
    ) if biblioteca.originales.is_dir() else 0

    bytes_elim = sum(d.tamano_bytes for d in eliminar) + bytes_huerfanos

    return InformeEstudioBiblioteca(
        fecha_estudio=hoy.isoformat(),
        total_analizados=len(fichas),
        conservar=conservar,
        eliminar=eliminar,
        archivos_huerfanos=huerfanos,
        bytes_totales_antes=bytes_db + bytes_orig,
        bytes_estimados_liberar=bytes_elim,
        usado_ia=usado_ia,
        proveedor_ia=prov_ia,
        aviso_ia=aviso_ia,
    )


def _medir_carpeta_bytes(carpeta: Path) -> int:
    if not carpeta.is_dir():
        return 0
    total = 0
    for raiz, _, archivos in os.walk(carpeta):
        for nom in archivos:
            try:
                total += (Path(raiz) / nom).stat().st_size
            except OSError:
                pass
    return total


def ejecutar_limpieza_biblioteca(
    biblioteca: Biblioteca,
    ids_a_eliminar: Sequence[int],
    limpiar_huerfanos: bool = True,
) -> ResultadoLimpiezaBiblioteca:
    """
    Elimina físicamente los documentos indicados, barre copias huérfanas en
    data/conocimiento/originales/, limpia cachés antiguas y ejecuta VACUUM en SQLite.
    """
    bytes_antes = _medir_carpeta_bytes(biblioteca.carpeta)
    conjunto_ids = {int(x) for x in ids_a_eliminar}

    eliminados = 0
    for doc_id in sorted(conjunto_ids):
        ficha = biblioteca.obtener(doc_id)
        if not ficha:
            continue
        guardada = str(ficha.get("ruta_copia") or "")
        nombre_copia = guardada.replace("\\", "/").rsplit("/", 1)[-1]
        biblioteca.eliminar(doc_id)
        if nombre_copia:
            try:
                (biblioteca.originales / nombre_copia).unlink(missing_ok=True)
            except OSError:
                pass
        eliminados += 1

    # Barrer archivos huérfanos que hayan quedado en originales/
    filas_restantes = [
        dict(f) for f in biblioteca._con.execute("SELECT * FROM docs").fetchall()
    ]
    huerfanos_borrados = 0
    if limpiar_huerfanos:
        for ruta_h in detectar_archivos_huerfanos(biblioteca, filas_restantes):
            try:
                ruta_h.unlink(missing_ok=True)
                huerfanos_borrados += 1
            except OSError:
                pass

    # Invalidar caché de manuales IA para que solo responda con los manuales vigentes
    try:
        (biblioteca.carpeta / "cache_manuales_ia.json").unlink(missing_ok=True)
    except OSError:
        pass

    # Reconstruir índice FTS5 y compactar físicamente biblioteca.db con VACUUM
    try:
        biblioteca.recalcular_todo()
        if getattr(biblioteca, "_fts", False):
            biblioteca._con.execute(
                "INSERT INTO docs_fts(docs_fts) VALUES('optimize')"
            )
        biblioteca._con.commit()
        aislamiento_previo = biblioteca._con.isolation_level
        biblioteca._con.isolation_level = None
        try:
            biblioteca._con.execute("VACUUM")
            biblioteca._con.execute("ANALYZE")
        finally:
            biblioteca._con.isolation_level = aislamiento_previo
    except sqlite3.Error:
        pass

    bytes_despues = _medir_carpeta_bytes(biblioteca.carpeta)
    return ResultadoLimpiezaBiblioteca(
        eliminados_db=eliminados,
        conservados_db=len(filas_restantes),
        huerfanos_borrados=huerfanos_borrados,
        bytes_antes=bytes_antes,
        bytes_despues=bytes_despues,
    )


def formatear_bytes(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1024 * 1024:
        return f"{n_bytes / 1024:.1f} KB"
    return f"{n_bytes / (1024 * 1024):.2f} MB"


# ======================================================================
# 5) Uso directo por consola (opcional): python src/modulos/depurador_biblioteca.py
# ======================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Estudio profundo y limpieza de obsoletos/repetidos en la Biblioteca de CineStock."
    )
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="Elimina los elementos obsoletos/repetidos detectados y compacta biblioteca.db.",
    )
    parser.add_argument(
        "--sin-ia",
        action="store_true",
        help="Ejecuta únicamente las reglas locales sin consultar a OpenAI/Gemini.",
    )
    args = parser.parse_args()

    bib = Biblioteca()
    informe = analizar_biblioteca_en_profundidad(bib, usar_ia=not args.sin_ia)
    print(f"\n=== ESTUDIO PROFUNDO DE BIBLIOTECA ({informe.fecha_estudio}) ===")
    print(f"Total analizados : {informe.total_analizados}")
    print(f"Conservar        : {len(informe.conservar)} manuales/guías vigentes")
    print(f"Eliminar         : {len(informe.eliminar)} obsoletos / repetidos / no-manuales")
    print(f"Huérfanos disco  : {len(informe.archivos_huerfanos)}")
    print(f"Tamaño actual    : {formatear_bytes(informe.bytes_totales_antes)}")
    print(f"Ahorro estimado  : {formatear_bytes(informe.bytes_estimados_liberar)}\n")

    if informe.conservar:
        print("--- ✅ SE CONSERVAN ---")
        for d in informe.conservar:
            print(f"  [{d.doc_id:03d}] ({d.tipo}) {d.titulo} [{d.fecha or 's/f'}] -> {d.motivo}")

    if informe.eliminar:
        print("\n--- 🗑️ PROPUESTOS PARA ELIMINAR ---")
        for d in informe.eliminar:
            print(f"  [{d.doc_id:03d}] [{d.codigo_motivo}] ({d.tipo}) {d.titulo} -> {d.motivo}")

    if args.aplicar and (informe.eliminar or informe.archivos_huerfanos):
        res = ejecutar_limpieza_biblioteca(bib, [d.doc_id for d in informe.eliminar])
        print(
            f"\n✔ Limpieza completada: {res.eliminados_db} eliminados, "
            f"{res.conservados_db} manuales activos, "
            f"liberados {formatear_bytes(res.bytes_liberados)}."
        )
    bib.cerrar()
