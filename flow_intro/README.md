# Intro animada para la app **FLOW** (la del almacén, la del gato)

Las letras **F · L · O · W** se escriben solas, trazo a trazo, con una punta de
luz que las dibuja. Entra el gato con su cajita de palomitas, aparece el lema
y se llena una barra de carga. Duración: **unos 5,5 segundos**.

> **Importante:** esto es solo una pantalla de presentación nueva. **No toca el
> robot, ni CineStock, ni los pedidos, ni el ERP.** Es un añadido independiente
> para la otra app (Flow). No hay que cambiar nada de lo que ya funciona.
> No necesita internet ni permisos especiales en el móvil.

---

## 1. Verlo ahora mismo (para ti, sin programar)

Haz **doble clic** en `flow_intro.html`. Se abre en el navegador y se ve la
animación. Abajo a la derecha tienes botones para probar los 3 colores
(`🌙 Cine`, `🍿 Palomitas`, `💙 Neón`) y `↻ Repetir`.

Si quieres verlo en el móvil, copia `flow_intro.html` al móvil y ábrelo.
También puedes ver directamente `flow_intro.gif` o `flow_intro.mp4`, que son
grabaciones de la misma animación.

---

## 2. Qué archivo es cada cosa

| Archivo | Para qué sirve |
|---|---|
| `flow_intro.html` | **El principal.** Animación completa, con colores y opciones. Se abre en un WebView. |
| `flow_intro.svg` | Lo mismo pero en un SVG solo, con animación SMIL. Útil si la app ya usa vectores. |
| `flow_intro.mp4` | Vídeo de la intro (268 KB). Para ponerlo de splash con un `VideoView`. |
| `flow_intro.gif` | GIF animado (1,2 MB). Para verlo o mandarlo por WhatsApp. |
| `flow_intro.png` | Fotograma final, por si hace falta una miniatura. |
| `generar_muestras.py` | Programa que vuelve a crear el GIF, MP4 y PNG. Solo hace falta si cambias el dibujo. |
| `index.html` | Atajo para que la vista previa abra directamente la animación. |

---

## 3. Cómo ponerlo en la app Flow

Elige **una** de las tres opciones. La A es la más bonita; la B es la más fácil.

### Opción A — HTML dentro de un WebView (recomendada)

**Paso 1.** Copia `flow_intro.html` en la carpeta de recursos de la app:

```
app/src/main/assets/flow_intro.html
```

(Si no existe `assets`, se crea: clic derecho en `app/src/main` → New →
Folder → Assets Folder.)

**Paso 2.** En la pantalla de carga (`SplashActivity`), pega esto. Lo pega la
persona que programe la app; tú solo tienes que pasarle este archivo.

**Kotlin:**

```kotlin
class SplashActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val web = WebView(this)
        setContentView(web)
        web.settings.javaScriptEnabled = true          // imprescindible
        web.settings.domStorageEnabled = false
        web.setBackgroundColor(Color.parseColor("#0b1226"))

        // La intro avisa sola al terminar llamando a introTerminada()
        web.addJavascriptInterface(object {
            @JavascriptInterface
            fun introTerminada() {
                runOnUiThread {
                    startActivity(Intent(this@SplashActivity, MainActivity::class.java))
                    finish()
                }
            }
        }, "AndroidFlow")

        // El "?app=1" quita los botones de prueba.
        web.loadUrl("file:///android_asset/flow_intro.html?app=1")
    }
}
```

**Java (si la app está en Java):**

```java
public class SplashActivity extends AppCompatActivity {
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        WebView web = new WebView(this);
        setContentView(web);
        web.getSettings().setJavaScriptEnabled(true);   // imprescindible
        web.setBackgroundColor(Color.parseColor("#0b1226"));

        web.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void introTerminada() {
                runOnUiThread(() -> {
                    startActivity(new Intent(SplashActivity.this, MainActivity.class));
                    finish();
                });
            }
        }, "AndroidFlow");

        web.loadUrl("file:///android_asset/flow_intro.html?app=1");
    }
}
```

> Recuerda: `SplashActivity` tiene que ser la actividad que arranca
> (el `intent-filter` MAIN / LAUNCHER del `AndroidManifest.xml`).
>
> Si `?app=1` no funcionase en ese móvil, usa `flow_intro.html#app` en su lugar.
> Si la app es **iOS**, es lo mismo pero con `WKWebView`: se carga el archivo
> del bundle y se escucha el mensaje `AndroidFlow` / `introTerminada` con un
> `WKScriptMessageHandler`.

**No hace falta** añadir el permiso de internet: la intro es 100 % local.

---

### Opción B — Vídeo MP4 (la más fácil, sin JavaScript)

**Paso 1.** Copia `flow_intro.mp4` en:

```
app/src/main/res/raw/flow_intro.mp4
```

