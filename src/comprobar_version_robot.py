# -*- coding: utf-8 -*-
r"""Alias SIN ESPACIOS de «comprobar version robot.py».

Hace exactamente lo mismo, pero su nombre se puede escribir en la terminal
sin comillas. Uso:

    python src\comprobar_version_robot.py
"""
import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).with_name("comprobar version robot.py")),
    run_name="__main__",
)
