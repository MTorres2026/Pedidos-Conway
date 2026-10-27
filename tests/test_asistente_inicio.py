"""Pruebas del Asistente de Alta Precisión de Inicio ('Manual exacto o No disponible')."""

import json
import os
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from modulos.asistente_inicio import (
    AdjuntoIA,
    MARCA_NO_DISPONIBLE,
    RespuestaAsistente,
    buscar_procedimiento_confirmado,
    buscar_pasajes_biblioteca,
    construir_contexto_conversacional,
    construir_manual_desde_respuesta,
    consultar_base_local,
    detectar_intencion_conversacional,
    detectar_proveedor_ia,
    dividir_documento_en_secciones,
    exportar_manual_ia_docx,
    exportar_manual_ia_excel,
    exportar_manual_ia_word,
    generar_todas_las_hojas_word,
    extraer_terminos_consulta,
    formatear_markdown_ia_html,
    guardar_cache_manual_ia,
    leer_cache_manual_ia,
    renderizar_respuesta_asistente_html,
    sintetizar_con_ia,
    sintetizar_respuesta_conversacional_ia,
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


class ChatConversacionalTests(AsistentePrecisionEstrictaTests):
    """Chat natural tipo IA, pero solo con el conocimiento de su biblioteca."""

    def test_saludo_responde_como_persona_sin_buscar_manual(self):
        self.assertEqual(detectar_intencion_conversacional("Hola"), ("saludo", ""))
        resp = consultar_base_local("Hola", self.bib)
        self.assertTrue(resp.es_chat)
        self.assertEqual(resp.intencion_chat, "saludo")
        self.assertIn("Hola", resp.texto_chat)
        self.assertFalse(resp.pasajes)
        html_out = renderizar_respuesta_asistente_html(resp)
        self.assertIn("TU ASISTENTE DE CINESTOCK", html_out)
        self.assertNotIn("PROCEDIMIENTO NO DISPONIBLE", html_out)

    def test_que_documentos_tienes_lista_la_biblioteca_real(self):
        resp = consultar_base_local("¿Qué documentos tienes?", self.bib)
        self.assertEqual(resp.intencion_chat, "inventario")
        self.assertTrue(resp.documentos_chat)
        ids = [d.doc_id for d in resp.documentos_chat]
        self.assertIn(self.id_manual, ids)
        html_out = renderizar_respuesta_asistente_html(resp)
        self.assertIn(f'href="abrir:{self.id_manual}"', html_out)
        self.assertIn("Manual_Inventario_y_Conciliacion_2026", html_out)

    def test_tienes_algo_sobre_un_tema_y_tema_inexistente(self):
        resp = consultar_base_local("¿Tienes algo sobre mermas?", self.bib)
        self.assertEqual(resp.intencion_chat, "tema")
        self.assertTrue(resp.documentos_chat)
        self.assertIn(self.id_manual, [d.doc_id for d in resp.documentos_chat])

        resp_no = consultar_base_local("¿Tienes algo sobre cohetes espaciales?", self.bib)
        self.assertEqual(resp_no.intencion_chat, "tema")
        self.assertEqual(resp_no.documentos_chat, [])
        self.assertIn("no tengo ningún documento", resp_no.texto_chat)

    def test_identidad_conteo_y_categorias_solo_con_su_biblioteca(self):
        resp_id = consultar_base_local("¿Quién eres y qué puedes hacer?", self.bib)
        self.assertEqual(resp_id.intencion_chat, "identidad")
        self.assertIn("CineStock", resp_id.texto_chat)

        resp_n = consultar_base_local("¿Cuántos documentos tienes?", self.bib)
        self.assertEqual(resp_n.intencion_chat, "conteo")
        self.assertIn(str(resp_n.total_documentos), resp_n.texto_chat)

        resp_cat = consultar_base_local("¿Qué categorías tienes?", self.bib)
        self.assertEqual(resp_cat.intencion_chat, "categorias")

    def test_como_se_hace_no_lo_secuestra_la_conversacion(self):
        self.assertEqual(
            detectar_intencion_conversacional("¿Cómo se hace la conciliación de inventario?"),
            ("", ""),
        )
        self.assertEqual(detectar_intencion_conversacional("Hola, ¿cómo se hace el inventario?"), ("", ""))
        resp = consultar_base_local("¿Cómo se hace la conciliación de inventario?", self.bib)
        self.assertFalse(resp.es_chat)
        self.assertTrue(resp.pasajes)

    def test_ia_conversacional_solo_narra_el_inventario_recibido(self):
        resp = consultar_base_local("¿Qué documentos tienes?", self.bib)
        contexto = construir_contexto_conversacional(resp)
        self.assertIn("Manual_Inventario_y_Conciliacion_2026", contexto)

        respuesta_falsa = {
            "choices": [{"message": {"content": "¡Claro! Tengo tu manual de inventario y conciliación."}}]
        }
        mock_http = MagicMock()
        mock_http.read.return_value = json.dumps(respuesta_falsa).encode("utf-8")
        mock_http.__enter__.return_value = mock_http
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123"}, clear=False):
            with patch("urllib.request.urlopen", return_value=mock_http) as mock_open:
                texto, prov, aviso = sintetizar_respuesta_conversacional_ia(
                    "¿Qué documentos tienes?", contexto
                )
                self.assertIn("manual de inventario", texto)
                self.assertIn("OpenAI", prov)
                self.assertEqual(aviso, "")
                payload = json.loads(mock_open.call_args[0][0].data.decode("utf-8"))
                system = payload["messages"][0]["content"]
                self.assertIn("EXCLUSIVAMENTE", system)
                self.assertIn("biblioteca", payload["messages"][1]["content"])


class HojasWordManualesTests(AsistentePrecisionEstrictaTests):
    """Hoja formateada al instante en el chat + Word .docx real para todos."""

    def test_hoja_formateada_aparece_sin_ia_al_preguntar_por_conciliacion(self):
        resp = consultar_base_local("¿Cómo se hace la conciliación de inventario?", self.bib)
        self.assertEqual(resp.texto_ia, "")
        manual = construir_manual_desde_respuesta(resp)
        self.assertGreaterEqual(sum(len(f.pasos) for f in manual.fases), 3)

        html_out = renderizar_respuesta_asistente_html(resp, turno_id=7)
        self.assertIn("HOJA DE MANUAL OPERATIVO PASO A PASO", html_out)
        self.assertIn("PASO 1", html_out)
        self.assertIn("Conciliación de Stock", html_out)
        self.assertIn('href="word:7"', html_out)
        self.assertIn('href="excel:7"', html_out)
        self.assertIn(f'href="abrir:{self.id_manual}"', html_out)

    def test_word_docx_es_un_documento_word_real_y_formateado(self):
        import xml.etree.ElementTree as ET

        resp = consultar_base_local("¿Cómo se hace la conciliación de inventario?", self.bib)
        ruta = exportar_manual_ia_docx(resp, self.raiz / "out" / "Manual_Conciliacion.docx")
        self.assertTrue(ruta.is_file())
        self.assertTrue(zipfile.is_zipfile(ruta))
        with zipfile.ZipFile(ruta) as z:
            nombres = set(z.namelist())
            self.assertIn("word/document.xml", nombres)
            self.assertIn("word/styles.xml", nombres)
            self.assertIn("word/header1.xml", nombres)
            self.assertIn("word/footer1.xml", nombres)
            document_xml = z.read("word/document.xml").decode("utf-8")
            ET.fromstring(document_xml)  # XML válido que Word puede abrir
        self.assertIn("PASO 1", document_xml)
        self.assertIn("Conciliaci", document_xml)
        self.assertIn("Cinema Manager", document_xml)
        self.assertIn("PANTALLA / MENÚ", document_xml)

    def test_crear_todas_las_hojas_word_de_la_biblioteca(self):
        carpeta = self.raiz / "hojas_word"
        resultado = generar_todas_las_hojas_word(self.bib, carpeta)
        self.assertGreaterEqual(len(resultado.generadas), 1)
        self.assertTrue(resultado.indice and resultado.indice.is_file())
        titulos = " ".join(g.titulo for g in resultado.generadas)
        self.assertIn("Conciliacion", titulos.replace("ó", "o"))
        for hoja in resultado.generadas:
            self.assertTrue(hoja.ruta.is_file())
            self.assertEqual(hoja.ruta.suffix, ".docx")
            self.assertTrue(zipfile.is_zipfile(hoja.ruta))
            self.assertGreaterEqual(hoja.n_pasos, 2)


class ManualRealFrenteACorreosTests(unittest.TestCase):
    """Regla general: si hay manual real, se muestra ESE manual paso a paso,
    nunca el saludo/criterios de un correo que solo menciona el proceso."""

    CORREO_SEDE = (
        "Buen día estimados,\n\n"
        "Espero estén teniendo un excelente día, les comento que el día de hoy "
        "se llevara la Toma de Inventarios bajo los siguientes criterios: "
        "Recomendaciones: No deben contar con recepciones pendientes en ninguno "
        "de los sistemas (ERP - VISTA). Las cargas de salida deben estar "
        "completas. Registrar su Toma de Inventarios EN BASE A LAS EXISTENCIAS "
        "FISICAS, validando la unidad de medida. Al final mencionamos la "
        "conciliación de inventario solo como aviso general para gerencia.\n\n"
        "Un saludo\n"
    )

    MANUAL_CONCILIACION_ERP = (
        "CONCILIACIÓN DE INVENTARIOS EN ERP\n"
        "1. Entrar en ERP Global (PeopleSoft) con usuario y contraseña y pulsar Conectar.\n"
        "2. Ir a Menú Principal -> Almacenes -> Recuentos de Inventario -> Conciliación de Inventarios.\n"
        "3. Seleccionar el ID Evento Ejecución y pulsar Procesar conciliación.\n"
        "4. Revisar la columna Variación y recontar las referencias con diferencias.\n"
        "5. Pulsar Guardar y verificar que el estado queda Conciliado.\n"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.bib = Biblioteca(self.raiz / "bib")
        self.addCleanup(self.bib.cerrar)
        self._ocr = patch.object(self.bib, "_ocr_callable", return_value=None)
        self._ocr.start()
        self.addCleanup(self._ocr.stop)

        correo = self.raiz / "mail_sede.eml"
        correo.write_text(
            "Subject: Mail tipo día antes de la toma de inventarios\n"
            "From: sede@example.com\n"
            "Content-Type: text/plain; charset=utf-8\n\n" + self.CORREO_SEDE,
            encoding="utf-8",
        )
        self.bib.agregar_archivo(correo)

        manual = self.raiz / "170608_Conciliacion_de_inventarios.txt"
        manual.write_text(self.MANUAL_CONCILIACION_ERP, encoding="utf-8")
        self.id_manual_conciliacion = self.bib.agregar_archivo(manual).id

        mermas = self.raiz / "Manual_Registro_Mermas.txt"
        mermas.write_text(
            "REGISTRO DE MERMAS EN CINEMA MANAGER\n"
            "1. Entrar en Cinema Manager -> Almacén -> Mermas.\n"
            "2. Pulsar Nueva merma y seleccionar el artículo dañado.\n"
            "3. Elegir el motivo Rotura o Caducidad y escribir la cantidad.\n"
            "4. Pulsar Guardar y comprobar que la merma queda registrada.\n",
            encoding="utf-8",
        )
        self.id_manual_mermas = self.bib.agregar_archivo(mermas).id

    def test_conciliacion_muestra_el_manual_erp_no_el_correo_de_sede(self):
        resp = consultar_base_local("¿Cómo se hace la conciliación de inventario?", self.bib)
        self.assertTrue(resp.pasajes)
        self.assertTrue(all(p.doc_id == self.id_manual_conciliacion for p in resp.pasajes))

        manual = construir_manual_desde_respuesta(resp)
        acciones = [p.accion for f in manual.fases for p in f.pasos]
        self.assertGreaterEqual(len(acciones), 4)
        self.assertIn("PeopleSoft", acciones[0])

        html_out = renderizar_respuesta_asistente_html(resp, turno_id=3)
        self.assertIn("HOJA DE MANUAL OPERATIVO", html_out)
        self.assertIn("PeopleSoft", html_out)
        self.assertNotIn("Buen día", html_out)
        self.assertNotIn("Mail tipo", html_out)

    def test_cualquier_proceso_con_manual_gana_a_los_correos(self):
        resp = consultar_base_local("¿Cómo se registran las mermas?", self.bib)
        self.assertTrue(resp.pasajes)
        self.assertEqual(resp.pasajes[0].doc_id, self.id_manual_mermas)
        html_out = renderizar_respuesta_asistente_html(resp, turno_id=4)
        self.assertIn("Nueva merma", html_out)
        self.assertNotIn("Buen día", html_out)

    def test_manual_adjunto_dentro_de_un_correo_se_usa_sin_el_saludo(self):
        from email.message import EmailMessage

        bib_adj = Biblioteca(self.raiz / "bib_adj")
        self.addCleanup(bib_adj.cerrar)
        with patch.object(bib_adj, "_ocr_callable", return_value=None):
            msg = EmailMessage()
            msg["Subject"] = "RV: Documentación inventarios"
            msg["From"] = "sede@example.com"
            msg.set_content("Buen día estimados,\nAdjunto el manual.\nUn saludo")
            msg.add_attachment(
                self.MANUAL_CONCILIACION_ERP.encode("utf-8"),
                maintype="text", subtype="plain",
                filename="Conciliacion_de_inventarios.txt",
            )
            fwd = self.raiz / "fwd.eml"
            fwd.write_bytes(msg.as_bytes())
            bib_adj.agregar_archivo(fwd)

        resp = consultar_base_local("¿Cómo se hace la conciliación de inventario?", bib_adj)
        self.assertTrue(resp.pasajes)
        html_out = renderizar_respuesta_asistente_html(resp, turno_id=5)
        self.assertIn("PeopleSoft", html_out)
        self.assertNotIn("Buen día", html_out)

    def test_sin_manual_el_correo_amable_no_se_disfraza_de_manual(self):
        bib_solo_correo = Biblioteca(self.raiz / "bib_solo_correo")
        self.addCleanup(bib_solo_correo.cerrar)
        with patch.object(bib_solo_correo, "_ocr_callable", return_value=None):
            correo = self.raiz / "solo_mail.eml"
            correo.write_text(
                "Subject: Mail tipo día antes de la toma de inventarios\n"
                "From: sede@example.com\n"
                "Content-Type: text/plain; charset=utf-8\n\n" + self.CORREO_SEDE,
                encoding="utf-8",
            )
            bib_solo_correo.agregar_archivo(correo)

        resp = consultar_base_local(
            "¿Cómo se hace la conciliación de inventario?", bib_solo_correo
        )
        self.assertFalse(resp.tiene_contenido)
        html_out = renderizar_respuesta_asistente_html(resp)
        self.assertIn("PROCEDIMIENTO NO DISPONIBLE", html_out)
        self.assertNotIn("Buen día", html_out)


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
                bloques_file = [b for b in contenido_user if b.get("type") == "file"]
                self.assertEqual(len(bloques_file), 1)
                self.assertIn("application/pdf;base64,", bloques_file[0]["file"]["file_data"])

                # Verificar que el renderizador genera tarjetas en colores con PASO 1, PASO 2,
                # botones destacados, sección importante y comprobación final
                html_bonito = formatear_markdown_ia_html(texto, proveedor_ia=prov, turno_id=1)
                self.assertIn("Segoe UI", html_bonito)
                self.assertIn("PASO 1", html_bonito)
                self.assertIn("PASO 2", html_bonito)
                self.assertIn("Cinema Manager", html_bonito)
                self.assertIn("Muy importante", html_bonito)
                self.assertIn("Comprobación final", html_bonito)
                self.assertIn('href="excel:1"', html_bonito)
                self.assertIn('href="word:1"', html_bonito)

            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("sin red")):
                texto_err, _, aviso_err = sintetizar_con_ia(
                    "¿Cómo hago la conciliación?",
                    "[FUENTE 1: Manual Inventario]\n1. Entrar.",
                )
                self.assertEqual(texto_err, "")
                self.assertIn("IA no disponible", aviso_err)

    def test_pptx_corporativo_extrae_diapositiva_menu_sin_texto_y_exporta_excel_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz_p = Path(tmp)
            bib_pptx = Biblioteca(raiz_p / "bib_pptx")
            self.addCleanup(bib_pptx.cerrar)

            ruta_pptx = raiz_p / "04c654d25d_170608 Conciliación de inventarios.pptx"
            png_falso_logo = b"\x89PNG\r\n\x1a\n" + (b"L" * 11_000)
            png_erp_login = b"\x89PNG\r\n\x1a\n" + (b"A" * 16_000)
            png_erp_menu = b"\x89PNG\r\n\x1a\n" + (b"M" * 18_000)
            png_erp_id = b"\x89PNG\r\n\x1a\n" + (b"E" * 17_000)

            def _slide_xml(textos, con_pic_contenido=False, con_recuadro_rojo=False):
                ns_p = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
                ns_a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
                ns_r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
                partes = [f'<p:sld {ns_p} {ns_a} {ns_r}><p:cSld><p:spTree>']
                # Logo de plantilla en rId1
                partes.append(
                    '<p:pic><p:blipFill><a:blip r:embed="rId1"/></p:blipFill>'
                    '<p:spPr><a:xfrm><a:off x="0" y="6000000"/><a:ext cx="1200000" cy="400000"/></a:xfrm></p:spPr></p:pic>'
                )
                if con_pic_contenido:
                    partes.append(
                        '<p:pic><p:blipFill><a:blip r:embed="rId2"/></p:blipFill>'
                        '<p:spPr><a:xfrm><a:off x="400000" y="400000"/><a:ext cx="8000000" cy="5200000"/></a:xfrm></p:spPr></p:pic>'
                    )
                if con_recuadro_rojo:
                    partes.append(
                        '<p:sp><p:spPr><a:xfrm><a:off x="6200000" y="4100000"/><a:ext cx="2100000" cy="360000"/></a:xfrm>'
                        '<a:ln w="28575"><a:solidFill><a:srgbClr val="FF0000"/></a:solidFill></a:ln></p:spPr></p:sp>'
                    )
                for idx_t, txt in enumerate(textos):
                    partes.append(
                        f'<p:sp><p:spPr><a:xfrm><a:off x="500000" y="{500000 + idx_t * 300000}"/>'
                        '<a:ext cx="4000000" cy="250000"/></a:xfrm></p:spPr>'
                        f'<p:txBody><a:p><a:r><a:t>{txt}</a:t></a:r></a:p></p:txBody></p:sp>'
                    )
                partes.append("</p:spTree></p:cSld></p:sld>")
                return "".join(partes)

            def _rels_xml(img_extra=None):
                base = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="img" Target="../media/logo_yelmo.png"/>'
                )
                if img_extra:
                    base += f'<Relationship Id="rId2" Type="img" Target="../media/{img_extra}"/>'
                return base + "</Relationships>"

            with zipfile.ZipFile(ruta_pptx, "w") as z:
                z.writestr(
                    "ppt/presentation.xml",
                    '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                    '<p:sldSz cx="9144000" cy="6858000"/></p:presentation>',
                )
                z.writestr("ppt/media/logo_yelmo.png", png_falso_logo)
                z.writestr("ppt/media/s9_login.png", png_erp_login)
                z.writestr("ppt/media/s11_menu.png", png_erp_menu)
                z.writestr("ppt/media/s12_evento.png", png_erp_id)

                # Diapositivas 1..3 de relleno con el logo repetido (>=4 usos -> logo descartado)
                for n_relleno in (1, 2, 3):
                    z.writestr(f"ppt/slides/slide{n_relleno}.xml", _slide_xml([f"Portada {n_relleno}"]))
                    z.writestr(f"ppt/slides/_rels/slide{n_relleno}.xml.rels", _rels_xml())

                # Slide 4: Login ERP
                z.writestr("ppt/slides/slide4.xml", _slide_xml(["Introducir Usuario", "Pinchar en Conectar"], True))
                z.writestr("ppt/slides/_rels/slide4.xml.rels", _rels_xml("s9_login.png"))

                # Slide 5: Índice
                z.writestr(
                    "ppt/slides/slide5.xml",
                    _slide_xml([
                        "1. Acceso a la plataforma",
                        "3. Creación del evento de toma de inventario",
                        "8. Conciliación de inventario",
                    ]),
                )
                z.writestr("ppt/slides/_rels/slide5.xml.rels", _rels_xml())

                # Slide 6 (equivalente a Slide 11 real): CERO TEXTO, solo captura de menú + recuadro rojo
                z.writestr("ppt/slides/slide6.xml", _slide_xml([], con_pic_contenido=True, con_recuadro_rojo=True))
                z.writestr("ppt/slides/_rels/slide6.xml.rels", _rels_xml("s11_menu.png"))

                # Slide 7 (equivalente a Slide 12 real): ID Evento Ejecución + Añadir un valor
                z.writestr(
                    "ppt/slides/slide7.xml",
                    _slide_xml(
                        [
                            "Creación de Evento de Recuento",
                            "Escribir un código para identificar el evento",
                            "Pinchar en Añadir",
                            "IMPORTANTE: La primera vez hay que añadir un valor",
                        ],
                        con_pic_contenido=True,
                        con_recuadro_rojo=True,
                    ),
                )
                z.writestr("ppt/slides/_rels/slide7.xml.rels", _rels_xml("s12_evento.png"))

            with patch.object(bib_pptx, "_ocr_callable", return_value=None):
                bib_pptx.agregar_archivo(ruta_pptx)

            resp = consultar_base_local("conciliación de inventarios", bib_pptx)
            self.assertTrue(resp.tiene_contenido)
            # Deben haberse extraído exactamente las 3 diapositivas con captura real (4, 6 y 7),
            # descartando el logo repetido en las 7 diapositivas e incluyendo la Slide 6 sin texto
            self.assertEqual(len(resp.adjuntos_ia), 3)
            nombres_adj = [a.nombre for a in resp.adjuntos_ia]
            self.assertTrue(nombres_adj[0].startswith("diapositiva_04"))
            self.assertTrue(nombres_adj[1].startswith("diapositiva_06"))
            self.assertTrue(nombres_adj[2].startswith("diapositiva_07"))
            self.assertIn("recuadros/flechas rojas", resp.adjuntos_ia[1].etiqueta_previa)

            # Verificar renderizado tipo Hoja Excel/Word de 4 columnas y exportación real a .xlsx y .doc
            resp.texto_ia = (
                "# Manual Operativo: Conciliación y Toma de Inventario\n"
                "SISTEMA: ERP Global (PeopleSoft)\n"
                "OBJETIVO: Crear el evento de inventario y conciliar existencias.\n\n"
                "## FASE 1: Acceso y Creación del Evento\n"
                "PASO 1 | Acceso a ERP | Entrar en [ERP Global], poner [Usuario] y pulsar [Conectar]. | Credenciales cine\n"
                "PASO 2 | Ruta de Menú | Ir a [Menú Principal] -> [Almacenes] -> [Recuentos de Inventario] -> [Preparación Evento de Recuento] -> [Creación de Evento de Recuento]. | Recuadro rojo\n"
                "PASO 3 | Pestaña inicial | Seleccionar [Añadir un nuevo valor]. | Importante la 1ª vez\n"
                "PASO 4 | ID Evento Ejecución | Escribir código en [ID Evento Ejecución] y pulsar [Añadir]. | Añadir valor = código\n\n"
                "## ⚠️ NOTAS IMPORTANTES\n"
                "- La primera vez que se realiza un evento de recuento hay que añadir un valor.\n\n"
                "## ✅ COMPROBACIÓN FINAL\n"
                "- Verificar que el evento aparece guardado en ERP."
            )
            resp.proveedor_ia = "OpenAI · gpt-4.1-mini"

            html_hoja = renderizar_respuesta_asistente_html(resp, turno_id=3)
            self.assertIn("HOJA DE MANUAL OPERATIVO PASO A PASO", html_hoja)
            self.assertIn("PANTALLA / MENÚ", html_hoja)
            self.assertIn("VALOR / DATO CLAVE", html_hoja)
            self.assertIn("FASE 1: Acceso y Creación del Evento", html_hoja)
            self.assertIn("ID Evento Ejecución", html_hoja)
            self.assertIn("➔", html_hoja)

            ruta_xlsx = exportar_manual_ia_excel(resp, raiz_p / "out" / "Manual_Inventario.xlsx")
            ruta_doc = exportar_manual_ia_word(resp, raiz_p / "out" / "Manual_Inventario.doc")
            self.assertTrue(ruta_xlsx.is_file() and ruta_xlsx.stat().st_size > 400)
            self.assertTrue(ruta_doc.is_file() and ruta_doc.stat().st_size > 800)

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


