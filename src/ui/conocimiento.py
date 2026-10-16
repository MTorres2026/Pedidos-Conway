"""
Pantalla "Conocimiento" de CineStock: biblioteca de correos (.eml) y
documentos (PDF, PowerPoint, Word, Excel, texto) con búsqueda y sugerencias.

La lógica está en modulos/biblioteca.py (sin Qt). Aquí solo hay pantalla.
"""

import html
import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modulos.biblioteca import Biblioteca

FILTROS = [
    ("⭐ Sugeridos (los más importantes)", "sugeridos"),
    ("Todos", "todos"),
    ("✉️ Correos", "correos"),
    ("📄 Manuales y documentos", "documentos"),
    ("Destacados", "destacados"),
    ("🗄 Archivados", "archivados"),
]

ICONOS_TIPO = {
    "correo": "✉️",
    "pdf": "📕",
    "pptx": "📊",
    "docx": "📘",
    "xlsx": "📗",
    "texto": "📄",
    "zip": "🗜",
}

FILTRO_ARCHIVOS = (
    "Correos y documentos (*.eml *.pdf *.pptx *.docx *.xlsx *.txt *.md "
    "*.csv *.html *.htm *.zip);;Todos los archivos (*.*)"
)

MAX_VISTA_PREVIA = 30000


