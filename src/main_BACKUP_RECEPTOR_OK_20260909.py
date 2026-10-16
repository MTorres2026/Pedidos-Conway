import sys
import threading
from PySide6.QtWidgets import QApplication
from ui.ventana_principal import VentanaPrincipal
from receptor import run_server


def main():
    # Arrancar el receptor HTTP en segundo plano
    receptor_thread = threading.Thread(
        target=run_server,
        daemon=True
    )
    receptor_thread.start()

    # Arrancar la interfaz gráfica
    app = QApplication(sys.argv)

    ventana = VentanaPrincipal()
    ventana.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()