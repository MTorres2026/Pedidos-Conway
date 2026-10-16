import os
import sys
import threading
from pathlib import Path

# ============================================================
# .env (OPENAI_API_KEY / ERP_USER / ERP_PASS)
#
# Hay que cargarlo ANTES de importar nada que lo necesite (la
# pantalla de Configuraci��n, el asistente de "Conocimiento"...).
# Mismo criterio que usa robot_erp.py: primero el .env de la ra��z
# del proyecto (un nivel por encima de src/); si no existe, el
# comportamiento habitual de python-dotenv.
# ============================================================
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    # Get the project root directory
    _RUTA_BASE = Path(__file__).resolve().parent
    _ENV_RAIZ = _RUTA_BASE.parent / ".env"
    
    if _ENV_RAIZ.is_file():
        load_dotenv(_ENV_RAIZ)
    else:
        # Fallback to current directory if root .env doesn't exist
        load_dotenv()

from PySide6.QtWidgets import QApplication
from ui.ventana_principal import VentanaPrincipal
from receptor import run_server


def main():
    # Start the HTTP receiver in background
    receptor_thread = threading.Thread(
        target=run_server,
        daemon=True
    )
    receptor_thread.start()

    # Start GUI
    app = QApplication(sys.argv)
    ventana = VentanaPrincipal()
    ventana.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()