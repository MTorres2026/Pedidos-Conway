"""Pruebas del relleno del informe Stockable para pedidos manuales."""

import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

from modulos.informes_pedido import (
    ErrorInformePedido,
    codigos_granel,
    generar_granel_rellenado,
    generar_stockable_rellenado,
    ruta_plantilla_granel,
    validar_stockable,
)


PDF_TOOLS_AVAILABLE = (
    importlib.util.find_spec("pypdf") is not None
    and importlib.util.find_spec("reportlab") is not None
)


@unittest.skipUnless(PDF_TOOLS_AVAILABLE, "requiere pypdf y reportlab")
class StockableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pypdf import PdfReader

        cls.PdfReader = PdfReader
        cls.plantilla = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "recursos"
            / "Vista_Reporte-1.PDF"
        )

    def test_valida_plantilla_stockable_real_y_detecta_todos_sus_productos(self):
        posiciones = validar_stockable(self.plantilla)
        self.assertEqual(len(posiciones), 173)
        self.assertIn("10321", posiciones)
        self.assertIn("94", posiciones)

    def test_rellena_varias_paginas_del_stockable(self):
        resultado = generar_stockable_rellenado(
            self.plantilla,
            [
                {"idVista": "10321", "descripcion": "Salsa Chunky", "cantidadPedida": 4},
                {"idVista": "1427", "descripcion": "Agua 1L Vilas", "cantidadPedida": 12},
                {"idVista": "94", "descripcion": "Gafas 3D Niños", "cantidadPedida": 50},
            ],
        )
        lector = self.PdfReader(io.BytesIO(resultado))
        self.assertIn("4", lector.pages[0].extract_text())
        self.assertIn("12", lector.pages[1].extract_text())
        self.assertIn("50", lector.pages[10].extract_text())

    def test_rellena_columna_stocktake_count_en_filas_por_id_vista(self):
        lineas = [
            {
                "idVista": "10321",
                "descripcion": "Salsa Chunky",
                "cantidadPedida": 37.125,
            },
            {
                "idVista": "10509",
                "descripcion": "Pizza Mac and Cheese",
                "cantidadPedida": 2,
            },
        ]

        resultado = generar_stockable_rellenado(self.plantilla, lineas)
        lector = self.PdfReader(io.BytesIO(resultado))

        self.assertEqual(len(lector.pages), 11)
        pagina = lector.pages[0]
        texto = pagina.extract_text()
        self.assertIn("37,125", texto)
        self.assertIn("2", texto)

        posiciones = []

        def capturar(texto, cm, tm, _font, _size):
            if str(texto).strip() == "37,125":
                x = cm[0] * tm[4] + cm[2] * tm[5] + cm[4]
                y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
                posiciones.append((x, y))

        pagina.extract_text(visitor_text=capturar)
        self.assertEqual(len(posiciones), 1)
        self.assertAlmostEqual(posiciones[0][0], 556.0, places=1)
        self.assertAlmostEqual(posiciones[0][1], 433.75, places=1)

    def test_no_elimina_ceros_finales_de_cantidades_enteras(self):
        resultado = generar_stockable_rellenado(
            self.plantilla,
            [{"idVista": "10321", "descripcion": "Salsa", "cantidad": 200}],
        )
        lector = self.PdfReader(io.BytesIO(resultado))
        posiciones = []

        def capturar(texto, _cm, _tm, _font, _size):
            valor = str(texto).strip()
            if valor == "200":
                posiciones.append(valor)

        lector.pages[0].extract_text(visitor_text=capturar)
        self.assertEqual(posiciones, ["200"])

    def test_suma_cantidades_repetidas_del_mismo_id_vista(self):
        resultado = generar_stockable_rellenado(
            self.plantilla,
            [
                {"idVista": "10321", "descripcion": "Salsa", "cantidad": "1,25"},
                {"idVista": "10321", "descripcion": "Salsa", "cantidad": 2},
            ],
        )
        lector = self.PdfReader(io.BytesIO(resultado))
        texto = lector.pages[0].extract_text()
        self.assertIn("3,25", texto)

    def test_falla_sin_id_vista_o_si_el_producto_no_esta_en_el_pdf(self):
        casos = [
            {"descripcion": "Sin ID Vista", "cantidad": 1},
            {"idVista": "99999999", "descripcion": "No listado", "cantidad": 1},
        ]
        for linea in casos:
            with self.subTest(linea=linea):
                with self.assertRaisesRegex(ErrorInformePedido, "No se puede rellenar"): 
                    generar_stockable_rellenado(self.plantilla, [linea])

    def test_rechaza_cantidad_no_positiva_o_con_mas_de_tres_decimales(self):
        for cantidad in (0, 1.2345):
            with self.subTest(cantidad=cantidad):
                with self.assertRaises(ErrorInformePedido):
                    generar_stockable_rellenado(
                        self.plantilla,
                        [{"idVista": "10321", "descripcion": "Salsa", "cantidad": cantidad}],
                    )

    def test_rechaza_otro_pdf_que_no_sea_un_stockable(self):
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(612, 792))
        pdf.drawString(40, 700, "Otro informe")
        pdf.save()
        with tempfile.TemporaryDirectory() as temporal:
            falso_stockable = Path(temporal) / "otro.pdf"
            falso_stockable.write_bytes(buffer.getvalue())
            with self.assertRaisesRegex(ErrorInformePedido, "no parece un informe Stockable"):
                generar_stockable_rellenado(
                    falso_stockable,
                    [{"idVista": "10321", "descripcion": "Salsa", "cantidad": 1}],
                )


