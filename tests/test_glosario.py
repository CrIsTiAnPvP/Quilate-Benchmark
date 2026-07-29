"""El glosario del informe HTML, que es memoria y por tanto puede quedarse sucia.

Un término técnico se explica la primera vez que aparece y solo esa vez:
repetir el mismo globo diez veces convierte una ayuda en ruido y a la tercera
ya nadie la lee. Eso obliga a llevar memoria de lo ya explicado, y esa memoria
es un `set` a nivel de módulo.

Es la única pieza de `html_export` con estado compartido, y por tanto la única
que puede romperse sin que nada falle: si dejara de vaciarse entre informes,
generar dos seguidos dejaría el segundo sin una sola explicación. No hay
excepción, no hay traza y el fichero se genera igual de bonito. Solo que quien
lo lea no entiende nada y nadie sabe por qué.

De ahí que esto se escriba antes de repartir el módulo y no después.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from quilate.export import html_export
from quilate.export.html_export import export_html
from quilate.projection import project_improvement
from tests.test_paridad import _auditoria, _benchmark, _sistema


def informe() -> str:
    """Un informe con contenido de verdad.

    Con el informe vacío no vale: los términos del glosario están dentro de
    secciones que solo se pintan cuando hay benchmark y hallazgos, así que un
    informe pelado no marca ninguno y el test pasaría sin mirar nada.
    """
    si, bench = _sistema(), _benchmark()
    auditor = _auditoria(si, bench)
    with tempfile.TemporaryDirectory() as d:
        destino = Path(d) / "informe.html"
        with redirect_stdout(io.StringIO()):
            export_html(destino, si, bench, auditor,
                        project_improvement(bench, auditor.findings))
        return destino.read_text(encoding="utf-8")


class LaMemoriaSeVaciaEntreInformes(unittest.TestCase):
    def test_el_segundo_informe_tambien_explica_los_terminos(self):
        # La garantía que se rompe en silencio. Sin el vaciado, el segundo
        # informe de la misma sesión sale sin una sola explicación.
        primero, segundo = informe(), informe()
        self.assertIn('class="term"', primero)
        self.assertIn('class="term"', segundo,
                      "el segundo informe ha salido sin glosario: la memoria de "
                      "términos no se ha vaciado entre uno y otro")

    def test_los_dos_traen_los_mismos_terminos(self):
        # No basta con que el segundo traiga alguno: tiene que traer los mismos.
        primero, segundo = informe(), informe()
        self.assertEqual(primero.count('class="term"'), segundo.count('class="term"'))

    def test_diez_seguidos_siguen_igual(self):
        # Si el vaciado fuera parcial, la degradación se vería a la tercera o la
        # cuarta y no a la segunda.
        cuentas = [informe().count('class="term"') for _ in range(4)]
        self.assertEqual(len(set(cuentas)), 1, f"el glosario se degrada: {cuentas}")


class CadaTerminoUnaSolaVez(unittest.TestCase):
    def setUp(self):
        html_export.reiniciar_glosario()
        self.addCleanup(html_export.reiniciar_glosario)

    def test_la_primera_aparicion_lleva_globo(self):
        marcado = html_export._term("cobertura", "cobertura")
        self.assertIn('class="term"', marcado)
        self.assertIn("cobertura", marcado)

    def test_la_segunda_ya_no(self):
        html_export._term("cobertura", "cobertura")
        self.assertNotIn('class="term"', html_export._term("cobertura", "cobertura"))

    def test_un_termino_que_no_esta_en_el_glosario_sale_tal_cual(self):
        self.assertNotIn('class="term"', html_export._term("chuchurrío", "chuchurrío"))

    def test_el_texto_se_escapa_lleve_globo_o_no(self):
        # El término lo elige el código, pero el texto visible puede venir de
        # fuera. Las dos ramas tienen que escaparlo.
        con_globo = html_export._term("<b>cobertura</b>", "cobertura")
        sin_globo = html_export._term("<b>cobertura</b>", "cobertura")
        for marcado in (con_globo, sin_globo):
            self.assertNotIn("<b>", marcado)
            self.assertIn("&lt;b&gt;", marcado)

    def test_reiniciar_deja_la_memoria_como_al_principio(self):
        html_export._term("cobertura", "cobertura")
        html_export.reiniciar_glosario()
        self.assertIn('class="term"', html_export._term("cobertura", "cobertura"))


if __name__ == "__main__":
    unittest.main()
