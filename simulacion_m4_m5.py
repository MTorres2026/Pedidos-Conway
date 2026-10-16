import time
import sys
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QTextEdit, QPushButton)
from PySide6.QtCore import Qt, QThread, Signal


# --- DATOS DE PRUEBA (Artículos de Conway) ---
LINEAS_PEDIDO_PRUEBA = [
    {"codigo": "CNW-10023", "cantidad": 5},   # SOLUCIÓN: string "5" -> integer 5
    {"codigo": "CNW-45089", "cantidad": 12}, # SOLUCIÓN: string "12" -> integer 12
    {"codigo": "CNW-88102", "cantidad": 2}   # SOLUCIÓN: string "2" -> integer 2
]


class HiloSimulacion(QThread):
    # Se�ales para actualizar la interfaz durante la secuencia
    log_signal = Signal(str)
    focus_signal = Signal(str)

    def __init__(self, tiempo_espera=1.2):
        super().__init__()
        self.tiempo_espera = tiempo_espera

    def run(self):
        self.log_signal.emit("=== INICIO DE SIMULACI�N M.4 -> M.5 ===")
        time.sleep(1)

        for idx, linea in enumerate(LINEAS_PEDIDO_PRUEBA, start=1):
            self.log_signal.emit(f"\n--- [L�NEA {idx}] Procesando art�culo: {linea['codigo']} ---")
            
            # Paso 1: Foco en ID Art�culo y Escritura
            self.focus_signal.emit("id_articulo")
            self.log_signal.emit(f"[FOCO: ID Art�culo] -> Escribiendo c�digo: '{linea['codigo']}'")
            time.sleep(0.6)

            # Paso 2: TAB -> Cantidad
            self.log_signal.emit("[TECLA: TAB] -> Salto de campo")
            self.focus_signal.emit("cantidad")
            self.log_signal.emit(f"[FOCO: Cantidad] -> Escribiendo cantidad: '{linea['cantidad']}'")
            time.sleep(0.6)

            # Paso 3: TAB -> UM / UOM
            self.log_signal.emit("[TECLA: TAB] -> Salto de campo")
            self.focus_signal.emit("um")
            self.log_signal.emit("[FOCO: UM/UOM] -> Validando Unidad de Medida por defecto")
            time.sleep(0.6)

            # Paso 4: Confirmaci�n de L�nea (Punto a validar en PeopleSoft)
            self.log_signal.emit("[ACCION] -> Confirmaci�n de l�nea (Pendiente definir en ERP: ENTER vs TECLA NAVEGACI�N)")
            
            # Paso 5: Pausa de validaci�n/latencia ERP
            self.log_signal.emit(f"[ESPERA] -> Simulando latencia del ERP ({self.tiempo_espera}s)...")
            time.sleep(self.tiempo_espera)

        self.log_signal.emit("\n=== FIN DE LA SIMULACI�N: Todas las l�neas procesadas correctamente ===")


class VentanaSimulacion(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CineStock - Simulaci�n Secuencia Teclado M.4 -> M.5")
        self.resize(700, 500)

        # Widget Principal
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # Indicador visual de FOCO
        layout.addWidget(QLabel("<b>Simulador de Formulario PeopleSoft (Visualizaci�n de Foco):</b>"))
        
        form_layout = QHBoxLayout()
        self.input_id = QLineEdit()
        self.input_id.setPlaceholderText("ID Art�culo")
        
        self.input_cant = QLineEdit()
        self.input_cant.setPlaceholderText("Cantidad")
        
        self.input_um = QLineEdit()
        self.input_um.setPlaceholderText("UM")

        form_layout.addWidget(QLabel("ID:"))
        form_layout.addWidget(self.input_id)
        form_layout.addWidget(QLabel("Cant:"))
        form_layout.addWidget(self.input_cant)
        form_layout.addWidget(QLabel("UM:"))
        form_layout.addWidget(self.input_um)
        
        layout.addLayout(form_layout)

        # Consola de logs
        layout.addWidget(QLabel("<b>Traza de ejecuci�n en tiempo real:</b>"))
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        layout.addWidget(self.console)

        # Bot�n de inicio
        self.btn_iniciar = QPushButton("Iniciar Simulaci�n Dry Run")
        self.btn_iniciar.clicked.connect(self.ejecutar_simulacion)
        layout.addWidget(self.btn_iniciar)

    def ejecutar_simulacion(self):
        self.btn_iniciar.setEnabled(False)
        self.console.clear()
        self.hilo = HiloSimulacion(tiempo_espera=1.2)
        self.hilo.log_signal.connect(self.log)
        self.hilo.focus_signal.connect(self.cambiar_foco_visual)
        self.hilo.finished.connect(lambda: self.btn_iniciar.setEnabled(True))
        self.hilo.start()

    def log(self, texto):
        self.console.append(texto)

    def cambiar_foco_visual(self, campo):
        # Limpiar estilos previos
        estilo_normal = "border: 1px solid #ccc; background-color: #fff;"
        estilo_foco = "border: 2px solid #2196F3; background-color: #E3F2FD; font-weight: bold;"

        self.input_id.setStyleSheet(estilo_normal)
        self.input_cant.setStyleSheet(estilo_normal)
        self.input_um.setStyleSheet(estilo_normal)

        if campo == "id_articulo":
            self.input_id.setStyleSheet(estilo_foco)
            self.input_id.setFocus()
        elif campo == "cantidad":
            self.input_cant.setStyleSheet(estilo_foco)
            self.input_cant.setFocus()
        elif campo == "um":
            self.input_um.setStyleSheet(estilo_foco)
            self.input_um.setFocus()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ventana = VentanaSimulacion()
    ventana.show()
    sys.exit(app.exec())