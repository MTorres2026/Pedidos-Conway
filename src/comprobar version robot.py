# -*- coding: utf-8 -*-
"""Solo LEE robot_erp.py y te dice qué versión tiene. No cambia nada."""
import os
from datetime import datetime

CARPETA = os.path.dirname(os.path.abspath(__file__))
RUTA = os.path.join(CARPETA, "robot_erp.py")


def version_de(texto):
    if "_añadir_todas" in texto:
        return "v7 (la que guardamos de reserva)"
    if "SIGUE con la siguiente" in texto:
        return "v6b (la buena: anota el error y SIGUE)"
    if "_mapear_ventana" in texto:
        return "v6 (se para si una línea falla)"
    if "fila_dom=None" in texto:
        return "v5 (antigua, se despistaba con la fila 13)"
    return "ORIGINAL (sin ningún parche)"


with open(RUTA, "r", encoding="utf-8") as f:
    texto = f.read()

cuando = datetime.fromtimestamp(os.path.getmtime(RUTA)).strftime("%d/%m/%Y a las %H:%M")
print("=" * 52)
print(f"  robot_erp.py -> versión: {version_de(texto)}")
print(f"  Última modificación del archivo: {cuando}")
print("=" * 52)
