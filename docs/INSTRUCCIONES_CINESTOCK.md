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
| `src/regenerar_pedido.py` | **NUEVO.** Reaplica el catálogo a pedidos ya guardados. |
| `.gitignore` | Excluye informes, capturas y backups del robot. |

**v7 queda aparcado.** No se sube ni se aplica.

---

## 4. En TU PC (después de que yo haga push)

En la terminal, en la raíz del proyecto
(`C:\Users\Marku\Desktop\PROYECTOS-IA\CineStock-Pedidos\claude-prueba\Pedidos-Conway`):

```
git fetch origin
git pull origin arena/67d0fffb-pedidos-conway
python "src\comprobar version robot.py"
```

Debe decir: **`v6b (la buena: anota el error y SIGUE)`**.

El `.env` NO se toca: ya lo tienes con `ERP_USER` / `ERP_PASS`. Nunca se sube.

### Regenerar el pedido de 100 líneas

```
python src\regenerar_pedido.py
```

Esto corrige `data/pedido_actual.json` y el archivo más reciente de
`data/pedidos_para_procesar/`, guardando una copia `.bak_<fecha>` de cada uno.
Te imprimirá línea por línea lo que cambia. **Pégame ese texto antes de lanzar
el robot.**

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

## 8. Pendiente (mejora futura, tarea 6)

Validar en CineStock, antes de pulsar «Enviar a ERP», que cada `idErp` y cada
`umErp` existen en el catálogo y que el artículo no esté marcado como
`NO_EXISTE_EN_ERP`. Así una UM inválida nunca llegaría al ERP.
