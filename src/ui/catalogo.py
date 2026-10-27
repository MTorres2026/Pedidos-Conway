# -*- coding: utf-8 -*-
"""
Pantalla de edición del catálogo SUPERMAESTRO (data/supermaestro.json).

Puerta trasera para mantenimiento de productos:
- Corregir ID ERP y UM ERP de cualquier producto (doble clic en la fila).
- Añadir productos nuevos (botón "+ NUEVO PRODUCTO").
- Buscar por nombre, ID VISTA o ID ERP.
- Guardar escribe el JSON que el receptor usa para AUTO-CORREGIR los
  pedidos entrantes (sobrescribe idErp/umErp si Flow trae datos viejos)
  y expone en GET /supermaestro para que Flow pueda descargarlo.

IMPORTANTE (2026-10-27): antes «Guardar» solo escribía data/supermaestro.json,
pero el receptor leía data/supermaestro_flow.json, que no existía. Ahora se
escriben LOS DOS a la vez. Además, las correcciones verificadas en el ERP
(src/recursos/correcciones_erp.json, SÍ versionadas en Git) tienen prioridad
sobre lo que haya en data/: ver src/catalogo_erp.py.

Columnas del JSON (se conservan tal cual): GRUPO, TIPO REGISTRO,
PRODUCTO, ID VISTA, ID ERP, UM ERP, ID CONWAY, DENOMINACIÓN LARGA
CONWAY, PRESENTACIÓN, UNIDADES POR CAJA/PACK, UNIDAD PEDIDO FLOW,
ESTADO, FUENTE, NOTA / REGLA.
"""

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
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
    QWidget,
)

# Columnas visibles en la tabla (clave del JSON -> texto de cabecera)
COLUMNAS = [
    ("GRUPO", "Grupo"),
    ("PRODUCTO", "Producto"),
    ("ID VISTA", "ID Vista"),
    ("ID ERP", "ID ERP"),
    ("UM ERP", "UM ERP"),
    ("ESTADO", "Estado"),
]

# Todas las claves editables en el diálogo (clave JSON -> etiqueta)
CAMPOS_EDITABLES = [
    ("GRUPO", "Grupo"),
    ("TIPO REGISTRO", "Tipo registro"),
    ("PRODUCTO", "Producto"),
    ("ID VISTA", "ID Vista"),
    ("ID ERP", "ID ERP"),
    ("UM ERP", "UM ERP"),
    ("ID CONWAY", "ID Conway"),
    ("DENOMINACIÓN LARGA CONWAY", "Denominación larga Conway"),
    ("PRESENTACIÓN", "Presentación"),
    ("UNIDADES POR CAJA/PACK", "Unidades por caja/pack"),
    ("UNIDAD PEDIDO FLOW", "Unidad pedido Flow"),
    ("ESTADO", "Estado"),
    ("FUENTE", "Fuente"),
    ("NOTA / REGLA", "Nota / regla"),
]


class DialogoProducto(QDialog):
    """Diálogo para editar o crear un producto del catálogo."""

    def __init__(self, producto, titulo, parent=None):
        super().__init__(parent)
        self.producto = producto
        self.setWindowTitle(titulo)
        self.resize(520, 640)

        layout = QVBoxLayout(self)
        formulario = QFormLayout()

        self.campos = {}
        for clave, etiqueta in CAMPOS_EDITABLES:
            campo = QLineEdit(str(producto.get(clave, "") or ""))
            self.campos[clave] = campo
            formulario.addRow(etiqueta + ":", campo)

        layout.addLayout(formulario)

        botones = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        botones.button(
            QDialogButtonBox.StandardButton.Save
        ).setText("GUARDAR")
        botones.button(
            QDialogButtonBox.StandardButton.Cancel
        ).setText("CANCELAR")
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        layout.addWidget(botones)

    def obtener_producto(self):
        resultado = dict(self.producto)  # conserva claves no editadas
        for clave, campo in self.campos.items():
            resultado[clave] = campo.text().strip()
        return resultado


