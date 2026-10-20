# Abrir resultados de Inicio

## Qué se corrigió

En este checkout, `Inicio._abrir_enlace` no ramificaba por tipo: pasaba
`ruta_copia` directamente a Qt, sin comprobar su existencia ni el resultado
de la apertura. La etiqueta «Archivo def.» no figura en este código.
Ahora Inicio y Conocimiento usan la misma apertura y muestran los errores.
Qt abre el programa asociado del sistema (equivalente a doble clic).

La resolución no depende de `tipo`: busca la copia en la biblioteca actual,
la ruta guardada y la ubicación de importación. Esta última se guarda desde
este cambio; no se puede reconstruir para importaciones antiguas. La base
existente se actualiza añadiendo una columna, sin borrar documentos.

Para un correo, si uno de sus adjuntos está también importado como documento
independiente y existe en disco, se prefiere ese documento. Se compara el
contenido exacto, no el nombre. Si hay varios documentos independientes se
abre el correo para no elegir un adjunto arbitrariamente. No se rastrean
carpetas ajenas a la biblioteca ni se extraen adjuntos de forma permanente.

## Pruebas automáticas

`PYTHONPATH=src python -m unittest discover -s tests -v`

Las pruebas de interfaz requieren PySide6 y sus bibliotecas de sistema.
Las pruebas de resolución no necesitan Qt ni Windows.

## Comprobación final en Windows

1. En Conocimiento, añadir el Excel «Los Prados (por semanas2027)».
2. Buscarlo desde Inicio y pulsar «Abrir original»: debe abrir Excel o el
   programa asociado. Repetir con un nombre con espacios y acentos.
3. Añadir un correo con un adjunto, sin importar el adjunto por separado:
   debe abrir el correo.
4. Importar también ese mismo adjunto (sin modificarlo): el resultado del
   correo debe abrir el documento independiente si es el único disponible.
5. Cerrar CineStock, mover tanto la copia de `originales` como el fichero
   de importación. Reabrir y pulsar el resultado: debe aparecer un aviso
   con las rutas buscadas, no quedarse sin hacer nada.
6. Probar una extensión sin programa asociado: debe aparecer un aviso si
   Windows/Qt rechaza la apertura. Si Windows acepta la solicitud y luego
   muestra su propio selector de aplicaciones, ese selector es el resultado
   esperado; Qt no puede detectar un fallo posterior del programa externo.

La apertura real de Excel/correo queda pendiente de comprobar en Windows;
el entorno de desarrollo es Linux.
