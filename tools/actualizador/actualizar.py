"""Actualizador puntual de Inicio: solo copia los cuatro archivos incluidos."""
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

DESTINO = Path(r'C:\Users\gerencia\OneDrive\Escritorio\06 - DOCS PDF\Proyecto Conway\Pedidos-Conway-main')
ARCHIVOS = ('src/modulos/biblioteca.py', 'src/ui/inicio.py',
            'src/ui/conocimiento.py', 'src/ui/apertura_documentos.py')


def huella(ruta):
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def main():
    paquete = Path(__file__).resolve().parent
    modificados = []
    existentes = set()
    copia = None
    try:
        if not (DESTINO / 'src/main.py').is_file():
            raise RuntimeError(f'No encuentro tu proyecto en:\n{DESTINO}\nNo se ha cambiado nada.')
        hashes = json.loads((paquete / 'hashes.json').read_text(encoding='utf-8'))
        for nombre in ARCHIVOS:
            origen = paquete / 'archivos' / nombre
            if huella(origen) != hashes[nombre]:
                raise RuntimeError(f'Archivo incompleto o alterado: {nombre}. Descarga y extrae todo el ZIP de nuevo.')
            if not (DESTINO / nombre).parent.is_dir():
                raise RuntimeError(f'Falta la carpeta de destino para {nombre}.')
        print('Este actualizador solo cambia cuatro archivos de codigo.')
        print('No cambia tus pedidos, documentos ni el BAT del escritorio.')
        print(f'Proyecto: {DESTINO}\n')
        if input('Cierra CineStock. Escribe SI y pulsa Intro cuando este cerrado: ').strip().upper() != 'SI':
            print('Cancelado. No se ha cambiado nada.')
            return 0
        copia = DESTINO / ('copia_antes_actualizar_inicio_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        copia.mkdir()
        for nombre in ARCHIVOS:
            actual = DESTINO / nombre
            if actual.exists():
                respaldo = copia / nombre
                respaldo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(actual, respaldo)
                if huella(actual) != huella(respaldo):
                    raise RuntimeError(f'No se pudo verificar la copia de seguridad de {nombre}.')
                existentes.add(nombre)
        for nombre in ARCHIVOS:
            modificados.append(nombre)
            shutil.copy2(paquete / 'archivos' / nombre, DESTINO / nombre)
            if huella(DESTINO / nombre) != hashes[nombre]:
                raise RuntimeError(f'No se pudo verificar la actualizacion de {nombre}.')
        print('\nACTUALIZACION COMPLETADA.')
        print(f'Copia de seguridad: {copia}')
        print('Abre CineStock con tu BAT habitual y prueba el Excel desde Inicio.')
        return 0
    except Exception as error:
        fallos = []
        for nombre in reversed(modificados):
            try:
                if nombre in existentes:
                    shutil.copy2(copia / nombre, DESTINO / nombre)
                else:
                    (DESTINO / nombre).unlink(missing_ok=True)
            except Exception:
                fallos.append(nombre)
        print(f'\nNO SE HA COMPLETADO LA ACTUALIZACION:\n{error}')
        if modificados and not fallos:
            print('Se han deshecho los cambios realizados.')
        if fallos:
            print(f'No se pudieron restaurar: {fallos}. Copia de seguridad: {copia}')
        print('Haz una foto de esta ventana y enviala en el chat.')
        return 1


if __name__ == '__main__':
    sys.exit(main())
