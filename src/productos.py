import csv
from pathlib import Path


def cargar_productos():
    """
    Carga productos desde el archivo CSV de datos del proyecto.
    
    Busca el archivo 'productos.csv' en la carpeta 'data/' del proyecto
    y devuelve una lista de diccionarios con los productos del proveedor Conway.
    
    Returns:
        list: Lista de diccionarios con los productos de Conway
              Cada diccionario contiene: articulo, descripcion, cantidad, um, proveedor, etc.
    """
    
    # Soluci�n de ruta multiplataforma: usar Path en lugar de uniones de cadenas
    # Esto funciona tanto en Windows, macOS y Linux
    project_root = Path(__file__).parent.parent
    data_dir = project_root / "data"
    
    csv_path = data_dir / "productos.csv"
    
    # Verificar que el archivo existe antes de intentar abrirlo
    if not csv_path.exists():
        print(f"Warning: productos.csv not found at {csv_path}")
        return []

    productos = []

    # Abrir el archivo CSV con codificaci�n UTF-8 para manejo correcto de caracteres especiales
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as archivo:
        lector = csv.DictReader(archivo)

        for fila in lector:
            # Filtrar s�lo los productos del proveedor Conway (insensible a may�sculas/min�sculas)
            if fila.get("proveedor", "").strip().lower() == "conway":
                productos.append(fila)

    return productos