@unittest.skipUnless(PDF_TOOLS_AVAILABLE, "requiere pypdf y reportlab")
class GranelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pypdf import PdfReader

        cls.PdfReader = PdfReader
        cls.plantilla = ruta_plantilla_granel()

    def test_valida_plantilla_oficial_y_detecta_codigos_erp(self):
        codigos = codigos_granel(self.plantilla)
        self.assertEqual(len(codigos), 100)
        self.assertIn("GAD", codigos)
        self.assertIn("IGM", codigos)
        self.assertNotIn("KGS", codigos)

    def test_rellena_filas_de_varias_paginas_y_resuelve_codigo_duplicado(self):
        salida = generar_granel_rellenado(
            self.plantilla,
            [
                {"codigo": "GAD", "descripcion": "Ositos Oro", "cantidad": 7},
                {
                    "codigo": "HCD",
                    "descripcion": "Haribo Dinos Pica Bolsa 1 Kilo",
                    "cantidad": "2,5",
                },
                {
                    "codigo": "IGM",
                    "descripcion": "M&M'S Cacahuete Granel 800 gr",
                    "cantidad": 3,
                },
            ],
        )
        lector = self.PdfReader(io.BytesIO(salida))
        self.assertEqual(len(lector.pages), 2)

        posiciones = {"HCD": [], "2,5": [], "IGM": [], "3": []}
        for indice, pagina in enumerate(lector.pages):
            pagina.extract_text(
                visitor_text=lambda texto, _cm, tm, *_args, p=indice: posiciones[
                    texto.strip()
                ].append((p, tm[4], tm[5]))
                if texto.strip() in posiciones
                else None
            )
        self.assertTrue(any(pagina == 0 for pagina, _x, _y in posiciones["2,5"]))
        self.assertTrue(any(pagina == 1 for pagina, _x, _y in posiciones["3"]))
        hcd_codigo = next(pos for pos in posiciones["HCD"] if pos[0] == 0)
        hcd_cantidad = next(pos for pos in posiciones["2,5"] if pos[0] == 0)
        self.assertAlmostEqual(hcd_cantidad[2], hcd_codigo[2] - 1, places=1)

    def test_no_genera_granel_si_no_hay_codigos_de_su_formulario(self):
        salida = generar_granel_rellenado(
            self.plantilla,
            [{"codigo": "NO-ES-GRANEL", "cantidad": 5}],
        )
        self.assertIsNone(salida)

    def test_stockable_omite_lineas_del_formulario_granel_sin_id_vista(self):
        plantilla_stockable = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "recursos"
            / "Vista_Reporte-1.PDF"
        )
        salida = generar_stockable_rellenado(
            plantilla_stockable,
            [
                {"codigo": "GAD", "descripcion": "Ositos", "cantidad": 2},
                {"codigo": "10020001", "idVista": "10321", "cantidad": 4},
            ],
        )
        pagina = self.PdfReader(io.BytesIO(salida)).pages[0]
        self.assertIn("4", pagina.extract_text())


if __name__ == "__main__":
    unittest.main()
