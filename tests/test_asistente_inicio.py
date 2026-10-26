"""Pruebas del Asistente Híbrido de Inicio (búsqueda por secciones + RAG opcional)."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from modulos.asistente_inicio import (
    buscar_procedimiento_confirmado,
    construir_contexto_rag,
    consultar_base_local,
    detectar_proveedor_ia,
    dividir_documento_en_secciones,
    extraer_terminos_consulta,
    renderizar_respuesta_asistente_html,
    sintetizar_con_ia,
)
from modulos.biblioteca import Biblioteca


MANUAL_OPERATIVA_EJEMPLO = """
MANUAL DE OPERATIVA DE BAR Y ALMACÉN - CINES

APERTURA DIARIA DE BAR
1. Encender vitrinas y palomitera 30 minutos antes de la primera sesión.
2. Revisar limpieza de dispensadores de refrescos.

PROCESO DE INVENTARIO SEMANAL
Para realizar el inventario semanal de almacén y barras sigue estos pasos:
1. Imprimir las hojas de conteo desde Cinema Manager antes del cierre del domingo.
2. Contar físicamente el almacén principal separando cajas cerradas y unidades sueltas.
3. Contar las neveras y barras secundarias con el local ya cerrado.
4. Introducir todas las cantidades en el módulo Stock -> Conteo Físico y pulsar Guardar.

CONCILIACIÓN DE STOCK Y DESCUADRES
Una vez grabado el inventario, hay que realizar la conciliación:
1. Entrar en Cinema Manager -> Inventario -> Conciliación de Stock.
2. Comprobar que todos los albaranes de Conway de la semana figuran recepcionados.
3. Revisar la columna Variación para localizar diferencias mayores de 5 unidades.
4. Recontar en almacén las referencias con descuadre antes de validar el cierre definitivo.

REGISTRO DE MERMAS Y CADUCIDADES
1. Anotar en la hoja diaria cualquier rotura de envase o producto caducado.
2. Dar de baja en el sistema eligiendo el motivo exacto (Rotura / Caducidad).
""".strip()


PRESENTACION_PPTX_SIMULADA = """
--- Diapositiva 1 ---
Formación General Yelmo 2026
Bienvenida al equipo

