"""Generación de los informes Stockable y Granel para pedidos manuales.

Stockable se localiza por ID Vista y Granel por el código ERP impreso en su
plantilla oficial. En ambos casos se superponen las cantidades sobre el PDF
original, sin reconstruir ni eliminar el resto de la información.
"""

from __future__ import annotations

import io
import re
import unicodedata
from difflib import SequenceMatcher
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


class ErrorInformePedido(ValueError):
    """Error mostrando qué impide generar un informe correctamente."""


# Posición comprobada con el Stockable de Vista_Reporte-1.PDF y la prueba
# visual aprobada por el usuario. Unidades en puntos PDF, origen inferior izq.
_X_COLUMNA_PEDIDO = 556.0
_TAMANO_TEXTO_PEDIDO = 6.0
_DECIMALES_MAXIMOS = Decimal("0.001")


def _cargar_librerias_pdf():
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib.colors import black
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise ErrorInformePedido(
            "Faltan las librerías para rellenar el PDF Stockable. "
            "Instala pypdf y reportlab (pip install pypdf reportlab)."
        ) from exc
    return PdfReader, PdfWriter, canvas, black


def _codigo_vista(linea: dict) -> str:
    valor = str(linea.get("idVista") or linea.get("ID VISTA") or "").strip()
    if valor.endswith(".0") and valor[:-2].isdigit():
        valor = valor[:-2]
    return valor


