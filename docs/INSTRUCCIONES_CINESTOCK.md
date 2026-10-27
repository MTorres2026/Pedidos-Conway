# CineStock — instrucciones de la sesión del 27/10/2026

> Documento de apoyo. La regla sigue siendo: **el robot NUNCA pulsa Guardar**.
> Tú revisas el pedido en Chrome y guardas a mano.

> ✅ **ESTADO DE LA SUBIDA (27/10/2026): COMPLETO.** En la rama
> `arena/67d0fffb-pedidos-conway` están el robot **v6b** (el archivo exacto del
> PC de la encargada, verificado con `comprobar version robot.py`), el catálogo
> corregido, el receptor, los parcheadores y el comprobador.
> `data/` y `.env` siguen FUERA de Git. Ya se puede hacer `git pull` en el PC
> del cine.

---

## 1. Qué estaba pasando (causa real de los 4 rechazos)

No era culpa del robot v6b. Era un problema de datos:

* La pantalla **CATÁLOGO SUPERMAESTRO** (`src/ui/catalogo.py`) guardaba en
  `data/supermaestro.json`.
* El receptor (`src/receptor.py`) leía `data/supermaestro_flow.json`, que
  **no existía**. Y si faltaba, se tragaba el error en silencio
  (`except: return data`).
* Resultado: los pedidos llegaban al robot con los `idErp` / `umErp` viejos
  que traía Flow, y el ERP rechazaba 4 artículos.

Además, en el informe `src/errores_pedido_20261027_143749.txt` las filas 33 a
100 NO son errores de datos: en la fila 33 aparece
`invalid session id: session deleted as the browser has closed the connection`.
**Chrome se desconectó ahí** (portátil suspendido). De ahí la cascada de
"No se localizó su fila vacía en el grid".

---

## 2. Correcciones del catálogo (verificadas en el ERP)

Archivo versionado: **`src/recursos/correcciones_erp.json`**.
Tiene **prioridad** sobre `data/supermaestro.json` y `data/supermaestro_flow.json`.

| Artículo | ID VISTA | Antes | Después | Estado |
|---|---|---|---|---|
| (CI) Bolsa Asa Papel | 9580 | `1003020091` / `IAH` | `1003020091` / **`ICG`** | ⚠️ UM pendiente de confirmar con la lupa de `*UM` |
| (CI) Cuchara Madera | 8969 | `1003020087` / `IDV` | **`1003020096` / `GDE`** | ✅ Verificado |
| (CI) Tapa Vaso Mediana | 122 | `1003020096` / `GDE` | **`1003020030` / `CJA`** | ✅ Verificado |
| (CI) Tapa Vaso Pequeña/Infantil | 123 | `1003020031` / `IDZ` | *(sin sustituir)* | ❌ **Ya no existe en el ERP** |
| (CI) Divisor Palomitas Universal | 9816 | `1003020099` / `GDE` | `1003020099` / `GDE` | ✅ Verificado (GDE sí vale aquí) |

**Ojo con dos confusiones que ya hemos resuelto:**

* `1003020096` es **Cuchara Madera**, NO una tapa.
* El código original de Tapa Vaso Mediana (`1003020030`) **era correcto**; lo
  que estaba mal era la UM (`GDE` en vez de `CJA`).
* Las UM son **por artículo**: que `GDE` valga para el Divisor Palomitas
  (`1003020099`) no significa que valga para una tapa.

**Tapa Infantil:** decisión de la encargada = **"LA DEJAMOS"**. La línea se
queda en el pedido de 100 líneas; el robot v6b la anotará como incidencia real
y seguirá hasta la 100. No se inventa un código sustituto.

### ⚠️ Regla crítica: SOLO se corrigen esos artículos

El 27/10/2026 por la tarde detectamos que aplicar el catálogo **entero**
(304 productos) a los pedidos cambiaba también las UM de familias con
variantes que comparten `ID VISTA`:

```
línea 71 | Kit Kat Normal   | HSI -> GBC   (MAL)
línea 81 | Skittle Frutas   | HWD -> HWC   (MAL)
```

Esas UM venían bien de Flow. Por eso `src/catalogo_erp.py` ahora aplica
**únicamente** las correcciones de `src/recursos/correcciones_erp.json`.
Todo lo demás se deja exactamente como lo mandó Flow. El catálogo completo
fusionado se sigue sirviendo en `GET /supermaestro` para que Flow pueda
descargarlo, pero **no se usa para reescribir pedidos**.

