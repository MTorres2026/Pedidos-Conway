from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import black


# Carpeta principal del proyecto
BASE_DIR = Path(__file__).resolve().parents[1]

# PDF original y PDF de prueba
PDF_ORIGINAL = BASE_DIR / "src" / "recursos" / "Vista_Reporte-1.PDF"
PDF_PRUEBA = BASE_DIR / "src" / "recursos" / "Vista_Reporte-1_PRUEBA.pdf"


# Producto de prueba
CANTIDAD = "1"

# Posición de la cantidad en la casilla de Salsa Chunky
# X se mantiene igual porque horizontalmente está bien.
# Y se baja aproximadamente 1 cm.
X = 556
Y = 430


if not PDF_ORIGINAL.exists():
    print(f"No se encuentra el PDF original: {PDF_ORIGINAL}")
    raise SystemExit(1)


reader = PdfReader(str(PDF_ORIGINAL))
writer = PdfWriter()

# Página 1 (índice 0)
pagina_objetivo = 0
pagina = reader.pages[pagina_objetivo]

ancho = float(pagina.mediabox.width)
alto = float(pagina.mediabox.height)

print(f"Pagina 1: {ancho} x {alto}")
print("Escribiendo 4 para Salsa Chunky (10321, KGS)")


# Crear una pequeña capa transparente con el número 4
capa_pdf = BASE_DIR / "capa_prueba.pdf"

c = canvas.Canvas(
    str(capa_pdf),
    pagesize=(ancho, alto)
)

c.setFillColor(black)

c.setFont("Helvetica", 6)

c.drawString(X, 430, "1")
c.drawString(X, 416, "2")
c.drawString(X, 398, "3")
c.drawString(X, 382, "4")
c.drawString(X, 364, "5")
c.drawString(X, 348, "6")
c.drawString(X, 332, "7")
c.drawString(X, 314, "8")
c.drawString(X, 296, "9")

c.drawString(X, 242, "12")
c.drawString(X, 224, "13")

c.drawString(X, 160, "16")
c.drawString(X, 148, "17")
c.drawString(X, 132, "18")
c.drawString(X, 114, "19")
c.drawString(X, 90, "20")
c.drawString(X, 72, "21")
c.save()


# Superponer la capa sobre la página original
capa_reader = PdfReader(str(capa_pdf))
capa = capa_reader.pages[0]

pagina.merge_page(capa)

writer.add_page(pagina)


# Añadir el resto de páginas sin modificar
for numero in range(1, len(reader.pages)):
    writer.add_page(reader.pages[numero])


# Guardar el PDF de prueba
with open(PDF_PRUEBA, "wb") as archivo:
    writer.write(archivo)


# Eliminar la capa temporal
try:
    capa_pdf.unlink()
except OSError:
    pass


print()
print(f"PDF de prueba creado en:")
print(PDF_PRUEBA)