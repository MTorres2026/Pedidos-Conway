# Respuesta a la auditoría de IA sobre `robot_erp.py`

**Fecha:** 27/10/2026 · **Versión analizada:** v6c
(`arena/67d0fffb-pedidos-conway`)

> Conclusión: **NO se reescribe el robot.** La auditoría es genérica y parte
> de una premisa falsa (que existe una API HTTP). Aplicarla «en limpio» sería
> repetir el error de la **v7**, que ya está aparcada. Solo se acepta la
> mejora de documentación y una comprobación nueva en el verificador.

---

## ❌ Error de base: no existe ninguna API

La auditoría dice:

> *«El script carga un archivo JSON y lo envía a la API de ERP (a
> erpg.orsa.mx)»* y *«si la API devuelve un error 500…»*.

**Falso.** En `src/robot_erp.py` **no hay ni una sola llamada**
`requests`, `urllib`, `http.client` ni `.post(`. Comprobado con:

```
grep -nE "requests\.|urllib\.request|http\.client|httpx|\.post\(" src/robot_erp.py
→ sin resultados
```

Lo que hace es abrir **Chrome con Selenium** y teclear en la pantalla de
PeopleSoft (`driver.get(ERP_URL)` en la línea 762, `webdriver.Chrome` en
2523). Por tanto:

- No hay «error 500» que gestionar: los fallos son **mensajes en pantalla**
  del ERP (`Valor no válido`), **alerts/modalos** de PeopleSoft o una
  **desconexión de Chrome** (`invalid session id`).
- Esos tres casos **ya están gestionados**: `_es_no_valido()`,
  `descartar_mensaje()` / `cerrar_ventanas()`, y `chrome_vivo()`.

---

## Punto por punto

### 1️⃣ Dependencia de `robot_id_pedido.py` — **YA está protegida**

La auditoría dice que «si el archivo no está, el script falla
completamente». Falso. El import está **dentro de un `try/except`**
(líneas 2651–2661):

```python
if args.auto and driver is not None and not inf.fatal:
    try:
        from robot_id_pedido import vigilar_id_pedido
        ...
        vigilar_id_pedido(driver, ruta)
    except Exception as e:
        print(f"[aviso] No se pudo vigilar el ID de pedido: {e}")
```

Además `src/robot_id_pedido.py` **existe y está en `main`** (12.952 bytes),
por lo que ya está en el PC del cine. El vigilante es **opcional**: si
falla, el robot ya ha terminado su trabajo y el pedido sigue en Chrome
esperando a que pulses Guardar.

✅ **Sí se ha añadido** una comprobación explícita en
`src/comprobar_instalacion.py`, para que el PC del cine avise si algún día
falta ese archivo. Es la única parte de esta sugerencia que aporta valor.

Crear un «mock» como propone la auditoría sería **peor**: daría a entender
que el ID se ha capturado cuando en realidad no se ha hecho.

---

### 2️⃣ «Falta manejo de errores en el proceso de envío» — **YA existe, y mucho**

Hay **114 bloques `except`** en `src/robot_erp.py`. Ejemplos reales:

| Situación | Dónde se gestiona | Qué hace |
|---|---|---|
| No se puede leer el archivo de datos | `main()`, línea 2557 | `mostrar_ventana("Pedido ERP - Error", …)` con el motivo |
| Falla el login / navegación | `main()`, `inf.fatal` | Anota el paso que falló y lo muestra |
| El ERP rechaza un código o UM | `procesar_fila()` | Anota la línea, la vacía y **sigue** |
| Se cierra Chrome | `cargar_lineas()` | `chrome_vivo()` → aviso y parada controlada |
| No se puede verificar al final | `verificar_final()` | `inf.general(...)`, no revienta |
| Cualquier error inesperado | `main()` | `except Exception` → `inf.fatal` + informe |

Además v6c añadió el **mensaje final y la ventana flotante con los
productos que han dado error y su código ERP**, que es justo «mostrar un
mensaje claro al usuario».

El mensaje de error «Error 500: verifica que erpg.orsa.mx esté activo»
que propone la auditoría **no tiene sentido aquí**: no hay petición HTTP
que devuelva un 500.

---

### 3️⃣ «Validar que el archivo tenga extensión `.json`» — **sería un paso atrás**

El robot **no confía en la extensión**: usa `detectar_tipo(b)` que inspecciona
**los bytes** del archivo, y `EXT_SOPORTADAS` acepta 15 formatos
(`.xlsx`, `.xlsm`, `.xls`, `.ods`, `.csv`, `.tsv`, `.txt`, `.json`,
`.jsonl`, `.ndjson`, `.xml`, `.html`, `.htm`, `.dat`, `.prn`, `.md`).

Obligar a que sea `.json` **rompería** los pedidos en Excel/CSV, que el
proyecto usa habitualmente. Y si el JSON está corrupto, `cargar_datos()`
ya está dentro de un `try/except` que muestra una ventana con el error.

✅ **Sí se ha reforzado** la validación con `src/verificar_pedido.py`
(creado el 27/10): comprueba el número de líneas, las 5 correcciones ERP y
que no haya alias de campos incompatibles **antes** de lanzar el robot.

---

### 4️⃣ «Documentación ausente / añadir README» — **ACEPTADO ✅**

Aquí la auditoría tenía razón a medias: el `README.md` tenía solo 2 líneas,
aunque existían 7 documentos en `docs/`.

Se ha reescrito **`README.md`** con:

- La aclaración de que **no hay API**, que es Selenium escribiendo en Chrome.
- El **flujo completo** del pedido (Flow → receptor → correcciones → robot →
  informe → Guardar manual).
- La **regla de oro**: el robot nunca pulsa Guardar.
- Comandos de arranque, comprobación y verificación.
- Tabla de versiones (v6c = la buena, v7 = aparcada).
- Seguridad: `.env` y `data/` fuera de Git.

---

## 🚫 Por qué NO hacemos un «Robot ERP v2»

1. **La v6c acaba de funcionar en el ERP real**: 100 líneas, 90 sin
   incidencias, Chrome aguantó hasta el final. Es la primera vez que llega
   a la 100.
2. Reescribir «limpio y robusto» es exactamente lo que se intentó con la
   **v7**, que está **aparcada por decisión de la encargada**. Volvería a
   romper el anclado de filas del grid paginado (el bug de la fila 13).
3. El paquete para el **PC del cine ya está generado y subido**
   (`ACTUALIZACION_CINE.zip`, release `actualizacion-cine-20261027`).
   Cambiar el robot ahora obligaría a rehacerlo y a repetir la prueba real.
4. La auditoría no ha ejecutado el robot ni conoce el ERP de ORSA. Es un
   análisis de texto genérico.

**Regla acordada:** mientras la v6c funcione, **no se toca
`src/robot_erp.py`**. Las mejoras se hacen en módulos auxiliares
(`comprobar_instalacion.py`, `verificar_pedido.py`, documentación), que son
seguros y no afectan a la carga en el ERP.

---

## Resumen de lo aplicado en esta revisión

| Sugerencia | Decisión |
|---|---|
| Mock de `robot_id_pedido` | ❌ No. Ya hay `try/except`. Sí se añade comprobación en el verificador. |
| try/except para «error 500 de la API» | ❌ No existe tal API. Ya hay 114 `except`. |
| Validar extensión `.json` | ❌ Rompería Excel/CSV. Ya hay detección por contenido + `verificar_pedido.py`. |
| Añadir README | ✅ Hecho: `README.md` reescrito. |
| «Robot ERP v2» completo | ❌ No. Sería repetir el error de la v7. |