Al ejecutar `regenerar_pedido.py` deben aparecer **solo 3 líneas corregidas**
por archivo (Bolsa Asa Papel, Cuchara Madera, Tapa Vaso Mediana). Si aparece
cualquier otra línea, **no lances el robot** y avisa.

### 🛡️ Familias blindadas

A petición de la encargada, estas familias y **todos sus "hijos"** están
protegidos por nombre: **Kit Kat, Skittle, Haribo 100, Estuchados 3.90,
Helados y Kojack**.

La lista está en `src/recursos/correcciones_erp.json` →
`FAMILIAS_PROTEGIDAS`. Ni `regenerar_pedido.py`, ni `src/receptor.py`, ni el
catálogo `GET /supermaestro` pueden cambiar una línea cuyo nombre contenga
una de esas palabras. Es un seguro doble: aunque alguien vuelva a cargar el
catálogo entero, esas UM no se tocan.

Además, al aplicar una corrección al catálogo solo se modifica la fila cuyo
**nombre** coincide. Antes se modificaban *todas* las filas con el mismo
`ID VISTA`, que es justo lo que estropeaba a los "hijos" de una familia.

`regenerar_pedido.py` imprime una línea de control, por ejemplo:

```
    familias BLINDADAS intactas: Haribo (3), Helado (2), Kit Kat (4), Kojack (1), Skittle (2)
```

### ❌ NO ejecutar «aplicar parche v6.py»

Ese parcheador es la versión **anterior** a la v6b. Trae las mismas funciones
de anclado/bloques, pero con una diferencia crítica: **se PARA en la primera
línea que falle**. Con la Tapa Infantil en la línea 30, no llegaría a la 100.
La v6b, en cambio, **anota la incidencia y SIGUE**.

`src/robot_erp.py` ya contiene todo el parche v6 **y** el arreglo "b". Si se
ejecutara ese parcheador sobre él, diría «El parche v6 ya está aplicado» (no
haría nada); si se ejecutara sobre un robot más antiguo, lo **desactualizaría**.
Por tanto: no se sube a Git y no se ejecuta.

---

## 3. Archivos nuevos / cambiados en esta sesión

| Archivo | Qué es |
|---|---|
| `src/robot_erp.py` | Robot **v6b** (ancla filas, bloques de 20, anota el error y SIGUE). Nunca guarda. |
| `src/aplicar parche v5.py` | Parcheador v5 (paginación, una a una). |
| `src/aplicar parche v6b.py` | Parcheador v6b (bloques de 20 + anotar y seguir). |
| `src/devolver v6.py` | Utilidad de emergencia para volver a v6. |
| `src/comprobar version robot.py` | Comprobador: debe decir **`v6b`**. |
| `src/comprobar_version_robot.py` | Alias sin espacios del anterior. |
| `src/catalogo_erp.py` | **NUEVO.** Unifica catálogos y aplica las correcciones ERP. |
| `src/recursos/correcciones_erp.json` | **NUEVO.** Las 5 correcciones verificadas (SÍ en Git). |
| `src/receptor.py` | **CAMBIADO.** Ya no falla en silencio; aplica `catalogo_erp`. |
| `src/ui/catalogo.py` | **CAMBIADO.** «Guardar» escribe los DOS archivos `data/*.json`. |
| `src/regenerar_pedido.py` | **NUEVO.** Reaplica SOLO las correcciones ERP a pedidos ya guardados (`--restaurar` deshace una ejecución anterior). |
| `.gitignore` | Excluye informes, capturas y backups del robot. |

**v7 queda aparcado.** No se sube ni se aplica.

---

## 4. En TU PC (después de que yo haga push)

En la terminal, en la raíz del proyecto
(`C:\Users\Marku\Desktop\PROYECTOS-IA\CineStock-Pedidos\claude-prueba\Pedidos-Conway`):

```
git fetch origin
git pull origin arena/67d0fffb-pedidos-conway
python "src\comprobar_version_robot.py"
```

Debe decir: **`v6b (la buena: anota el error y SIGUE)`**.

