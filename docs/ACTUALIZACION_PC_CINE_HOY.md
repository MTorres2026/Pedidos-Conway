# Actualización de los DOS DÍAS (26 + 27 oct) en el PC del cine — guía exprés

> **En el cine ya hay CineStock instalado con WinPython.** No hay que
> instalar nada, ni clonar el repositorio, ni tocar `.env`, ni `data/`.
> Solo hay que **copiar encima lo creado y revisado estos dos días**:
> el **menú Inicio con IA** (26/10) y el **robot v6c con las correcciones
> ERP** (27/10).

**Rama:** `arena/67d0fffb-pedidos-conway` · **Robot:** v6c ·
**Días incluidos:** 26 y 27 de octubre de 2026

---

## ✅ Lo más fácil (recomendado): el ZIP

He preparado **`ACTUALIZACION_CINE.zip`** con **solo** los archivos de hoy.
Está en la raíz del proyecto de tu PC y **no se sube a GitHub** (`*.zip`
está excluido). Se puede regenerar cuando quieras con:

```
python herramientas\generar_actualizacion_cine.py
```

### En el PC del cine

1. Copia `ACTUALIZACION_CINE.zip` a un **USB**.
2. Descomprímelo **dentro de la carpeta de CineStock** (al lado de `src\`).
   Te quedará una carpeta `ACTUALIZACION_CINE\`.
3. Doble clic en **`ACTUALIZACION_CINE\APLICAR.bat`**.
   - Hace una copia de seguridad automática en
     `RESPALDO_antes_actualizar_<fecha>\`.
   - Copia los archivos nuevos manteniendo sus rutas.
   - **No toca** `.env`, `data\`, WinPython ni `dist\`.
4. Doble clic en **`COMPROBAR.bat`** → debe decir **`TODO OK`**.
5. Comprueba la versión del robot:

```
python src\comprobar_version_robot.py
```

Debe decir **`v6c`**.

> Si al ejecutar los `.bat` dice que no encuentra Python, abre el
> **«WinPython Command Prompt»** (o «WinPython PowerShell Prompt») que ya
> tienes en el cine y ejecuta desde ahí:
> ```
> python src\comprobar_instalacion.py
> python src\comprobar_version_robot.py
> ```

---

## 🔀 Alternativa: si en el cine SÍ hay Git

En PowerShell, dentro de la carpeta del proyecto:

```powershell
git fetch origin
git checkout origin/arena/67d0fffb-pedidos-conway -- .gitignore requirements.txt src/robot_erp.py src/receptor.py src/ui/catalogo.py src/catalogo_erp.py src/ui/inicio.py src/ui/conocimiento.py src/modulos/asistente_inicio.py src/modulos/depurador_biblioteca.py src/modulos/conocimiento.py src/recursos/correcciones_erp.json "src/recursos/robot_erp_v6b_original.py.txt" src/regenerar_pedido.py src/verificar_pedido.py src/comprobar_instalacion.py "src/comprobar version robot.py" src/comprobar_version_robot.py "src/aplicar parche v5.py" "src/aplicar parche v6b.py" "src/devolver v6.py" "src/devolver v6b.py" docs/INSTRUCCIONES_CINESTOCK.md docs/INSTRUCCIONES_PC_CINE.md docs/ACTUALIZACION_PC_CINE_HOY.md herramientas/copia_seguridad_local.bat herramientas/arrancar_cinestock.bat herramientas/comprobar_instalacion.bat "herramientas/_buscar_python.bat" herramientas/APLICAR_ACTUALIZACION_CINE.bat herramientas/generar_actualizacion_cine.py
git reset
python src\comprobar_version_robot.py
```

> El menú Inicio con IA necesita 4 librerías que puede que no estén en el
> WinPython del cine. Si `COMPROBAR.bat` las marca como `[AVISO]`, ejecuta
> **una sola vez** desde el *WinPython Command Prompt*:
> ```
> python -m pip install -r requirements.txt
> ```
> No borra nada, solo añade/actualiza librerías.

---

## 📋 Qué se actualiza exactamente

| Archivo | Cambio |
|---|---|
| `src/ui/inicio.py` | **26/10 — Menú Inicio con IA**: chat asistente sobre procedimientos y manuales, con fuentes originales |
| `src/modulos/asistente_inicio.py` | **26/10 — NUEVO**: motor de estudio profundo, Hojas de Manual en Word/Excel, lectura de PPTX/PDF/imágenes, caché y OCR local |
| `src/modulos/depurador_biblioteca.py` | **26/10 — NUEVO**: análisis y limpieza de la Biblioteca |
| `src/ui/conocimiento.py` | **26/10**: integra el depurador y la apertura de originales |
| `src/modulos/conocimiento.py` | **26/10**: lee `src/datos/conocimiento.json` + `data/conocimiento.json` (prioridad local) |
| `src/robot_erp.py` | **27/10 — v6c**: ventana flotante y mensaje final con los productos con error **y su código ERP**; ya no borra líneas buenas |
| `src/recursos/correcciones_erp.json` | 7 correcciones ERP verificadas + familias blindadas (`Kojak` con una c incluido) |
| `src/catalogo_erp.py` | Solo aplica correcciones verificadas; búsqueda por nombre estricta |
| `src/receptor.py` | Aplica las correcciones a los pedidos que llegan de Flow |
| `src/ui/catalogo.py` | Mensaje de «Guardar» corregido |
| `src/regenerar_pedido.py` | Reaplica correcciones a pedidos ya guardados |
| `src/verificar_pedido.py` | Comprueba las 5 correcciones antes de lanzar |
| `src/comprobar_instalacion.py` | Nuevo: revisa las 8 cosas del PC |
| `src/comprobar version robot.py` | Reconoce v6c |
| `src/devolver v6b.py` + copia | Botón de deshacer |
| `herramientas/arrancar_cinestock.bat` | Ahora **busca el Python de WinPython** automáticamente |
| `herramientas/comprobar_instalacion.bat` | Igual, para el comprobador |
| `herramientas/_buscar_python.bat` | Buscador compartido de `python.exe` |
| `requirements.txt`, `docs/*`, parcheadores | Referencia y utilidades |

**NO se copia:** `.env` (credenciales), `data/` (pedidos y catálogo),
`dist/`, `build/`, WinPython.

---

## ⚠️ Tres advertencias

1. **NO abras `dist\CineStock\CineStock.exe`.** Es una compilación antigua
   sin la v6c ni las correcciones. Si en el cine se arranca con ese `.exe`,
   hay que cambiar el acceso directo para que apunte a:
   ```
   herramientas\arrancar_cinestock.bat
   ```
   o regenerar el ejecutable (tarea aparte).

2. **No hagas `git add .` ni el script viejo de backup** en el PC del cine:
   podría subir el `.env` o los 100 MB de `dist/`.

3. El `.env` del cine **se queda como está**. Si aún no tiene
   `ERP_USER` / `ERP_PASS`, créalo a mano (nunca por Git).

---

## Después de actualizar

`comprobar_instalacion.py` debe dar **TODO OK**. Si dice `NO ESTA LISTO`,
el propio mensaje dice qué falta. Pega su salida en el chat y lo revisamos.

Cuando esté listo, seguimos con el **pedido real de 146 líneas**.
