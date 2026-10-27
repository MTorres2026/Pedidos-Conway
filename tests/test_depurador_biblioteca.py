import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from modulos.biblioteca import Biblioteca
from modulos.depurador_biblioteca import (
    analizar_biblioteca_en_profundidad,
    ejecutar_limpieza_biblioteca,
)


def _insertar_ficha_prueba(
    bib: Biblioteca,
    *,
    clave: str,
    titulo: str,
    tipo: str,
    archivo: str,
    texto: str,
    fecha_doc: str = "2026-09-01",
    remitente: str = "",
    hilo: str = "",
    estado: str = "NORMAL",
    categoria: str = "Inventario y activos",
) -> int:
    ruta_copia = bib.originales / f"{clave}_{archivo}"
    ruta_copia.write_bytes((texto or "contenido").encode("utf-8"))
    cur = bib._con.execute(
        """
        INSERT INTO docs (
            clave, titulo, tipo, archivo, ruta_copia, fecha_doc, fecha_alta,
            remitente, destinatarios, adjuntos, texto, hilo, importancia,
            motivos, categoria, estado, ruta_origen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 0, ?, ?, 50.0, '', ?, ?, '')
        """,
        (
            clave,
            titulo,
            tipo,
            archivo,
            str(ruta_copia),
            fecha_doc,
            "2026-10-01",
            remitente,
            texto,
            hilo,
            categoria,
            estado,
        ),
    )
    doc_id = int(cur.lastrowid)
    if getattr(bib, "_fts", False):
        bib._con.execute(
            "INSERT INTO docs_fts(rowid, titulo, texto, meta) VALUES (?, ?, ?, ?)",
            (doc_id, titulo, texto, remitente),
        )
    bib._con.commit()
    return doc_id