class Conocimiento(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.biblioteca = Biblioteca()

        layout = QVBoxLayout(self)

        titulo = QLabel("📚 CONOCIMIENTO")
        titulo.setStyleSheet("font-size:28px; font-weight:bold;")
        layout.addWidget(titulo)

        explicacion = QLabel(
            "Correos y manuales del cine. Añade lo que quieras conservar y "
            "búscalo después por palabras (por ejemplo: albarán, ERP, cierre)."
        )
        explicacion.setWordWrap(True)
        layout.addWidget(explicacion)

        # ----- Fila de añadir -----
        fila_alta = QHBoxLayout()
        self.boton_archivos = QPushButton("➕ Añadir correos / documentos…")
        self.boton_carpeta = QPushButton("📁 Añadir carpeta…")
        fila_alta.addWidget(self.boton_archivos)
        fila_alta.addWidget(self.boton_carpeta)
        fila_alta.addStretch()
        layout.addLayout(fila_alta)

        # ----- Fila de búsqueda -----
        fila_busqueda = QHBoxLayout()
        self.caja_busqueda = QLineEdit()
        self.caja_busqueda.setPlaceholderText("Buscar en correos y manuales…")
        self.boton_buscar = QPushButton("Buscar")
        self.combo_filtro = QComboBox()
        for texto, valor in FILTROS:
            self.combo_filtro.addItem(texto, valor)
        fila_busqueda.addWidget(self.caja_busqueda, 1)
        fila_busqueda.addWidget(self.boton_buscar)
        fila_busqueda.addWidget(self.combo_filtro)
        layout.addLayout(fila_busqueda)

        # ----- Lista + vista previa -----
        divisor = QSplitter(Qt.Horizontal)
        self.lista = QListWidget()
        self.vista = QTextBrowser()
        self.vista.setOpenLinks(False)
        divisor.addWidget(self.lista)
        divisor.addWidget(self.vista)
        divisor.setStretchFactor(0, 2)
        divisor.setStretchFactor(1, 3)
        layout.addWidget(divisor, 1)

        # ----- Acciones sobre el elemento elegido -----
        fila_acciones = QHBoxLayout()
        self.boton_destacar = QPushButton("⭐ Destacar / quitar")
        self.boton_archivar = QPushButton("🗄 Archivar / recuperar")
        self.boton_abrir = QPushButton("📂 Abrir original")
        self.boton_quitar = QPushButton("🗑 Quitar de la biblioteca")
        for b in (
            self.boton_destacar,
            self.boton_archivar,
            self.boton_abrir,
            self.boton_quitar,
        ):
            fila_acciones.addWidget(b)
        fila_acciones.addStretch()
        layout.addLayout(fila_acciones)

        self.estado = QLabel("")
        layout.addWidget(self.estado)

        # ----- Conexiones -----
        self.boton_archivos.clicked.connect(self.elegir_archivos)
        self.boton_carpeta.clicked.connect(self.elegir_carpeta)
        self.boton_buscar.clicked.connect(self.refrescar)
        self.caja_busqueda.returnPressed.connect(self.refrescar)
        self.combo_filtro.currentIndexChanged.connect(self.refrescar)
        self.lista.currentItemChanged.connect(self.mostrar_seleccion)
        self.boton_destacar.clicked.connect(self.alternar_destacado)
        self.boton_archivar.clicked.connect(self.alternar_archivado)
        self.boton_abrir.clicked.connect(self.abrir_original)
        self.boton_quitar.clicked.connect(self.quitar)

        self.refrescar()

    # ------------------------------------------------------------------
    # Se llama cada vez que se muestra la pantalla desde el menú
    # ------------------------------------------------------------------
    def actualizar(self):
        self.refrescar()

    # ------------------------------------------------------------------
    # Lista
    # ------------------------------------------------------------------
    def _doc_actual_id(self):
        item = self.lista.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def refrescar(self, *_):
        seleccionado = self._doc_actual_id()
        consulta = self.caja_busqueda.text().strip()
        filtro = self.combo_filtro.currentData()

        try:
            if consulta:
                resultados = self.biblioteca.buscar(consulta, filtro, 100)
            else:
                resultados = self.biblioteca.listar(filtro, 500)
        except Exception as e:
            QMessageBox.warning(self, "Conocimiento", f"Error al buscar:\n{e}")
            return

        self.lista.blockSignals(True)
        self.lista.clear()
        fila_a_elegir = 0
        for i, r in enumerate(resultados):
            icono = ICONOS_TIPO.get(r.tipo, "📄")
            estrella = "⭐ " if r.estado == "DESTACADO" else ""
            linea2 = f"{r.fecha or 'sin fecha'}  ·  importancia {r.importancia:.0f}"
            if r.categoria:
                linea2 += f"  ·  {r.categoria}"
            if r.remitente:
                linea2 += f"  ·  {r.remitente[:40]}"
            item = QListWidgetItem(f"{estrella}{icono} {r.titulo}\n    {linea2}")
            item.setData(Qt.UserRole, r.id)
            self.lista.addItem(item)
            if r.id == seleccionado:
                fila_a_elegir = i
        self.lista.blockSignals(False)

        if self.lista.count():
            self.lista.setCurrentRow(fila_a_elegir)
            self.mostrar_seleccion(self.lista.currentItem(), None)
        else:
            self.vista.setHtml(self._mensaje_vacio(consulta))

        est = self.biblioteca.estadisticas()
        self.estado.setText(
            f"Biblioteca: {est['total']} elementos "
            f"({est['correos']} correos, {est['documentos']} documentos"
            f"{', ' + str(est['archivados']) + ' archivados' if est['archivados'] else ''})"
            f"  ·  Mostrando {self.lista.count()}"
        )

    def _mensaje_vacio(self, consulta):
        if consulta:
            return "<p>No hay resultados para esa búsqueda.</p>"
        if self.biblioteca.estadisticas()["total"] == 0:
            return (
                "<h3>La biblioteca está vacía</h3>"
                "<p>Pulsa <b>➕ Añadir correos / documentos…</b> y elige tus "
                "archivos .eml, PDF, PowerPoint, Word o Excel. Puedes elegir "
                "varios a la vez.</p>"
            )
        return "<p>No hay elementos en esta vista.</p>"

    # ------------------------------------------------------------------
    # Vista previa
    # ------------------------------------------------------------------
    def mostrar_seleccion(self, actual, _anterior):
        if actual is None:
            return
        ficha = self.biblioteca.obtener(actual.data(Qt.UserRole))
        if not ficha:
            return

        def fila(etiqueta, valor):
            if not valor:
                return ""
            return (f"<b>{etiqueta}:</b> {html.escape(str(valor))}<br>")

        cabecera = (
            f"<h3>{html.escape(ficha['titulo'])}</h3>"
            + fila("Tipo", ficha["tipo"])
            + fila("Categoría", ficha["categoria"])
            + fila("Fecha", ficha["fecha_doc"])
            + fila("De", ficha["remitente"])
            + fila("Para", ficha["destinatarios"])
            + fila("Adjuntos", ficha["adjuntos"] or "")
            + fila("Importancia", f"{ficha['importancia']:.0f}/100")
            + fila("Por qué", ficha["motivos"])
            + fila("Archivo original", ficha["archivo"])
            + "<hr>"
        )

        texto = ficha["texto"] or "(Sin texto legible.)"
        recortado = len(texto) > MAX_VISTA_PREVIA
        texto = texto[:MAX_VISTA_PREVIA]

        palabras = Biblioteca._palabras(self.caja_busqueda.text())
        cuerpo = self._resaltar(texto, palabras)
        if recortado:
            cuerpo += "<p><i>… (texto recortado en pantalla; el original está completo)</i></p>"

        self.vista.setHtml(cabecera + cuerpo)

    @staticmethod
    def _resaltar(texto, palabras):
        """Escapa el texto y marca en amarillo las palabras buscadas."""
        if not palabras:
            return html.escape(texto).replace("\n", "<br>")
        patron = re.compile(
            "(" + "|".join(re.escape(p) for p in palabras) + ")", re.IGNORECASE
        )
        partes = patron.split(texto)
        salida = []
        for i, parte in enumerate(partes):
            seguro = html.escape(parte).replace("\n", "<br>")
            if i % 2 == 1:
                salida.append(
                    f'<span style="background-color:#ffe08a;color:#000;">{seguro}</span>'
                )
            else:
                salida.append(seguro)
        return "".join(salida)

    # ------------------------------------------------------------------
    # Añadir
    # ------------------------------------------------------------------
    def elegir_archivos(self):
        rutas, _ = QFileDialog.getOpenFileNames(
            self, "Elige correos (.eml) o documentos", "", FILTRO_ARCHIVOS
        )
        if rutas:
            self._importar(lambda progreso: self.biblioteca.agregar_varios(rutas, progreso),
                           len(rutas))

    def elegir_carpeta(self):
        carpeta = QFileDialog.getExistingDirectory(
            self, "Elige una carpeta con correos o documentos"
        )
        if carpeta:
            self._importar(lambda progreso: self.biblioteca.agregar_carpeta(carpeta, progreso),
                           0)

    def _importar(self, accion, total_estimado):
        dialogo = QProgressDialog("Añadiendo…", "Cancelar", 0, max(total_estimado, 0), self)
        dialogo.setWindowTitle("Conocimiento")
        dialogo.setWindowModality(Qt.WindowModal)
        dialogo.setMinimumDuration(0)

        def progreso(hechos, total, nombre):
            dialogo.setMaximum(max(total, 1))
            dialogo.setValue(hechos)
            if nombre:
                dialogo.setLabelText(f"Añadiendo {hechos + 1} de {total}:\n{nombre}")
            QApplication.processEvents()
            return not dialogo.wasCanceled()

        try:
            resultados = accion(progreso)
        except Exception as e:
            dialogo.close()
            QMessageBox.critical(self, "Conocimiento", f"Error al añadir:\n{e}")
            return
        dialogo.close()

        nuevos = [r for r in resultados if r.ok and not r.duplicado]
        repetidos = [r for r in resultados if r.ok and r.duplicado]
        fallos = [r for r in resultados if not r.ok]

        mensaje = (
            f"Añadidos: {len(nuevos)}\n"
            f"Ya estaban: {len(repetidos)}\n"
            f"Con problemas: {len(fallos)}"
        )
        if fallos:
            mensaje += "\n\n" + "\n".join(
                f"• {r.archivo}: {r.mensaje}" for r in fallos[:10]
            )
            if len(fallos) > 10:
                mensaje += f"\n… y {len(fallos) - 10} más."
        QMessageBox.information(self, "Conocimiento", mensaje)

        self.refrescar()

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def alternar_destacado(self):
        doc_id = self._doc_actual_id()
        if doc_id is None:
            return
        ficha = self.biblioteca.obtener(doc_id)
        nuevo = "NORMAL" if ficha["estado"] == "DESTACADO" else "DESTACADO"
        self.biblioteca.marcar(doc_id, nuevo)
        self.refrescar()

    def alternar_archivado(self):
        doc_id = self._doc_actual_id()
        if doc_id is None:
            return
        ficha = self.biblioteca.obtener(doc_id)
        nuevo = "NORMAL" if ficha["estado"] == "ARCHIVADO" else "ARCHIVADO"
        self.biblioteca.marcar(doc_id, nuevo)
        self.refrescar()

    def abrir_original(self):
        doc_id = self._doc_actual_id()
        if doc_id is None:
            return
        ficha = self.biblioteca.obtener(doc_id)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(ficha["ruta_copia"])):
            QMessageBox.warning(
                self, "Conocimiento", "No se pudo abrir el archivo original."
            )

    def quitar(self):
        doc_id = self._doc_actual_id()
        if doc_id is None:
            return
        ficha = self.biblioteca.obtener(doc_id)
        respuesta = QMessageBox.question(
            self,
            "Quitar de la biblioteca",
            f"¿Quitar «{ficha['titulo']}» de la biblioteca?\n\n"
            "Se borra la copia guardada aquí; tu archivo original no se toca.",
        )
        if respuesta == QMessageBox.Yes:
            self.biblioteca.eliminar(doc_id)
            self.refrescar()
