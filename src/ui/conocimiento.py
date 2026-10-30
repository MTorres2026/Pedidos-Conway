"""
Pantalla "Conocimiento" de CineStock: biblioteca de correos (.eml) y
documentos (PDF, PowerPoint, Word, Excel, texto) con búsqueda y sugerencias.

La lógica está en modulos/biblioteca.py (sin Qt). Aquí solo hay pantalla.
"""

import html
import tempfile
from pathlib import Path

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
from ui import vista_documento
from ui.apertura_documentos import abrir_original

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
    "imagen": "🖼",
}

FILTRO_ARCHIVOS = (
    "Correos y documentos (*.eml *.pdf *.pptx *.docx *.xlsx *.txt *.md "
    "*.csv *.html *.htm *.zip *.png *.jpg *.jpeg);;Todos los archivos (*.*)"
)

MAX_VISTA_PREVIA = 30000


class Conocimiento(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.biblioteca = Biblioteca()
        # Miniaturas de la primera página de los PDF (temporales de sesión).
        self._tmp = tempfile.TemporaryDirectory(prefix="cinestock_vista_")
        self._miniaturas = {}

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
        self.boton_releer = QPushButton("🔄 Releer todo (con imágenes)")
        self.boton_releer.setToolTip(
            "Vuelve a leer los documentos ya guardados, incluido el texto de las "
            "capturas de imagen. Puede tardar varios minutos."
        )
        fila_alta.addWidget(self.boton_archivos)
        fila_alta.addWidget(self.boton_carpeta)
        fila_alta.addWidget(self.boton_releer)
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
        divisor = QSplitter(Qt.Orientation.Horizontal)
        self.lista = QListWidget()
        self.vista = QTextBrowser()
        self.vista.setOpenLinks(False)
        # Panel de lectura en «papel» cálido: distinto del resto de la
        # ventana y pensado para leer documentos largos sin cansancio.
        self.vista.setStyleSheet(
            "QTextBrowser { background-color: #f6f1e7; color: #26303a; "
            "selection-background-color: #ffe08a; selection-color: #000000; }"
        )
        divisor.addWidget(self.lista)
        divisor.addWidget(self.vista)
        divisor.setStretchFactor(0, 2)
        divisor.setStretchFactor(1, 4)
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
        self.boton_releer.clicked.connect(self.releer_todo)
        self.boton_buscar.clicked.connect(self.refrescar)
        self.caja_busqueda.returnPressed.connect(self.refrescar)
        self.combo_filtro.currentIndexChanged.connect(self.refrescar)
        self.lista.currentItemChanged.connect(self.mostrar_seleccion)
        self.vista.anchorClicked.connect(self._enlace_pulsado)
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
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

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
            item.setData(Qt.ItemDataRole.UserRole, r.id)
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
            f"  ·  Lectura de imágenes: {self.biblioteca.ocr_nombre or 'no instalada'}"
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
    def _enlace_pulsado(self, url: QUrl):
        """Enlaces de la vista: abrir el original o salir al navegador."""
        if url.scheme() == "abrir":
            self.abrir_original()
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def _ruta_local(self, ficha):
        """Dónde está en este equipo la copia guardada del archivo."""
        guardada = str(ficha.get("ruta_copia") or "")
        nombre = guardada.replace("\\", "/").rsplit("/", 1)[-1]
        candidatas = []
        if nombre:
            candidatas.append(self.biblioteca.originales / nombre)
        if guardada:
            candidatas.append(Path(guardada))
        if ficha.get("ruta_origen"):
            candidatas.append(Path(ficha["ruta_origen"]))
        original = Path(ficha.get("archivo") or "")
        if original.is_absolute():
            candidatas.append(original)
        for ruta in candidatas:
            if ruta.is_file():
                return ruta
        return None

    def _imagen_documento(self, ficha):
        """Foto del documento de verdad: la 1ª página si es PDF, o la
        propia imagen si es una captura. Ruta del PNG o None."""
        ruta = self._ruta_local(ficha)
        if ruta is None:
            return None
        sufijo = ruta.suffix.lower()
        if sufijo in (".png", ".jpg", ".jpeg", ".bmp", ".gif"):
            return ruta
        if sufijo != ".pdf":
            return None
        clave = (str(ruta), ruta.stat().st_mtime)
        if clave in self._miniaturas:
            return self._miniaturas[clave]
        destino = None
        try:
            import fitz  # PyMuPDF; si no está, la vista se queda sin foto

            with fitz.open(ruta) as doc:
                if doc.page_count:
                    pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.6, 1.6))
                    destino = Path(self._tmp.name) / f"pag_{abs(hash(clave))}.png"
                    pix.save(destino)
        except Exception:
            destino = None
        self._miniaturas[clave] = destino
        return destino

    def mostrar_seleccion(self, actual, _anterior):
        if actual is None:
            return
        ficha = self.biblioteca.obtener(actual.data(Qt.ItemDataRole.UserRole))
        if not ficha:
            return

        gris = "#6f6450"  # etiquetas y notas sobre el fondo papel
        icono = ICONOS_TIPO.get(ficha["tipo"], "📄")

        # ----- Cabecera: qué es, en cuatro datos claros -----
        datos = []
        tipo = ficha["tipo"].upper() if ficha["tipo"] != "correo" else "Correo"
        if ficha["categoria"]:
            tipo += f" · {ficha['categoria']}"
        datos.append(("Tipo", tipo))
        if ficha["fecha_doc"]:
            datos.append(("Fecha", ficha["fecha_doc"]))
        if ficha["remitente"]:
            datos.append(("De", ficha["remitente"]))
        if ficha["adjuntos"]:
            datos.append(("Adjuntos", ficha["adjuntos"]))
        datos.append(("Importancia", f"{ficha['importancia']:.0f} / 100"
                      + (f" — {ficha['motivos']}" if ficha["motivos"] else "")))
        filas = "".join(
            f'<tr><td style="color:{gris};padding:1px 10px 1px 0">{etiqueta}</td>'
            f"<td>{html.escape(str(valor))}</td></tr>"
            for etiqueta, valor in datos
        )
        cabecera = (
            f'<p style="font-size:19px;margin:0 0 8px 0">'
            f"<b>{icono} {html.escape(ficha['titulo'])}</b></p>"
            f"<table>{filas}</table>"
            f'<p style="margin:10px 0 4px 0;font-size:15px">📂 '
            f'<a href="abrir:original" style="color:#1f6fb4">'
            f"<b>Abrir el documento original</b></a>"
            f' <span style="color:{gris}">— para leerlo entero, con su formato</span></p>'
        )

        texto = ficha["texto"] or ""
        recortado = len(texto) > MAX_VISTA_PREVIA
        texto = texto[:MAX_VISTA_PREVIA]
        palabras = Biblioteca._palabras(self.caja_busqueda.text())

        # ----- En resumen: de qué trata, sin abrir nada -----
        resumen = vista_documento.resumen(texto)
        caja_resumen = ""
        if resumen:
            caja_resumen = (
                '<table width="100%" cellspacing="0" style="margin:12px 0">'
                '<tr><td bgcolor="#ece1c9" style="padding:10px 12px">'
                f'<span style="color:{gris}">🧭 EN RESUMEN</span><br>'
                f'<span style="line-height:150%">{html.escape(resumen)}</span>'
                "</td></tr></table>"
            )

        # ----- El documento a la vista (PDF o captura) -----
        foto = self._imagen_documento(ficha)
        caja_foto = ""
        if foto is not None:
            url = QUrl.fromLocalFile(str(foto)).toString()
            que = "Primera página" if foto.suffix.lower() == ".png" and ficha["tipo"] == "pdf" else "Imagen"
            caja_foto = (
                f'<p style="color:{gris};margin:12px 0 4px 0">🖼 {que} del documento:</p>'
                f'<p style="margin:0 0 6px 0"><img src="{url}" width="680"></p>'
                f'<p style="color:{gris};margin:0 0 10px 0">'
                f"Para verlo completo: «Abrir el documento original».</p>"
            )

        # ----- Contenido limpio y legible -----
        lista_bloques, hubo_legal = vista_documento.bloques(texto)
        if lista_bloques:
            cuerpo = (
                f'<p style="color:{gris};margin:12px 0 6px 0">📝 CONTENIDO '
                f'<span>(texto ordenado para leer aquí)</span></p>'
                + vista_documento.a_html(lista_bloques, palabras)
            )
        else:
            cuerpo = (
                f'<p style="color:{gris};margin-top:12px">Este elemento no tiene '
                "texto legible guardado. Ábrelo con «Abrir el documento "
                "original».</p>"
            )
        notas = []
        if hubo_legal:
            notas.append("pie legal omitido")
        if recortado:
            notas.append("texto recortado en pantalla; el original está completo")
        if notas:
            cuerpo += (
                f'<p style="color:{gris};margin-top:14px"><i>'
                f"({' · '.join(notas)})</i></p>"
            )

        self.vista.setHtml(cabecera + caja_resumen + caja_foto + cuerpo)

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
        dialogo = QProgressDialog("Procesando…", "Cancelar", 0, max(total_estimado, 0), self)
        dialogo.setWindowTitle("Conocimiento")
        dialogo.setWindowModality(Qt.WindowModality.WindowModal)
        dialogo.setMinimumDuration(0)

        def progreso(hechos, total, nombre):
            dialogo.setMaximum(max(total, 1))
            dialogo.setValue(hechos)
            if nombre:
                dialogo.setLabelText(f"Procesando {hechos + 1} de {total}:\n{nombre}")
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
            f"Nuevos o actualizados: {len(nuevos)}\n"
            f"Sin cambios / ya estaban: {len(repetidos)}\n"
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

    def releer_todo(self):
        if self.biblioteca.ocr_nombre is None:
            QMessageBox.information(
                self,
                "Conocimiento",
                "Aún no está instalada la lectura de imágenes.\n\n"
                "En la consola de WinPython ejecuta una vez:\n"
                "pip install rapidocr-onnxruntime\n\n"
                "Después cierra y abre CineStock y pulsa este botón otra vez.",
            )
            return
        respuesta = QMessageBox.question(
            self,
            "Releer todo",
            "Se volverán a leer todos los documentos guardados, incluido el texto "
            "de las imágenes. Puede tardar varios minutos. ¿Continuar?",
        )
        if respuesta != QMessageBox.StandardButton.Yes:
            return
        self._importar(lambda progreso: self.biblioteca.reprocesar_todo(progreso), 0)

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
        abrir_original(self, self.biblioteca, doc_id)

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
        if respuesta == QMessageBox.StandardButton.Yes:
            self.biblioteca.eliminar(doc_id)
            self.refrescar()