--- Diapositiva 2 ---
Recepción de pedidos Conway y Albarán
1. Revisar bultos físicos frente al albarán del transportista.
2. Comprobar temperaturas de producto congelado y refrigerado.
3. Anotar el ID Recepción manualmente en CineStock tras grabar el albarán en ERP.
""".strip()


class AsistenteInicioLocalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.bib = Biblioteca(self.raiz / "biblioteca")
        self.addCleanup(self.bib.cerrar)
        self.ocr_patch = patch.object(self.bib, "_ocr_callable", return_value=None)
        self.ocr_patch.start()
        self.addCleanup(self.ocr_patch.stop)

        ruta_manual = self.raiz / "Manual_Inventario_y_Conciliacion_2026.txt"
        ruta_manual.write_text(MANUAL_OPERATIVA_EJEMPLO, encoding="utf-8")
        alta1 = self.bib.agregar_archivo(ruta_manual)
        self.assertTrue(alta1.ok)
        self.id_manual = alta1.id

        ruta_ppt = self.raiz / "Recepcion_Conway_2026.txt"
        ruta_ppt.write_text(PRESENTACION_PPTX_SIMULADA, encoding="utf-8")
        alta2 = self.bib.agregar_archivo(ruta_ppt)
        self.assertTrue(alta2.ok)
        self.id_ppt = alta2.id

    def test_procedimientos_confirmados_cargan_desde_src_datos_sin_falsos_positivos(self):
        # Coincidencia legítima con matinales
        proc_matinal = buscar_procedimiento_confirmado("¿Cómo gestionamos una matinal de Disney?")
        self.assertIsNotNone(proc_matinal)
        self.assertEqual(proc_matinal.id, "matinales_centros_educativos")
        self.assertTrue(any("Disney" in p for p in proc_matinal.pasos))

        # Preguntar por inventario o conciliación NO debe disparar "cambiar película de sala"
        proc_inv = buscar_procedimiento_confirmado("¿Cómo hago para hacer el inventario?")
        self.assertIsNone(proc_inv)
        proc_conc = buscar_procedimiento_confirmado("¿Cómo hacer una conciliación?")
        self.assertIsNone(proc_conc)

    def test_extrae_seccion_exacta_de_inventario_completa(self):
        resp = consultar_base_local("¿Cómo hago para hacer el inventario?", self.bib)
        self.assertTrue(resp.pasajes)
        principal = resp.pasajes[0]
        self.assertEqual(principal.doc_id, self.id_manual)
        self.assertIn("Conteo Físico", principal.texto_seccion)
        self.assertIn("1. Imprimir las hojas de conteo", principal.texto_seccion)

    def test_extrae_seccion_exacta_de_conciliacion_incluso_con_verbo_o_sinonimo(self):
        for pregunta in (
            "¿Cómo se hace una conciliación?",
            "¿Cómo conciliar las diferencias del stock?",
        ):
            with self.subTest(pregunta=pregunta):
                resp = consultar_base_local(pregunta, self.bib)
                self.assertTrue(resp.pasajes)
                textos = "\n".join(p.texto_seccion for p in resp.pasajes)
                self.assertIn("Conciliación de Stock", textos)
                self.assertIn("Variación", textos)

    def test_divide_diapositivas_y_encuentra_albaran(self):
        secciones = dividir_documento_en_secciones(PRESENTACION_PPTX_SIMULADA)
        self.assertEqual(len(secciones), 2)
        self.assertEqual(secciones[1][0], "Diapositiva 2")

        resp = consultar_base_local("¿Cómo hago la recepción de un albarán de Conway?", self.bib)
        self.assertTrue(resp.pasajes)
        self.assertEqual(resp.pasajes[0].doc_id, self.id_ppt)
        self.assertEqual(resp.pasajes[0].titulo_seccion, "Diapositiva 2")
        self.assertIn("ID Recepción", resp.pasajes[0].texto_seccion)

    def test_memoria_conversacional_en_preguntas_de_seguimiento(self):
        principales, _ = extraer_terminos_consulta(
            "¿Y qué advertencias debo comprobar?",
            historial_preguntas=["¿Cómo se organiza una matinal de un colegio?"],
        )
        self.assertIn("matinal", principales)

    def test_html_renderiza_enlaces_abrir_y_escapa_html_peligroso(self):
        ruta_xss = self.raiz / "Nota_Mermas.txt"
        ruta_xss.write_text(
            "MERMAS\n1. Registrar merma en <script>alert(1)</script> barra.",
            encoding="utf-8",
        )
        alta = self.bib.agregar_archivo(ruta_xss)
        resp = consultar_base_local("¿Cómo registrar mermas?", self.bib)
        html_out = renderizar_respuesta_asistente_html(resp)
        self.assertIn(f'href="abrir:{alta.id}"', html_out)
        self.assertNotIn("<script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)


class AsistenteInicioIATests(unittest.TestCase):
    def test_sintetizar_con_openai_y_caida_segura_si_falla_red(self):
        respuesta_falsa = {
            "choices": [
                {
                    "message": {
                        "content": "1. Entra en **Inventario -> Conciliación** [Manual Inventario]."
                    }
                }
            ]
        }
        mock_http = MagicMock()
        mock_http.read.return_value = json.dumps(respuesta_falsa).encode("utf-8")
        mock_http.__enter__.return_value = mock_http

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123", "GEMINI_API_KEY": ""}, clear=False):
            self.assertEqual(detectar_proveedor_ia(), ("OpenAI", "sk-test-123"))
            with patch("urllib.request.urlopen", return_value=mock_http) as llamada:
                texto, prov, aviso = sintetizar_con_ia(
                    "¿Cómo hago la conciliación?",
                    "[FUENTE 1: Manual Inventario]\n1. Entra en Inventario -> Conciliación.",
                )
                self.assertIn("Conciliación", texto)
                self.assertIn("OpenAI", prov)
                self.assertEqual(aviso, "")
                llamada.assert_called_once()

            # Si falla Internet o el firewall del cine corta la salida, no lanza excepción
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("sin red")):
                texto_err, prov_err, aviso_err = sintetizar_con_ia(
                    "¿Cómo hago la conciliación?",
                    "[FUENTE 1: Manual Inventario]\n1. Entra en Inventario -> Conciliación.",
                )
                self.assertEqual(texto_err, "")
                self.assertEqual(prov_err, "OpenAI")
                self.assertIn("manuales", aviso_err)

    def test_sintetizar_con_gemini(self):
        respuesta_gemini = {
            "candidates": [
                {"content": {"parts": [{"text": "1. Revisa bultos frente al albarán [Recepcion Conway]."}]}}
            ]
        }
        mock_http = MagicMock()
        mock_http.read.return_value = json.dumps(respuesta_gemini).encode("utf-8")
        mock_http.__enter__.return_value = mock_http

        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "AIza-test"}, clear=False):
            with patch("modulos.asistente_inicio._leer_clave_env_local", side_effect=lambda k: "AIza-test" if k == "GEMINI_API_KEY" else ""):
                with patch("urllib.request.urlopen", return_value=mock_http):
                    texto, prov, aviso = sintetizar_con_ia(
                        "¿Cómo recibo un pedido?",
                        "[FUENTE 1: Recepcion Conway]\n1. Revisa bultos.",
                    )
                    self.assertIn("albarán", texto)
                    self.assertIn("Gemini", prov)
                    self.assertEqual(aviso, "")


class InicioInterfazChatTests(unittest.TestCase):
    def _cargar_modulo_inicio(self):
        import importlib
        import sys
        import types

        qt_core = types.ModuleType("PySide6.QtCore")
        qt_core.Qt = MagicMock()
        qt_core.QUrl = MagicMock()
        qt_core.Signal = lambda *a, **kw: MagicMock()

        class _DummyThread:
            def __init__(self, *args, **kwargs):
                self.resultado_listo = MagicMock()
                self.finished = MagicMock()

            def start(self):
                pass

            def deleteLater(self):
                pass

        qt_core.QThread = _DummyThread

        qt_gui = types.ModuleType("PySide6.QtGui")
        qt_gui.QDesktopServices = MagicMock()
        qt_gui.QTextCursor = MagicMock()

        class _DummyWidget:
            def __init__(self, *args, **kwargs):
                pass

            def showEvent(self, event):
                pass

        class _DummyLineEdit:
            def __init__(self):
                self._t = ""
                self.returnPressed = MagicMock()

            def setText(self, v):
                self._t = v

            def text(self):
                return self._t

            def clear(self):
                self._t = ""

            def setPlaceholderText(self, *a):
                pass

            def setStyleSheet(self, *a):
                pass

            def setFocus(self):
                pass

        class _DummyBrowser:
            def __init__(self):
                self.ultimo_html = ""
                self.anchorClicked = MagicMock()
                self._bar = MagicMock()
                self._bar.value.return_value = 0
                self._bar.maximum.return_value = 100

            def setHtml(self, h):
                self.ultimo_html = h

            def setOpenLinks(self, *a):
                pass

            def setOpenExternalLinks(self, *a):
                pass

            def setStyleSheet(self, *a):
                pass

            def verticalScrollBar(self):
                return self._bar

            def moveCursor(self, *a):
                pass

        class _DummyCheck:
            def __init__(self, *a):
                self._checked = False
                self.toggled = MagicMock()

            def isChecked(self):
                return self._checked

            def setChecked(self, v):
                self._checked = bool(v)

            def setEnabled(self, *a):
                pass

            def setToolTip(self, *a):
                pass

            def setStyleSheet(self, *a):
                pass

            def blockSignals(self, *a):
                pass

        qt_widgets = types.ModuleType("PySide6.QtWidgets")
        qt_widgets.QWidget = _DummyWidget
        qt_widgets.QLineEdit = _DummyLineEdit
        qt_widgets.QTextBrowser = _DummyBrowser
        qt_widgets.QCheckBox = _DummyCheck
        qt_widgets.QHBoxLayout = lambda *a, **kw: MagicMock()
        qt_widgets.QVBoxLayout = lambda *a, **kw: MagicMock()
        qt_widgets.QLabel = lambda *a, **kw: MagicMock()
        qt_widgets.QPushButton = lambda *a, **kw: MagicMock()
        qt_widgets.QMessageBox = MagicMock()

        pyside = types.ModuleType("PySide6")
        pyside.QtCore = qt_core
        pyside.QtGui = qt_gui
        pyside.QtWidgets = qt_widgets

        with patch.dict(
            sys.modules,
            {
                "PySide6": pyside,
                "PySide6.QtCore": qt_core,
                "PySide6.QtGui": qt_gui,
                "PySide6.QtWidgets": qt_widgets,
            },
        ):
            sys.modules.pop("ui.apertura_documentos", None)
            sys.modules.pop("ui.inicio", None)
            return importlib.import_module("ui.inicio")

    def test_flujo_chat_y_limpieza_en_inicio(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        raiz = Path(tmp.name)
        bib = Biblioteca(raiz / "bib")
        self.addCleanup(bib.cerrar)
        with patch.object(bib, "_ocr_callable", return_value=None):
            f = raiz / "Manual_Conciliacion.txt"
            f.write_text(MANUAL_OPERATIVA_EJEMPLO, encoding="utf-8")
            bib.agregar_archivo(f)

        mod_inicio = self._cargar_modulo_inicio()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "GEMINI_API_KEY": ""}, clear=False):
            with patch("modulos.asistente_inicio._leer_clave_env_local", return_value=""):
                widget = mod_inicio.Inicio()
                widget._biblioteca = bib
                widget.actualizar()
                self.assertIn("Hola", widget.respuesta.ultimo_html)

                widget.hacer_pregunta("¿Cómo se hace una conciliación?")
                self.assertEqual(widget.pregunta.text(), "")
                self.assertIn("CONCILIACIÓN DE STOCK Y DESCUADRES", widget.respuesta.ultimo_html)
                self.assertIn("Conciliación</span> de Stock", widget.respuesta.ultimo_html)
                self.assertIn("Variación", widget.respuesta.ultimo_html)
                self.assertIn("Tú:", widget.respuesta.ultimo_html)

                widget.limpiar_chat()
                self.assertIn("Hola", widget.respuesta.ultimo_html)


if __name__ == "__main__":
    unittest.main()
