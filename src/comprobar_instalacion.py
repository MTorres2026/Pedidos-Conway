# -*- coding: utf-8 -*-
r"""Comprueba que el PC DEL CINE esta listo para trabajar.

SOLO LEE. No modifica nada, no muestra credenciales y no toca el ERP.
Ejecutalo en la raiz del proyecto:

    python src\comprobar_instalacion.py

Dice [OK], [AVISO] o [FALTA] para cada cosa y, al final, si puedes arrancar.
"""

import importlib.util
import json
import os
import re
import shutil
import socket
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "src"
PORT = 8765

OK = "[OK]   "
AVISO = "[AVISO]"
FALTA = "[FALTA]"

fallos = []
avisos = []


def decir(marca, texto):
    print(f"  {marca} {texto}")
    if marca == FALTA:
        fallos.append(texto)
    elif marca == AVISO:
        avisos.append(texto)


def separador(titulo):
    print()
    print("=" * 74)
    print("  " + titulo)
    print("=" * 74)


def ip_local():
    """IP de la red local (la que debe poner Flow en el movil)."""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "(no detectada)"
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass


def main():
    separador("COMPROBACION DE INSTALACION - PC DEL CINE")

    # ---- 1. Version del robot -------------------------------------------
    print()
    print("1) ROBOT DEL ERP")
    robot = SRC / "robot_erp.py"
    if not robot.is_file():
        decir(FALTA, "No existe src/robot_erp.py - faltan archivos de la rama.")
    else:
        txt = robot.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'VERSION_ROBOT\s*=\s*"([^"]+)"', txt)
        if m and m.group(1) == "v6c":
            decir(OK, "robot_erp.py = v6c (la buena: v6b + lista de errores).")
        elif "SIGUE con la siguiente" in txt:
            decir(AVISO, "robot_erp.py = v6b. Funciona, pero falta la lista de "
                         "productos en la ventana flotante. Trae la rama.")
        elif "_mapear_ventana" in txt:
            decir(FALTA, "robot_erp.py = v6 (se PARA al primer fallo). Trae la rama.")
        else:
            decir(FALTA, "robot_erp.py no es una version reconocida. Trae la rama.")
        if "_anadir_todas" in txt or "_añadir_todas" in txt:
            decir(FALTA, "Parece la v7 experimental. NO usar.")

    # ---- 2. Correcciones ERP --------------------------------------------
    print()
    print("2) CORRECCIONES ERP (viajan por Git)")
    corr = SRC / "recursos" / "correcciones_erp.json"
    if not corr.is_file():
        decir(FALTA, "Falta src/recursos/correcciones_erp.json.")
    else:
        try:
            d = json.loads(corr.read_text(encoding="utf-8-sig"))
            n = len(d.get("CORRECCIONES", []))
            fams = d.get("FAMILIAS_PROTEGIDAS", [])
            if n >= 5 and len(fams) >= 5:
                decir(OK, f"{n} correcciones y {len(fams)} familias blindadas.")
            else:
                decir(AVISO, f"Solo {n} correcciones / {len(fams)} familias. "
                             "Puede ser una version antigua; trae la rama.")
        except Exception as e:
            decir(FALTA, f"correcciones_erp.json no se puede leer: {e}")

    # ---- 3. .env (SIN mostrar valores) ----------------------------------
    print()
    print("3) CREDENCIALES (.env) - no se muestran los valores")
    env = BASE / ".env"
    if not env.is_file():
        decir(FALTA, "No existe .env en la raiz. Crealo a mano (ver guia).")
    else:
        try:
            contenido = env.read_text(encoding="utf-8-sig", errors="replace")
        except Exception as e:
            contenido = ""
            decir(FALTA, f"No se pudo leer .env: {e}")
        claves = {}
        for linea in contenido.splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            claves[k.strip()] = v.strip()
        for k in ("ERP_USER", "ERP_PASS"):
            if claves.get(k):
                decir(OK, f"{k} esta relleno (valor oculto).")
            else:
                decir(FALTA, f"{k} falta o esta vacio en .env.")
        if claves.get("OPENAI_API_KEY"):
            decir(OK, "OPENAI_API_KEY esta relleno (asistente disponible).")
        else:
            decir(AVISO, "OPENAI_API_KEY vacio. El asistente de procedimientos "
                         "no funcionara; el robot del ERP SI.")

    gitignore = BASE / ".gitignore"
    if gitignore.is_file() and ".env" in gitignore.read_text(encoding="utf-8", errors="replace"):
        decir(OK, ".gitignore excluye .env (credenciales fuera de Git).")
    else:
        decir(AVISO, ".gitignore no excluye .env. NO hagas 'git add .'.")

    # ---- 4. Dependencias Python -----------------------------------------
    print()
    print("4) LIBRERIAS PYTHON")
    mods = [
        ("PySide6", "PySide6"),
        ("dotenv", "python-dotenv"),
        ("openai", "openai"),
        ("pandas", "pandas"),
        ("openpyxl", "openpyxl"),
        ("pypdf", "pypdf"),
        ("reportlab", "reportlab"),
        ("selenium", "selenium"),
    ]
    # Menu Inicio con IA (26/10/2026). Sin estas, el chat/Hojas de Manual
    # no arranca o pierde funciones, pero el resto de CineStock si funciona.
    mods_ia = [
        ("docx", "python-docx"),
        ("pptx", "python-pptx"),
        ("PIL", "Pillow"),
        ("certifi", "certifi"),
    ]
    for modulo, paquete in mods + mods_ia:
        try:
            encontrado = importlib.util.find_spec(modulo) is not None
        except Exception:
            encontrado = False
        if encontrado:
            decir(OK, f"{paquete} instalado.")
        elif paquete in ("openai", "python-docx", "python-pptx", "Pillow", "certifi"):
            decir(AVISO, f"Falta {paquete} (el menu Inicio con IA puede fallar; "
                         "el robot del ERP SI funciona).")
        else:
            decir(FALTA, f"Falta {paquete}. Instala con: python -m pip install -r requirements.txt")
    try:
        encontrado = importlib.util.find_spec("webdriver_manager") is not None
    except Exception:
        encontrado = False
    if encontrado:
        decir(OK, "webdriver-manager instalado.")
    else:
        decir(AVISO, "Falta webdriver-manager (solo se usa si Chrome no arranca solo).")

    # ---- 5. Chrome -------------------------------------------------------
    print()
    print("5) GOOGLE CHROME")
    rutas_chrome = [
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files"))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
        / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome"
        / "Application" / "chrome.exe",
    ]
    en_path = shutil.which("chrome") or shutil.which("chrome.exe")
    if any(p.is_file() for p in rutas_chrome) or en_path:
        decir(OK, "Chrome encontrado.")
    else:
        decir(FALTA, "No encuentro Chrome. Instalalo desde google.com/chrome.")

    # ---- 6. data/ y catalogo --------------------------------------------
    print()
    print("6) CARPETA data/ Y CATALOGO (NO viaja por Git)")
    data = BASE / "data"
    if not data.is_dir():
        decir(AVISO, "No existe data/. Se creara sola al arrancar; pero el "
                     "catalogo para Flow no estara disponible.")
    else:
        decir(OK, "data/ existe.")
    cat1 = data / "supermaestro.json"
    cat2 = data / "supermaestro_flow.json"
    if cat1.is_file() or cat2.is_file():
        decir(OK, "Catalogo local presente (GET /supermaestro funcionara).")
    else:
        decir(AVISO, "Falta el catalogo local. Las 5 correcciones ERP SI se "
                     "aplicaran, pero Flow no podra descargar el catalogo. "
                     "Copia supermaestro.json desde el otro PC por USB.")

    # ---- 7. Puerto del receptor -----------------------------------------
    print()
    print("7) RECEPTOR (puerto 8765)")
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("0.0.0.0", PORT))
        decir(OK, f"Puerto {PORT} libre. El receptor podra arrancar.")
    except OSError:
        decir(AVISO, f"Puerto {PORT} ocupado. Si CineStock ya esta abierto, "
                     "es normal. Si no, cierra el programa que lo usa.")
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass

    ip = ip_local()
    print(f"         IP de este PC: {ip}")
    print(f"         Ping local:    http://127.0.0.1:{PORT}/ping")
    print(f"         Desde el movil (misma wifi): http://{ip}:{PORT}/ping")
    decir(AVISO, "Si el movil no llega, abre el puerto 8765 en el Firewall "
                 "de Windows (red privada) y comprueba que esta en la misma wifi/VPN.")

    # ---- 8. Archivos de arranque ----------------------------------------
    print()
    print("8) ARCHIVOS DE ARRANQUE")
    for nombre in ("src/main.py", "src/receptor.py", "src/catalogo_erp.py",
                   "herramientas/arrancar_cinestock.bat",
                   "herramientas/_buscar_python.bat"):
        if (BASE / nombre).is_file():
            decir(OK, nombre)
        else:
            decir(FALTA, f"Falta {nombre}. Trae la rama.")

    # ---- 9. Menu Inicio con IA (26/10/2026) ------------------------------
    print()
    print("9) MENU INICIO CON IA (chat + Hojas de Manual)")
    ia = [
        "src/modulos/asistente_inicio.py",
        "src/modulos/depurador_biblioteca.py",
        "src/modulos/conocimiento.py",
        "src/ui/inicio.py",
        "src/ui/conocimiento.py",
    ]
    if all((BASE / f).is_file() for f in ia):
        decir(OK, "Los 5 archivos del asistente de IA estan presentes.")
    else:
        for f in ia:
            if not (BASE / f).is_file():
                decir(FALTA, f"Falta {f} (menu Inicio con IA).")
    datos = SRC / "datos" / "conocimiento.json"
    if datos.is_file():
        decir(OK, "src/datos/conocimiento.json (procedimientos base).")
    else:
        decir(AVISO, "Falta src/datos/conocimiento.json; el chat arrancara "
                     "sin procedimientos hasta que importes la biblioteca.")

    # ---- Resumen ---------------------------------------------------------
    separador("RESULTADO")
    if fallos:
        print(f"  NO ESTA LISTO: hay {len(fallos)} cosa(s) que arreglar:")
        for f in fallos:
            print(f"    - {f}")
        print()
        print("  Mira docs/INSTRUCCIONES_PC_CINE.md y vuelve a ejecutar este")
        print("  comprobador cuando lo hayas arreglado.")
        print("=" * 74)
        return 1
    print("  TODO OK. Puedes arrancar CineStock con:")
    print()
    print("      herramientas\\arrancar_cinestock.bat")
    print()
    print("  o, en terminal:  python src\\main.py")
    if avisos:
        print()
        print(f"  Hay {len(avisos)} aviso(s) menor(es) (no impiden arrancar):")
        for a in avisos:
            print(f"    - {a}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
