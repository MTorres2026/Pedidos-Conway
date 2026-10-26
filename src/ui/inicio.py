"""
Pantalla de Inicio de CineStock: Chat Asistente Híbrido de Procedimientos y Manuales.

Cómo responde a cada pregunta del equipo:
  1) Busca primero en los procedimientos confirmados (conocimiento.json) y
     extrae los apartados/diapositivas completos de los manuales y correos
     guardados en 📚 Conocimiento (100 % en local e instantáneo).
  2) Si hay OPENAI_API_KEY o GEMINI_API_KEY en .env y la casilla "Redactar con IA"
     está marcada, redacta en segundo plano una explicación paso a paso basada
     estrictamente en esos manuales, manteniendo debajo las fuentes originales
     con su enlace "📂 Abrir documento original".
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modulos.asistente_inicio import (
    AdjuntoIA,
    MARCA_NO_DISPONIBLE,
    RespuestaAsistente,
    construir_contexto_conversacional,
    construir_contexto_rag,
    consultar_base_local,
    detectar_proveedor_ia,
    exportar_manual_ia_docx,
    exportar_manual_ia_excel,
    exportar_manual_ia_word,
    generar_todas_las_hojas_word,
    guardar_cache_manual_ia,
    leer_cache_manual_ia,
    normalizar,
    renderizar_respuesta_asistente_html,
    sintetizar_con_ia,
    sintetizar_respuesta_conversacional_ia,
)

try:
    from ui.apertura_documentos import abrir_original
except ImportError:
    def abrir_original(parent, biblioteca, doc_id):
        """Respaldo autónomo para instalaciones que no tengan ui/apertura_documentos.py."""
        ruta = None
        try:
            if biblioteca is None:
                raise RuntimeError("La biblioteca de Conocimiento no está disponible.")
            if hasattr(biblioteca, "resolver_original"):
                ruta = biblioteca.resolver_original(doc_id)
            else:
                ficha = biblioteca.obtener(doc_id) if hasattr(biblioteca, "obtener") else None
                if not ficha:
                    raise FileNotFoundError("El documento ya no está en la biblioteca.")
                candidatas = []
                guardada = str(ficha.get("ruta_copia") or "")
                if guardada and hasattr(biblioteca, "originales"):
                    nombre = guardada.replace("\\", "/").rsplit("/", 1)[-1]
                    if nombre:
                        candidatas.append(Path(biblioteca.originales) / nombre)
                if guardada:
                    candidatas.append(Path(guardada))
                if ficha.get("archivo"):
                    candidatas.append(Path(ficha["archivo"]))
                for c in candidatas:
                    if c.is_file():
                        ruta = c.resolve()
                        break
                if ruta is None:
                    raise FileNotFoundError(
                        f"No se encuentra el archivo original ({ficha.get('archivo', '')})."
                    )
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(ruta))):
                raise RuntimeError(
                    "El sistema no ha podido abrir el archivo. Comprueba que hay "
                    "un programa asociado a esta extensión."
                )
        except Exception as error:
            detalle = f"\n\nArchivo: {ruta}" if ruta else ""
            QMessageBox.warning(parent, "Abrir original", f"{error}{detalle}")


# ======================================================================
# Compatibilidad con la función pura anterior (construir_html)
# ======================================================================

def _con_saltos(texto: str) -> str:
    return html.escape(texto or "").replace("\n", "<br>")


def construir_html(
    titulo_procedimiento,
    texto_procedimiento,
    documentos,
    biblioteca_vacia: bool = False,
    error_biblioteca: Optional[str] = None,
) -> str:
    """Función pura mantenida por compatibilidad con pruebas y llamadas previas."""
    bloques = []

    if texto_procedimiento:
        cabecera = "<h3>✅ Procedimiento confirmado</h3>"
        if titulo_procedimiento:
            cabecera += f"<p><b>{html.escape(titulo_procedimiento)}</b></p>"
        bloques.append(cabecera + f"<p>{_con_saltos(texto_procedimiento)}</p>")

    if documentos:
        partes = [
            "<h3>📚 En tus correos y manuales</h3>"
            "<p><i>Pueden ayudarte, pero no son procedimientos confirmados: "
            "revisa la fecha.</i></p>"
        ]
        for d in documentos:
            fragmento = _con_saltos(d.fragmento)
            fragmento = fragmento.replace("«", "<b>").replace("»", "</b>")
            detalle = " · ".join(x for x in (d.fecha, d.categoria) if x)
            partes.append(
                f"<p><b>{html.escape(d.titulo)}</b><br>"
                f"<small>{html.escape(detalle)}</small><br>"
                f"{fragmento}<br>"
                f'<a href="abrir:{d.id}">📂 Abrir original</a></p>'
            )
        bloques.append("".join(partes))

    if not bloques:
        if error_biblioteca:
            extra = (
                "<p><small>(La biblioteca de Conocimiento no está "
                "disponible en este momento.)</small></p>"
            )
        elif biblioteca_vacia:
            extra = (
                "<p>Aún no hay correos ni manuales en <b>📚 Conocimiento</b>. "
                "Añádelos allí y podré buscar en ellos.</p>"
            )
        else:
            extra = ""
        return (
            "<p>No encuentro un procedimiento confirmado ni documentos "
            "relacionados con esa pregunta.</p>" + extra
        )

    return "".join(bloques)


# ======================================================================
# Hilo en segundo plano para la síntesis con IA (sin bloquear la ventana)
# ======================================================================

class _TrabajadorIA(QThread):
    resultado_listo = Signal(int, str, str, str)

    def __init__(
        self,
        turno_id: int,
        pregunta: str,
        contexto_rag: str,
        historial_previo: List[Tuple[str, str]],
        adjuntos_ia: Optional[List[AdjuntoIA]] = None,
        parent=None,
        modo: str = "manual",
    ):
        super().__init__(parent)
        self.turno_id = turno_id
        self.pregunta = pregunta
        self.contexto_rag = contexto_rag
        self.historial_previo = historial_previo
        self.adjuntos_ia = list(adjuntos_ia or [])
        self.modo = modo

    def run(self) -> None:
        if self.modo == "conversacion":
            texto_ia, proveedor, aviso = sintetizar_respuesta_conversacional_ia(
                self.pregunta,
                self.contexto_rag,
                self.historial_previo,
            )
        else:
            texto_ia, proveedor, aviso = sintetizar_con_ia(
                self.pregunta,
                self.contexto_rag,
                self.historial_previo,
                adjuntos_ia=self.adjuntos_ia,
            )
        self.resultado_listo.emit(self.turno_id, texto_ia, proveedor, aviso)


@dataclass
class _TurnoChat:
    turno_id: int
    pregunta: str
    respuesta: RespuestaAsistente
    pensando_ia: bool = False


CONSULTAS_RAPIDAS: Tuple[Tuple[str, str], ...] = (
    ("👋 Hola", "Hola"),
    ("📚 ¿Qué documentos tienes?", "¿Qué documentos tienes?"),
    ("📋 Inventario", "¿Cómo se hace el inventario?"),
    ("⚖️ Conciliación inventario", "¿Cómo se hace la conciliación de inventario?"),
    ("🗑️ Mermas", "¿Cómo se registran las mermas?"),
    ("📦 Solicitud de pedido", "¿Cómo se hace una solicitud de pedido?"),
    ("🚚 Recepción / albarán", "¿Cómo se registra un albarán o recepción?"),
    ("🎓 Matinales", "¿Cómo gestionamos una matinal de un colegio?"),
)


# ======================================================================
# Widget principal de Inicio (Chat Asistente)
# ======================================================================

class Inicio(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self._biblioteca = None
        self._error_biblioteca: Optional[str] = None
        self._turnos: List[_TurnoChat] = []
        self._contador_turnos = 0
        self._hilos_activos: List[_TrabajadorIA] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        # ----- Cabecera superior (adaptable a tema claro y oscuro de Windows) -----
        fila_cabecera = QHBoxLayout()
        titulo = QLabel("🏠 INICIO · Asistente del Cine")
        titulo.setStyleSheet("font-size: 24px; font-weight: bold;")
        fila_cabecera.addWidget(titulo)
        fila_cabecera.addStretch()

        self.etiqueta_estado = QLabel("")
        self.etiqueta_estado.setStyleSheet(
            "background-color: #EFF6FF; color: #1E40AF; border: 1px solid #BFDBFE; "
            "border-radius: 10px; padding: 3px 10px; font-size: 12px; font-weight: 600;"
        )
        fila_cabecera.addWidget(self.etiqueta_estado)

        self.check_ia = QCheckBox("🤖 Redactar con IA")
        self.check_ia.setStyleSheet("font-size: 12.5px; font-weight: 600;")
        self.check_ia.toggled.connect(self._actualizar_indicador_estado)
        fila_cabecera.addWidget(self.check_ia)

        self.boton_hojas_word = QPushButton("📘 Crear hojas Word de mis manuales")
        self.boton_hojas_word.setCursor(Qt.CursorShape.PointingHandCursor)
        self.boton_hojas_word.setStyleSheet(
            "QPushButton { background-color: #1D4ED8; color: #FFFFFF; font-weight: bold; "
            "padding: 4px 10px; font-size: 12px; border-radius: 6px; } "
            "QPushButton:hover { background-color: #2563EB; }"
        )
        self.boton_hojas_word.setToolTip(
            "Convierte todos los manuales de tu biblioteca en Hojas Word (.docx) "
            "formateadas paso a paso, en data/manuales_ia/hojas_word/."
        )
        self.boton_hojas_word.clicked.connect(self.crear_todas_las_hojas_word)
        fila_cabecera.addWidget(self.boton_hojas_word)

        self.boton_limpiar = QPushButton("🧹 Limpiar chat")
        self.boton_limpiar.setStyleSheet(
            "QPushButton { padding: 4px 10px; font-size: 12px; }"
        )
        self.boton_limpiar.clicked.connect(self.limpiar_chat)
        fila_cabecera.addWidget(self.boton_limpiar)

        layout.addLayout(fila_cabecera)

        subtitulo = QLabel(
            "Háblame como a una persona: di «hola», pregunta «¿qué documentos tienes?» "
            "o pide cómo se hace cualquier proceso del cine. Solo conozco tus manuales "
            "de 📚 Conocimiento: con ellos te muestro al instante la Hoja de Manual "
            "formateada paso a paso, lista para abrir en Word o Excel."
        )
        subtitulo.setWordWrap(True)
        subtitulo.setStyleSheet("font-size: 12.5px;")
        layout.addWidget(subtitulo)

        # ----- Botones rápidos de consultas habituales -----
        fila_rapidas = QHBoxLayout()
        fila_rapidas.setSpacing(6)
        etiqueta_rapidas = QLabel("Consultas rápidas:")
        etiqueta_rapidas.setStyleSheet("font-size: 11.5px; font-weight: 600;")
        fila_rapidas.addWidget(etiqueta_rapidas)

        for texto_btn, consulta_btn in CONSULTAS_RAPIDAS:
            btn = QPushButton(texto_btn)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background-color: #F8FAFC; color: #1E293B; "
                "border: 1px solid #CBD5E1; border-radius: 12px; padding: 3px 10px; "
                "font-size: 11.5px; } "
                "QPushButton:hover { background-color: #E0F2FE; border-color: #38BDF8; }"
            )
            btn.clicked.connect(lambda _=False, q=consulta_btn: self.hacer_pregunta(q))
            fila_rapidas.addWidget(btn)
        fila_rapidas.addStretch()
        layout.addLayout(fila_rapidas)

        # ----- Área central de conversación -----
        self.respuesta = QTextBrowser()
        self.respuesta.setOpenLinks(False)
        self.respuesta.setOpenExternalLinks(False)
        self.respuesta.setStyleSheet(
            "QTextBrowser { background-color: #FFFFFF; border: 1px solid #CBD5E1; "
            "border-radius: 8px; padding: 8px; }"
        )
        self.respuesta.anchorClicked.connect(self._abrir_enlace)
        layout.addWidget(self.respuesta, 1)

        # ----- Barra inferior de escritura (estilo chat) -----
        fila_entrada = QHBoxLayout()
        fila_entrada.setSpacing(8)

        self.pregunta = QLineEdit()
        self.pregunta.setPlaceholderText(
            "Escríbeme… (ej.: Hola · ¿Qué documentos tienes? · ¿Cómo se hace el inventario?) y pulsa Enter…"
        )
        self.pregunta.setStyleSheet(
            "QLineEdit { padding: 8px 12px; font-size: 13.5px; "
            "border: 1px solid #94A3B8; border-radius: 6px; }"
        )

        self.boton_enviar = QPushButton("Preguntar ➤")
        self.boton_enviar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.boton_enviar.setStyleSheet(
            "QPushButton { background-color: #162B4D; color: #FFFFFF; "
            "font-weight: bold; font-size: 13px; padding: 8px 16px; border-radius: 6px; } "
            "QPushButton:hover { background-color: #243F6B; }"
        )

        self.boton_enviar.clicked.connect(self.buscar)
        self.pregunta.returnPressed.connect(self.buscar)

        fila_entrada.addWidget(self.pregunta, 1)
        fila_entrada.addWidget(self.boton_enviar)
        layout.addLayout(fila_entrada)

        # Inicializar estado de IA y mensaje de bienvenida
        self._sincronizar_disponibilidad_ia(inicial=True)
        self._actualizar_indicador_estado()
        self._pintar_conversacion()

    # ------------------------------------------------------------------
    # Ciclo de vida y estado de Biblioteca / IA
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.actualizar()

    def actualizar(self):
        """Refresca el contador de manuales y la detección de clave IA."""
        self._sincronizar_disponibilidad_ia(inicial=False)
        self._actualizar_indicador_estado()
        if not self._turnos:
            self._pintar_conversacion()

    def _obtener_biblioteca(self):
        if self._biblioteca is None and self._error_biblioteca is None:
            try:
                from modulos.biblioteca import Biblioteca
                self._biblioteca = Biblioteca()
            except Exception as e:
                self._error_biblioteca = str(e)
        return self._biblioteca

    def _contar_documentos(self) -> int:
        bib = self._obtener_biblioteca()
        if bib is None:
            return 0
        try:
            return int(bib.estadisticas().get("total", 0))
        except Exception:
            return 0

    def _sincronizar_disponibilidad_ia(self, inicial: bool = False):
        deteccion = detectar_proveedor_ia()
        if deteccion:
            proveedor, _ = deteccion
            self.check_ia.setEnabled(True)
            self.check_ia.setToolTip(
                f"Clave de {proveedor} detectada en .env. "
                "Redacta la explicación basándose únicamente en tus manuales."
            )
            if inicial:
                self.check_ia.setChecked(True)
        else:
            self.check_ia.blockSignals(True)
            self.check_ia.setChecked(False)
            self.check_ia.blockSignals(False)
            self.check_ia.setEnabled(False)
            self.check_ia.setToolTip(
                "Modo local activo (sin clave IA en .env). "
                "Si algún día añades OPENAI_API_KEY o GEMINI_API_KEY al archivo .env, "
                "se activará también la redacción automática con IA."
            )

    def _actualizar_indicador_estado(self):
        n_docs = self._contar_documentos()
        deteccion = detectar_proveedor_ia()
        modo = (
            f"🤖 IA + Manuales ({deteccion[0]})"
            if (deteccion and self.check_ia.isChecked())
            else "⚡ Búsqueda directa en manuales"
        )
        sufijo_docs = "documento" if n_docs == 1 else "documentos"
        self.etiqueta_estado.setText(f"📚 {n_docs} {sufijo_docs} · {modo}")

    # ------------------------------------------------------------------
    # Interacción del Chat
    # ------------------------------------------------------------------

    def hacer_pregunta(self, texto_consulta: str):
        self.pregunta.setText(texto_consulta)
        self.buscar()

    def crear_todas_las_hojas_word(self):
        """Convierte todos los manuales de la biblioteca en Hojas Word (.docx)."""
        biblioteca = self._obtener_biblioteca()
        if biblioteca is None or self._contar_documentos() == 0:
            QMessageBox.information(
                self, "Hojas Word de manuales",
                "Todavía no hay documentos en 📚 Conocimiento. Añade primero tus "
                "manuales y después podré convertirlos en Hojas Word paso a paso.",
            )
            return
        try:
            resultado = generar_todas_las_hojas_word(biblioteca)
        except Exception as err:
            QMessageBox.warning(
                self, "Hojas Word de manuales",
                f"No se pudieron crear las Hojas Word:\n{err}",
            )
            return
        if not resultado.generadas:
            QMessageBox.information(
                self, "Hojas Word de manuales",
                "No he encontrado todavía manuales con pasos suficientes para "
                "convertir. Cuando un manual tenga instrucciones paso a paso, "
                "aquí crearé su Hoja Word automáticamente.",
            )
            return
        titulos = "\n".join(
            f"• {g.titulo} ({g.n_pasos} pasos)"
            for g in resultado.generadas[:12]
        )
        extra = (
            f"\n… y {len(resultado.generadas) - 12} más."
            if len(resultado.generadas) > 12 else ""
        )
        QMessageBox.information(
            self, "📘 Hojas Word creadas",
            f"¡Listo! He creado {len(resultado.generadas)} Hoja(s) Word (.docx) "
            f"formateadas paso a paso:\n\n{titulos}{extra}\n\n"
            f"Están en:\n{resultado.carpeta}\n\n"
            "Ábrelas con Word para leerlas, imprimirlas o compartirlas con tus compañeros.",
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(resultado.carpeta.resolve())))

    def limpiar_chat(self):
        self._turnos.clear()
        self._pintar_conversacion()
        self.pregunta.setFocus()

    def buscar(self):
        texto_pregunta = self.pregunta.text().strip()
        if not texto_pregunta:
            return

        historial_preguntas = [t.pregunta for t in self._turnos[-4:]]
        biblioteca = self._obtener_biblioteca()

        respuesta = consultar_base_local(
            pregunta=texto_pregunta,
            biblioteca=biblioteca,
            historial_preguntas=historial_preguntas,
        )
        if self._error_biblioteca and not respuesta.error_biblioteca:
            respuesta.error_biblioteca = self._error_biblioteca

        self._contador_turnos += 1
        turno_id = self._contador_turnos

        # Conversación natural (hola, «qué documentos tienes»…): la narra la IA
        # solo con el inventario real; saludos/gracias se responden al instante.
        usar_hilo_chat = bool(
            respuesta.es_chat
            and respuesta.intencion_chat not in ("saludo", "gracias", "despedida")
            and self.check_ia.isChecked()
            and detectar_proveedor_ia() is not None
        )
        contexto_chat = (
            construir_contexto_conversacional(respuesta) if usar_hilo_chat else ""
        )

        contexto_rag = (
            "" if respuesta.es_chat
            else (construir_contexto_rag(respuesta) if respuesta.tiene_contenido else "")
        )
        tiene_material_ia = bool(
            respuesta.adjuntos_ia
            or (contexto_rag.strip() and not respuesta.solo_documento_sin_ocr)
        )
        ia_habilitada = bool(
            not respuesta.es_chat
            and self.check_ia.isChecked()
            and detectar_proveedor_ia() is not None
            and respuesta.tiene_contenido
            and tiene_material_ia
        )

        # Si este mismo manual ya fue redactado por la IA antes, mostrarlo al instante (0 s)
        usar_hilo_ia = False
        if ia_habilitada:
            cacheado = leer_cache_manual_ia(biblioteca, respuesta.clave_cache_ia)
            if cacheado:
                respuesta.texto_ia, respuesta.proveedor_ia = cacheado
            else:
                usar_hilo_ia = True

        # Historial breve para dar contexto a preguntas de continuación con IA
        historial_ia: List[Tuple[str, str]] = []
        for t in self._turnos[-2:]:
            resumen_prev = t.respuesta.texto_ia or t.respuesta.texto_chat
            if not resumen_prev and t.respuesta.procedimiento:
                resumen_prev = "; ".join(t.respuesta.procedimiento.pasos[:3])
            elif not resumen_prev and t.respuesta.pasajes:
                resumen_prev = t.respuesta.pasajes[0].texto_seccion[:260]
            historial_ia.append((t.pregunta, resumen_prev))

        turno = _TurnoChat(
            turno_id=turno_id,
            pregunta=texto_pregunta,
            respuesta=respuesta,
            pensando_ia=bool(usar_hilo_ia or usar_hilo_chat),
        )
        self._turnos.append(turno)

        self.pregunta.clear()
        self._actualizar_indicador_estado()
        self._pintar_conversacion(ir_al_final=True)

        if usar_hilo_ia or usar_hilo_chat:
            hilo = _TrabajadorIA(
                turno_id=turno_id,
                pregunta=texto_pregunta,
                contexto_rag=contexto_chat if usar_hilo_chat else contexto_rag,
                historial_previo=historial_ia,
                adjuntos_ia=[] if usar_hilo_chat else respuesta.adjuntos_ia,
                parent=self,
                modo="conversacion" if usar_hilo_chat else "manual",
            )
            hilo.resultado_listo.connect(self._al_terminar_ia)
            hilo.finished.connect(lambda h=hilo: self._limpiar_hilo(h))
            self._hilos_activos.append(hilo)
            hilo.start()

    def _al_terminar_ia(self, turno_id: int, texto_ia: str, proveedor: str, aviso: str):
        for turno in self._turnos:
            if turno.turno_id == turno_id:
                turno.pensando_ia = False
                if turno.respuesta.es_chat:
                    # Conversación: si la IA narró, usar su texto; si falló,
                    # conservar la respuesta local (nunca dejar al usuario sin contestar).
                    if texto_ia and texto_ia != MARCA_NO_DISPONIBLE:
                        turno.respuesta.texto_chat = texto_ia
                        turno.respuesta.proveedor_ia = proveedor
                    self._pintar_conversacion(ir_al_final=False)
                    break
                if texto_ia == MARCA_NO_DISPONIBLE:
                    turno.respuesta.rechazado_por_ia = True
                    turno.respuesta.texto_ia = ""
                else:
                    turno.respuesta.texto_ia = texto_ia
                    if texto_ia and not aviso:
                        guardar_cache_manual_ia(
                            self._obtener_biblioteca(),
                            turno.respuesta.clave_cache_ia,
                            texto_ia,
                            proveedor,
                        )
                turno.respuesta.proveedor_ia = proveedor
                turno.respuesta.aviso_ia = aviso
                self._pintar_conversacion(ir_al_final=False)
                break

    def _limpiar_hilo(self, hilo: _TrabajadorIA):
        if hilo in self._hilos_activos:
            self._hilos_activos.remove(hilo)
        hilo.deleteLater()

    # ------------------------------------------------------------------
    # Renderizado del historial de chat en QTextBrowser
    # ------------------------------------------------------------------

    def _html_bienvenida(self) -> str:
        n_docs = self._contar_documentos()
        if n_docs > 0:
            nota_bib = (
                f"Tienes <b>{n_docs} documentos/correos</b> indexados en "
                "<b>📚 Conocimiento</b> listos para consultar."
            )
        else:
            nota_bib = (
                "Aún no tienes manuales cargados en <b>📚 Conocimiento</b> "
                "(solo procedimientos base). Entra en <b>📚 Conocimiento → ➕ Añadir "
                "correos / documentos…</b> para incorporar tus PDF, Word, PowerPoint o correos."
            )

        return (
            '<table width="100%" cellpadding="14" cellspacing="0" bgcolor="#F8FAFC" '
            'style="border:1px solid #CBD5E1; margin-top:6px;">'
            "<tr><td>"
            '<div style="font-size:15px; font-weight:bold; color:#162B4D; margin-bottom:6px;">'
            "👋 ¡Hola! Soy tu asistente de operativa del cine"
            "</div>"
            '<div style="font-size:13px; color:#334155; line-height:1.45; margin-bottom:8px;">'
            "Háblame como a un compañero: salúdame, pregúntame qué tengo guardado o "
            "pídeme cómo se hace cualquier proceso. Solo sé lo que hay en tu biblioteca "
            "de 📚 Conocimiento, y con eso te respondo paso a paso:"
            "</div>"
            '<div style="font-size:12.5px; color:#1E293B; margin-left:8px; margin-bottom:8px;">'
            "• <i>«Hola»</i> &nbsp;•&nbsp; <i>«¿Qué documentos tienes?»</i><br>"
            "• <i>«¿Tienes algo sobre mermas?»</i><br>"
            "• <i>«¿Cómo se hace una conciliación y qué compruebo si hay descuadre?»</i><br>"
            "• <i>«¿Qué pasos sigo para tramitar una matinal de Disney?»</i><br>"
            "• <i>«¿Cómo registro las mermas o un albarán de Conway?»</i>"
            "</div>"
            f'<div style="font-size:12px; color:#475569;">{nota_bib}</div>'
            "</td></tr></table>"
        )

    def _pintar_conversacion(self, ir_al_final: bool = True):
        barra = self.respuesta.verticalScrollBar()
        pos_previa = barra.value() if barra is not None else 0

        if not self._turnos:
            self.respuesta.setHtml(self._html_bienvenida())
            return

        partes_chat: List[str] = []
        for idx, turno in enumerate(self._turnos):
            if idx > 0:
                partes_chat.append(
                    '<hr style="border:0; background-color:#E2E8F0; height:1px; margin:12px 0;">'
                )

            # Burbuja de la pregunta del usuario (derecha / azul corporativo)
            partes_chat.append(
                '<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px;">'
                "<tr>"
                '<td width="18%"></td>'
                '<td width="82%" bgcolor="#162B4D" style="padding:8px 12px; color:#FFFFFF; '
                'font-size:13px;">'
                f'<b>Tú:</b> {html.escape(turno.pregunta)}'
                "</td>"
                "</tr></table>"
            )

            # Tarjeta de respuesta del asistente (izquierda / completa)
            html_resp = renderizar_respuesta_asistente_html(
                turno.respuesta,
                pensando_ia=turno.pensando_ia,
                turno_id=turno.turno_id,
            )
            partes_chat.append(
                '<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:6px;">'
                f"<tr><td>{html_resp}</td></tr></table>"
            )

        self.respuesta.setHtml("".join(partes_chat))

        if barra is not None:
            if ir_al_final:
                self.respuesta.moveCursor(QTextCursor.MoveOperation.End)
                barra.setValue(barra.maximum())
            else:
                barra.setValue(min(pos_previa, barra.maximum()))

    # ------------------------------------------------------------------
    # Apertura de documentos originales y exportación a Excel / Word
    # ------------------------------------------------------------------

    def _carpeta_manuales_exportados(self) -> Path:
        bib = self._obtener_biblioteca()
        if bib is not None and getattr(bib, "carpeta", None):
            return Path(bib.carpeta).parent / "manuales_ia"
        return Path(__file__).resolve().parents[2] / "data" / "manuales_ia"

    def _exportar_y_abrir_hoja(self, turno_id: int, formato: str):
        turno_sel = next((t for t in self._turnos if t.turno_id == turno_id), None)
        if turno_sel is None or not (
            turno_sel.respuesta.texto_ia or turno_sel.respuesta.tiene_contenido
        ):
            QMessageBox.warning(self, "Exportar manual", "No se encontró el manual generado para exportar.")
            return
        try:
            slug_crudo = "_".join(normalizar(turno_sel.pregunta).split()[:6])
            slug = re.sub(r"[^a-z0-9_]+", "", slug_crudo).strip("_") or "manual_operativo"
            carpeta = self._carpeta_manuales_exportados()
            if formato == "excel":
                destino = carpeta / f"Manual_{slug}.xlsx"
                exportar_manual_ia_excel(turno_sel.respuesta, destino)
            else:
                destino = carpeta / f"Manual_{slug}.docx"
                exportar_manual_ia_docx(turno_sel.respuesta, destino)

            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(destino.resolve()))):
                QMessageBox.information(
                    self,
                    "Manual guardado",
                    f"Manual guardado correctamente en:\n{destino.resolve()}",
                )
        except Exception as err:
            QMessageBox.warning(self, "Exportar manual", f"No se pudo exportar la hoja:\n{err}")

    def _abrir_enlace(self, url):
        enlace = url.toString() if hasattr(url, "toString") else str(url)
        if enlace.startswith(("excel:", "word:")):
            esquema, valor = enlace.split(":", 1)
            try:
                self._exportar_y_abrir_hoja(int(valor), esquema)
            except ValueError:
                pass
            return
        if not enlace.startswith("abrir:"):
            return
        try:
            doc_id = int(enlace.split(":", 1)[1])
        except ValueError:
            QMessageBox.warning(
                self,
                "Abrir original",
                "El enlace del resultado no es válido. Vuelve a buscar.",
            )
            return
        abrir_original(self, self._obtener_biblioteca(), doc_id)