class ResilienciaManualVisualTests(unittest.TestCase):
    """El caso de la captura real: manual visual (PPT) dentro/fuera de correo
    y OpenAI devolviendo 429. La hoja debe salir igual."""

    def _mock_ok_openai(self, contenido="# Manual paso a paso: Conciliación\n1. Entrar en ERP."):
        mock_http = MagicMock()
        mock_http.read.return_value = json.dumps(
            {"choices": [{"message": {"content": contenido}}]}
        ).encode("utf-8")
        mock_http.__enter__.return_value = mock_http
        return mock_http

    def test_openai_429_prueba_el_siguiente_modelo(self):
        import io

        err429 = urllib.error.HTTPError(
            "https://api.openai.com", 429, "Too Many", {}, io.BytesIO(b"{}")
        )
        claves = {"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "", "GEMINI_API_KEY": ""}
        with patch("modulos.asistente_inicio._leer_clave_env_local",
                   side_effect=lambda n: claves.get(n, "")):
            with patch("urllib.request.urlopen",
                       side_effect=[err429, self._mock_ok_openai()]) as mock_open:
                texto, prov, aviso = sintetizar_con_ia(
                    "¿Cómo se hace la conciliación de inventario?",
                    "[DOCUMENTO 1: Conciliación]\n1. Entrar en ERP.",
                )
        self.assertIn("Manual paso a paso", texto)
        self.assertIn("gpt-4o-mini", prov)
        self.assertEqual(mock_open.call_count, 2)

    def test_openai_429_en_todos_cae_a_gemini_si_hay_clave(self):
        import io

        def _fake_urlopen(req, timeout=None, context=None):
            if "generativelanguage" in req.full_url:
                mock_g = MagicMock()
                mock_g.read.return_value = json.dumps({
                    "candidates": [{"content": {"parts": [
                        {"text": "# Manual paso a paso: Conciliación\n1. Entrar en ERP."}
                    ]}}]
                }).encode("utf-8")
                mock_g.__enter__.return_value = mock_g
                return mock_g
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many", {}, io.BytesIO(b"{}")
            )

        claves = {
            "OPENAI_API_KEY": "sk-test", "GEMINI_API_KEY": "gem-test",
            "OPENAI_MODEL": "", "GEMINI_MODEL": "",
        }
        with patch("modulos.asistente_inicio._leer_clave_env_local",
                   side_effect=lambda n: claves.get(n, "")):
            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                texto, prov, aviso = sintetizar_con_ia(
                    "¿Cómo se hace la conciliación de inventario?",
                    "[DOCUMENTO 1: Conciliación]\n1. Entrar en ERP.",
                )
        self.assertIn("Manual paso a paso", texto)
        self.assertIn("Gemini", prov)

    def test_tras_429_visual_se_espera_y_se_envian_menos_diapositivas(self):
        import io

        err429 = urllib.error.HTTPError(
            "https://api.openai.com", 429, "Too Many", {}, io.BytesIO(b"{}")
        )
        adjuntos = [
            AdjuntoIA(nombre=f"d{i}.jpg", mime="image/jpeg", datos_b64="QUJD")
            for i in range(20)
        ]
        claves = {"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "", "GEMINI_API_KEY": ""}
        with patch("modulos.asistente_inicio._leer_clave_env_local",
                   side_effect=lambda n: claves.get(n, "")):
            with patch("modulos.asistente_inicio.time.sleep") as mock_sleep:
                with patch("urllib.request.urlopen",
                           side_effect=[err429, self._mock_ok_openai()]) as mock_open:
                    texto, prov, aviso = sintetizar_con_ia(
                        "¿Cómo se hace la conciliación de inventario?",
                        "[DOCUMENTO 1: Conciliación]\n1. Entrar en ERP.",
                        adjuntos_ia=adjuntos,
                    )
        self.assertIn("Manual paso a paso", texto)
        self.assertEqual(mock_sleep.call_count, 1)
        payloads = []
        for llamada in mock_open.call_args_list:
            payloads.append(json.loads(llamada[0][0].data.decode("utf-8")))
        n_img_1 = sum(1 for b in payloads[0]["messages"][1]["content"] if b.get("type") == "image_url")
        n_img_2 = sum(1 for b in payloads[1]["messages"][1]["content"] if b.get("type") == "image_url")
        self.assertLessEqual(n_img_1, 18)
        self.assertLessEqual(n_img_2, 12)
        self.assertLess(n_img_2, n_img_1)

    def test_correo_con_presentacion_solo_aporta_las_diapositivas(self):
        class _Res:
            id = 7

        ficha = dict(
            id=7, titulo="Mail tipo día antes de la toma de inventarios",
            tipo="correo", archivo="mail.eml", fecha_doc="2026-10-20",
            categoria="Inventario y activos", remitente="sede@example.com",
            estado="NORMAL", importancia=80.0,
            texto=(
                "Buen día estimados,\n"
                "Espero estén bien. Hoy se llevará la toma de inventarios "
                "bajo los siguientes criterios generales.\n"
                "Un saludo\n\n"
                "[Adjunto: Conciliacion_de_inventarios.pptx]\n"
                "--- Diapositiva 1 ---\nConciliación de inventarios\n"
                "--- Diapositiva 2 ---\n"
                "1. Entrar en ERP Global (PeopleSoft) y pulsar Conectar.\n"
                "2. Ir a Almacenes -> Conciliación de Inventarios.\n"
                "3. Pulsar Procesar y Guardar."
            ),
        )

        class _BibFake:
            def listar(self, filtro="todos", limite=500):
                return [_Res()]

            def obtener(self, doc_id):
                return ficha

        _, ranuras = extraer_terminos_consulta("¿Cómo se hace la conciliación de inventario?")
        pasajes = buscar_pasajes_biblioteca(
            _BibFake(), "¿Cómo se hace la conciliación de inventario?", ranuras
        )
        self.assertTrue(pasajes)
        self.assertTrue(all(
            p.titulo_seccion.lower().startswith("diapositiva") for p in pasajes
        ))
        unido = "\n".join(p.texto_seccion for p in pasajes)
        self.assertNotIn("Buen día", unido)
        self.assertIn("PeopleSoft", unido)

    def test_hoja_garantizada_aunque_las_diapositivas_solo_tengan_rotulos(self):
        from modulos.asistente_inicio import PasajeManual

        resp = RespuestaAsistente(
            pregunta="¿Cómo se hace la conciliación de inventario?",
            pasajes=[
                PasajeManual(
                    doc_id=7, titulo_documento="Mail tipo día antes de la toma de inventarios",
                    tipo_documento="correo", fecha="", categoria="", remitente="",
                    estado="NORMAL", titulo_seccion="",
                    texto_seccion=(
                        "Stock Location Status\nCarga de salida\n"
                        "Informe de Conciliación\nHora de finalización, antes 1:00pm"
                    ),
                    puntuacion=80.0,
                ),
                PasajeManual(
                    doc_id=7, titulo_documento="Mail tipo día antes de la toma de inventarios",
                    tipo_documento="correo", fecha="", categoria="", remitente="",
                    estado="NORMAL", titulo_seccion="Diapositiva 1",
                    texto_seccion="Conciliación de inventarios",
                    puntuacion=80.0,
                ),
            ],
        )
        manual = construir_manual_desde_respuesta(resp)
        self.assertTrue(any(f.pasos for f in manual.fases))
        html_out = renderizar_respuesta_asistente_html(resp, turno_id=9)
        self.assertIn("HOJA DE MANUAL OPERATIVO", html_out)
        self.assertIn("HOJA DE MANUAL V4", html_out)
        self.assertIn("Stock Location Status", html_out)
        self.assertNotIn("GUÍA EXTRAÍDA", html_out)


    def test_hoja_local_descarta_numeritos_de_flechas(self):
        from modulos.asistente_inicio import PasajeManual

        resp = RespuestaAsistente(
            pregunta="¿Cómo se hace la conciliación de inventario?",
            pasajes=[
                PasajeManual(
                    doc_id=2, titulo_documento="170608 Conciliación de inventarios",
                    tipo_documento="pptx", fecha="", categoria="", remitente="",
                    estado="NORMAL", titulo_seccion="Diapositiva 12",
                    texto_seccion=(
                        "1\nEscribe un código para identificar el evento.\n"
                        "2\nPincha en «Añadir».\n"
                        "http://200.53.162.47:31370/psp/cpfi91ts/EMPLOYEE/ERP/?cmd=expire"
                    ),
                    puntuacion=90.0,
                ),
            ],
        )
        manual = construir_manual_desde_respuesta(resp)
        acciones = [p.accion for f in manual.fases for p in f.pasos]
        self.assertIn("Escribe un código para identificar el evento.", acciones)
        self.assertNotIn("1", acciones)
        self.assertNotIn("2", acciones)
        todo = " ".join(f"{p.pantalla} {p.accion}" for f in manual.fases for p in f.pasos)
        self.assertIn("Abrir el acceso indicado", todo)
        self.assertIn("http://200.53.162.47", todo)

    def test_manual_visual_exacto_gana_a_correo_con_mucho_texto(self):
        fichas = {
            1: dict(
                id=1, titulo="Mail tipo día antes de la toma de inventarios",
                tipo="pdf", archivo="mail.pdf", fecha_doc="2026-10-20",
                categoria="Inventario y activos", remitente="sede@example.com",
                estado="NORMAL", importancia=95.0,
                texto=(
                    "TOMA DE INVENTARIOS - CRITERIOS\n"
                    "• Stock Location Status de la conciliación de inventario.\n"
                    "• Carga de salida de la conciliación de inventario.\n"
                    "• Informe de Conciliación de inventario completo.\n"
                    "• Hora de finalización de la conciliación, antes 1:00pm.\n"
                    "• Revisar y verificar cada punto en ERP y Vista."
                ),
            ),
            2: dict(
                id=2, titulo="170608 Conciliación de inventarios",
                tipo="pptx", archivo="170608_Conciliacion_de_inventarios.pptx",
                fecha_doc="2017-06-08", categoria="Inventario y activos",
                remitente="", estado="NORMAL", importancia=40.0,
                texto="--- Diapositiva 1 ---\nConciliación de inventarios",
            ),
        }

        class _Res:
            def __init__(self, i):
                self.id = i

        class _BibFake:
            def listar(self, filtro="todos", limite=500):
                return [_Res(1), _Res(2)]

            def obtener(self, doc_id):
                return fichas[doc_id]

        _, ranuras = extraer_terminos_consulta("¿Cómo se hace la conciliación de inventario?")
        pasajes = buscar_pasajes_biblioteca(
            _BibFake(), "¿Cómo se hace la conciliación de inventario?", ranuras
        )
        self.assertTrue(pasajes)
        self.assertTrue(all(p.doc_id == 2 for p in pasajes))


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
