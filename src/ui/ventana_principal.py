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

BASE_DIR = Path(__file__).resolve().parents[2]

class VentanaPrincipal(QMainWindow):

    def __init__(self):
        super().__init__()

        self.base_dir = Path(__file__).resolve().parents[2]
        self.setWindowTitle("CineStock")
        self.resize(1200, 700)

        contenedor = QWidget()
        self.setCentralWidget(contenedor)

        layout = QHBoxLayout(contenedor)

        # ===== Menú lateral =====
        self.menu = QListWidget()
        self.menu.setFixedWidth(220)

        self.menu.addItem("🏠 Inicio")          # Índice 0
        self.menu.addItem("📦 Pedidos")         # Índice 1
        self.menu.addItem("📊 Historial")       # Índice 2
        self.menu.addItem("⚙️ Configuración")   # Índice 3
        self.menu.addItem("📚 Conocimiento")
        
        self.menu.addItem("📖 Catálogo")        # Índice 4

        # ===== Zona principal =====
        contenido = QWidget()
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
        elif fila == 5:
            self.catalogo.show()