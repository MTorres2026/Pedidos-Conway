# Guía para dejar CineStock funcionando en el PC DEL CINE

**Fecha:** 27/10/2026 · **Rama:** `arena/67d0fffb-pedidos-conway` ·
**Robot:** v6c · **Receptor:** puerto `8765`

> Regla de oro: **el robot NUNCA pulsa Guardar**. Tú revisas el pedido en
> Chrome y guardas a mano. Y el archivo **`.env` con las contraseñas NUNCA
> se sube a GitHub**: se crea a mano en cada PC.

---

## 🎒 Qué llevar al cine (desde tu PC)

1. **Un USB** con estos dos archivos (cópialos desde
   `C:\Users\Marku\Desktop\PROYECTOS-IA\CineStock-Pedidos\claude-prueba\Pedidos-Conway\data\`):
   - `supermaestro.json`
   - `supermaestro_flow.json` (si existe; si no, basta el primero)

   > Estos archivos **NO viajan por Git** (`data/` está excluido). Sin ellos
   > las correcciones ERP seguirán funcionando, pero Flow no podrá descargar
   > el catálogo (`GET /supermaestro` daría 404).

2. **Las credenciales** apuntadas en un papel o en tu móvil (no las pegues en
   el chat ni las guardes en el USB):
   - `ERP_USER` → usuario de Microsoft del ERP
   - `ERP_PASS` → contraseña del ERP
   - `OPENAI_API_KEY` → solo si se usa el asistente de procedimientos

---

## 0. ¿El PC del cine ya tiene el proyecto?

Abre **PowerShell** (botón derecho en Inicio → *Terminal* / *PowerShell*) y
escribe:

```powershell
dir
```

- Si ves una carpeta con `src`, `docs`, `herramientas` → **ya está clonado**:
  entra en ella con `cd <nombre-de-la-carpeta>` y sigue al **paso 1**.
- Si el proyecto NO existe, clónalo una sola vez:

```powershell
git clone https://github.com/MTorres2026/Pedidos-Conway.git
cd Pedidos-Conway
git checkout arena/67d0fffb-pedidos-conway
```

> Si `git clone` pide usuario/contraseña y no funciona, dímelo: lo miramos.
> GitHub ya está configurado en tu PC de casa; en el del cine puede hacer
> falta iniciar sesión una vez.

**Todos los comandos de esta guía se ejecutan DENTRO de la carpeta del
proyecto** (donde están `src`, `docs` y `herramientas`).

---

## 1. Traer los arreglos desde GitHub

Pega este bloque entero en PowerShell:

```powershell
git fetch origin
git checkout origin/arena/67d0fffb-pedidos-conway -- .gitignore requirements.txt src/robot_erp.py src/receptor.py src/ui/catalogo.py src/catalogo_erp.py src/recursos/correcciones_erp.json "src/recursos/robot_erp_v6b_original.py.txt" src/regenerar_pedido.py src/verificar_pedido.py src/comprobar_instalacion.py "src/comprobar version robot.py" src/comprobar_version_robot.py "src/aplicar parche v5.py" "src/aplicar parche v6b.py" "src/devolver v6.py" "src/devolver v6b.py" docs/INSTRUCCIONES_CINESTOCK.md docs/INSTRUCCIONES_PC_CINE.md herramientas/copia_seguridad_local.bat herramientas/arrancar_cinestock.bat
git reset
python src\comprobar_version_robot.py
```

Debe decir:

```
robot_erp.py -> versión: v6c (la buena: v6b + lista de PRODUCTOS CON ERROR ...)
```

Si dice `v6b`, `v6` o `v7`, **párate y dímelo**.

> ¿Por qué `git checkout ... -- archivos` y `git reset`, en vez de `git pull`?
> Porque el `main` local de cada PC tiene cambios de otras sesiones. Así
> traemos solo los archivos de esta rama sin mezclar historias ni crear
> conflictos.

---

## 2. Instalar / actualizar las librerías de Python

Una sola vez (o si el comprobador dice que falta algo):

```powershell
python -m pip install -r requirements.txt
```

Si tarda, es normal. Si dice *"Requirement already satisfied"*, perfecto.

---

## 3. Crear el `.env` A MANO (NUNCA por Git)

1. Abre el **Bloc de notas**.
2. Pega esto, sustituyendo los valores:

```
OPENAI_API_KEY=tu_clave_de_openai
ERP_USER=usuario_del_erp
ERP_PASS=contraseña_del_erp
```

3. **Guardar como** → en *Nombre* escribe **`.env` con comillas**: `".env"`.
   Las comillas evitan que el Bloc de notas le añada `.txt` al final.
4. Guarda el archivo en la **raíz del proyecto** (al lado de `src`, `docs`,
   `requirements.txt`).
5. Comprueba el nombre:

```powershell
dir .env
```

Debe aparecer `.env` (no `.env.txt`).

> 🔒 Este archivo contiene contraseñas. **No lo subas, no lo copies al USB,
> no lo mandes por WhatsApp.** En GitHub ya está excluido por `.gitignore`.

---

## 4. Copiar el catálogo desde el USB

1. Crea la carpeta `data` en la raíz del proyecto si no existe:

```powershell
mkdir data -Force
```

2. Copia desde el USB los dos archivos `supermaestro.json` y
   `supermaestro_flow.json` dentro de `data\`.

3. Comprueba:

```powershell
dir data\supermaestro*.json
```

> Si no tienes el USB, **no pasa nada crítico**: las 5 correcciones ERP
> (`src/recursos/correcciones_erp.json`) sí viajaron por Git y se aplicarán
> igual. Solo faltará que Flow pueda descargar el catálogo completo.

---

## 5. Comprobar que TODO está listo ⭐

Este es el paso importante. Un solo comando revisa las 8 cosas:

```powershell
python src\comprobar_instalacion.py
```

Tiene que acabar con:

```
==========================================================================
  TODO OK. Puedes arrancar CineStock con:

      herramientas\arrancar_cinestock.bat
==========================================================================
```

Si dice **`NO ESTA LISTO`**, el propio comando te dice qué falta:
- *Falta PySide6 / selenium / pandas…* → repite el **paso 2**.
- *No existe .env / ERP_USER vacío* → repite el **paso 3**.
- *No encuentra Chrome* → instala Chrome.
- *Puerto 8765 ocupado* → si CineStock ya está abierto es normal; si no,
  cierra el programa que lo usa.
- *Falta src/robot_erp.py* → repite el **paso 1**.

📋 **Pégame la salida de este comando** y la reviso contigo antes de arrancar.

---

## 6. Arrancar CineStock

```powershell
herramientas\arrancar_cinestock.bat
```

o, si prefieres la terminal:

```powershell
python src\main.py
```

Deja la ventana negra **abierta**. Deberías ver algo como:

```
  SERVIDOR RECEPTOR CINESTOCK INICIADO
  Escuchando en: http://0.0.0.0:8765/pedido
  Ping:          http://0.0.0.0:8765/ping
```

### 🔴 MUY IMPORTANTE: NO uses `dist\CineStock\CineStock.exe`

Ese ejecutable es una **compilación antigua**. No lleva el robot v6c ni las
correcciones ERP. Si lo abres, trabajarás con la versión vieja y volverán
los errores. Arranca siempre con el `.bat` de arriba.

---

## 7. Comprobar que el móvil llega al PC

1. En el **mismo PC**, abre un navegador y escribe:

```
http://127.0.0.1:8765/ping
```

Debe responder algo como `{"ok": true, ...}`.

2. El comprobador del paso 5 te habrá dicho la **IP del PC**. En el **móvil**,
   conectado a la **misma red/VPN**, abre:

```
http://IP_DEL_PC:8765/ping
```

3. Configura la app **Flow** con esa IP y el puerto `8765`.

> Si el móvil no llega: abre el **puerto 8765 en el Firewall de Windows**
> (regla de entrada, red privada) y comprueba que el móvil está en la misma
> wifi/VPN que el PC. Es la causa más habitual.

---

## 8. Prueba final (pedido pequeño)

Antes del pedido real de 146 líneas, haz una prueba corta desde el móvil:

1. Manda un pedido pequeño.
2. En la consola del PC debe aparecer:
   `[CATÁLOGO] N línea(s) corregidas con ID ERP / UM ERP.`
3. Abre CineStock → **Registro de Pedidos** → **📤 Enviar a ERP**.
4. El robot v6c abrirá Chrome, cargará las líneas y **NO pulsará Guardar**.
5. Revisa la **ventana flotante**: ahora te lista los productos con error
   **y su código ERP**. Rectifícalos a mano y **pulsa Guardar tú**.

---

## 9. Si algo falla mañana

Pega en el chat:

- La salida de `python src\comprobar_instalacion.py`.
- La salida de `python src\comprobar_version_robot.py`.
- El texto de la consola donde arrancaste CineStock.

Con eso lo vemos. **No pulses Guardar en el ERP** hasta haber revisado el
informe del robot.

---

## Resumen en una frase

> Traer la rama → instalar `requirements.txt` → crear `.env` a mano →
> copiar `data/` del USB → `comprobar_instalacion.py` → arrancar con el
> `.bat` (nunca el `.exe`) → probar `/ping` desde el móvil.
