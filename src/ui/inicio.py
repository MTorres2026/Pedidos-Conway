from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QLineEdit, QPushButton
from modulos.procedimientos import buscar_procedimiento, generar_respuesta

class Inicio(QWidget):

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)

        titulo = QLabel("🏠 INICIO")
        titulo.setStyleSheet("""
            font-size:28px;
            font-weight:bold;
        """)

        texto = QLabel(
            "Bienvenido a CineStock.\n\n"
            "Selecciona una opción del menú para comenzar."
        )
        self.pregunta = QLineEdit()
        self.pregunta.setPlaceholderText("Escribe tu pregunta...")
        boton = QPushButton("Buscar procedimiento")
        self.respuesta = QLabel("")
        self.respuesta.setWordWrap(True)
        boton.clicked.connect(self.buscar)
        layout.addWidget(titulo)
        layout.addWidget(texto)
        layout.addWidget(self.pregunta)
        layout.addWidget(boton)
        layout.addWidget(self.respuesta)
        layout.addStretch()


    def buscar(self):
        pregunta = self.pregunta.text()
        procedimiento = buscar_procedimiento(pregunta)

        if procedimiento:
            respuesta = generar_respuesta(pregunta, procedimiento)
            self.respuesta.setText(respuesta)
        else:
            self.respuesta.setText(
                "No encuentro un procedimiento confirmado para esa pregunta."
        )