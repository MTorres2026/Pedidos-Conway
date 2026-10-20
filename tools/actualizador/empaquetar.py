"""Genera un ZIP pequeno con codigo verificado, sin incluir datos del usuario."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

BASE = Path(__file__).resolve().parents[2]
ARCHIVOS = ('src/modulos/biblioteca.py', 'src/ui/inicio.py',
            'src/ui/conocimiento.py', 'src/ui/apertura_documentos.py')


def crear(destino):
    hashes = {n: hashlib.sha256((BASE / n).read_bytes()).hexdigest() for n in ARCHIVOS}
    with ZipFile(destino, 'w', ZIP_DEFLATED) as z:
        for n in ARCHIVOS:
            z.write(BASE / n, 'Actualizar_CineStock/archivos/' + n)
        for n in ('actualizar.py', 'ACTUALIZAR_CINESTOCK.bat'):
            datos = (BASE / 'tools/actualizador' / n).read_bytes()
            if n.endswith('.bat'):
                datos = datos.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            z.writestr('Actualizar_CineStock/' + n, datos)
        z.writestr('Actualizar_CineStock/hashes.json', json.dumps(hashes))
        z.writestr('Actualizar_CineStock/LEEME.txt',
                   '1. Cierra CineStock.\r\n2. Extrae TODO el ZIP.\r\n'
                   '3. Abre la carpeta y ejecuta ACTUALIZAR_CINESTOCK.bat.\r\n'
                   '4. Escribe SI cuando lo pida.\r\n'
                   '5. Al terminar, abre CineStock con tu BAT de siempre.\r\n'
                   'No lo ejecutes dentro del ZIP ni como administrador.\r\n'
                   'Si Windows lo bloquea, no desactives protecciones: envia una foto del aviso.\r\n')
    with ZipFile(destino) as z:
        assert z.testzip() is None
        for n, h in hashes.items():
            assert hashlib.sha256(z.read('Actualizar_CineStock/archivos/' + n)).hexdigest() == h


if __name__ == '__main__':
    import sys
    crear(sys.argv[1])