> Si `git pull` da conflicto porque tu `main` local tiene cambios de otra
> sesión, NO hagas merge. Usa la forma selectiva (trae solo los archivos de
> esta rama sin tocar tu historial):
>
> ```
> git fetch origin
> git checkout origin/arena/67d0fffb-pedidos-conway -- .gitignore src/robot_erp.py src/receptor.py src/ui/catalogo.py src/catalogo_erp.py src/recursos/correcciones_erp.json src/regenerar_pedido.py "src/comprobar version robot.py" src/comprobar_version_robot.py "src/aplicar parche v5.py" "src/aplicar parche v6b.py" "src/devolver v6.py" docs/INSTRUCCIONES_CINESTOCK.md
> git reset
> ```

El `.env` NO se toca: ya lo tienes con `ERP_USER` / `ERP_PASS`. Nunca se sube.

### Regenerar el pedido de 100 líneas

**Si ya ejecutaste `regenerar_pedido.py` ANTES del arreglo de la tarde**
(es decir, si su salida mencionaba Kit Kat Normal o Skittle Frutas), ejecuta
primero esto para deshacer esos dos cambios:

```
python src\regenerar_pedido.py --restaurar
```

`--restaurar` recupera la copia `.bak_*` más reciente de cada pedido y vuelve
a aplicar **solo las correcciones buenas**. Si es la primera vez, basta con:

```
python src\regenerar_pedido.py
```

Esto corrige `data/pedido_actual.json` y el archivo más reciente de
`data/pedidos_para_procesar/`, guardando una copia `.bak_<fecha>` de cada uno.
Te imprimirá línea por línea lo que cambia. **Pégame ese texto antes de lanzar
el robot.** Deben salir exactamente **3 líneas por archivo**:

```
(CI) Bolsa Asa Papel      1003020091 / IAH -> 1003020091 / ICG
(CI) Cuchara Madera       1003020087 / IDV -> 1003020096 / GDE
(CI) Tapa Vaso Mediana    1003020030 / GDE -> 1003020030 / CJA
```

> No intentes reenviar el mismo pedido desde el móvil: el receptor lo ignora
> por `orderId` duplicado. Para eso existe `regenerar_pedido.py`.

---

## 5. Antes de lanzar el robot (REGLA DE ORO)

Mientras el robot trabaja (10–15 min):

* ✅ Portátil **ABIERTO** (no bajar la tapa).
* ✅ **Con cable** de corriente.
* ✅ NO tocar Chrome, NO cerrar la consola negra.
* ✅ Mejor dejar el móvil y el ERP tranquilos hasta que acabe.

Lo de bajar la tapa fue exactamente lo que mató Chrome en la fila 33 del
último informe.

---

## 5 bis. ✅ Luz verde para el pedido de 100 líneas (27/10/2026, 17:47)

Salida verificada de `python src\regenerar_pedido.py --restaurar`:

* `v6b (la buena: anota el error y SIGUE)` en el comprobador.
* Restaurados los DOS archivos desde `*.bak_20261027_171149`.
* **Solo 3 líneas corregidas por archivo** (26 Bolsa Asa Papel, 28 Cuchara
  Madera, 29 Tapa Vaso Mediana). `TOTAL: 6`.
* Familias intactas: `Kit Kat (2): .../GBC, .../HSI`, `Skittle (2): .../HWC,
  .../HWD`, `Estuchado (3)`, `Haribo (8)`. Es decir, las dos UM que se habían
  estropeado por la mañana (`HSI` y `HWD`) están de vuelta.

### Antes de lanzar: verificar el JSON (OBLIGATORIO)

```
python src\verificar_pedido.py
```

`src/verificar_pedido.py` es **solo de lectura**: confirma que hay 100 líneas,
que las 3 correcciones están en el archivo, que la línea 30 sigue intacta y
que las familias conservan sus UM. Debe acabar con **`RESULTADO: TODO OK`**.
Si dice `PROBLEMA`, **no lances el robot**: ejecuta
`python src\regenerar_pedido.py --restaurar` y vuelve a verificar.

Motivo: el 27/10/2026 a las 14:29 se lanzó una carrera con el JSON **todavía
sin corregir** (informe `src/errores_pedido_20261027_143749.txt`). En su tabla
se veía `línea 26 / IAH`, `línea 28 / 1003020087 / IDV`, `línea 29 / GDE`.
Además, pulsar «Enviar a ERP» desde la pestaña **Pedidos** de CineStock
reescribe el JSON desde la memoria y puede borrar las correcciones. La
verificación lo detecta antes de perder 10–15 minutos.

