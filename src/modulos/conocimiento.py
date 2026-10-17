import json
from pathlib import Path

def cargar_conocimiento():
    """
    Lee el archivo de procedimientos (conocimiento.json).
    Si no existe, devuelve una estructura vacía para no romper la app.
    """
    # Busca el archivo en la carpeta 'data' que está en la raíz del proyecto
    ruta = Path(__file__).resolve().parent.parent.parent / "data" / "conocimiento.json"
    
    if ruta.exists():
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error leyendo conocimiento.json: {e}")
    
    # Si no lo encuentra, devuelve vacío
    return {"procedimientos": []}