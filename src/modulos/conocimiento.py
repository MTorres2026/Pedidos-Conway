import json
from pathlib import Path


RUTA_CONOCIMIENTO = (
    Path(__file__).resolve().parent.parent
    / "datos"
    / "conocimiento.json"
)


def cargar_conocimiento():
    with open(RUTA_CONOCIMIENTO, "r", encoding="utf-8") as archivo:
        return json.load(archivo)