"""Pruebas del flujo de captura automática de ID Solicitud en PeopleSoft."""
import json
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

import robot_id_pedido
from modulos.registro_pedidos import Registro


class _DriverFalso:
    """Simula Selenium sobre la pantalla de Mantenimiento de Solicitudes."""

    def __init__(self, pantallas):
        self._pantallas = list(pantallas)
        self._idx = 0
        self.title = "Mantenimiento de Solicitudes"

    def switch_to_noop(self):
        pass

    @property
    def switch_to(self):
        class _S:
            def default_content(self_s):
                pass

            def frame(self_s, _i):
                pass

            def parent_frame(self_s):
                pass

        return _S()

    def find_elements(self, *_args):
        return []

    def execute_script(self, _js, _ids):
        actual = self._pantallas[min(self._idx, len(self._pantallas) - 1)]
        self._idx += 1
        return actual


class ExtraccionIdSolicitudTests(unittest.TestCase):

    def test_antes_de_guardar_next_y_proveedor_no_disparan_falso_positivo(self):
        lecturas = [{
            "campos": {"REQ_HDR_REQ_ID": "NEXT"},
            "texto": (
                "Mantenimiento de Solicitudes\n"
                "Solicitud\n"
                "Unidad Negocio:\tSP001\tEstado:\tAbierta\n"
                "ID Solicitud:\tNEXT\tEstado Ppto:\tSin Ctrl\n"
                "Nombre Solicitud:\tConway 20/10/2026\n"
                "*Fecha Solicitud:\t20/10/2026\n"
                "*ID Proveedor\t0000001588\n"
            ),
        }]
        self.assertIsNone(robot_id_pedido.extraer_id(lecturas))

    def test_despues_de_guardar_extrae_desde_campo_dom_con_ceros(self):
        lecturas = [{
            "campos": {"REQ_HDR_REQ_ID": "0000447789"},
            "texto": "Unidad Negocio: SP001\nID Solicitud: 0000447789",
        }]
        encontrado = robot_id_pedido.extraer_id(lecturas)
        self.assertIsNotNone(encontrado)
        self.assertEqual(encontrado[0], "447789")

    def test_despues_de_guardar_extrae_desde_etiqueta_tabla_peoplesoft(self):
        lecturas = [{
            "campos": {"etiqueta_id_solicitud": "0000447789"},
            "texto": "",
        }]
        encontrado = robot_id_pedido.extraer_id(lecturas)
        self.assertIsNotNone(encontrado)
        self.assertEqual(encontrado[0], "447789")

    def test_despues_de_guardar_extrae_desde_texto_visible_en_pantalla(self):
        lecturas = [{
            "campos": {},
            "texto": (
                "Mantenimiento de Solicitudes\n"
                "Solicitud\n"
                "Unidad Negocio:\tSP001\tEstado:\tAbierta\n"
                "ID Solicitud:\t0000447789\tEstado Ppto:\tSin Ctrl\n"
                "Nombre Solicitud:\tCoca Cola 20/10/26\n"
                "*ID Proveedor\t0000001588\n"
            ),
        }]
        encontrado = robot_id_pedido.extraer_id(lecturas)
        self.assertIsNotNone(encontrado)
        self.assertEqual(encontrado[0], "447789")

    def test_rechaza_fechas_y_codigos_de_una_cifra(self):
        for invalido in ("NEXT", "", "20/10/2026", "0000000001", "0000000000"):
            with self.subTest(valor=invalido):
                self.assertIsNone(
                    robot_id_pedido.extraer_id([{"campos": {"REQ_HDR_REQ_ID": invalido}, "texto": ""}])
                )


class VigilanteYRegistroTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.db = self.raiz / "data" / "registro_pedidos.db"
        self.hist = self.raiz / "data" / "historial_pedidos"
        self.hist.mkdir(parents=True, exist_ok=True)

    def test_vigilante_guarda_en_id_solicitud_y_deja_pedido_y_recepcion_para_humano(self):
        order_id = "FLOW-1792001"
        datos_pedido = {
            "pedido": {
                "orderId": order_id,
                "timestamp": 1760978975000,
                "totalLineas": 7,
            },
            "lineas": [{"codigo": "1001020034", "cantidadPedida": 4, "unidad": "HAF"}],
        }
        archivo_hist = self.hist / f"20261020_164935_pedido_{order_id}.json"
        archivo_hist.write_text(json.dumps(datos_pedido), encoding="utf-8")

        archivo_proc = self.raiz / f"pedido_{order_id}.json"
        archivo_proc.write_text(json.dumps(datos_pedido), encoding="utf-8")

        driver = _DriverFalso([
            {"campos": {"REQ_HDR_REQ_ID": "NEXT"}, "texto": "ID Solicitud: NEXT"},
            {"campos": {"REQ_HDR_REQ_ID": "0000447789"}, "texto": "ID Solicitud: 0000447789"},
            {"campos": {"REQ_HDR_REQ_ID": "0000447789"}, "texto": "ID Solicitud: 0000447789"},
        ])

        detectados = []
        reloj_vals = iter([0.0, 1.0, 2.0, 3.0, 4.0])
        reg = Registro(ruta_db=self.db, historial_dir=self.hist)

        with patch("modulos.registro_pedidos.Registro", return_value=reg):
            resultado = robot_id_pedido.vigilar_id_pedido(
                driver,
                archivo_proc,
                intervalo=0.0,
                log=lambda _m: None,
                dormir=lambda _s: None,
                reloj=lambda: next(reloj_vals),
                al_detectar=detectados.append,
            )

        self.assertEqual(resultado, "447789")
        self.assertEqual(detectados, ["447789"])

        ficha = reg.obtener(order_id)
        self.assertIsNotNone(ficha)
        self.assertEqual(ficha["id_solicitud"], "447789")
        self.assertEqual(ficha["id_solicitud_origen"], "robot")
        self.assertEqual(ficha["id_pedido"], "")
        self.assertEqual(ficha["id_recepcion"], "")
        self.assertEqual(reg.estado_de(ficha), "Guardado en ERP · falta ID pedido")
        self.assertEqual(reg.nombre_visible(ficha), "Solicitud 447789")

        # Volcado en el JSON del historial
        hist_guardado = json.loads(archivo_hist.read_text(encoding="utf-8"))
        self.assertEqual(hist_guardado["pedido"].get("idSolicitud"), "447789")

        # Luego el humano anota ID pedido e ID recepción a mano
        ficha = reg.fijar(order_id, origen="manual", id_pedido="881234")
        self.assertEqual(reg.estado_de(ficha), "Pedido emitido · pendiente de recepción")
        self.assertEqual(reg.nombre_visible(ficha), "Pedido 881234")

        ficha = reg.fijar(order_id, origen="manual", id_recepcion="990112")
        self.assertEqual(reg.estado_de(ficha), "Recepcionado · falta factura")

        ficha = reg.fijar(order_id, origen="manual", factura_numero="CNW-2026-55")
        self.assertEqual(reg.estado_de(ficha), "Completo")

    def test_analizar_correo_asocia_por_id_solicitud(self):
        reg = Registro(ruta_db=self.db, historial_dir=self.hist)
        reg.asegurar("FLOW-100", "2026-10-20T16:00:00", 5)
        reg.fijar("FLOW-100", origen="robot", id_solicitud="447789")

        msg = EmailMessage()
        msg["Subject"] = "Orden de compra PO_ID 0000881234 (Solicitud 0000447789)"
        msg.set_content("Se ha generado el pedido 0000881234 correspondiente a la ID de solicitud 0000447789.")
        ruta_eml = self.raiz / "po.eml"
        ruta_eml.write_bytes(msg.as_bytes())

        analisis = reg.analizar_correo(ruta_eml)
        self.assertEqual(analisis.pedido_sugerido, "FLOW-100")
        ids_candidatos = [c[0] for c in analisis.candidatos]
        self.assertIn("881234", ids_candidatos)
        self.assertNotIn("447789", ids_candidatos)


if __name__ == "__main__":
    unittest.main()