class TestDepuradorBiblioteca(unittest.TestCase):

    def test_estudio_y_compactacion_profunda_conserva_solo_manuales_actuales(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp) / "conocimiento"
            bib = Biblioteca(carpeta)

            texto_conciliacion = (
                "--- Diapositiva 1 ---\nConciliación de inventarios en PeopleSoft ERP\n"
                "--- Diapositiva 2 ---\n1. Entrar en Menú Principal > Gestión de Inventario.\n"
                "2. Pulsar en Conciliar existencias y seleccionar unidad de negocio.\n"
                "--- Diapositiva 3 ---\n3. Introducir recuento físico y pulsar Guardar."
            )

            # 1) Manual único antiguo (170608 = 2017): DEBE CONSERVARSE
            id_concil_unico = _insertar_ficha_prueba(
                bib,
                clave="c1",
                titulo="170608 Conciliación de inventarios",
                tipo="pptx",
                archivo="04c654d25d_170608 Conciliación de inventarios.pptx",
                texto=texto_conciliacion,
                fecha_doc="2017-06-08",
            )

            # 2) Copia repetida del mismo PPTX: DEBE ELIMINARSE como DUPLICADO
            id_concil_copia = _insertar_ficha_prueba(
                bib,
                clave="c2",
                titulo="Conciliacion de Inventarios (1)",
                tipo="pptx",
                archivo="Conciliacion_de_Inventarios (1).pptx",
                texto=texto_conciliacion,
                fecha_doc="2017-06-08",
            )

            # 3) Dos versiones del manual de mermas (2023 vs 2026): SOLO queda 2026
            texto_mermas_base = (
                "Manual operativo de registro de mermas en ERP.\n"
                "1. Acceder a Gestión de Inventario > Ajustes de stock.\n"
                "2. Seleccionar motivo de merma caducidad o rotura.\n"
                "3. Introducir cantidad y pulsar Guardar."
            )
            id_mermas_2023 = _insertar_ficha_prueba(
                bib,
                clave="m23",
                titulo="Manual Mermas 2023",
                tipo="pdf",
                archivo="Manual_Mermas_2023.pdf",
                texto=texto_mermas_base,
                fecha_doc="2023-04-10",
            )
            id_mermas_2026 = _insertar_ficha_prueba(
                bib,
                clave="m26",
                titulo="Manual Mermas 2026 Actualizado",
                tipo="pdf",
                archivo="Manual_Mermas_2026_Actualizado.pdf",
                texto=texto_mermas_base + "\n4. Verificar confirmación en pantalla.",
                fecha_doc="2026-05-20",
            )

            # 4) Correo envoltorio cuyo adjunto ya existe suelto: DEBE ELIMINARSE
            id_correo_adj_suelto = _insertar_ficha_prueba(
                bib,
                clave="em_adj",
                titulo="FW: Envío Manual Mermas 2026",
                tipo="correo",
                archivo="envio_mermas.eml",
                texto=(
                    "Os paso adjunto el nuevo manual paso a paso:\n"
                    "1. Entrar en ERP.\n2. Pulsar Guardar.\n3. Verificar.\n"
                    "[Adjunto: Manual_Mermas_2026_Actualizado.pdf]"
                ),
                fecha_doc="2026-05-21",
                hilo="envio manual mermas",
            )

            # 5) Hilo de correo con procedimiento propio (2 mensajes -> queda solo el más reciente)
            texto_proc_correo = (
                "Procedimiento paso a paso para cierre de contingencia:\n"
                "1. Entrar en Cinema Manager y seleccionar Cierre Diario.\n"
                "2. Pulsar Validar sesiones pendientes en la pestaña Taquilla.\n"
                "3. Introducir observaciones y pulsar Guardar."
            )
            id_hilo_viejo = _insertar_ficha_prueba(
                bib,
                clave="h1",
                titulo="Procedimiento cierre contingencia",
                tipo="correo",
                archivo="hilo1.eml",
                texto=texto_proc_correo,
                fecha_doc="2026-07-01",
                hilo="procedimiento cierre contingencia",
            )
            id_hilo_nuevo = _insertar_ficha_prueba(
                bib,
                clave="h2",
                titulo="RE: Procedimiento cierre contingencia",
                tipo="correo",
                archivo="hilo2.eml",
                texto=texto_proc_correo + "\n4. Comprobar envío correcto a sede.",
                fecha_doc="2026-08-15",
                hilo="procedimiento cierre contingencia",
            )

            # 6) Ruido efímero / no-manuales: TODOS deben eliminarse
            id_vista = _insertar_ficha_prueba(
                bib,
                clave="r_vista",
                titulo="Vista_PerformanceReport_20261018",
                tipo="pdf",
                archivo="Vista_PerformanceReport_20261018.pdf",
                texto="PerformanceReport recaudacion taquilla domingo sesiones espectadores",
                fecha_doc="2026-10-18",
            )
            id_kdm = _insertar_ficha_prueba(
                bib,
                clave="r_kdm",
                titulo="Claves KDM estreno viernes",
                tipo="correo",
                archivo="kdm.eml",
                texto="Adjuntamos las claves KDM para las salas 1 a 6.",
                fecha_doc="2026-10-15",
            )
            id_charla = _insertar_ficha_prueba(
                bib,
                clave="r_chat",
                titulo="Aviso luces almacén",
                tipo="correo",
                archivo="luces.eml",
                texto="Hola equipo, acordaos por favor de apagar las luces al salir.",
                fecha_doc="2026-10-10",
            )
            id_albaran_suelto = _insertar_ficha_prueba(
                bib,
                clave="r_alb",
                titulo="Albaran 884120 Conway",
                tipo="pdf",
                archivo="Albaran_884120.pdf",
                texto="Albaran 884120 entrega mercancias cine 20 cajas palomitas",
                fecha_doc="2026-10-12",
            )
            id_manual_albaran = _insertar_ficha_prueba(
                bib,
                clave="ok_alb",
                titulo="Manual Recepción de Albaranes en ERP",
                tipo="pptx",
                archivo="Manual_Albaranes_ERP.pptx",
                texto=(
                    "--- Diapositiva 1 ---\n1. Entrar en Compras > Recepciones.\n"
                    "--- Diapositiva 2 ---\n2. Introducir ID Pedido y pulsar Guardar.\n"
                    "--- Diapositiva 3 ---\n3. Verificar estado Recibido."
                ),
                fecha_doc="2025-11-10",
            )
            id_archivado = _insertar_ficha_prueba(
                bib,
                clave="r_arch",
                titulo="Guía antigua archivada",
                tipo="pdf",
                archivo="vieja.pdf",
                texto="Manual antiguo 1. Entrar 2. Pulsar 3. Guardar",
                estado="ARCHIVADO",
            )

            # 7) Elemento DESTACADO: SIEMPRE debe protegerse aunque sea corto
            id_destacado = _insertar_ficha_prueba(
                bib,
                clave="star1",
                titulo="Teléfonos guardia soporte",
                tipo="texto",
                archivo="telefonos.txt",
                texto="Soporte Freshservice guardia: 900 123 456",
                estado="DESTACADO",
            )

            # 8) Archivo huérfano en originales/ + caché IA antigua
            huerfano = bib.originales / "resto_huerfano_sin_ficha.pdf"
            huerfano.write_bytes(b"datos huerfanos" * 200)
            cache_ia = carpeta / "cache_manuales_ia.json"
            cache_ia.write_text('{"viejo": true}', encoding="utf-8")

            informe = analizar_biblioteca_en_profundidad(
                bib, usar_ia=False, hoy=date(2026, 10, 26)
            )

            ids_conservar = {d.doc_id for d in informe.conservar}
            ids_eliminar = {d.doc_id for d in informe.eliminar}
            motivos_elim = {d.doc_id: d.codigo_motivo for d in informe.eliminar}

            # Verificaciones de conservación
            self.assertIn(id_concil_unico, ids_conservar)
            self.assertIn(id_mermas_2026, ids_conservar)
            self.assertIn(id_hilo_nuevo, ids_conservar)
            self.assertIn(id_destacado, ids_conservar)
            self.assertIn(id_manual_albaran, ids_conservar)

            # Verificaciones de eliminación y códigos exactos
            self.assertIn(id_concil_copia, ids_eliminar)
            self.assertEqual(motivos_elim[id_concil_copia], "DUPLICADO")

            self.assertIn(id_mermas_2023, ids_eliminar)
            self.assertEqual(motivos_elim[id_mermas_2023], "VERSION_OBSOLETA")

            self.assertIn(id_correo_adj_suelto, ids_eliminar)
            self.assertEqual(motivos_elim[id_correo_adj_suelto], "ADJUNTO_YA_SUELTO")

            self.assertIn(id_hilo_viejo, ids_eliminar)
            self.assertEqual(motivos_elim[id_hilo_viejo], "HILO_REPETIDO")

            for id_ruido in (id_vista, id_kdm, id_charla, id_albaran_suelto):
                self.assertIn(id_ruido, ids_eliminar)
                self.assertEqual(motivos_elim[id_ruido], "NO_ES_MANUAL")

            self.assertIn(id_archivado, ids_eliminar)
            self.assertEqual(motivos_elim[id_archivado], "ARCHIVADO")

            self.assertEqual(len(informe.archivos_huerfanos), 1)

            # Ejecutar limpieza física + VACUUM
            res = ejecutar_limpieza_biblioteca(
                bib, [d.doc_id for d in informe.eliminar], limpiar_huerfanos=True
            )
            self.assertEqual(res.eliminados_db, len(ids_eliminar))
            self.assertEqual(res.conservados_db, 5)
            self.assertEqual(res.huerfanos_borrados, 1)
            self.assertFalse(huerfano.exists())
            self.assertFalse(cache_ia.exists())
            self.assertGreater(res.bytes_liberados, 0)

            bib.cerrar()

    def test_auditoria_ia_descarta_redundante_sutil_pero_respeta_destacados(self):
        with tempfile.TemporaryDirectory() as tmp:
            bib = Biblioteca(Path(tmp) / "conocimiento")
            id_a = _insertar_ficha_prueba(
                bib,
                clave="ia1",
                titulo="Guía Limpieza Horno Merrychef e2s",
                tipo="pdf",
                archivo="merrychef_actual.pdf",
                texto="1. Pulsar enfriamiento.\n2. Aplicar químico oficial.\n3. Aclarar y guardar.",
                fecha_doc="2026-06-10",
            )
            id_b = _insertar_ficha_prueba(
                bib,
                clave="ia2",
                titulo="Instrucciones higienización cocina rápida",
                tipo="docx",
                archivo="higienizacion_vieja.docx",
                texto="1. Apagar equipo.\n2. Limpiar interior con bayeta.\n3. Comprobar cierre.",
                fecha_doc="2022-03-01",
            )
            id_star = _insertar_ficha_prueba(
                bib,
                clave="ia3",
                titulo="Protocolo crítico gerente",
                tipo="pdf",
                archivo="critico.pdf",
                texto="1. Verificar caja.\n2. Pulsar cierre.\n3. Guardar.",
                estado="DESTACADO",
            )

            with patch(
                "modulos.depurador_biblioteca.detectar_proveedor_ia",
                return_value=("OpenAI", "sk-test"),
            ), patch(
                "modulos.depurador_biblioteca._auditar_supervivientes_con_ia",
                return_value=(
                    {
                        id_b: "Procedimiento antiguo ya cubierto por Guía Limpieza Horno Merrychef e2s",
                        id_star: "Intento de borrar destacado (debe ignorarse)",
                    },
                    "OpenAI · gpt-4o-mini",
                    "",
                ),
            ):
                informe = analizar_biblioteca_en_profundidad(bib, usar_ia=True)

            self.assertTrue(informe.usado_ia)
            ids_ok = {d.doc_id for d in informe.conservar}
            ids_del = {d.doc_id: d.codigo_motivo for d in informe.eliminar}
            self.assertIn(id_a, ids_ok)
            self.assertIn(id_star, ids_ok)
            self.assertEqual(ids_del.get(id_b), "AUDITORIA_IA")
            bib.cerrar()


if __name__ == "__main__":
    unittest.main()
