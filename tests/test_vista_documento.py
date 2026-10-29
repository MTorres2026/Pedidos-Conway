"""Ejecutar: PYTHONPATH=src python -m unittest discover -s tests -v"""
import unittest

from ui import vista_documento as v

# El muro de texto tal y como salía en pantalla (Excel de Yelmo).
MURO_EXCEL = """Responsable de
Conciliación de inventarios
• (HISPRE) Reporte de Conteo de
Histórico de Variación, previo al
conteo.
• (ICON) Informe de Conciliación PS
____________________
Gerente de Cine
Inventarios
Quincenal
P2P-INCSP-7G
en Cines
PDF.
• (HISPOS) Reporte de Conteo de
Cine
Histórico de Variación, posterior al
conteo (Todo).

Este documento es propiedad de Yelmo Cines © (incluyendo a cualquiera de sus empresas afiliadas o subsidiarias) y está
directamente relacionado con los demás documentos
normativos. El responsable reconoce y acepta expresamente que el contenido de la información es correcta y
fidedigna y entiende que la firma plasmada en este
documento valida los informes ingresados al Micrositio definido, entendiéndose que la firma aquí plasmada se replica en cada
uno de ellos. La información contenida podrá ser
utilizada para los usos y fines que Yelmo Cines considere convenientes. Prohibido el uso de esta información para fines distintos
a los establecidos.
Página 2 de 2"""

CORREO = """Buenos días:

Os escribo para confirmar el procedimiento de cierre de caja de los
viernes. A partir de esta semana el arqueo se hará a las 23:30 y el
responsable deberá enviar la hoja firmada antes de salir.

• Revisar el fondo de caja (150 €).
• Contar las monedas de la máquina de cambio
y anotar el total en la hoja.
• Cerrar el TPV y guardar el justificante.

Gracias a todos.

--------------------------------------------------
De: Laura Gómez <laura@example.com>
Enviado el: lunes, 12 de mayo de 2026 9:03
Para: Gerentes
Asunto: Cierre de caja

Adjunto la hoja anterior por si sirve de modelo."""

TABLA = "Producto\tStock\tMínimo\nPalomitas 500 g\t12\t20\nVasos 1 L\t340\t200"


class BloquesTests(unittest.TestCase):

    def test_pie_legal_fuera(self):
        bloques, hubo_legal = v.bloques(MURO_EXCEL)
        html_out = v.a_html(bloques)
        self.assertTrue(hubo_legal)
        self.assertNotIn("propiedad de Yelmo", html_out)
        self.assertNotIn("Página 2 de 2", html_out)
        self.assertNotIn("subsidiarias", html_out)

    def test_vinetas_y_desenvuelto(self):
        bloques, _ = v.bloques(MURO_EXCEL)
        html_out = v.a_html(bloques)
        # La frase partida en tres líneas vuelve a ser una sola viñeta.
        self.assertIn(
            "(HISPRE) Reporte de Conteo de Histórico de Variación, "
            "previo al conteo.", html_out)
        self.assertIn("<li", html_out)

    def test_campos_sueltos_como_lista(self):
        bloques, _ = v.bloques(MURO_EXCEL)
        tipos = [t for t, _ in bloques]
        self.assertIn("lista", tipos)

    def test_hilo_de_correo_colapsado(self):
        bloques, _ = v.bloques(CORREO)
        hilos = [c for t, c in bloques if t == "hilo"]
        self.assertEqual(len(hilos), 1)
        self.assertIn("Laura Gómez", hilos[0]["de"])
        html_out = v.a_html(bloques)
        self.assertNotIn("Enviado el:", html_out)
        self.assertIn("Mensaje anterior", html_out)

    def test_parrafo_envuelto_por_outlook(self):
        bloques, _ = v.bloques(CORREO)
        parrafos = [c for t, c in bloques if t == "parrafo"]
        unidos = " ".join(parrafos)
        self.assertIn("cierre de caja de los viernes", unidos)
        self.assertIn("arqueo se hará a las 23:30 y el responsable", unidos)

    def test_tabla(self):
        bloques, _ = v.bloques(TABLA)
        tablas = [c for t, c in bloques if t == "tabla"]
        self.assertEqual(len(tablas), 1)
        self.assertEqual(tablas[0][1][0], "Palomitas 500 g")
        self.assertIn("<table", v.a_html(bloques))

    def test_resaltado_y_escape(self):
        bloques, _ = v.bloques("El cierre <b>no</b> es a las 23:30 de hoy.")
        html_out = v.a_html(bloques, ["cierre"])
        self.assertIn("background-color:#ffe08a", html_out)
        self.assertIn("&lt;b&gt;", html_out)

    def test_url_enlazada(self):
        bloques, _ = v.bloques("Manual en https://ejemplo.es/manual.pdf para todos.")
        self.assertIn('<a href="https://ejemplo.es/manual.pdf"', v.a_html(bloques))


class ResumenTests(unittest.TestCase):

    def test_resumen_sin_saludos(self):
        r = v.resumen(CORREO)
        self.assertTrue(r.startswith("Os escribo para confirmar"), r)
        self.assertIn("23:30", r)
        self.assertNotIn("Buenos días", r)
        self.assertLessEqual(len(r), 330)

    def test_sin_resumen_en_cascada_de_celdas(self):
        self.assertEqual(v.resumen(MURO_EXCEL), "")


if __name__ == "__main__":
    unittest.main()
