from pathlib import Path
import os
import shutil
import subprocess
import sys
import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QDialog,
    QHeaderView,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QComboBox,
    QAbstractItemView,
    QFrame,
)
# Importamos las herramientas de PDF de PySide6
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtPdf import QPdfDocument


class VisorPDFDialog(QDialog):
    """Ventana emergente para visualizar PDFs dentro de la aplicación."""

    def __init__(self, ruta_pdf, titulo="Visor de Documento", parent=None):
        super().__init__(parent)

        self.setWindowTitle(titulo)
        self.resize(900, 1100)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Componente de visualización de PDF
        self.visor = QPdfView()
        self.documento = QPdfDocument(self)

         # Cargamos el documento
        error_carga = self.documento.load(str(ruta_pdf))

        if error_carga == QPdfDocument.Error.None_ and self.documento.pageCount() > 0:
            self.visor.setDocument(self.documento)
            # Modo de documento continuo y ajustado al ancho (si la versión lo soporta)
            try:
                self.visor.setPageMode(QPdfView.PageMode.MultiPage)
                self.visor.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            except AttributeError:
                pass  # versiones antiguas de PySide6 sin estos modos
            layout.addWidget(self.visor)
        else:
            error_label = QLabel(
                f"No se pudo cargar el archivo PDF o el documento está vacío.\n"
                f"Ruta: {ruta_pdf}"
            )
            error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(error_label)

        # Botón para cerrar el visor
        boton_cerrar = QPushButton("CERRAR")
        boton_cerrar.setMinimumHeight(40)
        boton_cerrar.clicked.connect(self.accept)
        layout.addWidget(boton_cerrar)


