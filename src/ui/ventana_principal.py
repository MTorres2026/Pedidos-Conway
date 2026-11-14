from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QListWidget,
    QLabel,
    QVBoxLayout
)

from modulos.procedimientos import buscar_procedimiento

from ui.inicio import Inicio
from ui.pedidos import Pedidos, HistorialPedidosDialog
from ui.configuracion import Configuracion
from ui.conocimiento import Conocimiento
from ui.catalogo import Catalogo
from pathlib import Path
from ui.registro_pedidos import RegistroPedidosDialog

BASE_DIR = Path(__file__).resolve().parents[2]

class VentanaPrincipal(QMainWindow):

    def __init__(self):
        super().__init__()

        self.base_dir = Path(__file__).resolve().parents[2]
        self.setWindowTitle("CineStock")
        self.resize(1200, 700)

        contenedor = QWidget()
        contenedor.setObjectName("raizCineStock")
        self.setCentralWidget(contenedor)
        self.setStyleSheet("""
            QMainWindow, QWidget#raizCineStock, QWidget#zonaContenido {
                background-color: #383838;
                color: #eeeeee;
            }
            QWidget#zonaContenido QWidget {
                background-color: #383838;
                color: #eeeeee;
            }
            QLabel {
                background-color: transparent;
                color: #eeeeee;
            }
            QPushButton {
                background-color: #303030;
                color: #eeeeee;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 5px 8px;
            }
            QPushButton:hover {
                background-color: #484848;
            }
            QLineEdit, QTextEdit, QTextBrowser, QTableWidget, QTableView,
            QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #292929;
                color: #eeeeee;
                border: 1px solid #555555;
                selection-background-color: #316c73;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #252525;
                color: #eeeeee;
                border: 1px solid #494949;
                padding: 4px;
            }
            QListWidget#menuLateral {
                background-color: #242424;
                color: #f5f6f7;
                border: none;
                outline: none;
                padding: 8px 0;
            }
            QListWidget#menuLateral::item {
                padding: 11px 12px;
                border-left: 4px solid transparent;
            }
            QListWidget#menuLateral::item:hover {
                background-color: #414141;
            }
            QListWidget#menuLateral::item:selected {
                background-color: #3c666b;
                color: #ffffff;
                border-left: 4px solid #36a0a8;
                font-weight: bold;
            }
        """)

        layout = QHBoxLayout(contenedor)

        # ===== Menú lateral =====
        self.menu = QListWidget()
        self.menu.setObjectName("menuLateral")
        self.menu.setFixedWidth(220)

        self.menu.addItem("🏠 Inicio")          # Índice 0
        self.menu.addItem("📦 Pedidos")         # Índice 1
        self.menu.addItem("📊 Historial")       # Índice 2
        self.menu.addItem("⚙️ Configuración")   # Índice 3
        self.menu.addItem("📚 Conocimiento")
        self.menu.addItem("🔖 Registro de pedidos")
        
        self.menu.addItem("📖 Catálogo")        # Índice 4

        # ===== Zona principal =====
        contenido = QWidget()
        contenido.setObjectName("zonaContenido")
        layout_contenido = QVBoxLayout(contenido)

        self.inicio = Inicio()
        layout_contenido.addWidget(self.inicio)

        self.pedidos = Pedidos()
        layout_contenido.addWidget(self.pedidos)
        self.pedidos.hide()

        self.configuracion = Configuracion()
        layout_contenido.addWidget(self.configuracion)
        self.configuracion.hide()
        self.conocimiento = Conocimiento()
        layout_contenido.addWidget(self.conocimiento)
        self.conocimiento.hide()

        self.catalogo = Catalogo()
        layout_contenido.addWidget(self.catalogo)
        self.catalogo.hide()

        # Dentro de __init__ de VentanaPrincipal, después de crear self.catalogo:
        self.catalogo.cerrado.connect(lambda: self.menu.setCurrentRow(0))

        layout.addWidget(self.menu)
        layout.addWidget(contenido)

        self.menu.currentRowChanged.connect(
            self.cambiar_pantalla
        )

        self.menu.setCurrentRow(0)

    # El método ahora está correctamente indentado DENTRO de la clase VentanaPrincipal
    def cambiar_pantalla(self, fila):
        # Ocultar todas las pantallas principales primero
        self.inicio.hide()
        self.pedidos.hide()
        self.configuracion.hide()
        self.catalogo.hide()
        self.conocimiento.hide()
        if fila == 4:
         self.inicio.hide()
         self.pedidos.hide()
         self.configuracion.hide()
         self.conocimiento.actualizar()
         self.conocimiento.show()
         return

        if fila == 5:
            self.inicio.hide()
            self.pedidos.hide()
            self.configuracion.hide()
            dialogo = RegistroPedidosDialog(self)
            dialogo.exec()
            self.menu.setCurrentRow(0)
            return

        # Gestionar la selección del menú lateral
        if fila == 0:
            self.inicio.show()
        elif fila == 1:
            self.pedidos.show()
        elif fila == 2:
            # El Historial se abre como ventana aparte; al cerrarla,
            # volvemos a Inicio en vez de dejar la pantalla en blanco.
            dialogo = HistorialPedidosDialog(
                Path(__file__).resolve().parents[2] / "data" / "historial_pedidos",
                self
            )
            dialogo.exec()
            self.menu.setCurrentRow(0)
        elif fila == 3:
            self.configuracion.actualizar()
            self.configuracion.show()
        elif fila == 6:
            self.catalogo.show()