def _cantidad_decimal(linea: dict) -> Decimal:
    bruto = linea.get("cantidadPedida", linea.get("cantidad", 0))
    try:
        cantidad = Decimal(str(bruto).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        raise ErrorInformePedido(
            f"La cantidad de «{linea.get('descripcion', 'producto')}» no es válida."
        )
    if not cantidad.is_finite() or cantidad <= 0:
        raise ErrorInformePedido(
            f"La cantidad de «{linea.get('descripcion', 'producto')}» debe ser mayor que cero."
        )
    try:
        normalizada = cantidad.quantize(_DECIMALES_MAXIMOS)
    except InvalidOperation:
        normalizada = None
    if normalizada is None or cantidad != normalizada:
        raise ErrorInformePedido(
            f"La cantidad de «{linea.get('descripcion', 'producto')}» "
            "no puede tener más de tres decimales."
        )
    return normalizada


def _cantidad_texto(cantidad: Decimal) -> str:
    texto = format(cantidad.quantize(_DECIMALES_MAXIMOS), "f")
    return texto.rstrip("0").rstrip(".").replace(".", ",")


def ruta_plantilla_granel() -> Path:
    """Ruta del formulario oficial incluido con la aplicación."""
    return (
        Path(__file__).resolve().parents[1]
        / "recursos"
        / "Hoja_de_Pedido_Graneles_Oficial.pdf"
    )


def _leer_y_validar_granel(ruta_granel=None):
    ruta = Path(ruta_granel) if ruta_granel is not None else ruta_plantilla_granel()
    if not ruta.is_file():
        raise ErrorInformePedido(f"No se encuentra la plantilla oficial de Granel: {ruta}")
    if ruta.suffix.lower() != ".pdf":
        raise ErrorInformePedido("La plantilla de Granel debe ser un archivo PDF.")

    PdfReader, PdfWriter, canvas, black = _cargar_librerias_pdf()
    try:
        reader = PdfReader(str(ruta), strict=False)
        if reader.is_encrypted:
            raise ErrorInformePedido("La plantilla oficial de Granel está protegida con contraseña.")
        paginas = list(reader.pages)
    except ErrorInformePedido:
        raise
    except Exception as exc:
        raise ErrorInformePedido(f"No se pudo abrir la plantilla oficial de Granel: {exc}") from exc
    if not paginas:
        raise ErrorInformePedido("La plantilla oficial de Granel no contiene páginas.")

    texto = " ".join(pagina.extract_text() or "" for pagina in paginas)
    if "HOJA DE PEDIDO DE GRANELES" not in re.sub(r"\s+", " ", texto).upper():
        raise ErrorInformePedido("El PDF seleccionado no parece ser la plantilla oficial de Granel.")

    posiciones: dict[str, list[tuple[int, float, bool, str]]] = defaultdict(list)
    for indice_pagina, pagina in enumerate(paginas):
        if int(pagina.get("/Rotate", 0) or 0) % 360:
            raise ErrorInformePedido(
                f"La página {indice_pagina + 1} de Granel está girada; "
                "no se puede asegurar la alineación de las cantidades."
            )
        alto = float(pagina.mediabox.height)

        filas_pagina = []
        codigos_pagina = []

        def recibir_texto(texto, cm, tm, _fuente, _tamano):
            contenido = str(texto or "").strip()
            if not contenido:
                return
            try:
                x = float(cm[0]) * float(tm[4]) + float(cm[2]) * float(tm[5]) + float(cm[4])
                y = float(cm[1]) * float(tm[4]) + float(cm[3]) * float(tm[5]) + float(cm[5])
            except (TypeError, ValueError, IndexError):
                return
            if 15 <= x <= 412:
                es_derecha = False
            elif 415 <= x <= 821:
                es_derecha = True
            else:
                return
            if not 40 <= y <= alto - 30:
                return
            filas_pagina.append((x, y, es_derecha, contenido))
            codigo = re.sub(r"\s+", "", contenido).upper()
            if re.fullmatch(r"[A-Z0-9]{2,5}", codigo) and (
                (not es_derecha and x <= 60) or (es_derecha and x <= 465)
            ):
                codigos_pagina.append((codigo, y, es_derecha))

        try:
            pagina.extract_text(visitor_text=recibir_texto)
        except Exception as exc:
            raise ErrorInformePedido(
                f"No se pudo leer la plantilla de Granel, página {indice_pagina + 1}: {exc}"
            ) from exc
        for codigo, y, es_derecha in codigos_pagina:
            texto_fila = " ".join(
                texto
                for x, y_texto, lado, texto in filas_pagina
                if lado == es_derecha and abs(y_texto - y) <= 2.5
            )
            posiciones[codigo].append((indice_pagina, y, es_derecha, texto_fila))

    # KGS identifica una línea de unidad en blanco al pie de cada tabla, no un producto.
    posiciones.pop("KGS", None)
    if not posiciones:
        raise ErrorInformePedido("La plantilla de Granel no contiene códigos de producto reconocibles.")
    return reader, paginas, posiciones, PdfReader, PdfWriter, canvas, black


def codigos_granel(ruta_granel=None) -> set[str]:
    """Devuelve los códigos ERP publicados en la plantilla oficial de Granel."""
    _, _, posiciones, _, _, _, _ = _leer_y_validar_granel(ruta_granel)
    return set(posiciones)


def _codigo_erp(linea: dict) -> str:
    valor = linea.get("codigo") or linea.get("idErp") or linea.get("idArticulo") or ""
    return str(valor).strip().upper()


def generar_granel_rellenado(ruta_granel, lineas: list[dict]) -> bytes | None:
    """Rellena cantidades Granel por código ERP; devuelve None si no hay líneas Granel.

    Las líneas cuyo código ERP no aparece en el formulario pertenecen a otros
    informes (por ejemplo Stockable) y se ignoran aquí.
    """
    if not lineas:
        return None
    reader, paginas, posiciones, PdfReader, PdfWriter, canvas, black = _leer_y_validar_granel(ruta_granel)
    cantidades: dict[str, Decimal] = defaultdict(Decimal)
    ubicaciones: dict[str, tuple[int, float, bool, str]] = {}
    for linea in lineas:
        codigo = _codigo_erp(linea)
        if codigo not in posiciones:
            continue
        candidatos = posiciones[codigo]
        if len(candidatos) > 1:
            descripcion = str(linea.get("descripcion") or linea.get("nombre") or "")
            normalizar = lambda texto: " ".join(
                re.findall(
                    r"[a-z0-9]+",
                    unicodedata.normalize("NFKD", str(texto).casefold())
                    .encode("ascii", "ignore")
                    .decode("ascii"),
                )
            )
            palabras_linea = set(normalizar(descripcion).split())
            puntuados = []
            for candidato in candidatos:
                palabras_fila = set(normalizar(candidato[3]).split())
                cobertura = (
                    len(palabras_linea & palabras_fila) / len(palabras_linea)
                    if palabras_linea else 0.0
                )
                secuencia = SequenceMatcher(
                    None, normalizar(descripcion), normalizar(candidato[3])
                ).ratio()
                puntuados.append((max(cobertura, secuencia), candidato))
            puntuados.sort(key=lambda elemento: elemento[0], reverse=True)
            if (
                puntuados[0][0] < 0.35
                or (
                    len(puntuados) > 1
                    and puntuados[0][0] - puntuados[1][0] < 0.08
                )
            ):
                raise ErrorInformePedido(
                    f"El código ERP {codigo} aparece varias veces en Granel y "
                    f"no se pudo identificar la fila de «{descripcion}» de forma segura."
                )
            ubicacion = puntuados[0][1]
        else:
            ubicacion = candidatos[0]
        cantidades[codigo] += _cantidad_decimal(linea)
        # Los duplicados del mismo código se suman solo cuando corresponden a
        # una misma fila; conservamos la fila elegida para renderizarla.
        if codigo in ubicaciones and ubicaciones[codigo] != ubicacion:
            raise ErrorInformePedido(
                f"El código ERP {codigo} identifica más de un producto de Granel en este pedido."
            )
        ubicaciones[codigo] = ubicacion

    if not cantidades:
        return None
    escritor = PdfWriter()
    try:
        for pagina in paginas:
            escritor.add_page(pagina)
        for indice_pagina, pagina in enumerate(escritor.pages):
            overlay_buffer = io.BytesIO()
            ancho = float(pagina.mediabox.width)
            alto = float(pagina.mediabox.height)
            lienzo = canvas.Canvas(overlay_buffer, pagesize=(ancho, alto))
            lienzo.setFillColor(black)
            lienzo.setFont("Helvetica", 8)
            for codigo, (pagina_codigo, y, es_derecha, _texto_fila) in ubicaciones.items():
                if pagina_codigo != indice_pagina:
                    continue
                x_centro = 792.5 if es_derecha else 382.5
                cantidad = _cantidad_texto(cantidades[codigo])
                lienzo.drawCentredString(x_centro, y - 1.0, cantidad)
            lienzo.save()
            overlay_buffer.seek(0)
            overlay = PdfReader(overlay_buffer, strict=False).pages[0]
            pagina.merge_page(overlay)

        salida = io.BytesIO()
        escritor.write(salida)
        return salida.getvalue()
    except ErrorInformePedido:
        raise
    except Exception as exc:
        raise ErrorInformePedido(f"No se pudo rellenar la plantilla oficial de Granel: {exc}") from exc


def _posiciones_codigos_stockable(reader) -> dict[str, list[tuple[int, float]]]:
    """Localiza códigos numéricos de Vista en la primera columna de producto."""
    posiciones: dict[str, list[tuple[int, float]]] = defaultdict(list)

    for indice_pagina, pagina in enumerate(reader.pages):
        if int(pagina.get("/Rotate", 0) or 0) % 360:
            raise ErrorInformePedido(
                f"La página {indice_pagina + 1} del Stockable está girada; "
                "no se puede asegurar la alineación de las cantidades."
            )
        alto = float(pagina.mediabox.height)

        def recibir_texto(texto, cm, tm, _fuente, _tamano):
            codigo = re.sub(r"\s+", "", str(texto or ""))
            if not re.fullmatch(r"\d{1,10}", codigo):
                return
            try:
                # Convierte el origen local del texto a coordenadas de página.
                x = float(cm[0]) * float(tm[4]) + float(cm[2]) * float(tm[5]) + float(cm[4])
                y = float(cm[1]) * float(tm[4]) + float(cm[3]) * float(tm[5]) + float(cm[5])
            except (TypeError, ValueError, IndexError):
                return

            # Los IDs de Vista están en la primera columna de productos. Esto
            # descarta fechas, totales y números de las otras columnas.
            if not 15 <= x <= 45:
                return
            if not 40 <= y <= alto - 120:
                return
            posiciones[codigo].append((indice_pagina, y))

        try:
            pagina.extract_text(visitor_text=recibir_texto)
        except Exception as exc:
            raise ErrorInformePedido(
                f"No se pudo leer el texto del Stockable, página {indice_pagina + 1}: {exc}"
            ) from exc

    return posiciones


def _leer_y_validar_stockable(ruta_stockable):
    ruta = Path(ruta_stockable)
    if not ruta.is_file():
        raise ErrorInformePedido(f"No se encuentra el informe Stockable: {ruta}")
    if ruta.suffix.lower() != ".pdf":
        raise ErrorInformePedido("El informe Stockable debe ser un archivo PDF.")

    PdfReader, PdfWriter, canvas, black = _cargar_librerias_pdf()
    try:
        reader = PdfReader(str(ruta), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ErrorInformePedido(
                        "El PDF Stockable está protegido con contraseña."
                    )
            except ErrorInformePedido:
                raise
            except Exception as exc:
                raise ErrorInformePedido(
                    "El PDF Stockable está protegido con contraseña."
                ) from exc
        paginas_originales = list(reader.pages)
    except ErrorInformePedido:
        raise
    except Exception as exc:
        raise ErrorInformePedido(f"No se pudo abrir el informe Stockable: {exc}") from exc
    if not paginas_originales:
        raise ErrorInformePedido("El PDF Stockable no contiene páginas.")

    try:
        encabezado = " ".join(
            (pagina.extract_text() or "") for pagina in paginas_originales[:2]
        ).casefold()
    except Exception as exc:
        raise ErrorInformePedido(f"No se pudo validar el encabezado del Stockable: {exc}") from exc
    encabezado = re.sub(r"\s+", " ", encabezado)
    if "stocktake count" not in encabezado:
        raise ErrorInformePedido(
            "El PDF seleccionado no parece un informe Stockable: "
            "no se encuentra la columna «Stocktake Count»."
        )

    posiciones = _posiciones_codigos_stockable(reader)
    if not posiciones:
        raise ErrorInformePedido(
            "El PDF seleccionado no contiene filas de productos reconocibles en Stockable."
        )
    return reader, paginas_originales, posiciones, PdfReader, PdfWriter, canvas, black


def validar_stockable(ruta_stockable) -> dict[str, list[tuple[int, float]]]:
    """Comprueba que el PDF corresponde a un Stockable legible y devuelve sus filas."""
    _, _, posiciones, _, _, _, _ = _leer_y_validar_stockable(ruta_stockable)
    return posiciones


def generar_stockable_rellenado(ruta_stockable, lineas: list[dict]) -> bytes:
    """Devuelve el Stockable actual con las cantidades pedidas superpuestas.

    ``lineas`` debe contener ``idVista`` por producto. El código ERP se
    conserva para el robot, pero no se usa para localizar filas en Vista.
    """
    if not lineas:
        raise ErrorInformePedido("El pedido no contiene líneas para rellenar en Stockable.")

    (
        reader,
        paginas_originales,
        posiciones,
        PdfReader,
        PdfWriter,
        canvas,
        black,
    ) = _leer_y_validar_stockable(ruta_stockable)
    cantidades: dict[str, Decimal] = defaultdict(Decimal)
    faltan = []
    codigos_de_granel = codigos_granel()

    for linea in lineas:
        # Los productos publicados en el formulario oficial de Granel se
        # dirigen a ese informe, aunque el catálogo no tenga ID Vista.
        if (
            str(linea.get("origen", "")).casefold().startswith("granel")
            or str(linea.get("tipoInforme", "")).casefold() == "granel"
            or _codigo_erp(linea) in codigos_de_granel
        ):
            continue
        codigo = _codigo_vista(linea)
        descripcion = str(linea.get("descripcion") or linea.get("nombre") or "producto")
        if not codigo:
            faltan.append(f"{descripcion}: falta ID VISTA en el catálogo")
            continue
        ubicaciones = posiciones.get(codigo, [])
        if not ubicaciones:
            faltan.append(f"{descripcion} (ID VISTA {codigo}): no aparece en el Stockable cargado")
            continue
        if len(ubicaciones) > 1:
            faltan.append(
                f"{descripcion} (ID VISTA {codigo}): aparece más de una vez en el Stockable"
            )
            continue
        cantidades[codigo] += _cantidad_decimal(linea)

    if faltan:
        raise ErrorInformePedido(
            "No se puede rellenar Stockable de forma segura:\n\n"
            + "\n".join(f"• {motivo}" for motivo in faltan[:12])
            + ("\n• …" if len(faltan) > 12 else "")
        )
    if not cantidades:
        # Un pedido exclusivamente de Granel conserva un Stockable limpio
        # asociado al orderId, sin rechazar líneas que no tienen ID Vista.
        if lineas and not faltan:
            return Path(ruta_stockable).read_bytes()
        raise ErrorInformePedido("No hay productos de Stockable que rellenar en el informe.")

    ubicaciones = {codigo: posiciones[codigo][0] for codigo in cantidades}
    escritor = PdfWriter()
    try:
        # Asignar las páginas al escritor antes de editarlas es necesario en
        # versiones recientes de pypdf y evita alterar el objeto lector.
        for pagina in paginas_originales:
            escritor.add_page(pagina)
        if reader.metadata:
            metadata = {
                str(clave): str(valor)
                for clave, valor in reader.metadata.items()
                if clave and valor is not None
            }
            if metadata:
                escritor.add_metadata(metadata)

        for indice_pagina, pagina in enumerate(escritor.pages):
            ancho = float(pagina.mediabox.width)
            alto = float(pagina.mediabox.height)
            capa_buffer = io.BytesIO()
            capa = canvas.Canvas(capa_buffer, pagesize=(ancho, alto))
            capa.setFillColor(black)
            capa.setFont("Helvetica", _TAMANO_TEXTO_PEDIDO)

            for codigo, cantidad in cantidades.items():
                pagina_objetivo, y = ubicaciones[codigo]
                if pagina_objetivo == indice_pagina:
                    capa.drawString(_X_COLUMNA_PEDIDO, y, _cantidad_texto(cantidad))

            capa.save()
            capa_buffer.seek(0)
            pagina_capa = PdfReader(capa_buffer).pages[0]
            pagina.merge_page(pagina_capa)

        salida = io.BytesIO()
        escritor.write(salida)
    except Exception as exc:
        raise ErrorInformePedido(f"No se pudo generar el Stockable rellenado: {exc}") from exc
    return salida.getvalue()
