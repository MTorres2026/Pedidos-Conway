from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QTableWidget,
    QHeaderView
)

from pedido import Pedido


class Pedidos(QWidget):

    def __init__(self):
        super().__init__()

        self.pedido_actual = None

        layout = QVBoxLayout(self)

        titulo = QLabel("📦 Pedidos Conway")
        titulo.setStyleSheet("""
            font-size: 24px;
            font-weight: bold;
        """)

        layout.addWidget(titulo)

        boton_nuevo = QPushButton("➕ Nuevo pedido")
        layout.addWidget(boton_nuevo)

        boton_nuevo.clicked.connect(self.nuevo_pedido)

        self.formulario = QWidget()
        formulario_layout = QVBoxLayout(self.formulario)

        nombre_layout = QHBoxLayout()

        nombre_layout.addWidget(QLabel("Nombre del pedido:"))

        self.nombre_pedido = QLineEdit()
        self.nombre_pedido.setPlaceholderText(
            "Ejemplo: conway24/8/26"
        )

        nombre_layout.addWidget(self.nombre_pedido)

        formulario_layout.addLayout(nombre_layout)

        self.boton_comenzar = QPushButton("Comenzar pedido")
        formulario_layout.addWidget(self.boton_comenzar)

        self.boton_comenzar.clicked.connect(
            self.comenzar_pedido
        )

        self.formulario.hide()
        layout.addWidget(self.formulario)

        self.tabla = QTableWidget()

        self.tabla.setColumnCount(4)

        self.tabla.setHorizontalHeaderLabels([
            "Producto",
            "Artículo ERP",
            "UM",
            "Cantidad"
        ])

        self.tabla.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )

        layout.addWidget(self.tabla)

    def nuevo_pedido(self):
        self.formulario.show()
        self.nombre_pedido.clear()
        self.tabla.setRowCount(0)
        self.pedido_actual = None
        self.nombre_pedido.setFocus()

    def comenzar_pedido(self):
        nombre = self.nombre_pedido.text().strip()

        if not nombre:
            self.nombre_pedido.setFocus()
            return

        self.pedido_actual = Pedido(nombre)

        self.tabla.setRowCount(0)

        print("Nuevo pedido Conway:", nombre)
        print("Pedido preparado.")