### Comando exacto para lanzar el pedido de 100 líneas

Desde la **raíz del proyecto**, con el `.env` ya creado:

```
python src\robot_erp.py "data\pedidos_para_procesar\pedido_FLOW-1793107796283.json" --auto
```

Antes, comprobar que ese Python tiene selenium (debe decir `OK`):

```
python -c "import selenium, pandas; print('OK')"
```

Si no dice `OK`, lanzarlo desde CineStock → **Registro de Pedidos** → elegir
el pedido → **📤 Enviar a ERP** (esa pantalla usa el Python del programa).
**No usar la pestaña «Pedidos» para reenviar**: reconstruye el archivo desde
lo que tenga en memoria y podría perder las correcciones.

Al arrancar debe decir **`100 líneas a cargar`**. Incidencias esperadas en el
informe: línea 26 (UM `ICG` pendiente de confirmar), línea 30 (Tapa Infantil,
ya no existe). El robot **NO pulsa Guardar**; se revisa el informe antes.

---

## 6. Después de la carrera

1. El robot genera `src/errores_pedido_<fecha>.txt` y
   `src/pantalla_pedido_<fecha>.png`.
2. **Revisamos juntas el informe ANTES de que pulses Guardar.**
3. Si la fila 26 (Bolsa Asa Papel) vuelve a dar "UM no válida", es que `ICG`
   no vale para `1003020091`: abre la lupa del campo `*UM` en el ERP y dime
   qué UMs acepta.
4. La fila 30 (Tapa Infantil) aparecerá como incidencia real. Es esperado:
   el artículo ya no existe. Decidimos dejarla.
5. Cuando el informe esté claro, **pulsas Guardar tú** en el ERP.

---

## 7. En el PC DEL CINE

1. `git pull origin arena/67d0fffb-pedidos-conway`
2. `python "src\comprobar version robot.py"` → debe decir **`v6b`**.
3. Crear **a mano** su `.env` en la raíz del proyecto (NUNCA por Git):

```
ERP_USER=el_usuario_de_aqui
ERP_PASS=la_contraseña_de_aqui
```

4. Copiar el catálogo local si procede: `data/supermaestro.json` está en
   `.gitignore`, así que no viaja por Git. Las **correcciones ERP sí viajan**
   porque están en `src/recursos/correcciones_erp.json`.

---

## 8. Copias de seguridad (27/10/2026)

**NO usar el típico `.bat` de «backup automático» que hace
`git add .` + `git pull origin main` + `git push origin main`.** Motivos:

1. Empuja a **`main`**, no a `arena/67d0fffb-pedidos-conway`. Mezclaría los
   cambios locales de otra sesión con la rama de trabajo.
2. `git pull origin main --no-rebase` puede crear un **merge con conflictos**
   y el `.bat` sigue hasta el `push` igualmente (no comprueba errores).
3. `.env` y `data/` están en `.gitignore`, pero un `git add .` **sí subiría**
   `dist/` (más de 100 MB entre `CineStock.exe` y sus DLL) y `build/`.
4. Si `.env` llegó a estar rastreado en algún momento, `.gitignore` **ya no lo
   protege** (`git add .` lo seguiría incluyendo).
5. `git add .` también podría colar informes, capturas o experimentos v7.

**Lo que ya está a salvo:** todo el código importante (robot v6b,
correcciones, receptor, regenerador, documentación) está subido en la rama
`arena/67d0fffb-pedidos-conway`. No hace falta ningún script para eso.

**Lo que NO está en GitHub, y NO debe estarlo:** `data/` (pedidos, catálogo,
copias `.bak_*`) y `.env` (credenciales). Para eso existe
**`herramientas/copia_seguridad_local.bat`**: crea un ZIP fechado en
`C:\Users\Marku\Desktop\BACKUPS-CineStock` **solo en el PC**, sin tocar Git.
Es seguro ejecutarlo incluso justo antes de una carrera del robot.

> Recordatorio: el `.env` del PC del cine se crea **a mano** y nunca se sube.

---

## 9. Pendiente (mejora futura, tarea 6)

Validar en CineStock, antes de pulsar «Enviar a ERP», que cada `idErp` y cada
`umErp` existen en el catálogo y que el artículo no esté marcado como
`NO_EXISTE_EN_ERP`. Así una UM inválida nunca llegaría al ERP.