(Si no existe `raw`, se crea: clic derecho en `res` → New → Android Resource
Directory → tipo `raw`.)

**Paso 2.** En `SplashActivity`:

```kotlin
val video = VideoView(this)
setContentView(video)
video.setVideoURI(Uri.parse("android.resource://$packageName/${R.raw.flow_intro}"))
video.setOnCompletionListener {
    startActivity(Intent(this, MainActivity::class.java))
    finish()
}
video.start()
```

El vídeo mide 720×368 y pesa 268 KB. Es ideal para ponerlo también en la
pantalla de arranque del sistema (Android 12+ `SplashScreen` con vídeo no lo
admite directamente; en ese caso usa la Opción A).

---

### Opción C — SVG o GIF, para algo más sencillo

- **`flow_intro.svg`**: se puede cargar igual que el HTML en un WebView:
  `web.loadUrl("file:///android_asset/flow_intro.svg")`. La animación va dentro
  del propio archivo (SMIL), así que **no** hace falta JavaScript. Eso sí: no
  avisa de cuándo termina; hay que pasar a la app con un temporizador
  (`Handler.postDelayed(..., 5500)`).
- **`flow_intro.gif`**: cópialo a `res/drawable` y muéstralo con Coil o Glide
  en un `ImageView`. Solo decora: tampoco avisa de cuándo acaba.

---

## 4. Cambiar los colores, el texto o la velocidad

Abre `flow_intro.html` con el **Bloc de notas** (clic derecho → Abrir con →
Bloc de notas) y busca el apartado `CONFIG` (hacia la mitad del archivo).
Son las primeras líneas de la parte de abajo:

```js
const MODO_APP = ...        // true = sin botones (modo splash)
const VELOCIDAD = 1;        // 1 = normal; 2 = el doble de rápido; 0.5 = más lento
const REPETIR = false;      // true = la intro se repite en bucle
const TEXTO_SUBTITULO = "TU ALMACÉN, EN MOVIMIENTO";
```

Cambia el texto entre las comillas. **Guarda el archivo** y ábrelo otra vez
para verlo. En el mismo bloque está `PALETAS` con los 3 juegos de colores;
hay 8 valores por paleta (`fondo1`, `fondo2`, `tinta`, `brillo`, `gato`,
`gato_oscuro`, `rosa`, `texto`).

Si usas la **Opción A**, la app también puede cambiarlo desde código:

```js
FlowIntro.setPaleta('palomitas');   // o 'cine' / 'neon'
FlowIntro.setTexto('TU ALMACÉN, EN MOVIMIENTO');
FlowIntro.setVelocidad(1.5);
FlowIntro.replay();                 // repetir la animación
FlowIntro.onFin = function(){ /* al terminar */ };
```

En `flow_intro.svg` los colores están todos juntos en el bloque `<style>` del
principio (y sus dos degradados en `<defs>`).

---

## 5. Volver a generar el GIF, el MP4 y el PNG

Solo hace falta si has cambiado el dibujo, los colores o el texto del HTML y
quieres que las muestras en vídeo también lo reflejen. Se hace en el **PC**,
en la carpeta del proyecto, con la ventana de comandos de WinPython abierta:

```
python -m pip install Pillow imageio imageio-ffmpeg
python flow_intro\generar_muestras.py
```

Los tres archivos se escriben otra vez dentro de `flow_intro/`. Tarda unos
segundos. Si no tienes WinPython a mano, con un Python normal vale igual.

---

## 6. Detalles técnicos

- **Autocontenido:** ni CDN, ni fuentes externas, ni imágenes externas. Todo
  está dibujado dentro del archivo (SVG + CSS + JavaScript). Funciona sin red.
- **Tamaño:** HTML 17 KB, SVG 9 KB, MP4 268 KB, GIF 1,2 MB.
- **Móvil:** se adapta al ancho de pantalla con `viewport` y `viewBox`.
- **Accesibilidad:** si el móvil tiene activado «reducir movimiento», la intro
  se muestra quieta y avisa a la app igualmente (no se queda colgada).
- **Compatible** con Chrome, Edge, Firefox, Safari y WebView de Android.
- **El gato es un dibujo propio en SVG**, hecho para esta intro. Si Flow ya
  tiene su propio gato/logo (un PNG o SVG), pásamelo y lo sustituyo por el
  real manteniendo la misma animación.

---

## 7. Resumen para decidir

| Si quieres… | Usa |
|---|---|
| Verlo ya | `flow_intro.html` (doble clic) |
| La intro de verdad en la app | `flow_intro.html` + Opción A |
| Lo más fácil de integrar | `flow_intro.mp4` + Opción B |
| Mandarlo por WhatsApp para enseñarlo | `flow_intro.gif` |
| Solo el dibujo vectorial | `flow_intro.svg` |
