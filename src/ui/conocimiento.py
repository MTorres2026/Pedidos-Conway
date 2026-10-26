"""
Pantalla "Conocimiento" de CineStock: biblioteca de correos (.eml) y
documentos (PDF, PowerPoint, Word, Excel, texto) con búsqueda y sugerencias.

La lógica está en modulos/biblioteca.py (sin Qt). Aquí solo hay pantalla.
"""

import html
import os
import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
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
from modulos.depurador_biblioteca import (
    InformeEstudioBiblioteca,
    analizar_biblioteca_en_profundidad,
    ejecutar_limpieza_biblioteca,
    formatear_bytes,
)

try:
    from ui.apertura_documentos import abrir_original
except ImportError:
    def abrir_original(widget_padre, biblioteca: Biblioteca, doc_id: int) -> None:
        ficha = biblioteca.obtener(doc_id)
        if not ficha:
            return
        guardada = str(ficha.get("ruta_copia") or "")
        nombre = guardada.replace("\\", "/").rsplit("/", 1)[-1]
        ruta = (biblioteca.originales / nombre) if nombre else Path(guardada)
        if not ruta.is_file():
            ruta = Path(guardada)
        if not ruta.is_file():
            QMessageBox.warning(
                widget_padre,
                "Conocimiento",
                f"No se encuentra el archivo en disco:\n{ruta}",
            )
            return
        if sys.platform.startswith("win"):
            os.startfile(str(ruta))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(ruta)])
        else:
            subprocess.Popen(["xdg-open", str(ruta)])

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
        self.boton_depurar = QPushButton("🧹 Estudio y limpieza profunda")
        self.boton_depurar.setStyleSheet(
            "QPushButton { background-color: #0F766E; color: #FFFFFF; "
            "font-weight: bold; padding: 6px 14px; border-radius: 6px; }"
            "QPushButton:hover { background-color: #0D9488; }"
        )
        self.boton_depurar.setToolTip(
            "Analiza toda la biblioteca en profundidad, detecta duplicados, correos "
            "obsoletos o contenido que no es manual, deja solo lo más actual y compacta biblioteca.db."
        )
        fila_alta.addWidget(self.boton_archivos)
        fila_alta.addWidget(self.boton_carpeta)
        fila_alta.addWidget(self.boton_releer)
        fila_alta.addWidget(self.boton_depurar)
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
        self.boton_releer.clicked.connect(self.releer_todo)
        self.boton_depurar.clicked.connect(self.estudiar_y_limpiar)
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
    def mostrar_seleccion(self, actual, _anterior):
        if actual is None:
            return
        ficha = self.biblioteca.obtener(actual.data(Qt.ItemDataRole.UserRole))
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

    def estudiar_y_limpiar(self):
        est = self.biblioteca.estadisticas()
        if est["total"] == 0:
            # Aun sin filas en SQLite puede haber archivos huérfanos en originales/
            informe_vacio = analizar_biblioteca_en_profundidad(
                self.biblioteca, usar_ia=False
            )
            if not informe_vacio.archivos_huerfanos:
                QMessageBox.information(
                    self,
                    "Estudio de la biblioteca",
                    "Tu biblioteca está vacía en este momento (0 elementos).",
                )
                return

        progreso = QProgressDialog(
            "Analizando toda la biblioteca en profundidad\n"
            "(duplicados, hilos repetidos, versiones antiguas, correos efímeros y auditoría IA)…",
            "",
            0,
            0,
            self,
        )
        progreso.setWindowTitle("Estudio profundo de la biblioteca")
        progreso.setCancelButton(None)
        progreso.setWindowModality(Qt.WindowModality.WindowModal)
        progreso.setMinimumDuration(0)
        progreso.show()
        QApplication.processEvents()

        try:
            informe = analizar_biblioteca_en_profundidad(
                self.biblioteca, usar_ia=True
            )
        except Exception as e:
            progreso.close()
            QMessageBox.warning(
                self,
                "Estudio de la biblioteca",
                f"No se pudo completar el estudio:\n{e}",
            )
            return
        progreso.close()

        dialogo = _DialogoEstudioBiblioteca(self, self.biblioteca, informe)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self.refrescar()


