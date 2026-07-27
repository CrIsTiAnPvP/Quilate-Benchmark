#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 PCBench Suite  ·  Benchmark + Auditoría de Optimización
--------------------------------------------------------------------------------
 Autor : Cristian Alonso
 Web   : https://cristianac.es
 Lic.  : MIT
--------------------------------------------------------------------------------
 Mide el rendimiento real del equipo (CPU, RAM, almacenamiento), audita la
 configuración del sistema y estima el porcentaje de mejora alcanzable con
 cada optimización, ordenado por impacto/esfuerzo.

 Incluye una ficha por componente (procesador, memoria, almacenamiento,
 gráfica y sistema) que reúne inventario, nota medida y mejoras agrupadas.
 Aparece en la consola y en los tres formatos de exportación.

 IMPORTANTE: la herramienta es de SOLO LECTURA. No modifica el registro ni la
 configuración. Con --export-plan genera un script PowerShell comentado que
 puedes revisar y ejecutar tú mismo.

 Uso:
   python pcbench.py                      # análisis completo
   python pcbench.py --quick              # benchmark rápido (sin test de disco largo)
   python pcbench.py --no-bench           # solo auditoría
   python pcbench.py --disk-size 1024     # tamaño del fichero de test de disco (MB)
   python pcbench.py --export-plan        # genera plan_optimizacion.ps1
   python pcbench.py --html informe.html --json datos.json

 Este fichero es solo el lanzador. La implementación vive en el paquete
 `pcbench/`, dividida por responsabilidades:

   const.py          branding y detección de plataforma
   console.py        color ANSI, cajas, barras y formato de texto
   platform_utils.py comandos, PowerShell/WMI, registro y privilegios
   workloads.py      cargas de trabajo puras del benchmark
   sysinfo.py        inventario del equipo
   benchmark.py      motor de medición, puntuación y nota global
   audit.py          comprobaciones de configuración y hallazgos
   projection.py     combinación de ganancias y proyección
   components.py     ficha por componente
   report.py         informe de consola
   export/           json_export · html_export · plan_export
   cli.py            argumentos y orquestación

 Requisitos: Python 3.9+ · pip install psutil
================================================================================
"""

from __future__ import annotations

import sys

try:
    import psutil  # noqa: F401  (solo para dar un error claro si falta)
except ImportError:
    print("\n[!] Falta la dependencia 'psutil'.\n    Instálala con:  pip install psutil\n")
    sys.exit(1)

from pcbench.cli import run

if __name__ == "__main__":
    sys.exit(run())
