# -*- coding: utf-8 -*-
r"""Genera un ZIP con SOLO los archivos cambiados hoy, listo para el PC del cine.

Pensado para instalaciones con WinPython donde NO se quiere clonar ni
instalar nada: solo copiar los archivos nuevos encima de los viejos.

Uso (desde la raiz del proyecto):

    python herramientas\generar_actualizacion_cine.py

Crea: ACTUALIZACION_CINE.zip en la raiz. Ese ZIP esta excluido por
.gitignore (*.zip), asi que NUNCA se sube a GitHub.

Dentro del ZIP:
    ACTUALIZACION_CINE/
        LEEME.txt
        APLICAR.bat      <- copia los archivos (con respaldo previo)
        COMPROBAR.bat    <- comprueba la instalacion
        archivos/        <- los archivos de hoy, con sus rutas originales

NO incluye .env ni data/ (credenciales y pedidos locales).
"""

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
BASE_COMMIT = "af250ad4ca99bd4f44b645f2397e95b2f3e394c4"
SALIDA = BASE / "ACTUALIZACION_CINE.zip"


def git(*args):
    try:
        return subprocess.check_output(
            ["git", *args], cwd=str(BASE), stderr=subprocess.DEVNULL
        ).decode("utf-8", "replace").splitlines()
    except Exception:
        return []


def archivos_de_hoy():
    nombres = set()
    for linea in git("diff", "--name-only", BASE_COMMIT, "HEAD"):
        if linea.strip():
            nombres.add(linea.strip())
    # Cambios aun sin confirmar (por si se genera el ZIP antes del commit).
    for linea in git("diff", "--name-only"):
        if linea.strip():
            nombres.add(linea.strip())
    for linea in git("ls-files", "--others", "--exclude-standard"):
        if linea.strip():
            nombres.add(linea.strip())
    return sorted(
        n for n in nombres
        if (BASE / n).is_file()
        and not n.startswith("data/")
        and not n.startswith("tests/")   # los tests no hacen falta en el cine
        and ".env" not in n
        and not n.endswith(".zip")
        and not n.endswith(".pyc")
        and not n.endswith(".bak_actualizacion")
    )


COMPROBAR_BAT = """@echo off
chcp 65001 >nul
title CineStock - Comprobar instalacion
cd /d "%~dp0.."
if exist "herramientas\\comprobar_instalacion.bat" (
  call "herramientas\\comprobar_instalacion.bat"
) else (
  echo ERROR: falta herramientas\\comprobar_instalacion.bat
  echo Ejecuta primero APLICAR.bat
  pause
)
"""

