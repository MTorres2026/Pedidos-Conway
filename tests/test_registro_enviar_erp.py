"""Pruebas del envío a ERP desde la ventana «Registro de pedidos»."""
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from modulos.registro_pedidos import Registro


def _importar_ui_registro_pedidos():
    """Importa ui.registro_pedidos usando PySide6 real o stubs ligeros en headless."""
    try:
        import PySide6  # noqa: F401
        return importlib.import_module("ui.registro_pedidos")
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
        "QApplication",
        "QDialog",
        "QDialogButtonBox",
        "QFileDialog",
        "QFormLayout",
        "QHBoxLayout",
        "QHeaderView",
        "QInputDialog",
        "QLabel",
        "QLineEdit",
        "QPushButton",
        "QTableWidget",
        "QTableWidgetItem",
        "QVBoxLayout",
    ):
        setattr(qt_widgets, nombre, _BaseDummy if nombre == "QDialog" else MagicMock)

    msg = MagicMock()
    msg.StandardButton.Yes = 1
    msg.StandardButton.No = 2
    qt_widgets.QMessageBox = msg

    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qt_core
    pyside.QtWidgets = qt_widgets

    with patch.dict(
        sys.modules,
        {
            "PySide6": pyside,
            "PySide6.QtCore": qt_core,
            "PySide6.QtWidgets": qt_widgets,
        },
    ):
        sys.modules.pop("ui.registro_pedidos", None)
        return importlib.import_module("ui.registro_pedidos")


class RegistroEnviarErpTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.data_dir = self.raiz / "data"
        self.hist_dir = self.data_dir / "historial_pedidos"
        self.hist_dir.mkdir(parents=True, exist_ok=True)
        self.reg = Registro(
            ruta_db=self.data_dir / "registro_pedidos.db",
            historial_dir=self.hist_dir,
        )
        self.ui_mod = _importar_ui_registro_pedidos()

    def _instancia_sin_init(self):
        dlg = self.ui_mod.RegistroPedidosDialog.__new__(
            self.ui_mod.RegistroPedidosDialog
        )
        dlg.registro = self.reg
        dlg.boton_enviar_erp = MagicMock()
        return dlg

    def test_busca_en_historial_y_lanza_robot_con_auto(self):
        oid = "FLOW-5501"
        datos = {
            "pedido": {"orderId": oid, "totalLineas": 2},
            "lineas": [
                {"codigo": "1003020108", "cantidadPedida": 1, "unidad": "GDE"},
                {"codigo": "1001040025", "cantidadPedida": 2, "unidad": "HWC"},
            ],
        }
        (self.hist_dir / f"20261025_180000_pedido_{oid}.json").write_text(
            json.dumps(datos), encoding="utf-8"
        )
        (self.data_dir / "estado_pedido.json").write_text(
            json.dumps({"orderId": oid, "estado": "PENDIENTE"}), encoding="utf-8"
        )
        self.reg.asegurar(oid, "2026-10-25T18:00:00", 2)

        dlg = self._instancia_sin_init()
        dlg._seleccionado = lambda: self.reg.obtener(oid)
        ruta_robot_falsa = self.raiz / "src" / "robot_erp.py"
        ruta_robot_falsa.parent.mkdir(parents=True, exist_ok=True)
        ruta_robot_falsa.write_text("# robot", encoding="utf-8")
        dlg._ruta_robot = lambda: ruta_robot_falsa
        dlg._python_para_robot = lambda: sys.executable

        with patch.object(
            self.ui_mod.QMessageBox,
            "question",
            return_value=self.ui_mod.QMessageBox.StandardButton.Yes,
        ), patch.object(self.ui_mod.QMessageBox, "information") as info_mock, patch.object(
            self.ui_mod.subprocess, "Popen"
        ) as popen_mock:
            dlg.enviar_a_erp()

        popen_mock.assert_called_once()
        cmd = popen_mock.call_args.args[0]
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1], str(ruta_robot_falsa))
        self.assertTrue(cmd[2].endswith(f"pedido_{oid}.json"))
        self.assertEqual(cmd[3], "--auto")

        preparado = json.loads(Path(cmd[2]).read_text(encoding="utf-8"))
        self.assertEqual(preparado["pedido"]["orderId"], oid)
        self.assertEqual(preparado["pedido"]["estado"], "ENVIADO A ERP")
        self.assertEqual(len(preparado["lineas"]), 2)

        estado_sync = json.loads(
            (self.data_dir / "estado_pedido.json").read_text(encoding="utf-8")
        )
        self.assertEqual(estado_sync["estado"], "ENVIADO A ERP")
        info_mock.assert_called_once()

    def test_prioriza_correcciones_de_pedido_actual(self):
        oid = "FLOW-5502"
        original = {
            "pedido": {"orderId": oid},
            "lineas": [{"codigo": "1001040025", "cantidadPedida": 1, "unidad": "HWC"}],
        }
        corregido = {
            "pedido": {"orderId": oid},
            "lineas": [{"codigo": "1001040025", "cantidadPedida": 5, "unidad": "HWD"}],
        }
        (self.hist_dir / f"20261025_180000_pedido_{oid}.json").write_text(
            json.dumps(original), encoding="utf-8"
        )
        (self.data_dir / "pedido_actual.json").write_text(
            json.dumps(corregido), encoding="utf-8"
        )
        dlg = self._instancia_sin_init()
        encontrado = dlg._buscar_datos_pedido(oid)
        self.assertEqual(encontrado["lineas"][0]["cantidadPedida"], 5)
        self.assertEqual(encontrado["lineas"][0]["unidad"], "HWD")

    def test_no_reenvia_si_ya_tiene_id_solicitud(self):
        oid = "FLOW-5503"
        self.reg.asegurar(oid, "2026-10-25T18:00:00", 1)
        self.reg.fijar(oid, origen="robot", id_solicitud="447789")

        dlg = self._instancia_sin_init()
        dlg._seleccionado = lambda: self.reg.obtener(oid)
        dlg._actualizar_boton_erp()
        dlg.boton_enviar_erp.setEnabled.assert_called_with(False)

        with patch.object(self.ui_mod.QMessageBox, "information") as info_mock, patch.object(
            self.ui_mod.subprocess, "Popen"
        ) as popen_mock:
            dlg.enviar_a_erp()

        popen_mock.assert_not_called()
        info_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
