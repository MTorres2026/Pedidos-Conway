from pathlib import Path
from pypdf import PdfReader


def cargar_catalogo():

    ruta = Path(__file__).parent / "recursos" / "Vista_Reporte-1.PDF"

    lector = PdfReader(ruta)

    productos = []

    for pagina in lector.pages:

        texto = pagina.extract_text() or ""

        lineas = [
            linea.strip()
            for linea in texto.splitlines()
            if linea.strip()
        ]

        i = 0

        while i < len(lineas):

            linea = lineas[i]

            # El código de artículo aparece como una línea numérica.
            if linea.isdigit():

                articulo = linea

                # Buscamos hacia atrás las líneas que forman
                # la descripción completa.
                descripcion_partes = []

                j = i - 1

                while j >= 0:

                    anterior = lineas[j]

                    # Si encontramos una línea que parece ser
                    # una cabecera de categoría, paramos.
                    if anterior.isupper() and len(anterior) > 2:
                        break

                    # Estas líneas pertenecen al encabezado del informe.
                    if anterior.startswith("Informe de"):
                        break

                    if anterior.startswith("Yelmo Films"):
                        break

                    if anterior.startswith("Avd."):
                        break

                    if anterior.startswith("C.C"):
                        break

                    if anterior.startswith("Madrid"):
                        break

                    if anterior.startswith("Yelmo Cines"):
                        break

                    if anterior.startswith("From:"):
                        break

                    if anterior.startswith("Until:"):
                        break

                    # No queremos números como parte del nombre.
                    if anterior.replace(",", "").replace(".", "").isdigit():
                        break

                    descripcion_partes.insert(0, anterior)

                    j -= 1

                # Nos quedamos con las líneas de texto
                # antes del código.
                if descripcion_partes:

                    descripcion = " ".join(descripcion_partes).strip()

                    productos.append({
                        "articulo": articulo,
                        "descripcion": descripcion
                    })

            i += 1

    return productos