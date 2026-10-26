"""Pruebas del Asistente de Alta Precisión de Inicio ('Manual exacto o No disponible')."""

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from modulos.asistente_inicio import (
    AdjuntoIA,
    MARCA_NO_DISPONIBLE,
    buscar_procedimiento_confirmado,
    consultar_base_local,
    detectar_proveedor_ia,
    dividir_documento_en_secciones,
    extraer_terminos_consulta,
    formatear_markdown_ia_html,
    guardar_cache_manual_ia,
    leer_cache_manual_ia,
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
Una vez grabado el inventario, hay que realizar la conciliación de stock:
1. Entrar en Cinema Manager -> Inventario -> Conciliación de Stock.
2. Comprobar que todos los albaranes de Conway de la semana figuran recepcionados.
3. Revisar la columna Variación para localizar diferencias mayores de 5 unidades.
4. Recontar en almacén las referencias con descuadre antes de validar el cierre definitivo.

REGISTRO DE MERMAS Y CADUCIDADES
1. Anotar en la hoja diaria cualquier rotura de envase o merma de producto caducado.
2. Entrar al módulo de Mermas y dar de baja eligiendo el motivo exacto (Rotura / Caducidad).
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


DOCUMENTOS_FALSOS_AMIGOS = {
    "Politica_RRHH_Conciliacion.txt": """
CONCILIACIÓN DE LA VIDA FAMILIAR Y LABORAL
1. El empleado deberá presentar el formulario de conciliación horaria con 15 días de antelación.
2. Recursos Humanos revisará el cuadrante del centro y responderá por escrito.
3. Guardar copia firmada en el expediente laboral.
""".strip(),
    "Protocolo_Solicitudes_Permisos_RRHH.txt": """
SOLICITUD DE PERMISOS RETRIBUIDOS Y VACACIONES
1. Acceder al portal del empleado con usuario corporativo.
2. Pulsar en Nueva Solicitud y elegir el tipo de permiso o ausencia.
3. Adjuntar justificante médico o documento acreditativo y pulsar Enviar solicitud.
""".strip(),
    "Correo_Aviso_Informal.txt": """
Hola equipo,
Os recuerdo que esta semana por el festivo tenéis que mandar la solicitud de pedido el lunes antes de las 12:00.
Un saludo y buena semana.
""".strip(),
}


class AsistentePrecisionEstrictaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.bib = Biblioteca(self.raiz / "biblioteca")
        self.addCleanup(self.bib.cerrar)
        self.ocr_patch = patch.object(self.bib, "_ocr_callable", return_value=None)
        self.ocr_patch.start()
        self.addCleanup(self.ocr_patch.stop)

        # Cargar solo los "falsos amigos" en una biblioteca de ruido para verificar rechazos
        self.bib_ruido = Biblioteca(self.raiz / "bib_ruido")
        self.addCleanup(self.bib_ruido.cerrar)
        self.ocr_patch2 = patch.object(self.bib_ruido, "_ocr_callable", return_value=None)
        self.ocr_patch2.start()
        self.addCleanup(self.ocr_patch2.stop)

        for nombre, texto in DOCUMENTOS_FALSOS_AMIGOS.items():
            f = self.raiz / nombre
            f.write_text(texto, encoding="utf-8")
            self.bib_ruido.agregar_archivo(f)
            self.bib.agregar_archivo(f)

        # En self.bib sí añadimos además los manuales reales
        ruta_manual = self.raiz / "Manual_Inventario_y_Conciliacion_2026.txt"
        ruta_manual.write_text(MANUAL_OPERATIVA_EJEMPLO, encoding="utf-8")
        self.id_manual = self.bib.agregar_archivo(ruta_manual).id

        ruta_ppt = self.raiz / "Recepcion_Conway_2026.txt"
        ruta_ppt.write_text(PRESENTACION_PPTX_SIMULADA, encoding="utf-8")
        self.id_ppt = self.bib.agregar_archivo(ruta_ppt).id

    def test_rechaza_conciliacion_de_otra_indole_y_muestra_no_disponible(self):
        resp = consultar_base_local("¿Cómo se hace la conciliación de inventario?", self.bib_ruido)
        self.assertFalse(resp.tiene_contenido)
        html_out = renderizar_respuesta_asistente_html(resp)
        self.assertIn("PROCEDIMIENTO NO DISPONIBLE", html_out)
        self.assertNotIn("FAMILIAR", html_out)

    def test_rechaza_solicitudes_de_otra_indole_y_correos_sin_pasos(self):
        resp = consultar_base_local("¿Cómo hacer una solicitud de pedido?", self.bib_ruido)
        self.assertFalse(resp.tiene_contenido)
        html_out = renderizar_respuesta_asistente_html(resp)
        self.assertIn("PROCEDIMIENTO NO DISPONIBLE", html_out)
        self.assertNotIn("VACACIONES", html_out)
        self.assertNotIn("festivo", html_out)

    def test_acepta_solicitud_de_pedido_cuando_existe_manual_real(self):
        manual_erp = self.raiz / "Manual_Solicitud_Pedido_ERP.txt"
        manual_erp.write_text(
            "CREACIÓN DE SOLICITUD DE PEDIDO EN ERP\n"
            "1. Entrar en PeopleSoft -> Compras -> Crear Solicitud de Pedido.\n"
            "2. Introducir el código de artículo y la cantidad de cajas.\n"
            "3. Pulsar Guardar para obtener el ID Solicitud.",
            encoding="utf-8",
        )
        self.bib_ruido.agregar_archivo(manual_erp)
        resp = consultar_base_local("¿Cómo hacer una solicitud de pedido?", self.bib_ruido)
        self.assertTrue(resp.tiene_contenido)
        self.assertIn("PeopleSoft", resp.pasajes[0].texto_seccion)
        self.assertNotIn("VACACIONES", resp.pasajes[0].texto_seccion)

    def test_rechaza_mencion_lateral_de_inventario_en_cierre_de_dia(self):
        pdf_cierre = """
Confidencial
4. Ingresar el folio del comprobante del servicio de ETV en el campo “Número de Placa de
mensajero”.
5. Presionar el botón “Enviar”.
ESP-TLC-GC-CIMA-00 GC CINEMA MANAGER – VISTA CLOUD 04/11/2025 Página 21 de 29
Confidencial
10. Cierre de Día
Después de que los fondos se hayan enviado, el cierre de día deberá realizarse de la siguiente manera:
1. Si en el día actual se realizó toma de inventario, antes de cerrar el día, desde Gerencia se debe
generar el reporte “Estado de Inventario” y validar que se haya finalizado.
2. Presionar el botón “Día de cierre”.
""".strip()
        f_cierre = self.raiz / "GC_CINEMA_MANAGER_VISTA_CLOUD.txt"
        f_cierre.write_text(pdf_cierre, encoding="utf-8")
        self.bib_ruido.agregar_archivo(f_cierre)

        resp = consultar_base_local("¿Cómo hago el inventario?", self.bib_ruido)
        self.assertFalse(resp.tiene_contenido)
        html_out = renderizar_respuesta_asistente_html(resp)
        self.assertIn("PROCEDIMIENTO NO DISPONIBLE", html_out)
        self.assertNotIn("Placa de mensajero", html_out)

    def test_encuentra_exactamente_conciliacion_de_inventario_cuando_existe_manual(self):
        resp = consultar_base_local("¿Cómo se hace la conciliación de inventario?", self.bib)
        self.assertTrue(resp.tiene_contenido)
        self.assertEqual(resp.pasajes[0].doc_id, self.id_manual)
        self.assertIn("Conciliación de Stock", resp.pasajes[0].texto_seccion)
        for p in resp.pasajes:
            self.assertNotIn("FAMILIAR", p.texto_seccion)

    def test_encuentra_manual_con_guiones_bajos_prosa_y_plural_inventarios(self):
        bib_dedicada = Biblioteca(self.raiz / "bib_dedicada")
        self.addCleanup(bib_dedicada.cerrar)
        with patch.object(bib_dedicada, "_ocr_callable", return_value=None):
            # Manual con '_' en el nombre, viñetas PDF '' y subapartados que no repiten el título
            manual_pdf_prosa = self.raiz / "20260918_Manual_Conciliacion_de_Inventarios.txt"
            manual_pdf_prosa.write_text(
                "1. Acceso al módulo\n"
                " Ingresar en Cinema Manager con credenciales de gerencia y abrir la pestaña principal.\n"
                "2. Revisión de variaciones\n"
                " Seleccionar la fecha del conteo semanal en el desplegable y pulsar Calcular.\n"
                " Verificar en pantalla los artículos con diferencias antes de pulsar Finalizar.",
                encoding="utf-8",
            )
            alta = bib_dedicada.agregar_archivo(manual_pdf_prosa)

        for consulta in (
            "conciliacion de inventarios",
            "¿Cómo se hace la conciliación de inventario?",
        ):
            with self.subTest(consulta=consulta):
                resp = consultar_base_local(consulta, bib_dedicada)
                self.assertTrue(resp.tiene_contenido)
                self.assertEqual(resp.pasajes[0].doc_id, alta.id)
                unido = "\n".join(p.texto_seccion for p in resp.pasajes)
                self.assertIn("Cinema Manager", unido)
                self.assertIn("Finalizar", unido)

    def test_detecta_pdf_escaneado_sin_texto_por_su_titulo_y_ofrece_abrirlo(self):
        bib_scan = Biblioteca(self.raiz / "bib_scan")
        self.addCleanup(bib_scan.cerrar)
        with patch.object(bib_scan, "_ocr_callable", return_value=None):
            f_vacio = self.raiz / "2026_Conciliacion_Inventarios_Guia.txt"
            f_vacio.write_text("", encoding="utf-8")
            alta = bib_scan.agregar_archivo(f_vacio)

        resp = consultar_base_local("conciliación de inventarios", bib_scan)
        self.assertTrue(resp.tiene_contenido)
        self.assertTrue(resp.solo_documento_sin_ocr)
        html_out = renderizar_respuesta_asistente_html(resp)
        self.assertIn("Manual localizado", html_out)
        self.assertIn(f'href="abrir:{alta.id}"', html_out)

    def test_divide_diapositivas_y_encuentra_albaran_exacto(self):
        secciones = dividir_documento_en_secciones(PRESENTACION_PPTX_SIMULADA)
        self.assertEqual(len(secciones), 2)
        self.assertEqual(secciones[1][0], "Diapositiva 2")

        resp = consultar_base_local("¿Cómo registro el albarán de Conway?", self.bib)
        self.assertTrue(resp.tiene_contenido)
        self.assertEqual(resp.pasajes[0].doc_id, self.id_ppt)
        self.assertEqual(resp.pasajes[0].titulo_seccion, "Diapositiva 2")

    def test_procedimientos_confirmados_sin_falsos_positivos(self):
        proc_matinal = buscar_procedimiento_confirmado("¿Cómo gestionamos una matinal?")
        self.assertIsNotNone(proc_matinal)
        self.assertEqual(proc_matinal.id, "matinales_centros_educativos")

        self.assertIsNone(buscar_procedimiento_confirmado("¿Cómo hago un cambio de turno?"))
        self.assertIsNone(buscar_procedimiento_confirmado("¿Cómo hago una solicitud de pedido?"))
        self.assertIsNone(buscar_procedimiento_confirmado("¿Cómo hago el inventario?"))

    def test_html_escapa_scripts_peligrosos(self):
        ruta_xss = self.raiz / "Manual_Mermas_Seguridad.txt"
        ruta_xss.write_text(
            "REGISTRO DE MERMAS\n"
            "1. Entrar al módulo de mermas en <script>alert(1)</script>.\n"
            "2. Seleccionar motivo y pulsar Guardar merma.",
            encoding="utf-8",
        )
        alta = self.bib.agregar_archivo(ruta_xss)
        resp = consultar_base_local("¿Cómo registrar mermas?", self.bib)
        html_out = renderizar_respuesta_asistente_html(resp)
        self.assertIn(f'href="abrir:{alta.id}"', html_out)
        self.assertNotIn("<script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)


class AsistenteIATests(unittest.TestCase):
    def test_verificador_ia_devuelve_no_disponible_cuando_contexto_no_corresponde(self):
        respuesta_falsa = {
            "choices": [{"message": {"content": "NO_DISPONIBLE"}}]
        }
        mock_http = MagicMock()
        mock_http.read.return_value = json.dumps(respuesta_falsa).encode("utf-8")
        mock_http.__enter__.return_value = mock_http

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123"}, clear=False):
            with patch("urllib.request.urlopen", return_value=mock_http):
                texto, prov, aviso = sintetizar_con_ia(
                    "¿Cómo hago una solicitud de pedido?",
                    "[FUENTE 1: Solicitud vacaciones]\n1. Pedir días libres.",
                )
                self.assertEqual(texto, MARCA_NO_DISPONIBLE)
                self.assertIn("OpenAI", prov)
                self.assertEqual(aviso, "")

    def test_sintetizar_con_openai_y_caida_segura(self):
        respuesta_falsa = {
            "choices": [
                {
                    "message": {
                        "content": (
                            "# Manual paso a paso: Conciliación de Inventarios\n"
                            "OBJETIVO: Cuadrar el stock físico frente al sistema.\n\n"
                            "1. **Acceso al módulo**: Entra en [Cinema Manager] y pulsa [Inventario].\n"
                            "   - Cierra antes todos los TPV.\n"
                            "2. **Cierre y grabación**: Revisa diferencias y pulsa [Guardar].\n\n"
                            "## ⚠️ Muy importante\n"
                            "- Una vez cerrado no permite cambios.\n\n"
                            "## ✅ Comprobación final\n"
                            "- Verifica el estado [CONCILIADO]."
                        )
                    }
                }
            ]
        }
        mock_http = MagicMock()
        mock_http.read.return_value = json.dumps(respuesta_falsa).encode("utf-8")
        mock_http.__enter__.return_value = mock_http

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123"}, clear=False):
            self.assertEqual(detectar_proveedor_ia(), ("OpenAI", "sk-test-123"))
            with patch("urllib.request.urlopen", return_value=mock_http) as mock_open:
                adj_pdf = AdjuntoIA(
                    nombre="Conciliacion_Inventarios.pdf",
                    mime="application/pdf",
                    datos_b64="JVBERi0xLjQK",
                )
                texto, prov, aviso = sintetizar_con_ia(
                    "¿Cómo hago la conciliación de inventario?",
                    "[FUENTE 1: Manual Inventario]",
                    adjuntos_ia=[adj_pdf],
                )
                self.assertIn("Manual paso a paso", texto)
                self.assertEqual(aviso, "")

                # Verificar que el PDF viajó en el payload de OpenAI como bloque 'file'
                req_enviada = mock_open.call_args[0][0]
                payload_enviado = json.loads(req_enviada.data.decode("utf-8"))
                contenido_user = payload_enviado["messages"][1]["content"]
                self.assertEqual(contenido_user[0]["type"], "file")
                self.assertIn("application/pdf;base64,", contenido_user[0]["file"]["file_data"])

                # Verificar que el renderizador genera tarjetas en colores con PASO 1, PASO 2,
                # botones destacados, sección importante y comprobación final
                html_bonito = formatear_markdown_ia_html(texto, proveedor_ia=prov)
                self.assertIn("Segoe UI", html_bonito)
                self.assertIn("PASO 1", html_bonito)
                self.assertIn("PASO 2", html_bonito)
                self.assertIn("Cinema Manager", html_bonito)
                self.assertIn("Muy importante", html_bonito)
                self.assertIn("Comprobación final", html_bonito)

            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("sin red")):
                texto_err, _, aviso_err = sintetizar_con_ia(
                    "¿Cómo hago la conciliación?",
                    "[FUENTE 1: Manual Inventario]\n1. Entrar.",
                )
                self.assertEqual(texto_err, "")
                self.assertIn("IA no disponible", aviso_err)

    def test_cache_manuales_ia_guarda_y_recupera_al_instante(self):
        with tempfile.TemporaryDirectory() as tmp:
            bib_mock = MagicMock()
            bib_mock.carpeta = Path(tmp)
            guardar_cache_manual_ia(
                bib_mock,
                "clave_inv_123",
                "# Manual paso a paso: Inventario\n1. **Contar**:Abrir [Stock].",
                "OpenAI · gpt-4o-mini",
            )
            recuperado = leer_cache_manual_ia(bib_mock, "clave_inv_123")
            self.assertIsNotNone(recuperado)
            self.assertIn("Inventario", recuperado[0])
            self.assertEqual(recuperado[1], "OpenAI · gpt-4o-mini")


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

    def test_flujo_exacto_y_rechazo_por_ia_muestra_no_disponible(self):
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

                # 1. Consulta disponible -> devuelve manual exacto
                widget.hacer_pregunta("¿Cómo se hace la conciliación de inventario?")
                self.assertIn("CONCILIACIÓN DE STOCK Y DESCUADRES", widget.respuesta.ultimo_html)
                self.assertNotIn("PROCEDIMIENTO NO DISPONIBLE", widget.respuesta.ultimo_html)

                # 2. Consulta inexistente -> cartel directo de NO DISPONIBLE
                widget.hacer_pregunta("¿Cómo hago una solicitud de pedido?")
                self.assertIn("PROCEDIMIENTO NO DISPONIBLE", widget.respuesta.ultimo_html)

                # 3. Si la IA rechaza un candidato dudoso con NO_DISPONIBLE -> pasa a cartel NO DISPONIBLE
                turno_id = widget._turnos[0].turno_id
                widget._al_terminar_ia(turno_id, MARCA_NO_DISPONIBLE, "OpenAI", "")
                self.assertTrue(widget._turnos[0].respuesta.rechazado_por_ia)


if __name__ == "__main__":
    unittest.main()
