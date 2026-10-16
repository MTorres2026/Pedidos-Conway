"""
Pantalla de Inicio de CineStock con el cuadro de preguntas.

Orden de respuesta:
  1) Procedimiento confirmado (modulos/procedimientos.py), como siempre.
  2) Si además hay correos o manuales relacionados en la biblioteca de
     Conocimiento, se muestran debajo (máximo 3) con un fragmento y un
     enlace para abrir el original.
Si la biblioteca falla o está vacía, Inicio sigue funcionando igual que antes.
"""

import html

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modulos.procedimientos import buscar_procedimiento, generar_respuesta


def _con_saltos(texto):
    return html.escape(texto).replace("\n", "<br>")


def construir_html(titulo_procedimiento, texto_procedimiento, documentos,
                   biblioteca_vacia=False, error_biblioteca=None):
    """Devuelve el HTML de la respuesta (función pura, fácil de probar)."""
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
            extra = ("<p><small>(La biblioteca de Conocimiento no está "
                     "disponible en este momento.)</small></p>")
        elif biblioteca_vacia:
            extra = ("<p>Aún no hay correos ni manuales en <b>📚 Conocimiento</b>. "
                     "Añádelos allí y podré buscar en ellos.</p>")
        else:
            extra = ""
        return ("<p>No encuentro un procedimiento confirmado ni documentos "
                "relacionados con esa pregunta.</p>" + extra)

    return "".join(bloques)


class Inicio(QWidget):

    def __init__(self):
        super().__init__()

        self._biblioteca = None
        self._error_biblioteca = None

        layout = QVBoxLayout(self)

        titulo = QLabel("🏠 INICIO")
        titulo.setStyleSheet("""
            font-size:28px;
            font-weight:bold;
        """)

        texto = QLabel(
            "Bienvenido a CineStock.\n\n"
            "Selecciona una opción del menú para comenzar."
        )

        self.pregunta = QLineEdit()
        self.pregunta.setPlaceholderText("Escribe tu pregunta...")
        boton = QPushButton("Buscar procedimiento")

        self.respuesta = QTextBrowser()
        self.respuesta.setOpenLinks(False)
        self.respuesta.setOpenExternalLinks(False)
        self.respuesta.anchorClicked.connect(self._abrir_enlace)

        boton.clicked.connect(self.buscar)
        self.pregunta.returnPressed.connect(self.buscar)

        layout.addWidget(titulo)
        layout.addWidget(texto)
        layout.addWidget(self.pregunta)
        layout.addWidget(boton)
        layout.addWidget(self.respuesta, 1)

    # ------------------------------------------------------------------
    def _obtener_biblioteca(self):
        if self._biblioteca is None and self._error_biblioteca is None:
            try:
                from modulos.biblioteca import Biblioteca
                self._biblioteca = Biblioteca()
            except Exception as e:
                self._error_biblioteca = str(e)
        return self._biblioteca

    def buscar(self):
        pregunta = self.pregunta.text().strip()
        if not pregunta:
            self.respuesta.setHtml("")
            return

        titulo = None
        texto = None
        try:
            procedimiento = buscar_procedimiento(pregunta)
            if procedimiento:
                texto = generar_respuesta(pregunta, procedimiento)
                titulo = procedimiento.get("titulo")
        except Exception:
            texto = None

        documentos = []
        vacia = False
        biblioteca = self._obtener_biblioteca()
        if biblioteca is not None:
            try:
                documentos = biblioteca.respuesta_rapida(pregunta, 3)
                vacia = biblioteca.estadisticas()["total"] == 0
            except Exception as e:
                self._error_biblioteca = str(e)

        self.respuesta.setHtml(
            construir_html(titulo, texto, documentos, vacia, self._error_biblioteca)
        )

    def _abrir_enlace(self, url):
        enlace = url.toString()
        if not enlace.startswith("abrir:"):
            return
        biblioteca = self._obtener_biblioteca()
        if biblioteca is None:
            return
        try:
            ficha = biblioteca.obtener(int(enlace.split(":", 1)[1]))
        except ValueError:
            return
        if ficha:
            QDesktopServices.openUrl(QUrl.fromLocalFile(ficha["ruta_copia"]))