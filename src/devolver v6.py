# -*- coding: utf-8 -*-
"""Devuelve robot_erp.py a la versión v6 (la que hizo el pedido de 25 con
25/25 sin incidencias), usando la copia de seguridad que dejó el parche v7.

No hace falta tocar nada: busca el backup más reciente, comprueba que es el
v6 correcto, guarda el robot actual como robot_erp_con_v7_<fecha>.py y
restaura el v6. Si algo no cuadra, NO cambia nada y explica el motivo.
"""
import glob
import os
import shutil
import sys
from datetime import datetime

CARPETA = os.path.dirname(os.path.abspath(__file__))


def buscar_backup_v6():
    backups = sorted(glob.glob(os.path.join(CARPETA, "robot_erp.py.bak_v7_*")))
    if not backups:
        return None
    return backups[-1]  # el más reciente


def version_de(texto):
    if "_añadir_todas" in texto:
        return "v7"
    if "_mapear_ventana" in texto:
        return "v6"
    return "v5 o anterior"


def main():
    ruta_robot = os.path.join(CARPETA, "robot_erp.py")
    print(f"Carpeta: {CARPETA}")

    if not os.path.isfile(ruta_robot):
        print("\nNO SE HA CAMBIADO NADA.")
        print("No encuentro robot_erp.py en esta carpeta.")
        print("¿Lo habrás renombrado a mano ya? Si existe un archivo llamado")
        print("robot_erp_reserva_v7.py o parecido, NO lo toques y pásame una")
        print("captura de los archivos robot_erp* que hay en src.")
        return

    with open(ruta_robot, "r", encoding="utf-8") as f:
        actual = f.read()
    print(f"El robot actual es: {version_de(actual)}")

    if version_de(actual) == "v6":
        print("\n¡El robot YA está en v6! No hay nada que cambiar.")
        print("(No toques nada más y seguimos con el pedido de 146.)")
        return

    backup = buscar_backup_v6()
    if backup is None:
        print("\nNO SE HA CAMBIADO NADA. Motivo: no encuentro ningún archivo")
        print("robot_erp.py.bak_v7_... en esta carpeta (la copia que dejó el")
        print("parche v7). Pásame una captura de los archivos robot_erp* de src.")
        return
    print(f"Backup del v6 encontrado: {os.path.basename(backup)}")

    with open(backup, "r", encoding="utf-8") as f:
        contenido = f.read()
    if version_de(contenido) != "v6":
        print("\nNO SE HA CAMBIADO NADA. Motivo: el backup no contiene el v6")
        print("como esperaba (raro, pero por seguridad no toco nada).")
        print("Pásame una captura de los archivos robot_erp* de src.")
        return

    compile(contenido, backup, "exec")  # el backup debe compilar bien

    reserva = os.path.join(CARPETA, "robot_erp_con_v7_" +
                           datetime.now().strftime("%Y%m%d_%H%M%S") + ".py")
    shutil.copy2(ruta_robot, reserva)
    shutil.copy2(backup, ruta_robot)

    with open(ruta_robot, "r", encoding="utf-8") as f:
        restaurado = f.read()
    if version_de(restaurado) != "v6":
        print("\nAlgo no fue como esperaba. El robot actual se conserva en:")
        print(f"   {os.path.basename(reserva)}")
        print("Pásame una captura de lo que hay en src y lo miramos.")
        return

    print("\n==============================================")
    print("  LISTO: robot_erp.py ha vuelto a la versión v6")
    print("  (la que hizo el pedido 25/25 sin incidencias)")
    print("==============================================")
    print(f"El robot con v7 se ha guardado como: {os.path.basename(reserva)}")
    print("(puedes borrarlo otro día si quieres)")
    print("\nComprobación final: en TERMINAL debe decir 'v6' arriba.")
    print("Ahora sí: ¡al pedido real de 146 líneas!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nNO SE HA CAMBIADO NADA. Motivo: {e}")
        sys.exit(1)
