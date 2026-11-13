"""Diálogo para montar un pedido Conway desde el escritorio."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from modulos.informes_pedido import (
    ErrorInformePedido,
    generar_granel_rellenado,
    generar_stockable_rellenado,
    ruta_plantilla_granel,
    validar_stockable,
)
from modulos.pedido_manual import (
    ErrorPedidoManual,
    cargar_catalogo,
    normalizar_cantidad,
)


class PedidoManualDialog(QDialog):
    """Selecciona productos y cantidades leyendo el informe Stockable."""

    def __init__(self, ruta_data, parent=None):
        super().__init__(parent)
        self.ruta_data = Path(ruta_data)
        self.ruta_stockable = None
        self.pdf_stockable = None
        self.pdf_granel = None
        self.productos = []
        self.lineas = []
        self.error_catalogo = ""

        try:
            self.productos = cargar_catalogo(ruta_data)
        except (OSError, ErrorPedidoManual) as exc:
            self.error_catalogo = str(exc)

        self.setWindowTitle("Nuevo pedido Conway · oficina")
        self.resize(1060, 700)
        self.setMinimumSize(820, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        titulo = QLabel("CREAR PEDIDO MANUAL")
        titulo.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(titulo)

        ayuda = QLabel(
            "Carga el Stockable actual y añade los productos uno a uno. "
            "Al crear el pedido, CineStock escribirá la cantidad pedida en "
            "la columna «Stocktake Count» de cada producto, como hace la "
            "tablet. El resto del informe no se modifica."
        )
        ayuda.setWordWrap(True)
        ayuda.setStyleSheet(
            "background-color: #eef5fb; padding: 9px; border-radius: 5px;"
        )
        layout.addWidget(ayuda)

        barra_stockable = QHBoxLayout()
        self.boton_cargar_stockable = QPushButton("📄 CARGAR STOCKABLE")
        self.boton_cargar_stockable.setMinimumHeight(36)
        self.boton_cargar_stockable.clicked.connect(self._seleccionar_stockable)
        barra_stockable.addWidget(self.boton_cargar_stockable)
        self.etiqueta_stockable = QLabel("No se ha cargado el PDF Stockable")
        self.etiqueta_stockable.setWordWrap(True)
        self.etiqueta_stockable.setStyleSheet("color: #b00020;")
        barra_stockable.addWidget(self.etiqueta_stockable, 1)
        layout.addLayout(barra_stockable)

        aviso_granel = QLabel(
            "Granel: la plantilla oficial está integrada. Si añades productos "
            "publicados en ella, CineStock generará y asociará también ese informe."
        )
        aviso_granel.setWordWrap(True)
        aviso_granel.setStyleSheet("color: #2e7d32;")
        layout.addWidget(aviso_granel)

        self.etiqueta_catalogo = QLabel()
        self.etiqueta_catalogo.setWordWrap(True)
        if self.error_catalogo:
            self.etiqueta_catalogo.setText(
                "No se puede crear el pedido porque no está disponible un "
                "catálogo de productos válido.\n" + self.error_catalogo
            )
            self.etiqueta_catalogo.setStyleSheet("color: #b00020;")
        else:
            self.etiqueta_catalogo.setText(
                f"Catálogo listo · {len(self.productos)} productos con código "
                "y unidad ERP"
            )
            self.etiqueta_catalogo.setStyleSheet("color: #455a64;")
        layout.addWidget(self.etiqueta_catalogo)

        layout.addWidget(QLabel("1. Busca y añade productos"))
        self.buscar = QLineEdit()
        self.buscar.setPlaceholderText(
            "Buscar por producto, grupo, ID Vista, código ERP o unidad…"
        )
        self.buscar.textChanged.connect(self._pintar_catalogo)
        layout.addWidget(self.buscar)

        self.tabla_catalogo = QTableWidget()
        self.tabla_catalogo.setColumnCount(5)
        self.tabla_catalogo.setHorizontalHeaderLabels(
            ["Producto", "Grupo", "ID Vista", "Artículo ERP", "UM"]
        )
        self.tabla_catalogo.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tabla_catalogo.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tabla_catalogo.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tabla_catalogo.setAlternatingRowColors(True)
        self.tabla_catalogo.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for columna in (1, 2, 3, 4):
            self.tabla_catalogo.horizontalHeader().setSectionResizeMode(
                columna, QHeaderView.ResizeMode.ResizeToContents
            )
        self.tabla_catalogo.doubleClicked.connect(self._agregar_seleccionado)
        layout.addWidget(self.tabla_catalogo, 3)

        barra_agregar = QHBoxLayout()
        formulario_cantidad = QFormLayout()
        self.cantidad = QDoubleSpinBox()
        self.cantidad.setRange(0.001, 999999)
        self.cantidad.setDecimals(3)
        self.cantidad.setSingleStep(1)
        self.cantidad.setValue(1)
        formulario_cantidad.addRow("Cantidad:", self.cantidad)
        barra_agregar.addLayout(formulario_cantidad)
        barra_agregar.addStretch()

        self.boton_agregar = QPushButton("➕ AÑADIR SELECCIONADO")
        self.boton_agregar.setMinimumHeight(36)
        self.boton_agregar.clicked.connect(self._agregar_seleccionado)
        self.boton_agregar.setEnabled(bool(self.productos))
        barra_agregar.addWidget(self.boton_agregar)
        layout.addLayout(barra_agregar)

        titulo_pedido = QHBoxLayout()
        titulo_pedido.addWidget(QLabel("2. Revisa las líneas del pedido"))
        titulo_pedido.addStretch()
        self.resumen = QLabel("0 productos")
        titulo_pedido.addWidget(self.resumen)
        layout.addLayout(titulo_pedido)

        self.tabla_pedido = QTableWidget()
        self.tabla_pedido.setColumnCount(4)
        self.tabla_pedido.setHorizontalHeaderLabels(
            ["Producto", "Artículo ERP", "UM", "Cantidad"]
        )
        self.tabla_pedido.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tabla_pedido.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tabla_pedido.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tabla_pedido.setAlternatingRowColors(True)
        self.tabla_pedido.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for columna in (1, 2, 3):
            self.tabla_pedido.horizontalHeader().setSectionResizeMode(
                columna, QHeaderView.ResizeMode.ResizeToContents
            )
        layout.addWidget(self.tabla_pedido, 2)

        barra_pedido = QHBoxLayout()
        self.boton_quitar = QPushButton("🗑 QUITAR LÍNEA")
        self.boton_quitar.clicked.connect(self._quitar_seleccionado)
        barra_pedido.addWidget(self.boton_quitar)
        barra_pedido.addStretch()

        self.boton_cancelar = QPushButton("CANCELAR")
        self.boton_cancelar.setMinimumHeight(40)
        self.boton_cancelar.clicked.connect(self.reject)
        barra_pedido.addWidget(self.boton_cancelar)

        self.boton_crear = QPushButton("CREAR PEDIDO PENDIENTE")
        self.boton_crear.setMinimumHeight(40)
        self.boton_crear.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; "
            "font-weight: bold; padding: 7px 14px; border-radius: 5px; }"
            "QPushButton:hover { background-color: #256428; }"
        )
        self.boton_crear.clicked.connect(self._confirmar)
        barra_pedido.addWidget(self.boton_crear)
        layout.addLayout(barra_pedido)

        self._pintar_catalogo()
        self._pintar_lineas()

    def _pintar_catalogo(self, _texto=None):
        consulta = self.buscar.text().strip().casefold()
        self.tabla_catalogo.setRowCount(0)

        for producto in self.productos:
            campos = (
                producto["descripcion"],
                producto["grupo"],
                producto.get("idVista", ""),
                producto["codigo"],
                producto["unidad"],
                producto["presentacion"],
            )
            if consulta and not any(consulta in campo.casefold() for campo in campos):
                continue

            fila = self.tabla_catalogo.rowCount()
            self.tabla_catalogo.insertRow(fila)
            valores = (
                producto["descripcion"],
                producto["grupo"],
                producto.get("idVista") or "—",
                producto["codigo"],
                producto["unidad"],
            )
            for columna, valor in enumerate(valores):
                item = QTableWidgetItem(str(valor))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter
                    | (Qt.AlignmentFlag.AlignLeft if columna == 0 else Qt.AlignmentFlag.AlignCenter)
                )
                if columna == 0:
                    item.setData(Qt.ItemDataRole.UserRole, producto)
                self.tabla_catalogo.setItem(fila, columna, item)

    def _agregar_seleccionado(self, *_args):
        fila = self.tabla_catalogo.currentRow()
        if fila < 0:
            QMessageBox.information(
                self, "Añadir producto", "Selecciona un producto del catálogo."
            )
            return

        item = self.tabla_catalogo.item(fila, 0)
        producto = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(producto, dict):
            return

        cantidad = normalizar_cantidad(self.cantidad.value())
        existente = next(
            (
                linea for linea in self.lineas
                if linea["codigo"] == producto["codigo"]
                and linea["unidad"] == producto["unidad"]
                and linea.get("idVista", "") == producto.get("idVista", "")
            ),
            None,
        )
        if existente is None:
            self.lineas.append(
                {
                    "codigo": producto["codigo"],
                    "idVista": producto["idVista"],
                    "descripcion": producto["descripcion"],
                    "unidad": producto["unidad"],
                    "cantidad": cantidad,
                }
            )
        else:
            existente["cantidad"] = normalizar_cantidad(
                existente["cantidad"] + cantidad
            )
        self.pdf_stockable = None
        self._pintar_lineas()

    def _pintar_lineas(self):
        self.tabla_pedido.setRowCount(0)
        for fila, linea in enumerate(self.lineas):
            self.tabla_pedido.insertRow(fila)
            for columna, clave in enumerate(("descripcion", "codigo", "unidad")):
                item = QTableWidgetItem(str(linea[clave]))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignVCenter
                    | (Qt.AlignmentFlag.AlignLeft if columna == 0 else Qt.AlignmentFlag.AlignCenter)
                )
                self.tabla_pedido.setItem(fila, columna, item)

            cantidad = QDoubleSpinBox()
            cantidad.setRange(0.001, 999999)
            cantidad.setDecimals(3)
            cantidad.setSingleStep(1)
            cantidad.setValue(float(linea["cantidad"]))
            cantidad.valueChanged.connect(
                lambda valor, indice=fila: self._cambiar_cantidad(indice, valor)
            )
            self.tabla_pedido.setCellWidget(fila, 3, cantidad)

        self.resumen.setText(f"{len(self.lineas)} líneas")
        self._actualizar_boton_crear()

    def _actualizar_boton_crear(self):
        self.boton_crear.setEnabled(bool(self.lineas) and self.ruta_stockable is not None)

    def _seleccionar_stockable(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar informe Stockable",
            str(self.ruta_data.parent),
            "Archivos PDF (*.pdf)",
        )
        if not ruta:
            return
        archivo = Path(ruta)
        if not archivo.is_file() or archivo.suffix.lower() != ".pdf":
            QMessageBox.warning(
                self,
                "Stockable no válido",
                "Selecciona un archivo PDF Stockable válido.",
            )
            return
        try:
            posiciones = validar_stockable(archivo)
        except ErrorInformePedido as exc:
            QMessageBox.warning(
                self,
                "Stockable no válido",
                str(exc),
            )
            return

        self.ruta_stockable = archivo
        self.pdf_stockable = None
        self.etiqueta_stockable.setText(
            f"Cargado: {archivo.name} ({len(posiciones)} productos detectados)"
        )
        self.etiqueta_stockable.setStyleSheet("color: #2e7d32;")
        self._actualizar_boton_crear()

    def _cambiar_cantidad(self, fila, valor):
        if 0 <= fila < len(self.lineas):
            self.lineas[fila]["cantidad"] = normalizar_cantidad(valor)
            self.pdf_stockable = None

    def _quitar_seleccionado(self):
        fila = self.tabla_pedido.currentRow()
        if fila < 0:
            return
        del self.lineas[fila]
        self.pdf_stockable = None
        self._pintar_lineas()

    def _confirmar(self):
        if not self.lineas:
            QMessageBox.information(
                self, "Pedido vacío", "Añade al menos un producto al pedido."
            )
            return
        if self.ruta_stockable is None:
            QMessageBox.warning(
                self,
                "Falta el Stockable",
                "Carga el PDF Stockable actual para poder rellenar y guardar el pedido.",
            )
            return
        try:
            self.pdf_stockable = generar_stockable_rellenado(
                self.ruta_stockable,
                self.lineas,
            )
            self.pdf_granel = generar_granel_rellenado(
                ruta_plantilla_granel(),
                self.lineas,
            )
        except (ErrorInformePedido, ErrorPedidoManual, OSError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "No se pueden generar los informes",
                str(exc),
            )
            self.pdf_stockable = None
            self.pdf_granel = None
            return
        self.accept()

    def lineas_pedido(self) -> list[dict]:
        """Copia las líneas para que el llamador construya el pedido final."""
        return [dict(linea) for linea in self.lineas]
