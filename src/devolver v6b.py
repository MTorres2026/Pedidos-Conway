# -*- coding: utf-8 -*-
r"""BOTÓN DE DESHACER: devuelve robot_erp.py a la v6b EXACTA.

v6c = v6b + lista de productos con error en la ventana flotante + no borrar
líneas buenas. Si en algún momento quieres volver a la v6b aprobada, ejecuta
esto desde la raíz del proyecto:

    python "src\devolver v6b.py"

La versión v6c que tengas se guarda como copia de seguridad antes de volver.
Después comprueba con:

    python src\comprobar_version_robot.py
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parent
RUTA = SRC / "robot_erp.py"
ORIGINAL = SRC / "recursos" / "robot_erp_v6b_original.py.txt"


def main():
    if not ORIGINAL.is_file():
        print(f"ERROR: no encuentro la copia de la v6b en:\n  {ORIGINAL}")
        print("Vuelve a traer los archivos de la rama con git checkout.")
        sys.exit(1)
    if not RUTA.is_file():
        print(f"ERROR: no encuentro {RUTA}")
        sys.exit(1)

    copia = RUTA.with_name(
        "robot_erp.py.bak_v6c_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    shutil.copy2(RUTA, copia)
    shutil.copy2(ORIGINAL, RUTA)
    compile(RUTA.read_text(encoding="utf-8"), str(RUTA), "exec")

    print("=" * 60)
    print("  LISTO: robot_erp.py ha vuelto a la v6b EXACTA.")
    print(f"  Tu v6c se ha guardado en: {copia.name}")
    print()
    print("  Comprueba la versión con:")
    print("      python src\\comprobar_version_robot.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