class HistorialPedidosDialog(QDialog):

    def __init__(self, historial_dir, parent=None):
        super().__init__(parent)

        self.historial_dir = Path(historial_dir)

        self.setWindowTitle("Historial de pedidos Conway")
        self.resize(950, 550)

        layout = QVBoxLayout(self)

        titulo = QLabel("HISTORIAL DE PEDIDOS")
        titulo.setStyleSheet("""
            QLabel {
                font-size: 20px;
                font-weight: bold;
                padding: 8px;
            }
        """)
        layout.addWidget(titulo)

        self.tabla = QTableWidget()
        self.tabla.setColumnCount(5)
        self.tabla.setHorizontalHeaderLabels([
            "Pedido",
            "Fecha",
            "Líneas",
            "Estado",
            "Archivo"
        ])

        # --- NUEVO: Centrar las cabeceras de la tabla ---
        self.tabla.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)

        self.tabla.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )

        self.tabla.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )

        self.tabla.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        layout.addWidget(self.tabla)

        # ----------------------------------------------------------
        # BARRA INFERIOR: ELIMINAR HISTORIAL + CERRAR
        # ----------------------------------------------------------
        botones = QHBoxLayout()

        self.boton_eliminar = QPushButton("🗑  ELIMINAR HISTORIAL")
        self.boton_eliminar.setMinimumHeight(40)
        self.boton_eliminar.setStyleSheet("""
            QPushButton {
                background-color: #b00020;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 18px;
            }
            QPushButton:hover {
                background-color: #8a0018;
            }
            QPushButton:pressed {
                background-color: #6a0012;
            }
        """)
        self.boton_eliminar.clicked.connect(self.eliminar_historial)
        botones.addWidget(self.boton_eliminar)

        botones.addStretch()

        boton_cerrar = QPushButton("CERRAR")
        boton_cerrar.setMinimumHeight(40)
        boton_cerrar.clicked.connect(self.accept)
        botones.addWidget(boton_cerrar)

        layout.addLayout(botones)

        self.cargar_historial()

        self.tabla.cellDoubleClicked.connect(
            self.ver_pedido
        )

    # ==========================================================
    # HISTORIAL
    # ==========================================================

    def cargar_historial(self):
        self.tabla.setRowCount(0)

        if not self.historial_dir.exists():
            return

        archivos = sorted(
            self.historial_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        for archivo in archivos:
            try:
                with open(
                    archivo,
                    "r",
                    encoding="utf-8"
                ) as f:
                    datos = json.load(f)
            except Exception:
                continue

            pedido = datos.get(
                "pedido",
                datos
            )

            order_id = pedido.get(
                "orderId",
                ""
            )

            fecha = pedido.get(
                "fechaInforme",
                ""
            )

            lineas = datos.get(
                "lineas",
                []
            )

            if not lineas:
                lineas = pedido.get(
                    "lineas",
                    []
                )

            estado = pedido.get(
                "estado",
                datos.get(
                    "estado",
                    ""
                )
            )

            if lineas:
                numero_lineas = len(lineas)
            else:
                numero_lineas = pedido.get(
                    "totalLineas",
                    0
                )

            if order_id in ("", None):
                continue

            fila = self.tabla.rowCount()
            self.tabla.insertRow(fila)

            # --- NUEVO: Centrar el contenido de cada celda ---
            item_pedido = QTableWidgetItem(str(order_id))
            item_pedido.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabla.setItem(fila, 0, item_pedido)

            item_fecha = QTableWidgetItem(str(fecha))
            item_fecha.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabla.setItem(fila, 1, item_fecha)

            item_lineas = QTableWidgetItem(str(numero_lineas))
            item_lineas.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabla.setItem(fila, 2, item_lineas)

            item_estado = QTableWidgetItem(str(estado))
            item_estado.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabla.setItem(fila, 3, item_estado)

            item_archivo = QTableWidgetItem(archivo.name)
            item_archivo.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabla.setItem(fila, 4, item_archivo)

    # ==========================================================
    # ELIMINAR HISTORIAL COMPLETO
    # ==========================================================

    def eliminar_historial(self):
        """
        Borra todos los pedidos del historial (.json) y sus PDFs asociados.
        Pide confirmación dos veces porque es irreversible.
        """
        # 1) Primera confirmación
        respuesta = QMessageBox.question(
            self,
            "Eliminar historial",
            "¿Seguro que quieres borrar TODO el historial de pedidos?\n\n"
            "Se borrarán los archivos .json del historial y sus PDFs "
            "asociados.\n\n"
            "Esta acción NO se puede deshacer.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if respuesta != QMessageBox.StandardButton.Yes:
            return

        # 2) Segunda confirmación (última oportunidad)
        respuesta2 = QMessageBox.warning(
            self,
            "Confirmar borrado",
            "Última oportunidad.\n\n"
            "¿Borrar TODOS los pedidos del historial?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if respuesta2 != QMessageBox.StandardButton.Yes:
            return

        borrados = 0
        errores = []

        # 3) Borrar los .json del historial
        if self.historial_dir.exists():
            for archivo in list(self.historial_dir.glob("*.json")):
                try:
                    archivo.unlink()
                    borrados += 1
                except OSError as e:
                    errores.append(f"{archivo.name}: {e}")

        # 4) Borrar los PDFs asociados
        #    (carpeta hermana: data/pdf_pedidos)
        pdf_dir = self.historial_dir.parent / "pdf_pedidos"
        if pdf_dir.exists():
            for archivo in list(pdf_dir.glob("*.pdf")):
                try:
                    archivo.unlink()
                except OSError as e:
                    errores.append(f"{archivo.name}: {e}")

        # 5) Refrescar la tabla en pantalla
        self.cargar_historial()

        # 6) Avisar al usuario
        if errores:
            QMessageBox.warning(
                self,
                "Borrado parcial",
                f"Se borraron {borrados} pedidos del historial "
                f"pero hubo errores:\n\n"
                + "\n".join(errores[:10])
                + (
                    "\n\n(...y más)"
                    if len(errores) > 10
                    else ""
                )
            )
        else:
            QMessageBox.information(
                self,
                "Historial borrado",
                f"Se han borrado {borrados} pedidos del historial "
                "y sus PDFs asociados."
            )

    # ==========================================================
    # VER PEDIDO DEL HISTORIAL
    # ==========================================================

    def ver_pedido(
        self,
        fila,
        columna
    ):
        archivo_item = self.tabla.item(
            fila,
            4
        )

        if not archivo_item:
            return

        archivo = (
            self.historial_dir
            / archivo_item.text()
        )

        if not archivo.exists():
            return

        try:
            with open(
                archivo,
                "r",
                encoding="utf-8"
            ) as f:
                datos = json.load(f)

        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"No se pudo abrir el pedido:\n{e}"
            )
            return

        pedido = datos.get(
            "pedido",
            datos
        )

        lineas = datos.get(
            "lineas",
            []
        )

        if not lineas:
            lineas = pedido.get(
                "lineas",
                []
            )

        order_id = pedido.get(
            "orderId",
            ""
        )

        dialogo = QDialog(self)

        dialogo.setWindowTitle(
            f"Pedido Conway #{order_id}"
        )

        dialogo.resize(
            1000,
            600
        )

        layout = QVBoxLayout(
            dialogo
        )

        titulo = QLabel(
            f"PEDIDO CONWAY #{order_id}"
        )

        titulo.setStyleSheet("""
            QLabel {
                font-size: 20px;
                font-weight: bold;
                padding: 8px;
            }
        """)
        layout.addWidget(titulo)

        tabla = QTableWidget()

        tabla.setColumnCount(6)

        tabla.setHorizontalHeaderLabels([
            "Producto",
            "Artículo ERP",
            "UM",
            "Cantidad original",
            "Cantidad final",
            "Corregido"
        ])

        tabla.setRowCount(
            len(lineas)
        )

        for i, linea in enumerate(lineas):

            descripcion = linea.get(
                "descripcion",
                linea.get(
                    "nombre",
                    ""
                )
            )

            codigo = linea.get(
                "codigo",
                linea.get(
                    "idArticulo",
                    ""
                )
            )

            unidad = linea.get(
                "unidad",
                linea.get(
                    "um",
                    ""
                )
            )

            cantidad_original = linea.get(
                "cantidadOriginal",
                linea.get(
                    "cantidadPedida",
                    linea.get(
                        "cantidad",
                        0
                    )
                )
            )

            cantidad_final = linea.get(
                "cantidadPedida",
                linea.get(
                    "cantidad",
                    cantidad_original
                )
            )

            corregido = linea.get(
                "corregido",
                False
            )

            valores = [
                descripcion,
                codigo,
                unidad,
                cantidad_original,
                cantidad_final,
                "SÍ" if corregido else "NO"
            ]

            for j, valor in enumerate(valores):

                item = QTableWidgetItem(
                    str(valor)
                )

                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter
                )

                tabla.setItem(
                    i,
                    j,
                    item
                )

        tabla.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        layout.addWidget(
            tabla
        )

        boton = QPushButton(
            "CERRAR"
        )

        boton.clicked.connect(
            dialogo.accept
        )

        layout.addWidget(
            boton
        )

        dialogo.exec()


class Pedidos(QWidget):

    def __init__(
        self,
        parent=None
    ):
        super().__init__(parent)

        self.setWindowTitle(
            "Pedidos Conway"
        )

        self.resize(
            1200,
            750
        )

        if getattr(sys, "frozen", False):
            BASE_DIR = Path(sys.executable).resolve().parents[2]
        else:
            BASE_DIR = Path(__file__).resolve().parents[2]

        self.base_dir = BASE_DIR

        self.archivo_pedido = (
            BASE_DIR
            / "data"
            / "pedido_actual.json"
        )

        self.historial_dir = (
            BASE_DIR
            / "data"
            / "historial_pedidos"
        )

        self.estado_pedido = (
            BASE_DIR
            / "data"
            / "estado_pedido.json"
        )

        self.recibidos_dir = (
            BASE_DIR
            / "data"
            / "pedidos_recibidos"
        )

        self.procesamiento_dir = (
            BASE_DIR
            / "data"
            / "pedidos_para_procesar"
        )

        self.historial_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.recibidos_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.procesamiento_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.estado_actual = (
            self.cargar_estado_pedido()
        )

        self.ultimo_mtime = None
        self.datos_pedido = None
        self.order_id_actual = None
        self.fila_seleccionada = None
        self.pedido_mostrado = False
        self.pedido_coherente = False

        self.construir_interfaz()

        self.cargar_estado_inicial()

        self.timer = QTimer(self)

        self.timer.timeout.connect(
            self.comprobar_pedido_nuevo
        )

        self.timer.start(1000)

    # ==========================================================
    # INTERFAZ
    # ==========================================================

    def construir_interfaz(self):

        layout_principal = QVBoxLayout(
            self
        )

        layout_principal.setContentsMargins(
            20,
            20,
            20,
            20
        )

        layout_principal.setSpacing(
            12
        )

        cabecera = QHBoxLayout()

        titulo = QLabel(
            "PEDIDOS CONWAY"
        )

        titulo.setStyleSheet("""
            QLabel {
                font-size: 26px;
                font-weight: bold;
            }
        """)
        cabecera.addWidget(
            titulo
        )
        cabecera.addStretch()

        # Layout para Stockable (Ver + Descargar)
        layout_stockable = QHBoxLayout()
        self.boton_ver_stockable = QPushButton("📄 VER STOCKABLE")
        self.boton_ver_stockable.setMinimumHeight(40)
        self.boton_ver_stockable.clicked.connect(self.abrir_pdf_stockable)
        layout_stockable.addWidget(self.boton_ver_stockable)
        
        self.boton_descargar_stockable = QPushButton("💾 DESCARGAR PDF")
        self.boton_descargar_stockable.setMinimumHeight(40)
        self.boton_descargar_stockable.clicked.connect(self.descargar_stockable)
        layout_stockable.addWidget(self.boton_descargar_stockable)
        
        cabecera.addLayout(layout_stockable)

        # Layout para Granel (Ver + Descargar)
        layout_granel = QHBoxLayout()
        self.boton_ver_granel = QPushButton("🍯 VER GRANEL")
        self.boton_ver_granel.setMinimumHeight(40)
        self.boton_ver_granel.clicked.connect(self.abrir_pdf_granel)
        layout_granel.addWidget(self.boton_ver_granel)
        
        self.boton_descargar_granel = QPushButton("💾 DESCARGAR PDF")
        self.boton_descargar_granel.setMinimumHeight(40)
        self.boton_descargar_granel.clicked.connect(self.descargar_granel)
        layout_granel.addWidget(self.boton_descargar_granel)
        
        cabecera.addLayout(layout_granel)

        self.boton_historial = QPushButton(
            "📚 HISTORIAL"
        )

        self.boton_historial.setMinimumHeight(
            40
        )

        self.boton_historial.clicked.connect(
            self.abrir_historial
        )
        cabecera.addWidget(
            self.boton_historial
        )

        layout_principal.addLayout(
            cabecera
        )

        self.panel_pendiente = QFrame()

        self.panel_pendiente.setObjectName(
            "panelPendiente"
        )

        self.panel_pendiente.setStyleSheet("""
            QFrame#panelPendiente {
                background-color: #f4f6f8;
                border: 1px solid #8a9aaa;
                border-radius: 8px;
            }

            QLabel {
                background: transparent;
                border: none;
            }
        """)

        panel_layout = QVBoxLayout(
            self.panel_pendiente
        )

        panel_layout.setContentsMargins(
            18,
            14,
            18,
            14
        )

        titulo_pendiente = QHBoxLayout()

        indicador = QLabel(
            "●"
        )

        indicador.setStyleSheet("""
            QLabel {
                color: #c62828;
                font-size: 20px;
                font-weight: bold;
            }
        """)
        self.texto_pendiente = QLabel(
            "PEDIDO CONWAY PENDIENTE"
        )

        self.texto_pendiente.setStyleSheet("""
            QLabel {
                color: #263238;
                font-size: 18px;
                font-weight: bold;
            }
        """)

        titulo_pendiente.addWidget(
            indicador
        )

        titulo_pendiente.addWidget(
            self.texto_pendiente
        )

        titulo_pendiente.addStretch()

        panel_layout.addLayout(
            titulo_pendiente
        )

        self.info_pendiente = QLabel(
            ""
        )

        self.info_pendiente.setStyleSheet("""
            QLabel {
                color: #455a64;
                font-size: 14px;
                padding-left: 27px;
                padding-top: 2px;
                padding-bottom: 4px;
            }
        """)

        panel_layout.addWidget(
            self.info_pendiente
        )

        self.boton_ver_pendiente = QPushButton(
            "📦  VER PEDIDO PENDIENTE"
        )

        self.boton_ver_pendiente.setMinimumHeight(
            44
        )

        self.boton_ver_pendiente.setStyleSheet("""
            QPushButton {
                background-color: #1976d2;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 15px;
                font-weight: bold;
                padding: 8px 18px;
            }

            QPushButton:hover {
                background-color: #1565c0;
            }

            QPushButton:pressed {
                background-color: #0d47a1;
            }
        """)

        self.boton_ver_pendiente.clicked.connect(
            self.mostrar_pedido
        )

        panel_layout.addWidget(
            self.boton_ver_pendiente
        )

        layout_principal.addWidget(
            self.panel_pendiente
        )

        self.tabla = QTableWidget()

        self.tabla.setColumnCount(
            4
        )

        self.tabla.setHorizontalHeaderLabels([
            "Producto",
            "Artículo ERP",
            "UM",
            "Cantidad pedida"
        ])

        self.tabla.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )

        self.tabla.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )

        self.tabla.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )

        self.tabla.setAlternatingRowColors(
            True
        )

        self.tabla.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch
        )

        self.tabla.horizontalHeader().setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.ResizeToContents
        )

        self.tabla.horizontalHeader().setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.ResizeToContents
        )

        self.tabla.horizontalHeader().setSectionResizeMode(
            3,
            QHeaderView.ResizeMode.ResizeToContents
        )

        self.tabla.itemSelectionChanged.connect(
            self.seleccion_cambiada
        )

        layout_principal.addWidget(
            self.tabla
        )

        self.tabla.hide()

        self.panel_correccion = QFrame()

        self.panel_correccion.setFrameShape(
            QFrame.Shape.StyledPanel
        )

        panel_correccion_layout = QVBoxLayout(
            self.panel_correccion
        )

        titulo_correccion = QLabel(
            "CORREGIR PRODUCTO"
        )

        titulo_correccion.setStyleSheet("""
            QLabel {
                font-size: 17px;
                font-weight: bold;
            }
        """)

        panel_correccion_layout.addWidget(
            titulo_correccion
        )

        formulario = QFormLayout()

        self.campo_producto = QLineEdit()

        self.campo_producto.setReadOnly(
            True
        )

        self.campo_codigo = QLineEdit()

        self.campo_um = QComboBox()

        self.campo_um.setEditable(
            True
        )

        self.campo_cantidad = QSpinBox()

        self.campo_cantidad.setRange(
            0,
            999999
        )

        formulario.addRow(
            "Producto:",
            self.campo_producto
        )

        formulario.addRow(
            "Artículo ERP:",
            self.campo_codigo
        )

        formulario.addRow(
            "UM:",
            self.campo_um
        )

        formulario.addRow(
            "Cantidad:",
            self.campo_cantidad
        )

        panel_correccion_layout.addLayout(
            formulario
        )

        botones_correccion = QHBoxLayout()

        self.boton_guardar_correccion = QPushButton(
            "GUARDAR CORRECCIÓN"
        )

        self.boton_guardar_correccion.clicked.connect(
            self.guardar_correccion
        )

        self.boton_cancelar_correccion = QPushButton(
            "CANCELAR"
        )

        self.boton_cancelar_correccion.clicked.connect(
            self.cancelar_correccion
        )

        botones_correccion.addWidget(
            self.boton_guardar_correccion
        )

        botones_correccion.addWidget(
            self.boton_cancelar_correccion
        )

        panel_correccion_layout.addLayout(
            botones_correccion
        )

        layout_principal.addWidget(
            self.panel_correccion
        )

        self.panel_correccion.hide()

        botones_principales = QHBoxLayout()

        self.boton_corregir = QPushButton(
            "✏️ CORREGIR PRODUCTO"
        )

        self.boton_corregir.setMinimumHeight(
            44
        )

        self.boton_corregir.clicked.connect(
            self.mostrar_correccion
        )

        self.boton_procesar = QPushButton(
            "📤 ENVIAR A ERP"
        )

        self.boton_procesar.setMinimumHeight(
            44
        )

        self.boton_procesar.clicked.connect(
            self.enviar_a_erp
        )

        botones_principales.addWidget(
            self.boton_corregir
        )

        botones_principales.addWidget(
            self.boton_procesar
        )

        layout_principal.addLayout(
            botones_principales
        )

        self.estado_label = QLabel(
            "Estado: —"
        )

        self.estado_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.estado_label.setStyleSheet("""
            QLabel {
                color: #455a64;
                font-size: 14px;
                padding: 8px;
            }
        """)

        layout_principal.addWidget(
            self.estado_label
        )

    # ==========================================================
    # ESTADO
    # ==========================================================

    def cargar_estado_pedido(self):

        if not self.estado_pedido.exists():
            return {}

        try:
            with open(
                self.estado_pedido,
                "r",
                encoding="utf-8"
            ) as f:
                return json.load(f)

        except Exception:
            return {}

    def guardar_estado_pedido(
        self,
        order_id,
        estado
    ):

        datos = {
            "orderId": order_id,
            "estado": estado
        }

        try:
            with open(
                self.estado_pedido,
                "w",
                encoding="utf-8"
            ) as f:
                json.dump(
                    datos,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

            self.estado_actual = datos

        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"No se pudo guardar el estado:\n{e}"
            )

    def obtener_order_id_estado(self):

        return self.estado_actual.get(
            "orderId"
        )

    def obtener_estado_activo(self):

        return self.estado_actual.get(
            "estado"
        )

    def ids_coinciden(
        self,
        id1,
        id2
    ):

        if id1 is None or id2 is None:
            return False

        return str(id1) == str(id2)

    # ==========================================================
    # CARGA INICIAL
    # ==========================================================

    def cargar_estado_inicial(self):

        self.pedido_mostrado = False

        if not self.archivo_pedido.exists():

            self.datos_pedido = None
            self.order_id_actual = None
            self.pedido_coherente = False

            self.actualizar_interfaz()

            return

        datos = self.leer_pedido_actual()

        if not datos:

            self.actualizar_interfaz()

            return

        pedido = datos.get(
            "pedido",
            datos
        )

        order_id_archivo = pedido.get(
            "orderId"
        )

        estado_order_id = (
            self.obtener_order_id_estado()
        )

        estado = (
            self.obtener_estado_activo()
        )

        if estado_order_id is not None:

            if self.ids_coinciden(
                estado_order_id,
                order_id_archivo
            ):

                self.datos_pedido = datos

                self.order_id_actual = (
                    order_id_archivo
                )

                self.pedido_coherente = True

                self.ultimo_mtime = (
                    self.archivo_pedido.stat().st_mtime
                )

            else:

                self.datos_pedido = None

                self.order_id_actual = (
                    estado_order_id
                )

                self.pedido_coherente = False

                self.ultimo_mtime = (
                    self.archivo_pedido.stat().st_mtime
                )

        else:

            self.datos_pedido = datos

            self.order_id_actual = (
                order_id_archivo
            )

            self.pedido_coherente = True

            self.ultimo_mtime = (
                self.archivo_pedido.stat().st_mtime
            )

            self.guardar_copia_recepcion_original(
                datos
            )

            self.guardar_estado_pedido(
                order_id_archivo,
                "PENDIENTE"
            )

        self.actualizar_interfaz()

    # ==========================================================
    # LECTURA PEDIDO ACTUAL
    # ==========================================================

    def leer_pedido_actual(self):

        if not self.archivo_pedido.exists():
            return None

        try:

            with open(
                self.archivo_pedido,
                "r",
                encoding="utf-8"
            ) as f:

                return json.load(f)

        except Exception:

            return None

    # ==========================================================
    # INTERFAZ
    # ==========================================================

    def actualizar_interfaz(self):

        estado = (
            self.obtener_estado_activo()
        )

        if not self.pedido_coherente:

            self.panel_pendiente.hide()
            self.boton_ver_pendiente.hide()
            self.tabla.hide()
            self.panel_correccion.hide()

            self.boton_corregir.setEnabled(
                False
            )

            self.boton_procesar.setEnabled(
                False
            )

            if self.order_id_actual is not None:

                self.estado_label.setText(
                    f"Estado: "
                    f"{estado or 'PENDIENTE'}"
                    f" · Pedido #{self.order_id_actual}"
                    f" no cargado"
                )

            else:

                self.estado_label.setText(
                    "Estado: —"
                )

            return

        pedido = self.datos_pedido.get(
            "pedido",
            self.datos_pedido
        )

        order_id = pedido.get(
            "orderId",
            ""
        )

        fecha = pedido.get(
            "fechaRecepcion",
            pedido.get("fechaInforme", "")
        )

        cine = pedido.get(
            "cine",
            ""
        )

        lineas = self.datos_pedido.get(
            "lineas",
            []
        )

        if not lineas:

            lineas = pedido.get(
                "lineas",
                []
            )

        if estado == "PENDIENTE":

            self.panel_pendiente.show()

            texto = (
                f"Pedido #{order_id}"
                f"  ·  {fecha}"
                f"  ·  {len(lineas)} líneas"
            )

            if cine:

                texto += (
                    f"  ·  {cine}"
                )

            self.info_pendiente.setText(
                texto
            )

            if self.pedido_mostrado:

                self.boton_ver_pendiente.hide()

            else:

                self.boton_ver_pendiente.show()

        else:

            self.panel_pendiente.hide()
            self.boton_ver_pendiente.hide()

        if self.pedido_mostrado:

            self.tabla.show()

            self.cargar_tabla()

        else:

            self.tabla.hide()

        pendiente = (
            estado == "PENDIENTE"
            and self.pedido_coherente
        )

        self.boton_corregir.setEnabled(
            pendiente
            and self.fila_seleccionada is not None
        )

        self.boton_procesar.setEnabled(
            pendiente
            and self.pedido_mostrado
        )

        if estado:

            self.estado_label.setText(
                f"Estado: {estado}"
                f"  ·  Pedido #{order_id}"
            )

        else:

            self.estado_label.setText(
                f"Estado: —"
                f"  ·  Pedido #{order_id}"
            )

    # ==========================================================
    # MOSTRAR PEDIDO
    # ==========================================================

    def mostrar_pedido(self):

        if not self.pedido_coherente:
            return

        if self.obtener_estado_activo() != "PENDIENTE":
            return

        self.pedido_mostrado = True

        self.cargar_tabla()

        self.tabla.show()

        self.boton_ver_pendiente.hide()

        self.actualizar_botones_estado()

    # ==========================================================
    # TABLA
    # ==========================================================

    def cargar_tabla(self):

        self.tabla.setRowCount(0)

        if not self.datos_pedido:
            return

        pedido = self.datos_pedido.get(
            "pedido",
            self.datos_pedido
        )

        lineas = self.datos_pedido.get(
            "lineas",
            []
        )

        if not lineas:

            lineas = pedido.get(
                "lineas",
                []
            )

        for i, linea in enumerate(lineas):

            self.tabla.insertRow(
                i
            )

            descripcion = linea.get(
                "descripcion",
                linea.get(
                    "nombre",
                    ""
                )
            )

            codigo = linea.get(
                "codigo",
                linea.get(
                    "idArticulo",
                    ""
                )
            )

            unidad = linea.get(
                "unidad",
                linea.get(
                    "um",
                    ""
                )
            )

            cantidad = linea.get(
                "cantidadPedida",
                linea.get(
                    "cantidad",
                    0
                )
            )

            valores = [
                descripcion,
                codigo,
                unidad,
                cantidad
            ]

            for j, valor in enumerate(
                valores
            ):

                item = QTableWidgetItem(
                    str(valor)
                )

                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter
                )

                self.tabla.setItem(
                    i,
                    j,
                    item
                )

    # ==========================================================
    # SELECCIÓN
    # ==========================================================

    def seleccion_cambiada(self):

        filas = (
            self.tabla
            .selectionModel()
            .selectedRows()
        )

        if not filas:

            self.fila_seleccionada = None

        else:

            self.fila_seleccionada = (
                filas[0].row()
            )

        self.actualizar_botones_estado()

    def actualizar_botones_estado(self):

        estado = (
            self.obtener_estado_activo()
        )

        pendiente = (
            estado == "PENDIENTE"
            and self.pedido_coherente
        )

        self.boton_corregir.setEnabled(
            pendiente
            and self.fila_seleccionada is not None
            and self.pedido_mostrado
        )

        self.boton_procesar.setEnabled(
            pendiente
            and self.pedido_mostrado
        )

    # ==========================================================
    # CORRECCIÓN
    # ==========================================================

    def mostrar_correccion(self):

        if self.fila_seleccionada is None:
            return

        if not self.datos_pedido:
            return

        pedido = self.datos_pedido.get(
            "pedido",
            self.datos_pedido
        )

        lineas = self.datos_pedido.get(
            "lineas",
            []
        )

        if not lineas:

            lineas = pedido.get(
                "lineas",
                []
            )

        if (
            self.fila_seleccionada < 0
            or self.fila_seleccionada >= len(lineas)
        ):
            return

        linea = lineas[
            self.fila_seleccionada
        ]

        descripcion = linea.get(
            "descripcion",
            linea.get(
                "nombre",
                ""
            )
        )

        codigo = linea.get(
            "codigo",
            linea.get(
                "idArticulo",
                ""
            )
        )

        unidad = linea.get(
            "unidad",
            linea.get(
                "um",
                ""
            )
        )

        cantidad = linea.get(
            "cantidadPedida",
            linea.get(
                "cantidad",
                0
            )
        )

        self.campo_producto.setText(
            str(descripcion)
        )

        self.campo_codigo.setText(
            str(codigo)
        )

        self.campo_um.clear()

        if unidad:

            self.campo_um.addItem(
                str(unidad)
            )

        self.campo_um.setCurrentText(
            str(unidad)
        )

        try:

            cantidad_int = int(
                float(cantidad)
            )

        except Exception:

            cantidad_int = 0

        self.campo_cantidad.setValue(
            cantidad_int
        )

        self.panel_correccion.show()

    def cancelar_correccion(self):

        self.panel_correccion.hide()

    def guardar_correccion(self):

        if self.fila_seleccionada is None:
            return

        if not self.datos_pedido:
            return

        pedido = self.datos_pedido.get(
            "pedido",
            self.datos_pedido
        )

        lineas = self.datos_pedido.get(
            "lineas",
            []
        )

        if not lineas:

            lineas = pedido.get(
                "lineas",
                []
            )

        if (
            self.fila_seleccionada < 0
            or self.fila_seleccionada >= len(lineas)
        ):
            return

        linea = lineas[
            self.fila_seleccionada
        ]

        cantidad_actual = linea.get(
            "cantidadPedida",
            linea.get(
                "cantidad",
                0
            )
        )

        if "cantidadOriginal" not in linea:

            linea["cantidadOriginal"] = (
                cantidad_actual
            )

        nueva_cantidad = (
            self.campo_cantidad.value()
        )

        nuevo_codigo = (
            self.campo_codigo
            .text()
            .strip()
        )

        nueva_um = (
            self.campo_um
            .currentText()
            .strip()
        )

        linea["cantidadPedida"] = (
            nueva_cantidad
        )

        if nuevo_codigo:

            linea["codigo"] = (
                nuevo_codigo
            )

        if nueva_um:

            linea["unidad"] = (
                nueva_um
            )

        linea["corregido"] = True

        self.datos_pedido[
            "lineas"
        ] = lineas

        try:

            with open(
                self.archivo_pedido,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    self.datos_pedido,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

            self.ultimo_mtime = (
                self.archivo_pedido.stat().st_mtime
            )

        except Exception as e:

            QMessageBox.critical(
                self,
                "Error",
                f"No se pudo guardar la corrección:\n{e}"
            )

            return

        self.cargar_tabla()

        self.panel_correccion.hide()

        QMessageBox.information(
            self,
            "Corrección guardada",
            "La corrección se ha guardado correctamente."
        )

    # ==========================================================
    # COMPROBAR PEDIDO NUEVO
    # ==========================================================

    def comprobar_pedido_nuevo(self):

        if not self.archivo_pedido.exists():
            return

        try:

            mtime = (
                self.archivo_pedido
                .stat()
                .st_mtime
            )

        except Exception:

            return

        if self.ultimo_mtime is None:

            self.ultimo_mtime = mtime

            return

        if mtime == self.ultimo_mtime:

            return

        self.ultimo_mtime = mtime

        datos_nuevos = (
            self.leer_pedido_actual()
        )

        if not datos_nuevos:

            return

        pedido_nuevo = datos_nuevos.get(
            "pedido",
            datos_nuevos
        )

        nuevo_order_id = (
            pedido_nuevo.get(
                "orderId"
            )
        )

        if self.ids_coinciden(
            nuevo_order_id,
            self.order_id_actual
        ):

            self.datos_pedido = (
                datos_nuevos
            )

            self.pedido_coherente = True

            if self.pedido_mostrado:

                self.cargar_tabla()

            self.actualizar_interfaz()

            return

        self.guardar_copia_recepcion_original(
            datos_nuevos
        )

        self.datos_pedido = (
            datos_nuevos
        )

        self.order_id_actual = (
            nuevo_order_id
        )

        self.pedido_coherente = True

        self.pedido_mostrado = False

        self.fila_seleccionada = None

        self.panel_correccion.hide()

        self.guardar_estado_pedido(
            nuevo_order_id,
            "PENDIENTE"
        )

        self.actualizar_interfaz()

        QMessageBox.information(
            self,
            "Nuevo pedido recibido",
            (
                f"Se ha recibido el pedido "
                f"Conway #{nuevo_order_id}.\n\n"
                "El pedido queda pendiente de revisión."
            )
        )

    # ==========================================================
    # COPIA ORIGINAL DE RECEPCIÓN
    # ==========================================================

    def guardar_copia_recepcion_original(
        self,
        datos
    ):

        if not datos:
            return

        pedido = datos.get(
            "pedido",
            datos
        )

        order_id = pedido.get(
            "orderId"
        )

        if order_id is None:
            return

        archivo = (
            self.recibidos_dir
            / f"pedido_{order_id}_original.json"
        )

        if archivo.exists():
            return

        try:

            with open(
                archivo,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    datos,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

        except Exception:

            pass

    # ==========================================================
    # LOCALIZAR EL ROBOT
    # ==========================================================

    def _ruta_robot(self):
        """Ruta al script del robot de carga en el ERP."""
        return self.base_dir / "src" / "robot_erp.py"

    def _python_para_robot(self):
        """
        Qué intérprete de Python usar para lanzar el robot.
        """
        ruta_env = os.environ.get("ROBOT_PYTHON")

        if ruta_env and Path(ruta_env).exists():
            return ruta_env

        if not getattr(sys, "frozen", False):
            return sys.executable

        return shutil.which("python") or shutil.which("py")

    # ==========================================================
    # ENVIAR A ERP
    # ==========================================================

    def enviar_a_erp(self):

        if not self.pedido_coherente:
            return

        estado = (
            self.obtener_estado_activo()
        )

        if estado != "PENDIENTE":
            return

        if not self.datos_pedido:
            return

        pedido = self.datos_pedido.get(
            "pedido",
            self.datos_pedido
        )

        order_id = pedido.get(
            "orderId"
        )

        if order_id is None:

            QMessageBox.warning(
                self,
                "Error",
                "El pedido no tiene número de pedido."
            )

            return

        respuesta = QMessageBox.question(
            self,
            "Enviar a ERP",
            (
                f"¿Quieres enviar el pedido Conway #{order_id} "
                "al robot de carga en el ERP?\n\n"
                "Se abrirá Chrome y se irán rellenando las líneas "
                "automáticamente. El robot NO pulsa Guardar: tendrás "
                "que revisar el pedido y guardarlo tú en el ERP."
            ),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )

        if (
            respuesta
            != QMessageBox.StandardButton.Yes
        ):
            return

        archivo_procesar = (
            self.procesamiento_dir
            / f"pedido_{order_id}.json"
        )

        datos_procesar = (
            self.datos_pedido.copy()
        )

        if "pedido" in datos_procesar:

            datos_procesar["pedido"] = (
                datos_procesar[
                    "pedido"
                ].copy()
            )

            datos_procesar[
                "pedido"
            ]["estado"] = (
                "ENVIADO A ERP"
            )

        else:

            datos_procesar[
                "estado"
            ] = "ENVIADO A ERP"

        try:

            with open(
                archivo_procesar,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    datos_procesar,
                    f,
                    ensure_ascii=False,
                    indent=2
                )

        except Exception as e:

            QMessageBox.critical(
                self,
                "Error",
                (
                    "No se pudo preparar el pedido "
                    f"para enviar:\n{e}"
                )
            )

            return

        # ----------------------------------------------------
        # LANZAR EL ROBOT (proceso aparte, no bloqueante)
        # ----------------------------------------------------

        robot_python = self._python_para_robot()
        ruta_robot = self._ruta_robot()

        if not robot_python or not ruta_robot.exists():

            QMessageBox.critical(
                self,
                "Robot no disponible",
                (
                    "No se encontró cómo lanzar el robot de carga en el ERP.\n\n"
                    f"Script esperado en: {ruta_robot}\n"
                    f"Python para el robot: {robot_python or 'no encontrado'}\n\n"
                    "El pedido ha quedado guardado en pedidos_para_procesar/ y "
                    "puedes ejecutar el robot a mano sobre ese archivo:\n\n"
                    f'python "{ruta_robot}" "{archivo_procesar}"\n\n'
                    "Si el robot vive en un entorno (venv) distinto del de "
                    "CineStock, define ROBOT_PYTHON en el archivo .env con la "
                    "ruta exacta a ese python.exe."
                )
            )

            self.guardar_estado_pedido(
                order_id,
                "PROCESANDO"
            )

            self.estado_actual = {
                "orderId": order_id,
                "estado": "PROCESANDO"
            }

            self.pedido_mostrado = True

            self.actualizar_interfaz()

            return

        try:

            subprocess.Popen(
                [
                    robot_python,
                    str(ruta_robot),
                    str(archivo_procesar),
                    "--auto",
                ],
                cwd=str(ruta_robot.parent)
            )

        except Exception as e:

            QMessageBox.critical(
                self,
                "Error",
                f"No se pudo lanzar el robot:\n{e}"
            )

            return

        self.guardar_estado_pedido(
            order_id,
            "ENVIADO A ERP"
        )

        self.estado_actual = {
            "orderId": order_id,
            "estado": "ENVIADO A ERP"
        }

        self.pedido_mostrado = True

        self.actualizar_interfaz()

        QMessageBox.information(
            self,
            "Robot en marcha",
            (
                f"Se ha lanzado el robot para el pedido Conway #{order_id}\n\n"
                "Se abrirá una ventana de Chrome e irá rellenando las líneas.\n"
                "Cuando termine, revisa el pedido y pulsa Guardar tú mismo en el ERP."
            )
        )

    # ==========================================================
    # HISTORIAL
    # ==========================================================

    def abrir_historial(self):

        dialogo = HistorialPedidosDialog(
            self.historial_dir,
            self
        )

        dialogo.exec()

    # ==========================================================
    # VISUALIZACIÓN DE INFORMES (PDFs RECIBIDOS)
    # ==========================================================

    def _buscar_pdf_pedido(self, sufijo=""):
        """
        Busca el PDF asociado al pedido actual en la carpeta de PDFs.
        """
        if not self.order_id_actual:
            return None

        pdf_dir = self.base_dir / "data" / "pdf_pedidos"
        if not pdf_dir.exists():
            # Si no existe la carpeta, avisamos para debug
            print(f"DEBUG: La carpeta de PDFs no existe en: {pdf_dir}")
            return None

        order_id_str = str(self.order_id_actual).lower()

        # Buscamos archivos que contengan el order_id (ignorando mayúsculas) y el sufijo
        for archivo in pdf_dir.glob("*.pdf"):
            nombre_archivo = archivo.name.lower()
            if order_id_str in nombre_archivo and nombre_archivo.endswith(f"{sufijo}.pdf"):
                return archivo

        # Si llegamos aquí, no se encontró. Imprimimos en consola para debug.
        print(f"DEBUG: No se encontró PDF para ID {self.order_id_actual} con sufijo '{sufijo}' en {pdf_dir}")
        print(f"DEBUG: Archivos disponibles en la carpeta: {[f.name for f in pdf_dir.glob('*.pdf')]}")

        return None

    def descargar_pdf(self, archivo_pdf, nombre_descarga):
        """Descarga el PDF a la carpeta 'descargas'."""
        from pathlib import Path
        
        # Crear carpeta de descargas si no existe
        descargas_dir = self.base_dir / "data" / "descargas"
        descargas_dir.mkdir(parents=True, exist_ok=True)
        
        # Ruta final del archivo descargado
        ruta_final = descargas_dir / nombre_descarga
        
        # Copiar el archivo
        try:
            shutil.copy(archivo_pdf, ruta_final)
            QMessageBox.information(
                self,
                "Descarga exitosa",
                f"PDF descargado a:\n{ruta_final}"
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"No se pudo descargar el PDF:\n{e}"
            )

    def abrir_pdf_stockable(self):
        """Abre el PDF normal (Stockable) recibido en el visor interno de la aplicación."""
        archivo = self._buscar_pdf_pedido(sufijo="")
        if archivo and archivo.exists():
            try:
                dialogo = VisorPDFDialog(archivo, titulo="Informe Stockable", parent=self)
                dialogo.exec()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"No se pudo abrir el PDF Stockable:\n{e}")
        else:
            QMessageBox.warning(self, "No encontrado", "No se ha recibido el PDF Stockable para este pedido.")

    def abrir_pdf_granel(self):
        """Abre el PDF de Granel recibido en el visor interno de la aplicación."""
        archivo = self._buscar_pdf_pedido(sufijo="_granel")
        if archivo and archivo.exists():
            try:
                dialogo = VisorPDFDialog(archivo, titulo="Informe de Granel", parent=self)
                dialogo.exec()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"No se pudo abrir el PDF de Granel:\n{e}")
        else:
            QMessageBox.warning(self, "No encontrado", "No se ha recibido el PDF de Granel para este pedido")

    def descargar_stockable(self):
        """Descarga el PDF Stockable."""
        archivo = self._buscar_pdf_pedido(sufijo="")
        if archivo and archivo.exists():
            nombre_archivo = f"stockable_{self.order_id_actual}.pdf"
            self.descargar_pdf(archivo, nombre_archivo)
        else:
            QMessageBox.warning(
                self,
                "No encontrado",
                "No se ha recibido el PDF Stockable para este pedido."
            )

    def descargar_granel(self):
        """Descarga el PDF de Granel."""
        archivo = self._buscar_pdf_pedido(sufijo="_granel")
        if archivo and archivo.exists():
            nombre_archivo = f"granel_{self.order_id_actual}.pdf"
            self.descargar_pdf(archivo, nombre_archivo)
        else:
            QMessageBox.warning(
                self,
                "No encontrado",
                "No se ha recibido el PDF de Granel para este pedido."
            )