_ETIQUETAS_MOTIVO = {
    "DUPLICADO": "🔁 Duplicado",
    "VERSION_OBSOLETA": "⏳ Versión obsoleta",
    "HILO_REPETIDO": "💬 Hilo repetido",
    "ADJUNTO_YA_SUELTO": "📎 Adjunto ya suelto",
    "NO_ES_MANUAL": "🧹 No es manual",
    "ARCHIVADO": "🗄 Archivado",
    "AUDITORIA_IA": "🤖 Descarte IA",
}


class _DialogoEstudioBiblioteca(QDialog):

    def __init__(
        self,
        parent: QWidget,
        biblioteca: Biblioteca,
        informe: InformeEstudioBiblioteca,
    ):
        super().__init__(parent)
        self.biblioteca = biblioteca
        self.informe = informe
        self.setWindowTitle("🧹 Estudio Profundo y Limpieza de la Biblioteca")
        self.resize(980, 660)

        layout = QVBoxLayout(self)

        # Cabecera resumen
        nota_ia = (
            f" · 🤖 Auditado también con {informe.proveedor_ia}"
            if informe.usado_ia
            else (f" · ℹ️ {informe.aviso_ia}" if informe.aviso_ia else "")
        )
        resumen = QLabel(
            f"<b>📊 Resultado del estudio ({informe.fecha_estudio}):</b>  "
            f"Analizados: <b>{informe.total_analizados}</b>  ·  "
            f"✅ Manuales vigentes a conservar: <b>{len(informe.conservar)}</b>  ·  "
            f"🗑️ Obsoletos / repetidos / ruido a eliminar: <b>{len(informe.eliminar)}</b>"
            f"{f' (+ {len(informe.archivos_huerfanos)} huérfanos en disco)' if informe.archivos_huerfanos else ''}  ·  "
            f"💾 Liberación estimada: <b>{formatear_bytes(informe.bytes_estimados_liberar)}</b>"
            f"{html.escape(nota_ia)}"
        )
        resumen.setWordWrap(True)
        resumen.setStyleSheet(
            "background-color: #0F172A; color: #F8FAFC; padding: 12px 14px; "
            "border-radius: 8px; font-size: 13px;"
        )
        layout.addWidget(resumen)

        divisor = QSplitter(Qt.Orientation.Horizontal)

        # Panel izquierdo: lista marcable de elementos a eliminar
        panel_izq = QWidget()
        lay_izq = QVBoxLayout(panel_izq)
        lay_izq.setContentsMargins(0, 0, 0, 0)
        lbl_izq = QLabel(
            f"<b>🗑️ Elementos marcados para eliminar ({len(informe.eliminar)}):</b><br>"
            "<span style='font-size:12px;'>Desmarca cualquiera que prefieras conservar antes de aplicar.</span>"
        )
        lbl_izq.setWordWrap(True)
        lay_izq.addWidget(lbl_izq)

        self.lista_eliminar = QListWidget()
        for d in informe.eliminar:
            icono = ICONOS_TIPO.get(d.tipo, "📄")
            etiqueta = _ETIQUETAS_MOTIVO.get(d.codigo_motivo, d.codigo_motivo)
            texto_item = (
                f"[{etiqueta}] {icono} {d.titulo}\n"
                f"    {d.fecha or 'sin fecha'} · {formatear_bytes(d.tamano_bytes)} — {d.motivo}"
            )
            item = QListWidgetItem(texto_item)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, d.doc_id)
            item.setToolTip(d.motivo)
            self.lista_eliminar.addItem(item)
        lay_izq.addWidget(self.lista_eliminar, 1)

        fila_sel = QHBoxLayout()
        btn_todos = QPushButton("Marcar todos")
        btn_ninguno = QPushButton("Desmarcar todos")
        btn_todos.clicked.connect(lambda: self._marcar_todos(Qt.CheckState.Checked))
        btn_ninguno.clicked.connect(lambda: self._marcar_todos(Qt.CheckState.Unchecked))
        fila_sel.addWidget(btn_todos)
        fila_sel.addWidget(btn_ninguno)
        fila_sel.addStretch()
        lay_izq.addLayout(fila_sel)

        # Panel derecho: informe detallado (qué manuales quedan y por qué se descarta el resto)
        self.visor_informe = QTextBrowser()
        self.visor_informe.setHtml(self._generar_html_informe())

        divisor.addWidget(panel_izq)
        divisor.addWidget(self.visor_informe)
        divisor.setStretchFactor(0, 5)
        divisor.setStretchFactor(1, 6)
        layout.addWidget(divisor, 1)

        # Botones inferiores
        fila_botones = QHBoxLayout()
        fila_botones.addStretch()
        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_aplicar = QPushButton("🧹 Eliminar marcados y compactar biblioteca ahora")
        self.btn_aplicar.setStyleSheet(
            "QPushButton { background-color: #DC2626; color: #FFFFFF; "
            "font-weight: bold; padding: 8px 18px; border-radius: 6px; font-size: 13px; }"
            "QPushButton:hover { background-color: #B91C1C; }"
            "QPushButton:disabled { background-color: #64748B; color: #CBD5E1; }"
        )
        if not informe.eliminar and not informe.archivos_huerfanos:
            self.btn_aplicar.setEnabled(False)
            self.btn_aplicar.setText("✅ La biblioteca ya está limpia y compacta")
        self.btn_cancelar.clicked.connect(self.reject)
        self.btn_aplicar.clicked.connect(self._aplicar_limpieza)
        fila_botones.addWidget(self.btn_cancelar)
        fila_botones.addWidget(self.btn_aplicar)
        layout.addLayout(fila_botones)

    def _marcar_todos(self, estado: Qt.CheckState):
        for i in range(self.lista_eliminar.count()):
            self.lista_eliminar.item(i).setCheckState(estado)

    def _generar_html_informe(self) -> str:
        inf = self.informe
        filas_ok = []
        for d in inf.conservar:
            icono = ICONOS_TIPO.get(d.tipo, "📄")
            estrella = "⭐ " if d.estado == "DESTACADO" else ""
            filas_ok.append(
                "<tr>"
                f"<td style='padding:6px 8px; border-bottom:1px solid #E2E8F0; color:#0F172A;'>"
                f"<b>{estrella}{icono} {html.escape(d.titulo)}</b><br>"
                f"<span style='color:#475569; font-size:11px;'>"
                f"{html.escape(d.categoria or 'General')} · {html.escape(d.archivo)}</span></td>"
                f"<td style='padding:6px 8px; border-bottom:1px solid #E2E8F0; color:#334155; "
                f"white-space:nowrap; font-size:12px;'>{html.escape(d.fecha or 'Vigente')}</td>"
                "</tr>"
            )
        tabla_ok = (
            "<table width='100%' cellspacing='0' cellpadding='0' "
            "style='background:#FFFFFF; border:1px solid #CBD5E1; border-radius:6px;'>"
            "<tr style='background:#DCFCE7;'>"
            "<th align='left' style='padding:7px 8px; color:#14532D;'>Manual / Guía que SE CONSERVA</th>"
            "<th align='left' style='padding:7px 8px; color:#14532D;'>Fecha</th></tr>"
            + "".join(filas_ok)
            + "</table>"
            if filas_ok
            else "<p style='color:#475569;'>No hay manuales que conservar.</p>"
        )

        filas_del = []
        for d in inf.eliminar:
            etiqueta = _ETIQUETAS_MOTIVO.get(d.codigo_motivo, d.codigo_motivo)
            filas_del.append(
                "<tr>"
                f"<td style='padding:6px 8px; border-bottom:1px solid #FEE2E2; color:#0F172A;'>"
                f"<b>{html.escape(d.titulo)}</b><br>"
                f"<span style='color:#991B1B; font-size:11px;'><b>{html.escape(etiqueta)}</b>: "
                f"{html.escape(d.motivo)}</span></td>"
                f"<td style='padding:6px 8px; border-bottom:1px solid #FEE2E2; color:#475569; "
                f"white-space:nowrap; font-size:11px;'>{formatear_bytes(d.tamano_bytes)}</td>"
                "</tr>"
            )
        tabla_del = (
            "<table width='100%' cellspacing='0' cellpadding='0' "
            "style='background:#FFFFFF; border:1px solid #FECACA; border-radius:6px;'>"
            "<tr style='background:#FEE2E2;'>"
            "<th align='left' style='padding:7px 8px; color:#7F1D1D;'>Elemento obsoleto / repetido / ruido</th>"
            "<th align='left' style='padding:7px 8px; color:#7F1D1D;'>Tamaño</th></tr>"
            + "".join(filas_del)
            + "</table>"
            if filas_del
            else "<p style='color:#15803D;'><b>¡No se han detectado duplicados ni documentos obsoletos!</b></p>"
        )

        bloque_huerfanos = ""
        if inf.archivos_huerfanos:
            nombres_h = ", ".join(html.escape(p.name) for p in inf.archivos_huerfanos[:12])
            bloque_huerfanos = (
                "<div style='background:#FEF3C7; color:#78350F; padding:9px 12px; "
                "border-radius:6px; margin-top:12px; font-size:12px;'>"
                f"<b>🧹 Archivos huérfanos en originales/ ({len(inf.archivos_huerfanos)}):</b> "
                f"también se limpiarán copias antiguas sin ficha ({nombres_h}).</div>"
            )

        return (
            "<div style='background:#F8FAFC; color:#0F172A; padding:12px; "
            "font-family:Segoe UI, Arial, sans-serif; font-size:13px;'>"
            f"<h3 style='margin-top:0; color:#0F766E;'>✅ Quedarán en tu Biblioteca ({len(inf.conservar)} manuales limpios)</h3>"
            f"{tabla_ok}"
            f"{bloque_huerfanos}"
            f"<h3 style='margin-top:16px; color:#B91C1C;'>🗑️ Detalle de limpieza propuesta ({len(inf.eliminar)})</h3>"
            f"{tabla_del}"
            "</div>"
        )

    def _aplicar_limpieza(self):
        ids_marcados = []
        for i in range(self.lista_eliminar.count()):
            it = self.lista_eliminar.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                ids_marcados.append(int(it.data(Qt.ItemDataRole.UserRole)))

        if not ids_marcados and not self.informe.archivos_huerfanos:
            QMessageBox.information(
                self,
                "Limpieza de biblioteca",
                "No hay ningún elemento marcado para eliminar.",
            )
            return

        confirmar = QMessageBox.question(
            self,
            "Confirmar limpieza y compactación",
            f"Se van a eliminar {len(ids_marcados)} elemento(s) obsoletos/repetidos "
            f"de la biblioteca y se compactará la base de datos SQLite (VACUUM).\n\n"
            f"Se conservarán intactos los manuales vigentes ({len(self.informe.conservar)} "
            f"+ los que hayas desmarcado).\n\n¿Ejecutar limpieza ahora?",
        )
        if confirmar != QMessageBox.StandardButton.Yes:
            return

        res = ejecutar_limpieza_biblioteca(
            self.biblioteca, ids_marcados, limpiar_huerfanos=True
        )
        QMessageBox.information(
            self,
            "Biblioteca depurada y compactada",
            f"✔ Limpieza profunda completada con éxito:\n\n"
            f"  • Elementos eliminados: {res.eliminados_db}\n"
            f"  • Copias huérfanas borradas: {res.huerfanos_borrados}\n"
            f"  • Manuales vigentes activos: {res.conservados_db}\n"
            f"  • Tamaño antes: {formatear_bytes(res.bytes_antes)}\n"
            f"  • Tamaño actual compactado: {formatear_bytes(res.bytes_despues)}\n"
            f"  • Espacio liberado: {formatear_bytes(res.bytes_liberados)}",
        )
        self.accept()