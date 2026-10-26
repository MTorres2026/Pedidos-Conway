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
    RespuestaAsistente,
    construir_contexto_rag,
    consultar_base_local,
    detectar_proveedor_ia,
    renderizar_respuesta_asistente_html,
    sintetizar_con_ia,
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
        parent=None,
    ):
        super().__init__(parent)
        self.turno_id = turno_id
        self.pregunta = pregunta
        self.contexto_rag = contexto_rag
        self.historial_previo = historial_previo

    def run(self) -> None:
        texto_ia, proveedor, aviso = sintetizar_con_ia(
            self.pregunta,
            self.contexto_rag,
            self.historial_previo,
        )
        self.resultado_listo.emit(self.turno_id, texto_ia, proveedor, aviso)


@dataclass
class _TurnoChat:
    turno_id: int
    pregunta: str
    respuesta: RespuestaAsistente
    pensando_ia: bool = False


CONSULTAS_RAPIDAS: Tuple[Tuple[str, str], ...] = (
    ("📋 Inventario", "¿Cómo se hace el inventario?"),
    ("⚖️ Conciliación", "¿Cómo se hace una conciliación de inventario?"),
    ("🗑️ Mermas / bajas", "¿Cómo se registran las mermas o bajas de producto?"),
    ("📦 Pedido ERP", "¿Cómo se tramita un pedido en el ERP?"),
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

        self.boton_limpiar = QPushButton("🧹 Limpiar chat")
        self.boton_limpiar.setStyleSheet(
            "QPushButton { padding: 4px 10px; font-size: 12px; }"
        )
        self.boton_limpiar.clicked.connect(self.limpiar_chat)
        fila_cabecera.addWidget(self.boton_limpiar)

        layout.addLayout(fila_cabecera)

        subtitulo = QLabel(
            "Pregunta con tus palabras cómo se hace cualquier proceso del cine. "
            "El asistente genera una guía paso a paso basándose únicamente en tus "
            "manuales y correos guardados en 📚 Conocimiento."
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
            "Escribe tu duda (ej.: ¿Cómo hago una conciliación? ¿Cómo se hace el inventario?) y pulsa Enter…"
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

        usar_ia = bool(
            self.check_ia.isChecked()
            and detectar_proveedor_ia() is not None
            and respuesta.tiene_contenido
        )

        # Historial breve para dar contexto a preguntas de continuación con IA
        historial_ia: List[Tuple[str, str]] = []
        for t in self._turnos[-2:]:
            resumen_prev = t.respuesta.texto_ia
            if not resumen_prev and t.respuesta.procedimiento:
                resumen_prev = "; ".join(t.respuesta.procedimiento.pasos[:3])
            elif not resumen_prev and t.respuesta.pasajes:
                resumen_prev = t.respuesta.pasajes[0].texto_seccion[:260]
            historial_ia.append((t.pregunta, resumen_prev))

        turno = _TurnoChat(
            turno_id=turno_id,
            pregunta=texto_pregunta,
            respuesta=respuesta,
            pensando_ia=usar_ia,
        )
        self._turnos.append(turno)

        self.pregunta.clear()
        self._actualizar_indicador_estado()
        self._pintar_conversacion(ir_al_final=True)

        if usar_ia:
            contexto_rag = construir_contexto_rag(respuesta)
            hilo = _TrabajadorIA(
                turno_id=turno_id,
                pregunta=texto_pregunta,
                contexto_rag=contexto_rag,
                historial_previo=historial_ia,
                parent=self,
            )
            hilo.resultado_listo.connect(self._al_terminar_ia)
            hilo.finished.connect(lambda h=hilo: self._limpiar_hilo(h))
            self._hilos_activos.append(hilo)
            hilo.start()

    def _al_terminar_ia(self, turno_id: int, texto_ia: str, proveedor: str, aviso: str):
        for turno in self._turnos:
            if turno.turno_id == turno_id:
                turno.pensando_ia = False
                turno.respuesta.texto_ia = texto_ia
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
            "Escríbeme abajo cualquier duda como si hablaras con un compañero y buscaré "
            "el apartado correspondiente en los procedimientos y manuales de CineStock:"
            "</div>"
            '<div style="font-size:12.5px; color:#1E293B; margin-left:8px; margin-bottom:8px;">'
            "• <i>«¿Cómo hago para hacer el inventario?»</i><br>"
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
    # Apertura de documentos originales desde los enlaces del chat
    # ------------------------------------------------------------------

    def _abrir_enlace(self, url):
        enlace = url.toString() if hasattr(url, "toString") else str(url)
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
