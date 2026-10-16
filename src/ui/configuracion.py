import os
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout,
    QPushButton,
    QFrame,
)

import receptor


class Configuracion(QWidget):
    """
    Pantalla de solo lectura: un vistazo rápido a si todo lo que
    necesita CineStock está en su sitio, antes de llamar a nadie.

    No cambia nada por sí sola: solo informa.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        titulo = QLabel("⚙️ CONFIGURACIÓN")
        titulo.setStyleSheet("font-size: 28px; font-weight: bold;")
        layout.addWidget(titulo)

        subtitulo = QLabel(
            "Esta pantalla no cambia nada: solo te dice si falta algo "
            "antes de que te encuentres el problema a medio pedido."
        )
        subtitulo.setWordWrap(True)
        subtitulo.setStyleSheet("color: #607080;")
        layout.addWidget(subtitulo)

        layout.addSpacing(12)

        self.lista = QVBoxLayout()
        layout.addLayout(self.lista)

        layout.addSpacing(12)

        boton_actualizar = QPushButton("🔄 Comprobar de nuevo")
        boton_actualizar.clicked.connect(self.actualizar)
        layout.addWidget(boton_actualizar)

        layout.addStretch()

        self.actualizar()

    # ==========================================================
    # RUTA BASE (igual que en Pedidos: funciona en dev y en el .exe)
    # ==========================================================

    def _base_dir(self):
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parents[2]
        return Path(__file__).resolve().parents[2]

    # ==========================================================
    # COMPROBACIONES
    # ==========================================================

    def _comprobaciones(self):
        base_dir = self._base_dir()

        comprobaciones = []

        comprobaciones.append((
            "Receptor de pedidos (puerto 8765)",
            receptor.RECEPTOR_ACTIVO,
            "Reinicia CineStock si no está activo." if not receptor.RECEPTOR_ACTIVO else "",
        ))

        comprobaciones.append((
            "Carpeta de datos (data/)",
            (base_dir / "data").is_dir(),
            f"No existe: {base_dir / 'data'}",
        ))

        tiene_openai = bool(os.environ.get("OPENAI_API_KEY"))
        comprobaciones.append((
            "OPENAI_API_KEY (para Conocimiento)",
            tiene_openai,
            "Añádela al .env de la raíz del proyecto." if not tiene_openai else "",
        ))

        tiene_erp_user = bool(os.environ.get("ERP_USER"))
        tiene_erp_pass = bool(os.environ.get("ERP_PASS"))
        comprobaciones.append((
            "ERP_USER / ERP_PASS (para Enviar a ERP)",
            tiene_erp_user and tiene_erp_pass,
            "Añade ambas al .env de la raíz del proyecto." if not (tiene_erp_user and tiene_erp_pass) else "",
        ))

        ruta_robot = base_dir / "src" / "robot_erp.py"
        comprobaciones.append((
            "Robot de carga en el ERP (src/robot_erp.py)",
            ruta_robot.is_file(),
            f"No se encuentra: {ruta_robot}" if not ruta_robot.is_file() else "",
        ))

        return comprobaciones

    # ==========================================================
    # PINTAR
    # ==========================================================

    def actualizar(self):

        while self.lista.count():
            item = self.lista.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for texto, ok, detalle in self._comprobaciones():

            fila = QFrame()
            fila_layout = QVBoxLayout(fila)
            fila_layout.setContentsMargins(10, 8, 10, 8)

            icono = "✅" if ok else "❌"
            color = "#1b6e2f" if ok else "#b00020"

            etiqueta = QLabel(f"{icono}  {texto}")
            etiqueta.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {color};")
            fila_layout.addWidget(etiqueta)

            if not ok and detalle:
                aviso = QLabel(detalle)
                aviso.setStyleSheet("color: #455a64; padding-left: 26px;")
                aviso.setWordWrap(True)
                fila_layout.addWidget(aviso)

            fila.setStyleSheet(
                "QFrame { background-color: #f4f6f8; border-radius: 6px; }"
            )

            self.lista.addWidget(fila)
