"""Lo que `--compare` enseña por pantalla, que hasta ahora no miraba nadie.

`compare.py` estaba bien probado: decide si una diferencia supera el margen y
si dos ejecuciones son comparables. Pero de ahí a lo que ve el usuario hay un
módulo entero sin una sola prueba, y es uno de los tres modos de la
herramienta. Un `KeyError` aquí no es un dato mal calculado: es que no sale
nada.

Lo que se comprueba no es la maquetación —eso cambiará— sino lo que no puede
dejar de aparecer: los avisos de comparabilidad cuando los hay, el veredicto de
cada prueba, y sobre todo las dos advertencias que impiden leer mal el
resultado: que una mejora dentro del margen no es una mejora, y que un hallazgo
que desaparece porque dejó de comprobarse no es un hallazgo resuelto.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from quilate.compare import compare_runs
from quilate.compare_report import print_comparison
from quilate.console import C
from tests.test_comparacion import con_lectura, ejecucion


def con_nota(overall: float) -> dict:
    """Una ejecución con la puntuación global que se le pida.

    El helper `ejecucion` de `test_comparacion` acepta claves de primer nivel,
    así que la nota hay que darla dentro de `scores` y no como argumento suelto.
    """
    return ejecucion(scores={"overall": overall, "components": {"disk": 100.0}})


def salida(antes: dict, despues: dict) -> str:
    C.disable()
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        print_comparison(compare_runs(antes, despues), "antes.json", "despues.json")
    return buffer.getvalue()


class ElInformeSaleEntero(unittest.TestCase):
    def test_dos_ejecuciones_normales(self):
        texto = salida(ejecucion(), ejecucion())
        # `section()` los imprime en mayúsculas.
        for bloque in ("COMPARACIÓN DE DOS EJECUCIONES", "PRUEBA A PRUEBA",
                       "QUÉ HA CAMBIADO EN LA AUDITORÍA"):
            with self.subTest(bloque=bloque):
                self.assertIn(bloque, texto)

    def test_nombra_los_dos_ficheros(self):
        texto = salida(ejecucion(), ejecucion())
        self.assertIn("antes.json", texto)
        self.assertIn("despues.json", texto)

    def test_un_json_minimo_no_lo_tumba(self):
        # `compare_runs` tolera ejecuciones a las que les falta casi todo. El
        # informe tiene que tolerar lo mismo o esa tolerancia no sirve de nada.
        minimo = {"meta": {}, "scores": {}}
        texto = salida(minimo, minimo)
        self.assertIn("PRUEBA A PRUEBA", texto)

    def test_sin_dispersion_se_explica_el_interrogante(self):
        sin_margen = ejecucion()
        del sin_margen["dispersion"]
        texto = salida(sin_margen, ejecucion())
        self.assertIn("?", texto)
        self.assertIn("margen amplio", texto)


class LoQueNoSePuedeDejarDeDecir(unittest.TestCase):
    """Las dos advertencias sin las cuales el informe se lee al revés."""

    def test_avisa_de_que_el_margen_manda(self):
        # Es la idea entera de este modo: sin esta frase, quien lea la tabla
        # celebra un +3% que es ruido.
        self.assertIn("dentro del margen no es una mejora", salida(ejecucion(), ejecucion()))

    def test_un_hallazgo_que_dejo_de_comprobarse_no_es_uno_resuelto(self):
        antes, despues = ejecucion(), ejecucion()
        antes["coverage"] = {"checks_conclusive": 20, "checks_total": 24,
                             "unverified": [{"check": "Chip TPM"}]}
        texto = salida(antes, despues)
        self.assertIn("Chip TPM", texto)
        self.assertIn("no es un hallazgo resuelto", texto)

    def test_los_motivos_graves_se_marcan_como_tales(self):
        otra = ejecucion()
        otra["system"]["cpu_name"] = "CPU DISTINTA"
        texto = salida(otra, ejecucion())
        self.assertIn("no se restan sin más", texto)
        self.assertIn("CPU DISTINTA", texto)
        self.assertIn("no la pone el equipo", texto)

    def test_sin_pegas_no_se_inventa_ninguna(self):
        self.assertNotIn("no se restan sin más", salida(ejecucion(), ejecucion()))


class ElVeredictoDeCadaPrueba(unittest.TestCase):
    def _fila(self, antes_mbs: float, despues_mbs: float, spread: float = 4.0) -> str:
        texto = salida(con_lectura(antes_mbs, spread), con_lectura(despues_mbs, spread))
        return next(l for l in texto.splitlines() if "Lectura" in l or "lectura" in l)

    def test_una_mejora_grande_se_dice_mejora(self):
        self.assertIn("mejora", self._fila(1000.0, 1400.0))

    def test_una_subida_pequena_se_dice_ruido(self):
        # La misma cifra que arriba pero por debajo del margen: el informe no
        # puede enseñarla como mejora aunque el número suba.
        fila = self._fila(1000.0, 1020.0)
        self.assertIn("dentro del margen", fila)
        self.assertNotIn("mejora", fila.replace("dentro del margen", ""))

    def test_una_bajada_grande_se_dice_empeora(self):
        self.assertIn("empeora", self._fila(1000.0, 600.0))

    def test_las_dos_cifras_aparecen(self):
        fila = self._fila(1000.0, 1400.0)
        self.assertIn("1,000", fila)
        self.assertIn("1,400", fila)


class LaProyeccionFrenteALaRealidad(unittest.TestCase):
    """La razón de ser del modo: contrastar lo prometido con lo que pasó."""

    def _con_proyeccion(self, prometido: float, logrado: float) -> str:
        antes = con_nota(100.0)
        antes["projection"] = {"projected_overall": prometido, "current_overall": 100.0}
        return salida(antes, con_nota(logrado))

    def test_se_dice_cuanto_se_materializo(self):
        texto = self._con_proyeccion(120.0, 120.0)
        self.assertIn("LA PROYECCIÓN FRENTE A LA REALIDAD", texto)
        self.assertIn("100%", texto)

    def test_una_mejora_a_medias_se_dice_a_medias(self):
        self.assertIn("50%", self._con_proyeccion(120.0, 110.0))

    def test_se_explica_por_que_puede_no_haberse_cumplido(self):
        # Sin esto, un 30% se lee como «el programa miente» en vez de como
        # «no se aplicaron las recomendaciones».
        texto = self._con_proyeccion(120.0, 106.0)
        self.assertIn("no llegaran a aplicarse", texto)

    def test_sin_proyeccion_no_se_inventa_el_bloque(self):
        self.assertNotIn("LA PROYECCIÓN FRENTE A LA REALIDAD",
                         salida(ejecucion(), ejecucion()))


class LasCondicionesDeMedida(unittest.TestCase):
    def test_se_avisa_de_la_cpu_ajena(self):
        ruidosa = ejecucion()
        ruidosa["ambient_load"] = {"antes": {"cpu_pct": 41.0}}
        texto = salida(ruidosa, ejecucion())
        self.assertIn("Condiciones de medida", texto)
        self.assertIn("41% de CPU ajena", texto)

    def test_sin_ruido_no_se_dice_nada(self):
        limpia = ejecucion()
        limpia["ambient_load"] = {}
        self.assertNotIn("Condiciones de medida", salida(limpia, limpia))


if __name__ == "__main__":
    unittest.main()
