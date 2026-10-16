from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QListWidget,
    QLabel,
    QVBoxLayout
)

from ui.inicio import Inicio

class VentanaPrincipal(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Conway Assistant")
        self.resize(1200, 700)

        contenedor = QWidget()
        self.setCentralWidget(contenedor)

        layout = QHBoxLayout(contenedor)

        # ===== Menú lateral =====
        self.menu = QListWidget()
        self.menu.setFixedWidth(220)

        self.menu.addItem("🏠 Inicio")
        self.menu.addItem("📂 Expedientes")
        self.menu.addItem("📦 Pedidos")
        self.menu.addItem("📥 Recepciones")
        self.menu.addItem("🧾 Facturas")
        self.menu.addItem("💸 Abonos")
        self.menu.addItem("📧 Comunicaciones")
        self.menu.addItem("⚠️ Incidencias")
        self.menu.addItem("📚 Conocimiento")
        self.menu.addItem("📊 Historial")
        self.menu.addItem("⚙️ Configuración")        

        # ===== Zona principal =====
        contenido = QWidget()

        layout_contenido = QVBoxLayout(contenido)

        self.titulo = QLabel("PEDIDOS CONWAY")
        self.titulo.setStyleSheet("""
            font-size:28px;
            font-weight:bold;
        """)

        self.estado = QLabel("Bienvenido al proyecto.")
        layout_contenido.addWidget(self.titulo)
        layout_contenido.addWidget(self.estado)
        layout_contenido.addStretch()

        layout.addWidget(self.menu)
        layout.addWidget(contenido)
        self.menu.currentRowChanged.connect(self.cambiar_pantalla)
        self.menu.setCurrentRow(0)

    def cambiar_pantalla(self, fila):

        pantallas = [
            ("🏠 INICIO", "Bienvenido a Conway Assistant."),
            ("📂 EXPEDIENTES", "Gestión de expedientes."),
            ("📦 PEDIDOS", "Módulo de Pedidos."),
            ("📥 RECEPCIONES", "Módulo de Recepciones."),
            ("🧾 FACTURAS", "Módulo de Facturas."),
            ("💸 ABONOS", "Módulo de Abonos."),
            ("📧 COMUNICACIONES", "Comunicaciones de sede y proveedores."),
            ("⚠️ INCIDENCIAS", "Gestión de incidencias."),
            ("📚 CONOCIMIENTO", "Centro de conocimiento."),
            ("📊 HISTORIAL", "Historial de operaciones."),
            ("⚙️ CONFIGURACIÓN", "Configuración del programa.")
        ]

        titulo, texto = pantallas[fila]   

        self.titulo.setText(titulo)
        self.estado.setText(texto)