LEEME = """ACTUALIZACION DE LOS DOS DIAS (26 y 27 OCT 2026) - PC DEL CINE
================================================================

Incluye TODO lo creado y revisado en estos dos dias:
  * 26/10: MENU INICIO CON IA (chat de procedimientos, Hojas de Manual
    Word/Excel, lector de PPTX/PDF/imagenes, depurador de Biblioteca).
  * 27/10: Robot ERP v6c, 7 correcciones ERP, familias blindadas,
    verificador de pedidos, guias y herramientas.

NO tienes que instalar nada, ni clonar, ni tocar el .env ni la carpeta data/.
Solo hay que COPIAR los archivos nuevos encima de tu CineStock (WinPython).

PASO 1
  Copia esta carpeta ACTUALIZACION_CINE completa (con su subcarpeta
  "archivos") DENTRO de la carpeta de CineStock del cine, es decir,
  al lado de la carpeta "src" y de "src\\main.py".

PASO 2
  Haz doble clic en APLICAR.bat.
  - Guarda una copia de seguridad automatica en
    RESPALDO_antes_actualizar_<fecha>
  - Copia los archivos nuevos manteniendo sus rutas.
  - NO toca .env, data/, WinPython ni dist/.

PASO 3
  Haz doble clic en COMPROBAR.bat.
  Debe acabar diciendo: TODO OK
  y la version del robot debe ser: v6c

  Si COMPROBAR.bat dice que no encuentra Python, abre el
  "WinPython Command Prompt" y escribe:
      python src\\comprobar_instalacion.py
      python src\\comprobar_version_robot.py

PASO 4
  Arranca CineStock como lo hacias hasta ahora (o con
  herramientas\\arrancar_cinestock.bat, que ya busca el Python de
  WinPython). NO uses dist\\CineStock\\CineStock.exe: es una
  compilacion antigua sin los arreglos de hoy.

SI ALGO VA MAL
  Los archivos anteriores estan en RESPALDO_antes_actualizar_<fecha>.
  Copialos de vuelta para deshacer la actualizacion.

QUE SE HA ACTUALIZADO
  26/10 - MENU INICIO CON IA:
    - src/ui/inicio.py            (chat asistente sobre manuales)
    - src/modulos/asistente_inicio.py   (motor de estudio, Hojas de Manual)
    - src/modulos/depurador_biblioteca.py
    - src/ui/conocimiento.py, src/modulos/conocimiento.py
    Ojo: necesita python-docx, python-pptx, Pillow y certifi. Si el PC
    del cine no las tiene, ejecuta UNA VEZ:
        python -m pip install -r requirements.txt
    (COM probar.bat te avisa si faltan.)

  27/10 - ROBOT ERP Y CORRECCIONES:
    - src/robot_erp.py v6c: la ventana flotante y el mensaje final indican
      los PRODUCTOS CON ERROR con su CODIGO ERP; ya no borra lineas buenas.
    - 7 correcciones ERP verificadas (ICG, Cuchara Madera, Tapa Vaso
      Mediana, Piruleta Corazon, Carton Palomitas XXL, ...).
    - Familias con variantes blindadas (Kit Kat, Skittle, Haribo,
      Estuchados, Helados, Kojak/Kojack).
    - Receptor, catalogo, regenerador y verificador de pedidos.

Las credenciales (.env) y los pedidos (data/) NO viajan en este ZIP.
La biblioteca de documentos del cine se queda EXACTAMENTE como esta.
"""


def main():
    lista = archivos_de_hoy()
    if not lista:
        print("ERROR: no se han encontrado archivos cambiados.")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="actualizacion_cine_"))
    raiz = tmp / "ACTUALIZACION_CINE"
    dest = raiz / "archivos"
    dest.mkdir(parents=True)

    for nombre in lista:
        origen = BASE / nombre
        objetivo = dest / nombre
        objetivo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, objetivo)

    aplicar = BASE / "herramientas" / "APLICAR_ACTUALIZACION_CINE.bat"
    if not aplicar.is_file():
        print("ERROR: falta herramientas/APLICAR_ACTUALIZACION_CINE.bat")
        return 1
    shutil.copy2(aplicar, raiz / "APLICAR.bat")

    (raiz / "COMPROBAR.bat").write_text(
        COMPROBAR_BAT.replace("\n", "\r\n"), encoding="ascii", newline=""
    )
    (raiz / "APLICAR.bat").write_bytes(
        (raiz / "APLICAR.bat").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    )
    (raiz / "LEEME.txt").write_text(LEEME, encoding="utf-8-sig")

    if SALIDA.exists():
        SALIDA.unlink()
    with zipfile.ZipFile(SALIDA, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(raiz.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(tmp).as_posix())

    print("=" * 70)
    print("  ZIP CREADO:", SALIDA)
    print("  Tamanio:", round(SALIDA.stat().st_size / 1024, 1), "KB")
    print("  Archivos incluidos:", len(lista))
    print("=" * 70)
    for n in lista:
        print("   ", n)
    print("=" * 70)
    print("  NO incluye .env ni data/ (correcto).")
    print("  Copia el ZIP a un USB y descomprimelo en el PC del cine.")
    print("=" * 70)

    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
