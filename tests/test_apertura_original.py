"""Ejecutar: PYTHONPATH=src python -m unittest discover -s tests -v"""
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from modulos.biblioteca import Biblioteca


class ResolucionOriginalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.b = Biblioteca(self.raiz / 'biblioteca')
        self.addCleanup(self.b.cerrar)
        self.ocr = patch.object(self.b, '_ocr_callable', return_value=None)
        self.ocr.start()
        self.addCleanup(self.ocr.stop)

    def agregar(self, nombre, datos):
        ruta = self.raiz / nombre
        ruta.write_bytes(datos)
        alta = self.b.agregar_archivo(ruta)
        self.assertTrue(alta.ok, alta)
        return alta.id

    def correo(self, adjuntos=(('Informe.txt', b'contenido del informe'),)):
        msg = EmailMessage()
        msg['Subject'] = 'RE: Nuevo formulario Disney_Matinales y pases privados'
        msg.set_content('Consultar los adjuntos.')
        for nombre, datos in adjuntos:
            msg.add_attachment(datos, maintype='application', subtype='octet-stream', filename=nombre)
        return self.agregar('correo.eml', msg.as_bytes())

    def test_suelto_tipo_desconocido_y_nombre_unicode(self):
        doc = self.agregar('Los Prados (por semanas2027).txt', b'planificacion')
        self.b._con.execute("UPDATE docs SET tipo='Archivo def.' WHERE id=?", (doc,))
        ruta = self.b.resolver_original(doc)
        self.assertTrue(ruta.is_file())
        self.assertEqual(ruta.suffix, '.txt')

    def test_excel_real_con_nombre_del_caso_reportado(self):
        from io import BytesIO
        from zipfile import ZipFile
        contenido = BytesIO()
        with ZipFile(contenido, 'w') as archivo:
            archivo.writestr('xl/worksheets/sheet1.xml',
                             '<worksheet><sheetData><row><c t="inlineStr">'
                             '<is><t>Los Prados</t></is></c></row></sheetData></worksheet>')
        doc = self.agregar('Los Prados (por semanas2027).xlsx', contenido.getvalue())
        self.assertEqual(self.b.resolver_original(doc).suffix, '.xlsx')

    def test_solo_adjunto_abre_correo(self):
        doc = self.correo()
        self.assertEqual(self.b.resolver_original(doc).suffix, '.eml')

    def test_ambos_prefiere_suelto_por_contenido(self):
        correo = self.correo()
        suelto = self.agregar('otro nombre.txt', b'contenido del informe')
        self.assertEqual(self.b.resolver_original(correo), self.b.resolver_original(suelto))

    def test_no_confunde_archivos_con_igual_nombre(self):
        correo = self.correo()
        self.agregar('Informe.txt', b'contenido diferente')
        self.assertEqual(self.b.resolver_original(correo).suffix, '.eml')

    def test_varios_adjuntos_no_elige_arbitrariamente(self):
        correo = self.correo((('uno.txt', b'uno'), ('dos.txt', b'dos')))
        self.agregar('uno.txt', b'uno')
        self.agregar('dos.txt', b'dos')
        self.assertEqual(self.b.resolver_original(correo).suffix, '.eml')

    def test_biblioteca_trasladada_desde_windows(self):
        doc = self.agregar('informe.txt', b'informe')
        esperada = self.b.resolver_original(doc)
        self.b._con.execute('UPDATE docs SET ruta_copia=? WHERE id=?',
                            ('C:\\antiguo\\originales\\' + esperada.name, doc))
        self.assertEqual(self.b.resolver_original(doc), esperada)

    def test_falta_archivo_detalla_ruta(self):
        doc = self.agregar('informe.txt', b'informe')
        ruta = self.b.resolver_original(doc)
        ruta.unlink()
        (self.raiz / 'informe.txt').unlink()
        with self.assertRaises(FileNotFoundError) as ctx:
            self.b.resolver_original(doc)
        self.assertIn(str(ruta), str(ctx.exception))
        self.assertIn('Se ha buscado en:', str(ctx.exception))

    def test_copia_perdida_usa_ruta_de_importacion(self):
        doc = self.agregar('origen.txt', b'contenido')
        self.b.resolver_original(doc).unlink()
        self.assertEqual(self.b.resolver_original(doc), self.raiz / 'origen.txt')

    def test_resultado_eliminado(self):
        with self.assertRaisesRegex(FileNotFoundError, 'Vuelve a buscar'):
            self.b.resolver_original(9999)


class AperturaInterfazTests(unittest.TestCase):
    def setUp(self):
        try:
            from ui import apertura_documentos
        except ImportError:
            self.skipTest('Estas pruebas requieren PySide6')
        self.modulo = apertura_documentos

    def test_programa_asociado_recibe_url_local(self):
        from unittest.mock import Mock
        biblioteca = Mock()
        biblioteca.resolver_original.return_value = Path('/tmp/Los Prados (por semanas2027).xlsx')
        with patch.object(self.modulo.QDesktopServices, 'openUrl', return_value=True) as abrir:
            with patch.object(self.modulo.QMessageBox, 'warning') as aviso:
                self.modulo.abrir_original(None, biblioteca, 7)
        self.assertEqual(abrir.call_args.args[0].toLocalFile(), str(biblioteca.resolver_original.return_value))
        aviso.assert_not_called()

    def test_asociacion_ausente_avisa_con_ruta(self):
        from unittest.mock import Mock
        biblioteca = Mock()
        biblioteca.resolver_original.return_value = Path('/tmp/plan.xlsx')
        with patch.object(self.modulo.QDesktopServices, 'openUrl', return_value=False):
            with patch.object(self.modulo.QMessageBox, 'warning') as aviso:
                self.modulo.abrir_original(None, biblioteca, 7)
        self.assertIn('/tmp/plan.xlsx', aviso.call_args.args[2])

    def test_error_consulta_y_biblioteca_ausente_no_son_silenciosos(self):
        from unittest.mock import Mock
        biblioteca = Mock()
        biblioteca.resolver_original.side_effect = RuntimeError('Error de base de datos')
        for valor in (biblioteca, None):
            with self.subTest(biblioteca=valor):
                with patch.object(self.modulo.QMessageBox, 'warning') as aviso:
                    self.modulo.abrir_original(None, valor, 7)
                aviso.assert_called_once()


if __name__ == '__main__':
    unittest.main()
