"""Comprobaciones que dependen del texto que devuelve Windows.

Los programas de consola escriben en la página de códigos OEM (850 en un Windows
en español), no en la ANSI que Python usa por defecto (1252). Decodificar con la
que no es no rompe nada visible: convierte «Máximo» en «M ximo» y «no está
sucio» en «no est  sucio». Ahí es donde una comprobación que busca texto
localizado deja de encontrarlo y empieza a informar de problemas inexistentes.
"""

import unittest

from quilate import audit
from quilate.platform_utils import CmdResult
from tests.support import FixtureCase, patched

# Bytes reales de `powercfg /getactivescheme` en un Windows 11 en español.
POWERCFG_ES = (b"GUID de plan de energ\xa1a: 037bb836-2b7f-4297-ae5e-911a24836144"
               b"  (M\xa0ximo rendimiento)")


class Decodificacion(unittest.TestCase):
    def test_cp850_frente_a_cp1252(self):
        # El fallo original, en una línea: la misma respuesta, dos lecturas.
        self.assertIn("máximo rendimiento", POWERCFG_ES.decode("cp850").lower())
        self.assertNotIn("máximo rendimiento", POWERCFG_ES.decode("cp1252").lower())

    def test_el_plan_de_energia_se_reconoce(self):
        from quilate.sysinfo import SystemInfo
        with patched(audit, run_cmd=lambda *a, **k: CmdResult(POWERCFG_ES.decode("cp850"))):
            a = audit.Auditor(SystemInfo(), None)
            resumen = a.check_power_plan()
        self.assertEqual([f for f in a.findings if f.id == "power_plan"], [])
        self.assertIn("Máximo rendimiento", resumen)


class VolumenSucio(FixtureCase):
    """Un falso positivo aquí manda al usuario a un chkdsk /r de horas."""

    def _auditar(self, salida):
        with patched(audit, run_cmd=lambda *a, **k: CmdResult(salida)):
            a = self.auditor()
            return a, a.check_filesystem_health()

    def test_limpio_en_varios_idiomas(self):
        for salida in ("El volumen - C: no está sucio",
                       "Volume - C: is NOT Dirty",
                       "Das Volume - C: ist nicht verschmutzt",
                       "O volume - C: não está sujo"):
            with self.subTest(salida=salida):
                a, resumen = self._auditar(salida)
                self.assertEqual(a.findings, [], f"falso positivo con: {salida}")
                self.assertEqual(resumen, "limpio")

    def test_sucio_de_verdad_si_se_detecta(self):
        for salida in ("El volumen - C: está sucio", "Volume - C: is Dirty"):
            with self.subTest(salida=salida):
                a, resumen = self._auditar(salida)
                self.assertTrue([f for f in a.findings if f.id == "fs_dirty"])

    def test_texto_ilegible_no_inventa_un_problema(self):
        # Idioma no contemplado, o codificación que aún así se tuerza: callar.
        a, resumen = self._auditar("Le volume - C: n'est pas concerné")
        self.assertEqual(a.findings, [])

    def test_respuesta_irreconocible(self):
        with patched(audit, run_cmd=lambda *a, **k: CmdResult("respuesta inesperada del sistema")):
            a = self.auditor()
            with self.assertRaises(audit.SinDato):
                a.check_filesystem_health()
        self.assertEqual(a.findings, [])

    def test_sin_salida_no_hay_hallazgo(self):
        # Responder sin decir nada no es «limpio»: es que no se ha podido mirar.
        with patched(audit, run_cmd=lambda *a, **k: CmdResult("")):
            a = self.auditor()
            with self.assertRaises(audit.SinDato):
                a.check_filesystem_health()
        self.assertEqual(a.findings, [])

    def test_el_motivo_del_fallo_llega_al_informe(self):
        # Sin privilegios `fsutil dirty query` sale con código de error. Antes
        # eso era «no ha respondido» a secas, indistinguible de un Windows sin
        # fsutil; el usuario merece saber cuál de los dos le ha pasado.
        fallo = CmdResult(ok=False, error="fsutil.exe ha terminado con código 1: "
                                          "Acceso denegado")
        with patched(audit, run_cmd=lambda *a, **k: fallo):
            a = self.auditor()
            with self.assertRaises(audit.SinDato) as caso:
                a.check_filesystem_health()
        self.assertIn("Acceso denegado", str(caso.exception))
        self.assertEqual(a.findings, [])

    def test_el_caso_que_fallaba(self):
        # Exactamente lo que producía el bug: cp850 leído como cp1252.
        roto = "El volumen - C: no est\xa0 sucio"
        a, _ = self._auditar(roto)
        self.assertEqual(a.findings, [], "el acento roto no debe disparar el hallazgo")


if __name__ == "__main__":
    unittest.main()
