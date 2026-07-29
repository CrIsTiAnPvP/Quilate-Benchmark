"""Contraste del informe: aritmética pura sobre los colores declarados.

El informe se lee en pantalla y una parte no pequeña de la gente que lo va a
leer no distingue un gris de otro tan bien como quien lo diseñó. WCAG AA pide
4,5:1 para texto normal y 3:1 para el grande, y eso no es una opinión de estilo:
es la diferencia entre leer una nota al pie y adivinarla.

Se comprueba aquí y no a ojo porque a ojo es exactamente como se coló el fallo
que esto viene a arreglar: `--faint` estaba en 4,03:1 y parecía perfectamente
legible en el monitor de quien lo eligió.

La fórmula es la de la especificación (WCAG 2.1, relative luminance), sin
dependencias: son quince líneas.
"""

from __future__ import annotations

import re
import unittest

from quilate.export.html_export import HTML_CSS

# Mínimos de WCAG AA.
AA_TEXTO_NORMAL = 4.5
AA_TEXTO_GRANDE = 3.0


def luminancia(hexadecimal: str) -> float:
    """Luminancia relativa de un color, según WCAG 2.1."""
    crudo = hexadecimal.lstrip("#")
    if len(crudo) == 3:
        crudo = "".join(c * 2 for c in crudo)
    canales = [int(crudo[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lineales = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                for c in canales]
    return 0.2126 * lineales[0] + 0.7152 * lineales[1] + 0.0722 * lineales[2]


def contraste(uno: str, otro: str) -> float:
    """Relación de contraste entre dos colores. Va de 1:1 a 21:1."""
    a, b = luminancia(uno), luminancia(otro)
    claro, oscuro = max(a, b), min(a, b)
    return (claro + 0.05) / (oscuro + 0.05)


def variables() -> dict[str, str]:
    """Las variables de color declaradas en `:root`, leídas del CSS de verdad.

    Se sacan del módulo y no se copian aquí para que cambiar un color y no
    pasar por este test sea imposible: si alguien toca `--faint`, lo que se
    comprueba es el valor nuevo.
    """
    raiz = re.search(r":root\{(.*?)\}", HTML_CSS, re.S)
    if raiz is None:                      # pragma: no cover - el CSS siempre lo trae
        raise AssertionError("no se ha encontrado el bloque :root del informe")
    return {nombre: valor.lower()
            for nombre, valor in re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\b",
                                            raiz.group(1))}


class NavegarSinRaton(unittest.TestCase):
    """Dos cosas concretas, no una auditoría de accesibilidad entera.

    El informe se maqueta con una barra superior de una docena de enlaces antes
    del contenido, y trae un buscador que filtra secciones sin recargar nada.
    Las dos cosas son cómodas con ratón y un estorbo sin él: llegar al informe
    obliga a tabular por toda la navegación en cada carga, y el resultado del
    filtro solo existe como un número que aparece en pantalla.
    """

    def informe(self) -> str:
        import io
        import tempfile
        from pathlib import Path
        from quilate.audit import Auditor
        from quilate.export.html_export import export_html
        from quilate.projection import project_improvement
        from quilate.sysinfo import SystemInfo

        si = SystemInfo()
        auditor = Auditor(si, None)
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "informe.html"
            export_html(destino, si, None, auditor, project_improvement(None, []))
            return destino.read_text(encoding="utf-8")

    def test_hay_un_enlace_para_saltar_al_contenido(self):
        html = self.informe()
        self.assertIn('href="#contenido"', html)
        self.assertIn("Saltar al contenido", html)

    def test_el_destino_del_salto_existe(self):
        # Un enlace de salto que apunta a un ancla inexistente no salta a
        # ningún sitio y encima parece que funciona.
        self.assertIn('id="contenido"', self.informe())

    def test_el_salto_es_lo_primero_del_cuerpo(self):
        # Si va después de la navegación no sirve para nada: lo que ahorra es
        # precisamente pasar por ella.
        html = self.informe()
        cuerpo = html[html.index("<body"):]
        self.assertLess(cuerpo.index("Saltar al contenido"),
                        cuerpo.index('class="topbar"'))

    def test_el_salto_se_ve_al_recibir_el_foco(self):
        # Escondido siempre sirve al lector de pantalla pero no a quien tabula
        # mirando: un enlace que actúa sin aparecer es peor que no tenerlo.
        self.assertIn(".skip:focus", HTML_CSS)
        regla = HTML_CSS[HTML_CSS.index(".skip:focus"):][:400]
        self.assertIn("clip:auto", regla, "sin deshacer el recorte sigue invisible")

    def test_el_contador_del_filtro_se_anuncia(self):
        # Cambia sin recargar nada: sin esto, quien no ve la pantalla escribe en
        # el buscador y no se entera de que no queda ninguna sección.
        html = self.informe()
        contador = html[html.index('id="buscar-cnt"') - 60:][:200]
        self.assertIn('aria-live="polite"', contador)

    def test_el_contador_no_interrumpe_en_cada_tecla(self):
        # `assertive` interrumpiría al lector con cada pulsación mientras se
        # escribe, que es justo cuando la persona está haciendo otra cosa.
        html = self.informe()
        contador = html[html.index('id="buscar-cnt"') - 60:][:200]
        self.assertNotIn('aria-live="assertive"', contador)


class Formula(unittest.TestCase):
    """Los extremos conocidos, para que un error en la fórmula no pase por bueno."""

    def test_blanco_sobre_negro_es_el_maximo(self):
        self.assertAlmostEqual(contraste("#ffffff", "#000000"), 21.0, places=1)

    def test_un_color_consigo_mismo_es_el_minimo(self):
        self.assertAlmostEqual(contraste("#141821", "#141821"), 1.0, places=6)

    def test_da_igual_el_orden(self):
        self.assertAlmostEqual(contraste("#ffffff", "#141821"),
                               contraste("#141821", "#ffffff"), places=9)


class TextoSobreElFondo(unittest.TestCase):
    """Cada color de texto contra cada superficie sobre la que se pinta."""

    # Los tres fondos que declara el informe. `--surface2` es el más claro de
    # los tres, así que es el que menos contraste deja: si un texto pasa ahí,
    # pasa en los otros dos.
    FONDOS = ("--ink", "--surface", "--surface2")
    TEXTOS = ("--txt", "--dim", "--faint")

    def setUp(self):
        self.colores = variables()

    def test_estan_declarados_todos(self):
        for nombre in self.FONDOS + self.TEXTOS:
            self.assertIn(nombre, self.colores, f"{nombre} ya no está en :root")

    def test_todo_el_texto_cumple_wcag_aa(self):
        for texto in self.TEXTOS:
            for fondo in self.FONDOS:
                with self.subTest(texto=texto, fondo=fondo):
                    ratio = contraste(self.colores[texto], self.colores[fondo])
                    self.assertGreaterEqual(
                        round(ratio, 2), AA_TEXTO_NORMAL,
                        f"{texto} ({self.colores[texto]}) sobre {fondo} "
                        f"({self.colores[fondo]}) da {ratio:.2f}:1, por debajo "
                        f"de {AA_TEXTO_NORMAL}:1")

    def test_el_faint_sigue_siendo_el_mas_tenue(self):
        # No es solo cuestión de pasar el umbral: la jerarquía visual tiene que
        # seguir siendo la misma, o subir el contraste habría aplanado el diseño.
        fondo = self.colores["--surface"]
        self.assertLess(contraste(self.colores["--faint"], fondo),
                        contraste(self.colores["--dim"], fondo))
        self.assertLess(contraste(self.colores["--dim"], fondo),
                        contraste(self.colores["--txt"], fondo))


class ColoresDelSemaforo(unittest.TestCase):
    """El diagnóstico se lee en texto pequeño sobre la superficie de la tarjeta."""

    def setUp(self):
        self.colores = variables()

    def test_llegan_al_minimo_de_texto_grande(self):
        # Estos van en insignias y cifras grandes, no en texto corrido, así que
        # el umbral que les aplica es el de 3:1. Se comprueba igualmente: un
        # rojo que no se distinga del fondo no avisa de nada.
        fondo = self.colores["--surface"]
        for nombre in ("--ok", "--warn", "--bad", "--gold", "--acc"):
            with self.subTest(color=nombre):
                ratio = contraste(self.colores[nombre], fondo)
                self.assertGreaterEqual(
                    round(ratio, 2), AA_TEXTO_GRANDE,
                    f"{nombre} ({self.colores[nombre]}) da {ratio:.2f}:1 sobre "
                    f"la superficie de las tarjetas")


if __name__ == "__main__":
    unittest.main()
