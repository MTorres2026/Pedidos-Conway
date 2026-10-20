"""Apertura compartida por Inicio y Conocimiento, sin fallos silenciosos."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox


def abrir_original(parent, biblioteca, doc_id):
    ruta = None
    try:
        if biblioteca is None:
            raise RuntimeError("La biblioteca de Conocimiento no está disponible.")
        ruta = biblioteca.resolver_original(doc_id)
        # Qt usa la asociación de Windows, igual que un doble clic.
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(ruta))):
            raise RuntimeError(
                "El sistema no ha podido abrir el archivo. Comprueba que hay "
                "un programa asociado a esta extensión."
            )
    except Exception as error:
        detalle = f"\n\nArchivo: {ruta}" if ruta else ""
        QMessageBox.warning(parent, "Abrir original", f"{error}{detalle}")