class Catalogo(QWidget):
    """Pantalla de gestión del catálogo SUPERMAESTRO."""

    # 1. Declarar la señal para volver a Inicio al cerrar
    cerrado = Signal()

    def __init__(self):
        super().__init__()

        self.ruta_catalogo = (
            Path(__file__).resolve().parents[2]
            / "data"
            / "supermaestro.json"
        )
        # El nombre histórico que leía el receptor. Se mantiene sincronizado
        # para que nunca vuelva a pasarnos lo del pedido de 100 líneas.
        self.ruta_catalogo_flow = self.ruta_catalogo.with_name(
            "supermaestro_flow.json"
        )
        self.productos = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        titulo = QLabel("CATÁLOGO SUPERMAESTRO")
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        titulo.setStyleSheet(
            "QLabel { font-size: 18px; font-weight: bold; "
            "padding: 6px; }"
        )
        layout.addWidget(titulo)

        # ------------------------------------------------------------
        # BARRA DE BÚSQUEDA + BOTONES
        # ------------------------------------------------------------
        barra = QHBoxLayout()
        self.campo_buscar = QLineEdit()
        self.campo_buscar.setPlaceholderText(
            "Buscar por producto, ID Vista, ID ERP o UM..."
        )
        self.campo_buscar.setMinimumHeight(36)
        self.campo_buscar.textChanged.connect(self.filtrar)
        barra.addWidget(self.campo_buscar)

        boton_nuevo = QPushButton("➕ NUEVO PRODUCTO")
        boton_nuevo.setMinimumHeight(36)
        boton_nuevo.clicked.connect(self.nuevo_producto)
        barra.addWidget(boton_nuevo)

        boton_guardar = QPushButton("💾 GUARDAR CATÁLOGO")
        boton_guardar.setMinimumHeight(36)
        boton_guardar.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; "
            "border-radius: 6px; padding: 6px 14px; font-weight: bold; }"
            "QPushButton:hover { background-color: #256428; }"
        )
        boton_guardar.clicked.connect(self.guardar)
        barra.addWidget(boton_guardar)
        layout.addLayout(barra)

        self.etiqueta_estado = QLabel("")
        self.etiqueta_estado.setStyleSheet("color: #455a64;")
        layout.addWidget(self.etiqueta_estado)

        # ------------------------------------------------------------
        # TABLA
        # ------------------------------------------------------------
        self.tabla = QTableWidget()
        self.tabla.setColumnCount(len(COLUMNAS))
        self.tabla.setHorizontalHeaderLabels(
            [texto for _, texto in COLUMNAS]
        )
        self.tabla.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tabla.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tabla.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tabla.setAlternatingRowColors(True)
        self.tabla.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.tabla.doubleClicked.connect(self.editar_producto)
        layout.addWidget(self.tabla)

        boton_cerrar = QPushButton("CERRAR")
        boton_cerrar.clicked.connect(self.cerrar)  # Conecta al método cerrar
        boton_cerrar.setMinimumHeight(36)
        layout.addWidget(boton_cerrar)

        self.cargar()

    # ============================================================
    # CERRAR CATALOGO
    # ============================================================

    def cerrar(self):
        """Ocultar la pantalla del catálogo y emitir la señal para volver a Inicio"""
        self.hide()
        self.cerrado.emit()

    # ============================================================
    # CARGA / GUARDADO
    # ============================================================

    def cargar(self):
        """Lee data/supermaestro.json y pinta la tabla."""
        self.productos = []

        ruta = self.ruta_catalogo
        if not ruta.exists() and self.ruta_catalogo_flow.exists():
            ruta = self.ruta_catalogo_flow
        if ruta.exists():
            try:
                datos = json.loads(
                    ruta.read_text(encoding="utf-8")
                )
                # Formato {"SUPERMAESTRO": [...]} o lista directa
                if isinstance(datos, dict):
                    self.productos = datos.get("SUPERMAESTRO", [])
                elif isinstance(datos, list):
                    self.productos = datos
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Error",
                    f"No se pudo leer el catálogo:\n{e}"
                )
                return
        else:
            QMessageBox.warning(
                self,
                "Catálogo no encontrado",
                "No existe data/supermaestro.json.\n\n"
                "Copia ahí el JSON del catálogo SUPERMAESTRO "
                "(el mismo que Flow tiene como asset) para poder "
                "editarlo desde aquí."
            )

        self.etiqueta_estado.setText(
            f"{len(self.productos)} productos · "
            f"{self.ruta_catalogo.name}"
        )
        self.cargar_tabla()

    def guardar(self):
        """Escribe el catálogo en LOS DOS nombres que puede leer el receptor.

        data/supermaestro.json       -> el que edita esta pantalla
        data/supermaestro_flow.json  -> el nombre histórico del receptor

        Escribir los dos evita que una corrección hecha aquí no llegue al
        pedido (fallo detectado en el pedido de 100 líneas del 27/10/2026).
        """
        try:
            self.ruta_catalogo.parent.mkdir(
                parents=True,
                exist_ok=True
            )
            texto = json.dumps(
                {"SUPERMAESTRO": self.productos},
                indent=2,
                ensure_ascii=False
            )
            self.ruta_catalogo.write_text(texto, encoding="utf-8")
            self.ruta_catalogo_flow.write_text(texto, encoding="utf-8")
            QMessageBox.information(
                self,
                "Catálogo guardado",
                "Catálogo actualizado en los dos archivos que lee el receptor.\n\n"
                "El receptor lo aplicará automáticamente a los pedidos que "
                "lleguen desde ahora (corrigiendo ID ERP / UM si Flow trae "
                "datos desactualizados).\n\n"
                "Recuerda: las correcciones ya verificadas en el ERP "
                "(src/recursos/correcciones_erp.json) tienen prioridad y SÍ "
                "se suben a Git para que lleguen al PC del cine."
            )
            self.etiqueta_estado.setText(
                f"{len(self.productos)} productos · guardado ✓ (2 archivos)"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"No se pudo guardar el catálogo:\n{e}"
            )

    # ============================================================
    # TABLA
    # ============================================================

    def cargar_tabla(self):
        self.tabla.setRowCount(0)
        texto = self.campo_buscar.text().strip().lower()

        for producto in self.productos:
            if texto and not self._coincide(producto, texto):
                continue

            fila = self.tabla.rowCount()
            self.tabla.insertRow(fila)

            for columna, (clave, _) in enumerate(COLUMNAS):
                valor = str(producto.get(clave, "") or "")
                item = QTableWidgetItem(valor)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter
                )
                # Guardamos la posición en el JSON para editarla
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    self.productos.index(producto)
                )
                self.tabla.setItem(fila, columna, item)

    def _coincide(self, producto, texto):
        """True si el producto contiene el texto en campos clave."""
        campos = (
            producto.get("PRODUCTO", ""),
            producto.get("ID VISTA", ""),
            producto.get("ID ERP", ""),
            producto.get("UM ERP", ""),
            producto.get("GRUPO", ""),
        )
        return any(
            texto in str(c or "").lower() for c in campos
        )

    def filtrar(self, _texto):
        self.cargar_tabla()

    # ============================================================
    # EDICIÓN / ALTA DE PRODUCTOS
    # ============================================================

    def _indice_fila(self):
        fila = self.tabla.currentRow()
        if fila < 0:
            return None
        item = self.tabla.item(fila, 0)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def editar_producto(self):
        indice = self._indice_fila()
        if indice is None:
            QMessageBox.information(
                self,
                "Editar",
                "Selecciona un producto de la tabla."
            )
            return

        dialogo = DialogoProducto(
            self.productos[indice],
            "Editar producto",
            parent=self
        )
        if dialogo.exec():
            self.productos[indice] = (
                dialogo.obtener_producto()
            )
            self.cargar_tabla()

    def nuevo_producto(self):
        vacio = {
            "GRUPO": "",
            "TIPO REGISTRO": "PRODUCTO",
            "PRODUCTO": "",
            "ID VISTA": "",
            "ID ERP": "",
            "UM ERP": "",
            "ID CONWAY": "—",
            "DENOMINACIÓN LARGA CONWAY": "—",
            "PRESENTACIÓN": "—",
            "UNIDADES POR CAJA/PACK": "—",
            "UNIDAD PEDIDO FLOW": "CAJA",
            "ESTADO": "CONFIRMADO",
            "FUENTE": "EDITADO_EN_WINDOWS",
            "NOTA / REGLA": "Añadido desde la pantalla de catálogo.",
        }
        dialogo = DialogoProducto(
            vacio,
            "Nuevo producto",
            parent=self
        )
        if dialogo.exec():
            nuevo = dialogo.obtener_producto()
            if not nuevo.get("PRODUCTO"):
                QMessageBox.warning(
                    self,
                    "Falta el nombre",
                    "El campo Producto es obligatorio."
                )
                return
            self.productos.append(nuevo)
            self.cargar_tabla()
            QMessageBox.information(
                self,
                "Producto añadido",
                "Producto añadido al catálogo en memoria.\n\n"
                "Recuerda pulsar GUARDAR CATÁLOGO para que los "
                "cambios se apliquen a los pedidos."
            )