import os

# Ruta exacta de tu proyecto Android
ruta_proyecto = r"C:\Users\Marku\Desktop\PROYECTOS-IA\Flow\Flow"

# Se guardará en tu Escritorio con este nombre
archivo_salida = r"C:\Users\Marku\Desktop\Codigo_Android_Flow.txt"

extensiones = ('.py', '.kt', '.xml', '.json', '.txt', '.md', '.bat', '.gradle')
carpetas_ignoradas = {'.git', 'build', '.idea', '__pycache__', 'venv', '.gradle', 'res'}

archivos_leidos = 0
print(f"Buscando codigo en: {ruta_proyecto}...")

with open(archivo_salida, 'w', encoding='utf-8') as salida:
    for raiz, dirs, archivos in os.walk(ruta_proyecto):
        # Ignorar carpetas que no son código
        dirs[:] = [d for d in dirs if d not in carpetas_ignoradas]
        for archivo in archivos:
            if archivo.endswith(extensiones):
                ruta_completa = os.path.join(raiz, archivo)
                try:
                    with open(ruta_completa, 'r', encoding='utf-8') as f:
                        contenido = f.read()
                    
                    salida.write(f"\n\n{'='*50}\n")
                    salida.write(f"ARCHIVO: {ruta_completa}\n")
                    salida.write(f"{'='*50}\n\n")
                    salida.write(contenido)
                    archivos_leidos += 1
                except Exception as e:
                    print(f"No se pudo leer {ruta_completa}: {e}")

print(f"\n¡Listo! Se han copiado {archivos_leidos} archivos.")
print(f"El texto se ha guardado en tu Escritorio: {archivo_salida}")