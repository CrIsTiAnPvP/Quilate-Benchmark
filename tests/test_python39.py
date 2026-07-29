"""La promesa de funcionar en Python 3.9, comprobada en vez de recordada.

`README.md` y `quilate.py` prometen Python 3.9 o superior, pero el entorno de
desarrollo es un 3.10 y no hay nada que ejecute la suite en 3.9. La promesa se
sostiene hoy por disciplina —los 21 módulos llevan `from __future__ import
annotations`, así que `str | None` y `list[dict]` nunca se evalúan— y es fácil
de romper sin darse cuenta con una línea que parece inofensiva.

Esto no ejecuta nada en 3.9: comprueba que el código se puede *parsear* con la
gramática de 3.9. Eso sí caza un `match` (3.10) o un `except*` (3.11), que son
gramática.

Lo que NO caza, comprobado y dicho aquí para que nadie le suponga más alcance
del que tiene:

  · `zip(a, b, strict=True)` — es un argumento, no sintaxis: `ast.parse` lo
    acepta encantado y revienta en 3.9 al ejecutarse.
  · `"cadena".removeprefix(...)` — un método que en 3.9 no existe.
  · `typing.get_type_hints()` sobre una anotación moderna, o un
    `isinstance(x, int | str)`, que sí evalúa el `|`.

Para esos tres hace falta ejecutar la suite en un 3.9 de verdad, y eso es lo que
hace la matriz de `.github/workflows/tests.yml` desde que incluye 3.9. Este
fichero sigue teniendo sentido igual: caza la regresión en local, antes de
empujar, y `LaMatrizDeCiEmpiezaEnLaMinima` comprueba que la matriz no vuelva a
dejarse fuera la única versión que de verdad hay que vigilar.
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

VERSION_MINIMA = (3, 9)

PAQUETE = pathlib.Path(__file__).resolve().parent.parent / "quilate"
RAIZ = PAQUETE.parent


def modulos() -> list[pathlib.Path]:
    """Todos los .py del paquete, más el lanzador y las herramientas."""
    encontrados = sorted(PAQUETE.rglob("*.py"))
    encontrados += [RAIZ / "quilate.py"]
    encontrados += sorted((RAIZ / "tools").glob("*.py"))
    return [p for p in encontrados if p.is_file() and "__pycache__" not in p.parts]


def _anotaciones(arbol: ast.AST):
    """Todas las anotaciones escritas en el módulo: argumentos, retornos y campos."""
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.arg, ast.AnnAssign)) and nodo.annotation is not None:
            yield nodo.annotation
        elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.returns:
            yield nodo.returns


def _anotaciones_con_barra(arbol: ast.AST) -> bool:
    """Si el módulo escribe `str | None` en alguna anotación (PEP 604, 3.10+)."""
    return any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr)
               for anotacion in _anotaciones(arbol)
               for n in ast.walk(anotacion))


def _aplaza_anotaciones(arbol: ast.AST) -> bool:
    return any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(alias.name == "annotations" for alias in n.names)
               for n in arbol.body)


class Sintaxis(unittest.TestCase):
    def setUp(self):
        self.ficheros = modulos()

    def test_hay_modulos_que_comprobar(self):
        # Si el glob deja de encontrar nada, este fichero pasaría en verde sin
        # haber mirado ni una línea.
        self.assertGreaterEqual(len(self.ficheros), 20,
                                f"solo se han encontrado {len(self.ficheros)} módulos")

    def test_todo_se_parsea_con_la_gramatica_de_39(self):
        for ruta in self.ficheros:
            with self.subTest(modulo=ruta.name):
                fuente = ruta.read_text(encoding="utf-8")
                try:
                    ast.parse(fuente, filename=str(ruta),
                              feature_version=VERSION_MINIMA)
                except SyntaxError as exc:
                    self.fail(f"{ruta.relative_to(RAIZ)}:{exc.lineno} usa sintaxis "
                              f"posterior a Python {'.'.join(map(str, VERSION_MINIMA))}: "
                              f"{exc.msg}")

    def test_las_anotaciones_no_se_evaluan_en_tiempo_de_ejecucion(self):
        """`str | None` en una firma revienta en 3.9 si el módulo no lo aplaza.

        Es la forma más probable de romper la promesa: escribir una anotación
        moderna en un módulo nuevo y no acordarse del import de futuro. Con él,
        la anotación se queda en texto y da igual la versión.
        """
        for ruta in self.ficheros:
            with self.subTest(modulo=ruta.name):
                arbol = ast.parse(ruta.read_text(encoding="utf-8"))
                if not _anotaciones_con_barra(arbol):
                    continue
                self.assertTrue(
                    _aplaza_anotaciones(arbol),
                    f"{ruta.name} escribe anotaciones con `|` y no aplaza su "
                    f"evaluación: en Python 3.9 esto revienta al importar")


class LaMatrizDeCiEmpiezaEnLaMinima(unittest.TestCase):
    """La versión mínima es la única que no se puede dejar sin ejecutar.

    La matriz empezaba en 3.10 mientras el comentario de al lado decía que
    cubría «desde la mínima». Nadie lo notó porque un comentario no falla. Esto
    sí: si alguien sube el suelo de la matriz sin subir `VERSION_MINIMA` —o al
    revés— se entera aquí y no meses después, en el equipo de un usuario.
    """

    FLUJO = RAIZ / ".github" / "workflows" / "tests.yml"

    def versiones(self) -> list[tuple[int, ...]]:
        texto = self.FLUJO.read_text(encoding="utf-8")
        linea = next(l for l in texto.splitlines() if l.strip().startswith("python:"))
        return sorted(tuple(int(p) for p in v.split("."))
                      for v in re.findall(r'"(\d+\.\d+)"', linea))

    def test_la_matriz_se_puede_leer(self):
        # Si el formato del flujo cambia y el regex deja de encontrar nada, el
        # test de abajo pasaría en vacío.
        self.assertGreaterEqual(len(self.versiones()), 2)

    def test_la_mas_baja_es_la_version_minima(self):
        self.assertEqual(
            self.versiones()[0], VERSION_MINIMA,
            f"la matriz de CI empieza en {self.versiones()[0]} y el proyecto "
            f"promete {VERSION_MINIMA}: la versión mínima es justo la que no "
            f"puede quedarse sin ejecutar")


if __name__ == "__main__":
    unittest.main()
