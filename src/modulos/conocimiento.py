import json
from pathlib import Path

def cargar_conocimiento():
    """
    Lee los archivos de procedimientos confirmados:
      1) src/datos/conocimiento.json (base incluida en el proyecto)
      2) data/conocimiento.json      (personalizaciones locales del cine; tienen prioridad)
    Si ninguno existe o falla la lectura, devuelve estructura vacía sin romper la app.
    """
    ruta_modulo = Path(__file__).resolve()
    ruta_src_datos = ruta_modulo.parent.parent / "datos" / "conocimiento.json"
    ruta_data_raiz = ruta_modulo.parent.parent.parent / "data" / "conocimiento.json"

    por_id = {}
    orden = []

    for ruta in (ruta_src_datos, ruta_data_raiz):
        if not ruta.is_file():
            continue
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                contenido = json.load(f)
            for proc in contenido.get("procedimientos", []):
                pid = proc.get("id")
                if not pid:
                    continue
                if pid not in por_id:
                    orden.append(pid)
                por_id[pid] = proc
        except Exception as e:
            print(f"Error leyendo {ruta.name}: {e}")

    return {"procedimientos": [por_id[pid] for pid in orden]}