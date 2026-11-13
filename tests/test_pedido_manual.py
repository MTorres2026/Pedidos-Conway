"""Pruebas de la lógica sin interfaz para pedidos creados desde la oficina."""

import importlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from modulos.pedido_manual import (
    ErrorPedidoManual,
    actualizar_granel_pedido,
    actualizar_stockable_pedido,
    cargar_catalogo,
    crear_datos_pedido,
    guardar_pedido_manual,
    normalizar_producto_catalogo,
)


PDF_TOOLS_AVAILABLE = (
    importlib.util.find_spec("pypdf") is not None
    and importlib.util.find_spec("reportlab") is not None
)


def _importar_ui_pedidos():
    try:
        import PySide6  # noqa: F401
        return importlib.import_module("ui.pedidos")
    except ImportError:
        pass

    qt_core = types.ModuleType("PySide6.QtCore")
    qt_core.Qt = MagicMock()
    qt_core.QTimer = MagicMock

    class _BaseDummy:
        def __init__(self, *args, **kwargs):
            pass

    qt_widgets = types.ModuleType("PySide6.QtWidgets")
    for nombre in (
        "QAbstractItemView",
        "QComboBox",
        "QDialog",
        "QDoubleSpinBox",
        "QFileDialog",
        "QFormLayout",
        "QFrame",
        "QHBoxLayout",
        "QHeaderView",
        "QLabel",
        "QLineEdit",
        "QMessageBox",
        "QPushButton",
        "QTableWidget",
        "QTableWidgetItem",
        "QVBoxLayout",
        "QWidget",
    ):
        setattr(
            qt_widgets,
            nombre,
            _BaseDummy if nombre in ("QDialog", "QWidget") else MagicMock,
        )

    qt_pdf_widgets = types.ModuleType("PySide6.QtPdfWidgets")
    qt_pdf_widgets.QPdfView = MagicMock
    qt_pdf = types.ModuleType("PySide6.QtPdf")
    qt_pdf.QPdfDocument = MagicMock

    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qt_core
    pyside.QtWidgets = qt_widgets
    pyside.QtPdfWidgets = qt_pdf_widgets
    pyside.QtPdf = qt_pdf

    with patch.dict(
        sys.modules,
        {
            "PySide6": pyside,
            "PySide6.QtCore": qt_core,
            "PySide6.QtWidgets": qt_widgets,
            "PySide6.QtPdfWidgets": qt_pdf_widgets,
            "PySide6.QtPdf": qt_pdf,
        },
    ):
        sys.modules.pop("ui.pedido_manual", None)
        sys.modules.pop("ui.pedidos", None)
        return importlib.import_module("ui.pedidos")


class CatalogoManualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_carga_supermaestro_y_solo_incluye_productos_mapeados(self):
        (self.data / "supermaestro.json").write_text(
            json.dumps(
                {
                    "SUPERMAESTRO": [
                        {
                            "PRODUCTO": "Vasos grandes",
                            "GRUPO": "Vasos",
                            "ID VISTA": "10321",
                            "ID ERP": "10020001",
                            "UM ERP": "CAJ / caja",
                        },
                        {
                            "PRODUCTO": "Sin código ERP",
                            "UM ERP": "CAJ",
                        },
                        {
                            "PRODUCTO": "Sin unidad ERP",
                            "ID ERP": "10020002",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        productos = cargar_catalogo(self.data)

        self.assertEqual(len(productos), 1)
        self.assertEqual(productos[0]["descripcion"], "Vasos grandes")
        self.assertEqual(productos[0]["codigo"], "10020001")
        self.assertEqual(productos[0]["unidad"], "CAJ")
        self.assertEqual(productos[0]["grupo"], "Vasos")
        self.assertEqual(productos[0]["idVista"], "10321")

    def test_usa_el_nombre_alternativo_del_catalogo_flow(self):
        (self.data / "supermaestro_flow.json").write_text(
            json.dumps(
                [
                    {
                        "descripcion": "Agua",
                        "idErp": "1003",
                        "umErp": "UN",
                    }
                ]
            ),
            encoding="utf-8",
        )
        self.assertEqual(cargar_catalogo(self.data)[0]["codigo"], "1003")

    def test_producto_sin_codigo_o_um_no_se_puede_enviar(self):
        self.assertIsNone(
            normalizar_producto_catalogo(
                {"PRODUCTO": "Sin ERP", "UM ERP": "CAJ"}
            )
        )
        self.assertIsNone(
            normalizar_producto_catalogo(
                {"PRODUCTO": "Sin UM", "ID ERP": "1001"}
            )
        )

    def test_catalogo_ausente_da_error_claro(self):
        with self.assertRaisesRegex(FileNotFoundError, "supermaestro"):
            cargar_catalogo(self.data)


class PedidoManualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.ahora = datetime(2026, 11, 13, 12, 34, 56)

    def linea(self, **cambios):
        resultado = {
            "descripcion": "Vasos grandes",
            "codigo": "10020001",
            "idVista": "10321",
            "unidad": "CAJ / caja",
            "cantidad": 2,
        }
        resultado.update(cambios)
        return resultado

    def test_crea_pedido_en_formato_aceptado_por_el_robot(self):
        datos = crear_datos_pedido(
            [self.linea(cantidad="2,5")],
            ahora=self.ahora,
            order_id="MANUAL-PRUEBA-1",
        )

        self.assertEqual(datos["pedido"]["orderId"], "MANUAL-PRUEBA-1")
        self.assertEqual(datos["pedido"]["estado"], "PENDIENTE")
        self.assertEqual(datos["pedido"]["origen"], "manual-oficina")
        self.assertEqual(datos["pedido"]["totalLineas"], 1)
        linea = datos["lineas"][0]
        self.assertEqual(linea["codigo"], "10020001")
        self.assertEqual(linea["idArticulo"], "10020001")
        self.assertEqual(linea["unidad"], "CAJ")
        self.assertEqual(linea["idVista"], "10321")
        self.assertEqual(linea["cantidadPedida"], 2.5)
        self.assertEqual(linea["cantidadOriginal"], 2.5)
        self.assertEqual(linea["origen"], "manual-oficina")

    def test_genera_identificador_interno_unico_y_compatible_con_ficheros(self):
        datos = crear_datos_pedido([self.linea()], ahora=self.ahora)
        order_id = datos["pedido"]["orderId"]
        self.assertRegex(order_id, r"^MANUAL-20261113-123456-[A-F0-9]{6}$")

    def test_rechaza_pedido_vacio_cantidad_no_positiva_o_codigo_ausente(self):
        with self.assertRaisesRegex(ErrorPedidoManual, "al menos un producto"):
            crear_datos_pedido([], ahora=self.ahora)
        with self.assertRaisesRegex(ErrorPedidoManual, "mayor que cero"):
            crear_datos_pedido([self.linea(cantidad=0)], ahora=self.ahora)
        with self.assertRaisesRegex(ErrorPedidoManual, "código ERP"):
            crear_datos_pedido([self.linea(codigo="")], ahora=self.ahora)

    def test_guarda_en_historial_y_actualiza_pedido_actual(self):
        datos = crear_datos_pedido(
            [self.linea()],
            ahora=self.ahora,
            order_id="MANUAL-GUARDAR-1",
        )

        pdf = b"%PDF-1.4\nmanual stockable de prueba\n"
        archivo_historial = guardar_pedido_manual(
            self.data,
            datos,
            pdf_stockable=pdf,
        )

        self.assertTrue(archivo_historial.is_file())
        self.assertIn("_pedido_MANUAL-GUARDAR-1.json", archivo_historial.name)
        historial = json.loads(archivo_historial.read_text(encoding="utf-8"))
        actual = json.loads(
            (self.data / "pedido_actual.json").read_text(encoding="utf-8")
        )
        self.assertEqual(historial, datos)
        self.assertEqual(actual, datos)
        nombre_pdf = datos["pedido"]["informes"]["stockable"]
        self.assertIsNone(datos["pedido"]["informes"]["granel"])
        self.assertEqual(
            (self.data / "pdf_pedidos" / nombre_pdf).read_bytes(),
            pdf,
        )

    def test_exige_stockable_pdf_antes_de_persistir_pedido(self):
        datos = crear_datos_pedido(
            [self.linea()],
            ahora=self.ahora,
            order_id="MANUAL-SIN-PDF-1",
        )

        with self.assertRaisesRegex(ErrorPedidoManual, "PDF Stockable válido"):
            guardar_pedido_manual(self.data, datos, pdf_stockable=b"no es pdf")

        self.assertFalse((self.data / "pedido_actual.json").exists())
        self.assertEqual(list((self.data / "historial_pedidos").glob("*.json")), [])

    def test_asocia_el_pdf_de_granel_si_se_proporciona(self):
        datos = crear_datos_pedido(
            [self.linea()],
            ahora=self.ahora,
            order_id="MANUAL-GRANEL-1",
        )
        pdf_stockable = b"%PDF-1.4\nstockable\n"
        pdf_granel = b"%PDF-1.4\ngranel\n"

        guardar_pedido_manual(
            self.data,
            datos,
            pdf_stockable=pdf_stockable,
            pdf_granel=pdf_granel,
        )

        informes = datos["pedido"]["informes"]
        self.assertTrue(informes["granel"].endswith("_granel.pdf"))
        self.assertEqual(
            (self.data / "pdf_pedidos" / informes["granel"]).read_bytes(),
            pdf_granel,
        )

    def test_no_sobrescribe_un_pedido_que_ya_existe_en_historial(self):
        datos = crear_datos_pedido(
            [self.linea()],
            ahora=self.ahora,
            order_id="MANUAL-DUPLICADO-1",
        )
        pdf = b"%PDF-1.4\nmanual stockable de prueba\n"
        guardar_pedido_manual(self.data, datos, pdf_stockable=pdf)
        with self.assertRaisesRegex(ErrorPedidoManual, "Ya existe"):
            guardar_pedido_manual(self.data, datos, pdf_stockable=pdf)

    @unittest.skipUnless(PDF_TOOLS_AVAILABLE, "requiere pypdf y reportlab")
    def test_actualiza_stockable_tras_corregir_cantidades(self):
        from pypdf import PdfReader
        from modulos.informes_pedido import generar_stockable_rellenado

        plantilla = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "recursos"
            / "Vista_Reporte-1.PDF"
        )
        datos = crear_datos_pedido(
            [self.linea(cantidad=4)],
            ahora=self.ahora,
            order_id="MANUAL-CORRECCION-1",
        )
        pdf_inicial = generar_stockable_rellenado(plantilla, datos["lineas"])
        guardar_pedido_manual(
            self.data,
            datos,
            pdf_stockable=pdf_inicial,
            ruta_stockable_origen=plantilla,
        )

        datos["lineas"][0]["cantidadPedida"] = 19
        actualizado = actualizar_stockable_pedido(self.data, datos)
        self.assertTrue(actualizado)

        ruta_pdf = self.data / "pdf_pedidos" / datos["pedido"]["informes"]["stockable"]
        lector = PdfReader(io.BytesIO(ruta_pdf.read_bytes()))
        posiciones_19 = []
        lector.pages[0].extract_text(
            visitor_text=lambda t, *_a: posiciones_19.append(t.strip())
            if str(t).strip() == "19"
            else None
        )
        self.assertEqual(posiciones_19, ["19"])

    @unittest.skipUnless(PDF_TOOLS_AVAILABLE, "requiere pypdf y reportlab")
    def test_actualiza_granel_tras_corregir_cantidades(self):
        from pypdf import PdfReader
        from modulos.informes_pedido import generar_granel_rellenado, ruta_plantilla_granel

        plantilla_stockable = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "recursos"
            / "Vista_Reporte-1.PDF"
        )
        plantilla_granel = ruta_plantilla_granel()
        datos = crear_datos_pedido(
            [
                self.linea(
                    codigo="GAD",
                    idVista="",
                    descripcion="Goma Granel Ositos Oro Azucar",
                    unidad="KGS",
                    cantidad=2,
                )
            ],
            ahora=self.ahora,
            order_id="MANUAL-GRANEL-1",
        )
        pdf_granel = generar_granel_rellenado(plantilla_granel, datos["lineas"])
        guardar_pedido_manual(
            self.data,
            datos,
            pdf_stockable=plantilla_stockable.read_bytes(),
            pdf_granel=pdf_granel,
            ruta_stockable_origen=plantilla_stockable,
        )

        datos["lineas"][0]["cantidadPedida"] = 9
        self.assertTrue(actualizar_granel_pedido(self.data, datos))
        ruta_pdf = self.data / "pdf_pedidos" / datos["pedido"]["informes"]["granel"]
        lector = PdfReader(io.BytesIO(ruta_pdf.read_bytes()))
        valores = []
        lector.pages[0].extract_text(
            visitor_text=lambda texto, _cm, tm, *_args: valores.append((texto.strip(), tm[4], tm[5]))
            if texto.strip() in ("GAD", "9")
            else None
        )
        codigo = next(item for item in valores if item[0] == "GAD")
        cantidad = next(item for item in valores if item[0] == "9")
        self.assertAlmostEqual(cantidad[2], codigo[2] - 1, places=1)

    def test_buscar_pdf_pedido_usa_informe_registrado_y_no_confunde_ids(self):
        ui_pedidos = _importar_ui_pedidos()
        pdf_dir = self.data / "pdf_pedidos"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        (pdf_dir / "20261113_120000_pedido_12.pdf").write_bytes(b"%PDF-1.4\n12\n")
        (pdf_dir / "20261113_120000_pedido_12_granel.pdf").write_bytes(b"%PDF-1.4\n12g\n")

        pantalla = ui_pedidos.Pedidos.__new__(ui_pedidos.Pedidos)
        pantalla.base_dir = self.data.parent
        pantalla.order_id_actual = "2"
        pantalla.datos_pedido = {"pedido": {"orderId": "2"}}

        self.assertIsNone(pantalla._buscar_pdf_pedido(sufijo=""))
        self.assertIsNone(pantalla._buscar_pdf_pedido(sufijo="_granel"))

        archivo_2 = pdf_dir / "20261113_123456_pedido_2.pdf"
        archivo_2.write_bytes(b"%PDF-1.4\n2\n")
        self.assertEqual(pantalla._buscar_pdf_pedido(sufijo=""), archivo_2)

        manual = pdf_dir / "20261113_123456_pedido_MANUAL-1.pdf"
        manual.write_bytes(b"%PDF-1.4\nmanual\n")
        pantalla.order_id_actual = "MANUAL-1"
        pantalla.datos_pedido = {
            "pedido": {
                "orderId": "MANUAL-1",
                "origen": "manual-oficina",
                "informes": {"stockable": manual.name, "granel": None},
            }
        }
        self.assertEqual(pantalla._buscar_pdf_pedido(sufijo=""), manual)
        self.assertIsNone(pantalla._buscar_pdf_pedido(sufijo="_granel"))
        self.assertIn("plantilla oficial", pantalla._mensaje_pdf_granel_ausente())


if __name__ == "__main__":
    unittest.main()
