from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout
)


class Expedientes(QWidget):

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)

        titulo = QLabel("📂 EXPEDIENTES")
        titulo.setStyleSheet("""
            font-size: 28px;
            font-weight: bold;
        """)

        boton_nuevo = QPushButton("➕ Nuevo expediente")
        boton_abrir = QPushButton("📂 Abrir expediente")
        boton_buscar = QPushButton("🔎 Buscar expediente")
        boton_historial = QPushButton("📚 Historial")

        layout.addWidget(titulo)
        layout.addSpacing(20)

        layout.addWidget(boton_nuevo)
        layout.addWidget(boton_abrir)
        layout.addWidget(boton_buscar)
        layout.addWidget(boton_historial)

        layout.addStretch()