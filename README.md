# CineStock — Pedidos Conway

Automatización de pedidos Conway en **PeopleSoft (ERP de ORSA)**.

> 📖 **Guías paso a paso:** [`docs/INSTRUCCIONES_CINESTOCK.md`](docs/INSTRUCCIONES_CINESTOCK.md)
> · [`docs/INSTRUCCIONES_PC_CINE.md`](docs/INSTRUCCIONES_PC_CINE.md)
> · [`docs/ACTUALIZACION_PC_CINE_HOY.md`](docs/ACTUALIZACION_PC_CINE_HOY.md)

---

## ⚠️ CÓMO FUNCIONA REALMENTE (importante)

**NO hay una API ni un "envío de JSON al ERP".** El robot
`src/robot_erp.py` abre **Google Chrome con Selenium** y **escribe las
líneas en la pantalla de PeopleSoft**, como lo haría una persona:

1. Inicia sesión en `erpg.orsa.mx` con `ERP_USER` / `ERP_PASS` (del `.env`).
2. Navega a *Compras → Solicitudes → Introducir/Actualizar Solicitudes*.
3. Rellena la cabecera y va escribiendo cada artículo, cantidad y UM.
4. Genera un informe y **se detiene**.

🔴 **REGLA DE ORO: el robot NUNCA pulsa Guardar.** Tú revisas el pedido
en Chrome y guardas a mano. Por eso no existe un "error 500 de la API":
cualquier fallo es un mensaje en pantalla del ERP o una desconexión de
Chrome, y ambos están controlados.

---

## Flujo completo de un pedido

```
Móvil (app Flow)
   │  POST /pedido  (HTTP, puerto 8765)
   ▼
src/receptor.py                 ← recibe el pedido, guarda JSON en data/
   │  aplica SOLO las correcciones verificadas
   ▼
src/recursos/correcciones_erp.json   ← 7 correcciones ERP (SÍ está en Git)
   │
   ▼
data/pedidos_para_procesar/pedido_*.json
   │
   ▼
CineStock → botón «📤 Enviar a ERP»  (o terminal)
   │
   ▼
src/robot_erp.py (v6c)  →  Chrome  →  PeopleSoft
   │
   ▼
informe src/errores_pedido_<fecha>.txt + ventana flotante
   │
   ▼
TÚ revisas y pulsas GUARDAR en el ERP
```

`data/` y `.env` **NO están en Git** (`.gitignore`). Las correcciones sí,
para que lleguen a todos los PCs con un `git pull`.

---

## Arrancar CineStock

**PC con WinPython (el del cine):**

```
herramientas\arrancar_cinestock.bat
```

Ese `.bat` busca automáticamente el `python.exe` de WinPython.
Alternativa: `python src\main.py` (arranca la ventana y el receptor).

**Comprobar que todo está listo:**

```
herramientas\comprobar_instalacion.bat
```
o `python src\comprobar_instalacion.py` → debe acabar en **TODO OK**.

**Instalar dependencias** (solo primera vez):

```
python -m pip install -r requirements.txt
```

---

## Comandos útiles

| Para qué | Comando |
|---|---|
| Saber la versión del robot | `python src\comprobar_version_robot.py` → **v6c** |
| Verificar un pedido antes de lanzarlo | `python src\verificar_pedido.py` |
| Verificar el pedido de 146 líneas | `python src\verificar_pedido.py "<ruta>" --lineas 146` |
| Reaplicar correcciones a un pedido ya guardado | `python src\regenerar_pedido.py --restaurar` |
| Lanzar el robot a mano | `python src\robot_erp.py "<ruta.json>" --auto` |
| Volver a la v6b exacta | `python "src\devolver v6b.py"` |

---

## Versiones del robot

| Versión | Estado |
|---|---|
| v5 / v6 | Antiguas. v6 **se paraba** en el primer fallo. |
| **v6c** | ✅ **La buena.** v6b + lista de productos con error con su código ERP en la ventana flotante + no borra líneas buenas. |
| v7 | ⛔ Aparcada. No aplicar. |

---

## Correcciones ERP verificadas (27/10/2026)

Bolsa Asa Papel `ICG`, Cuchara Madera `1003020096/GDE`, Tapa Vaso Mediana
`1003020030/CJA`, Piruleta Corazon `1001040022/HYR`, Cartón Palomitas XXL
`GEI`, Divisor Palomitas `GDE`, y Tapa Infantil marcada como
`NO_EXISTE_EN_ERP` (se deja, decisión «LA DEJAMOS»).

Las **familias con variantes** (Kit Kat, Skittle, Haribo, Estuchados,
Helados, Kojak/Kojack) están blindadas: sus UM vienen bien de Flow y
ningún proceso las modifica.

---

## Seguridad

- `.env` contiene `OPENAI_API_KEY`, `ERP_USER`, `ERP_PASS`.
  **NUNCA se sube a GitHub.** Se crea a mano en cada PC.
- `data/` (pedidos, catálogo, backups) tampoco se sube.
- No usar el script de backup que hace `git add .` + `git push origin main`:
  podría subir credenciales y los 100 MB de `dist/`. Ver
  [`docs/INSTRUCCIONES_CINESTOCK.md`](docs/INSTRUCCIONES_CINESTOCK.md),
  apartado 8.

---

## Estructura

```
src/
  main.py                    arranca ventana + receptor
  receptor.py                HTTP :8765, recibe pedidos de Flow
  robot_erp.py               robot Selenium (v6c)
  catalogo_erp.py            aplica SOLO correcciones verificadas
  recursos/correcciones_erp.json
  ui/                        pantallas (Inicio con IA, Conocimiento, Pedidos...)
  modulos/                   asistente_inicio.py, biblioteca, depurador...
docs/                        guías
herramientas/                .bat de arranque, comprobación y actualización
tests/
```
