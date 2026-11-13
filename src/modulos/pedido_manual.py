"""Lógica de dominio para crear pedidos Conway desde la oficina.

La persona introduce las cantidades y el pedido manual conserva el ID Vista
para rellenar Stockable. El envío al ERP continúa requiriendo revisión explícita.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

from modulos.informes_pedido import (
    generar_granel_rellenado,
    generar_stockable_rellenado,
    ruta_plantilla_granel,
)


_ID_PEDIDO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ErrorPedidoManual(ValueError):
    """Error de datos del pedido con un mensaje apto para mostrar al usuario."""


def _texto(producto: dict, *claves: str) -> str:
    for clave in claves:
        valor = producto.get(clave)
        if valor is None:
            continue
        resultado = str(valor).strip()
        if resultado and resultado not in ("—", "-"):
            return resultado
    return ""


def _productos_del_json(datos) -> list:
    if isinstance(datos, list):
        return datos
    if isinstance(datos, dict):
        for clave in ("SUPERMAESTRO", "productos", "PRODUCTOS"):
            productos = datos.get(clave)
            if isinstance(productos, list):
                return productos
    raise ErrorPedidoManual(
        "El catálogo no contiene una lista de productos reconocible."
    )


def normalizar_producto_catalogo(producto: dict) -> dict | None:
    """Devuelve los campos que necesita el robot, o None si no está mapeado."""
    if not isinstance(producto, dict):
        return None

    descripcion = _texto(
        producto,
        "PRODUCTO",
        "DENOMINACIÓN LARGA CONWAY",
        "descripcion",
        "nombre",
    )
    codigo = _texto(producto, "ID ERP", "idErp", "codigo", "idArticulo")
    unidad = _texto(producto, "UM ERP", "umErp", "unidad", "um")
    # PeopleSoft necesita una sola UM. El receptor aplica la misma regla al
    # catálogo: cuando viene expresada como «UM / descripción», usa la primera.
    unidad = unidad.split("/", 1)[0].strip()

    if not descripcion or not codigo or not unidad:
        return None

    return {
        "descripcion": descripcion,
        "codigo": codigo,
        "unidad": unidad,
        "grupo": _texto(producto, "GRUPO", "grupo", "categoria"),
        "idVista": _texto(producto, "ID VISTA", "idVista"),
        "presentacion": _texto(producto, "PRESENTACIÓN", "presentacion"),
    }


def cargar_catalogo(ruta_data) -> list[dict]:
    """Carga productos con código ERP y UM de los catálogos conocidos.

    La interfaz de catálogo guarda ``supermaestro.json``. Se admite también
    ``supermaestro_flow.json``, que es el nombre usado por el receptor.
    """
    carpeta = Path(ruta_data)
    rutas = (carpeta / "supermaestro.json", carpeta / "supermaestro_flow.json")
    errores = []

    for ruta in rutas:
        if not ruta.is_file():
            continue
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
            productos = _productos_del_json(datos)
        except Exception as exc:
            errores.append(f"{ruta.name}: {exc}")
            continue

        normalizados = [
            normalizado
            for producto in productos
            if (normalizado := normalizar_producto_catalogo(producto)) is not None
        ]
        if normalizados:
            return sorted(normalizados, key=lambda p: p["descripcion"].casefold())
        errores.append(f"{ruta.name}: no contiene productos con ID ERP y UM ERP.")

    if errores:
        detalle = "\n".join(errores)
        raise ErrorPedidoManual(
            "No se pudo cargar un catálogo de productos válido.\n\n" + detalle
        )
    raise FileNotFoundError(
        "No se encontró data/supermaestro.json ni "
        "data/supermaestro_flow.json."
    )


def normalizar_cantidad(valor) -> int | float:
    """Valida una cantidad positiva y conserva enteros como enteros."""
    if isinstance(valor, bool):
        raise ErrorPedidoManual("La cantidad no es válida.")
    try:
        numero = float(str(valor).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise ErrorPedidoManual(f"La cantidad «{valor}» no es un número válido.")
    if not math.isfinite(numero) or numero <= 0:
        raise ErrorPedidoManual("La cantidad debe ser mayor que cero.")
    return int(numero) if numero.is_integer() else numero


def preparar_linea_manual(linea: dict) -> dict:
    """Normaliza una línea para que el formato lo acepte el robot del ERP."""
    if not isinstance(linea, dict):
        raise ErrorPedidoManual("Hay una línea de pedido que no es válida.")

    descripcion = _texto(linea, "descripcion", "PRODUCTO", "nombre")
    codigo = _texto(linea, "codigo", "ID ERP", "idErp", "idArticulo")
    unidad = _texto(linea, "unidad", "UM ERP", "umErp", "um")
    unidad = unidad.split("/", 1)[0].strip()

    if not descripcion:
        raise ErrorPedidoManual("Todas las líneas deben tener una descripción.")
    if not codigo:
        raise ErrorPedidoManual(
            f"Falta el código ERP de «{descripcion}»; no se puede enviar al robot."
        )
    if not unidad:
        raise ErrorPedidoManual(
            f"Falta la unidad de medida ERP de «{descripcion}»."
        )

    cantidad = normalizar_cantidad(
        linea.get("cantidadPedida", linea.get("cantidad", 0))
    )
    return {
        "codigo": codigo,
        "idArticulo": codigo,
        "idErp": codigo,
        "idVista": _texto(linea, "idVista", "ID VISTA"),
        "descripcion": descripcion,
        "nombre": descripcion,
        "unidad": unidad,
        "um": unidad,
        "umErp": unidad,
        "cantidadPedida": cantidad,
        "cantidadOriginal": cantidad,
        "origen": "manual-oficina",
    }


def crear_datos_pedido(lineas: Iterable[dict], ahora=None, order_id=None) -> dict:
    """Construye el JSON pendiente que consume la pantalla y el robot ERP."""
    momento = ahora or datetime.now()
    lineas_preparadas = [preparar_linea_manual(linea) for linea in lineas]
    if not lineas_preparadas:
        raise ErrorPedidoManual("Añade al menos un producto al pedido.")

    identificador = str(order_id or "").strip()
    if not identificador:
        identificador = (
            f"MANUAL-{momento:%Y%m%d-%H%M%S}-"
            f"{uuid.uuid4().hex[:6].upper()}"
        )
    if not _ID_PEDIDO_RE.fullmatch(identificador):
        raise ErrorPedidoManual("El identificador interno del pedido no es válido.")

    return {
        "pedido": {
            "orderId": identificador,
            "fechaInforme": momento.strftime("%d/%m/%Y"),
            "fechaCreacion": momento.isoformat(timespec="seconds"),
            "cine": "CineStock · oficina",
            "totalLineas": len(lineas_preparadas),
            "timestamp": int(momento.timestamp() * 1000),
            "origen": "manual-oficina",
            "estado": "PENDIENTE",
        },
        "origen": "manual-oficina",
        "lineas": lineas_preparadas,
    }


def _escribir_json_atomico(ruta: Path, datos: dict) -> None:
    temporal = ruta.with_name(f".{ruta.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporal.open("w", encoding="utf-8", newline="\n") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
            archivo.flush()
            os.fsync(archivo.fileno())
        for intento in range(5):
            try:
                os.replace(temporal, ruta)
                break
            except PermissionError:
                if intento == 4:
                    raise
                time.sleep(0.05 * (intento + 1))
    finally:
        try:
            temporal.unlink(missing_ok=True)
        except OSError:
            pass


def _escribir_bytes_atomico(ruta: Path, contenido: bytes) -> None:
    temporal = ruta.with_name(f".{ruta.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporal.open("wb") as archivo:
            archivo.write(contenido)
            archivo.flush()
            os.fsync(archivo.fileno())
        for intento in range(5):
            try:
                os.replace(temporal, ruta)
                break
            except PermissionError:
                if intento == 4:
                    raise
                time.sleep(0.05 * (intento + 1))
    finally:
        try:
            temporal.unlink(missing_ok=True)
        except OSError:
            pass


def guardar_pedido_manual(
    ruta_data,
    datos: dict,
    pdf_stockable: bytes,
    pdf_granel: bytes | None = None,
    ruta_stockable_origen=None,
) -> Path:
    """Guarda informes, historial y pedido activo para su revisión.

    Los PDF se guardan primero en ``data/pdf_pedidos`` con el mismo convenio
    de nombres que usa el receptor. Sus nombres relativos se añaden al bloque
    del pedido para que la UI los pueda abrir y descargar por ``orderId``.
    El PDF Stockable es obligatorio. Granel se asocia solo cuando el pedido
    incluye artículos de la plantilla oficial. No se interactúa con PeopleSoft
    ni se envía el pedido al ERP: eso sigue requiriendo la acción explícita habitual.
    """
    if not isinstance(pdf_stockable, bytes) or not pdf_stockable.startswith(b"%PDF-"):
        raise ErrorPedidoManual(
            "No se ha generado un PDF Stockable válido; no se guardó el pedido."
        )
    if pdf_granel is not None and (
        not isinstance(pdf_granel, bytes) or not pdf_granel.startswith(b"%PDF-")
    ):
        raise ErrorPedidoManual("El PDF de Granel generado no es válido.")

    plantilla_bytes = None
    if ruta_stockable_origen is not None:
        origen_path = Path(ruta_stockable_origen)
        if origen_path.is_file():
            plantilla_bytes = origen_path.read_bytes()
            if not plantilla_bytes.startswith(b"%PDF-"):
                raise ErrorPedidoManual("La plantilla Stockable original no es un PDF válido.")

    carpeta_data = Path(ruta_data)
    pedido = datos.get("pedido", {}) if isinstance(datos, dict) else {}
    order_id = str(pedido.get("orderId") or "").strip()
    if not _ID_PEDIDO_RE.fullmatch(order_id):
        raise ErrorPedidoManual("El pedido no tiene un identificador interno válido.")
    if not datos.get("lineas"):
        raise ErrorPedidoManual("El pedido no contiene líneas.")

    historial = carpeta_data / "historial_pedidos"
    pdf_dir = carpeta_data / "pdf_pedidos"
    historial.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    carpeta_data.mkdir(parents=True, exist_ok=True)

    if any(historial.glob(f"*_pedido_{order_id}.json")):
        raise ErrorPedidoManual(
            f"Ya existe un pedido con el identificador {order_id}."
        )

    try:
        momento = datetime.fromtimestamp(int(pedido.get("timestamp", 0)) / 1000)
    except (TypeError, ValueError, OSError, OverflowError):
        momento = datetime.now()

    prefijo_pdf = f"{momento:%Y%m%d_%H%M%S}_pedido_{order_id}"
    nombre_stockable = f"{prefijo_pdf}.pdf"
    nombre_granel = f"{prefijo_pdf}_granel.pdf" if pdf_granel is not None else None
    ruta_stockable = pdf_dir / nombre_stockable
    ruta_granel = pdf_dir / nombre_granel if nombre_granel else None
    if ruta_stockable.exists() or (ruta_granel and ruta_granel.exists()):
        raise ErrorPedidoManual(
            f"Ya existen informes PDF asociados al pedido {order_id}."
        )

    informes = {
        "stockable": nombre_stockable,
        "granel": nombre_granel,
    }
    pedido["informes"] = informes
    datos["pedido"] = pedido

    nombre = f"{momento:%Y%m%d_%H%M%S_%f}_pedido_{order_id}.json"
    archivo_historial = historial / nombre
    informe_paths = []
    historial_guardado = False

    try:
        _escribir_bytes_atomico(ruta_stockable, pdf_stockable)
        informe_paths.append(ruta_stockable)
        if pdf_granel is not None and ruta_granel is not None:
            _escribir_bytes_atomico(ruta_granel, pdf_granel)
            informe_paths.append(ruta_granel)
        if plantilla_bytes is not None:
            plantillas_dir = pdf_dir / ".plantillas"
            plantillas_dir.mkdir(parents=True, exist_ok=True)
            ruta_plantilla = plantillas_dir / f"{order_id}_stockable.pdf"
            _escribir_bytes_atomico(ruta_plantilla, plantilla_bytes)
            informe_paths.append(ruta_plantilla)

        # Mismo orden de commit que el receptor: informes -> historial ->
        # pedido_actual, para no anunciar un pedido sin sus informes.
        _escribir_json_atomico(archivo_historial, datos)
        historial_guardado = True
        _escribir_json_atomico(carpeta_data / "pedido_actual.json", datos)
    except Exception:
        # Si el commit del historial no ocurrió, los PDFs sueltos no deben
        # parecer asociados a un pedido válido. Tras el commit se conservan.
        if not historial_guardado:
            for ruta in informe_paths:
                try:
                    ruta.unlink(missing_ok=True)
                except OSError:
                    pass
        raise

    return archivo_historial


def actualizar_stockable_pedido(ruta_data, datos: dict) -> bool:
    """Regenera el Stockable de un pedido manual tras corregir cantidades."""
    carpeta_data = Path(ruta_data)
    pedido = datos.get("pedido", {}) if isinstance(datos, dict) else {}
    order_id = str(pedido.get("orderId") or "").strip()
    if not _ID_PEDIDO_RE.fullmatch(order_id):
        return False

    pdf_dir = carpeta_data / "pdf_pedidos"
    plantilla_base = pdf_dir / ".plantillas" / f"{order_id}_stockable.pdf"
    if not plantilla_base.is_file():
        return False

    informes = pedido.get("informes", {}) if isinstance(pedido, dict) else {}
    nombre_registrado = informes.get("stockable") if isinstance(informes, dict) else None
    ruta_destino = None
    if nombre_registrado:
        nombre_registrado = str(nombre_registrado)
        if Path(nombre_registrado).name == nombre_registrado:
            ruta_destino = pdf_dir / nombre_registrado
    if ruta_destino is None:
        coincidencias = sorted(pdf_dir.glob(f"*_pedido_{order_id}.pdf"))
        if not coincidencias:
            return False
        ruta_destino = coincidencias[0]

    lineas_positivas = []
    for linea in datos.get("lineas", []):
        bruto = linea.get("cantidadPedida", linea.get("cantidad", 0))
        try:
            valor = float(str(bruto).strip().replace(",", "."))
        except (TypeError, ValueError):
            valor = 0.0
        if valor > 0:
            lineas_positivas.append(linea)

    if lineas_positivas:
        contenido = generar_stockable_rellenado(plantilla_base, lineas_positivas)
    else:
        contenido = plantilla_base.read_bytes()

    _escribir_bytes_atomico(ruta_destino, contenido)
    return True


def actualizar_granel_pedido(ruta_data, datos: dict) -> bool:
    """Regenera el Granel asociado a un pedido manual tras corregir líneas."""
    carpeta_data = Path(ruta_data)
    pedido = datos.get("pedido", {}) if isinstance(datos, dict) else {}
    order_id = str(pedido.get("orderId") or "").strip()
    if not _ID_PEDIDO_RE.fullmatch(order_id):
        return False

    informes = pedido.get("informes", {}) if isinstance(pedido, dict) else {}
    nombre_registrado = informes.get("granel") if isinstance(informes, dict) else None
    if not nombre_registrado:
        return False
    nombre_registrado = str(nombre_registrado)
    if Path(nombre_registrado).name != nombre_registrado:
        return False
    ruta_destino = carpeta_data / "pdf_pedidos" / nombre_registrado
    if not ruta_destino.is_file():
        return False

    lineas_positivas = []
    for linea in datos.get("lineas", []):
        bruto = linea.get("cantidadPedida", linea.get("cantidad", 0))
        try:
            valor = float(str(bruto).strip().replace(",", "."))
        except (TypeError, ValueError):
            valor = 0.0
        if valor > 0:
            lineas_positivas.append(linea)

    contenido = generar_granel_rellenado(ruta_plantilla_granel(), lineas_positivas)
    if contenido is None:
        contenido = ruta_plantilla_granel().read_bytes()
    _escribir_bytes_atomico(ruta_destino, contenido)
    return True
