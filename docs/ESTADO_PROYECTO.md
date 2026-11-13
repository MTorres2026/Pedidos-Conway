# Estado del proyecto — CineStock / Pedidos-Conway

## 1. Punto actual

El proyecto está en fase de desarrollo.

La aplicación se está construyendo con Python y PySide6.

La ventana principal de CineStock ya está funcionando.

---

## 2. Funcionalidad ya implementada

### Pantalla principal

Se ha añadido un cuadro de texto para que el usuario pueda realizar preguntas a CineStock.

La finalidad es que el usuario pueda consultar procedimientos de trabajo utilizando lenguaje natural.

### Buscador de procedimientos

Existe un módulo:

`src/modulos/procedimientos.py`

Actualmente contiene procedimientos estructurados y una función de búsqueda.

El sistema puede recibir una pregunta y localizar un procedimiento relacionado.

### Prueba realizada

Se realizó una prueba preguntando cómo cambiar una película de una sala a otra en Cinema Manager.

La aplicación respondió correctamente utilizando el procedimiento registrado.

Esto confirma que el flujo básico:

Pregunta del usuario → búsqueda → procedimiento → respuesta

funciona.

---

## 3. Procedimiento actualmente registrado

### Cambiar una película de una sala a otra

Sistema:

Cinema Manager

Categoría:

Programación

Estado:

CONFIRMADO

Fuente:

Explicación proporcionada por el usuario.

El procedimiento contiene actualmente:

- objetivo;
- preguntas clave;
- pasos;
- comprobaciones;
- advertencias;
- respuesta;
- fuente;
- nivel de confirmación.

---

## 4. Arquitectura actual

El proyecto utiliza módulos independientes.

El archivo principal de arranque es:

`main.py`

El archivo principal de procedimientos es:

`src/modulos/procedimientos.py`

La interfaz principal utiliza:

`ui/ventana_principal.py`

No modificar la arquitectura existente sin comprobar primero los archivos actuales.

---

## 5. Base de conocimiento futura

Se ha decidido ampliar CineStock para que pueda utilizar documentación empresarial seleccionada.

Posibles fuentes:

- procedimientos internos;
- manuales;
- correos electrónicos seleccionados;
- documentación de proveedores;
- documentación de ERP;
- normativa laboral;
- documentación sobre prevención y accidentes;
- otras fuentes relevantes.

Los documentos no deben incorporarse automáticamente sin control del usuario.

La idea futura es que el usuario pueda decidir qué documentación considera suficientemente importante para formar parte del conocimiento de CineStock.

---

## 6. Correos electrónicos

Se ha comprobado que los archivos `.eml` descargados desde Outlook pueden utilizarse como fuentes de información.

Algunos correos contienen procedimientos internos útiles.

Ejemplos de información encontrada:

- procedimientos de matinales;
- instrucciones relacionadas con Programación;
- procedimientos específicos para determinados títulos;
- información de proveedores;
- instrucciones relacionadas con pedidos, ERP y facturación.

Los correos originales de Outlook nunca deben ser eliminados ni modificados por CineStock.

La futura integración con Outlook deberá ser únicamente de lectura y requerirá autorización explícita del usuario.

---

## 7. Principio de control del conocimiento

La IA puede ayudar a detectar información relevante, pero el usuario mantiene el control final sobre qué información entra en la base de conocimiento oficial.

La aplicación debe distinguir, cuando sea posible, entre:

- procedimiento confirmado;
- información pendiente de revisión;
- normativa;
- documentación interna;
- documentación de proveedor;
- fuente original.

Las respuestas importantes deberían poder relacionarse con su fuente.

---

## 8. Decisiones importantes

### Inventario

NO se automatizará la modificación de existencias del ERP.

CineStock podrá ayudar a analizar inventarios, pero no modificará automáticamente las existencias.

Motivo:

El inventario es un proceso crítico y un error puede afectar a pedidos, stock e histórico.

El control final permanece en manos del usuario.

### Automatización

CineStock no pretende sustituir el trabajo de las personas.

Objetivos:

- reducir tareas repetitivas;
- disminuir errores administrativos;
- ahorrar tiempo;
- ayudar en la toma de decisiones;
- mantener el control final en manos del usuario.

---

## 9. Especificación funcional

Existe otro documento:

`docs/ESPECIFICACION FUNCIONAL`

Este documento define el alcance y las decisiones funcionales originales del proyecto.

No sustituirlo.

Utilizarlo como referencia junto con este archivo.

---

## 10. Proveedores previstos

Según la especificación funcional original:

- Conway
- Coca-Cola
- Folder
- Adis

---

## 11. Módulos previstos

Según la especificación funcional:

- gestión de pedidos Conway;
- gestión de pedidos Coca-Cola;
- recepciones ERP;
- gestión de facturas;
- expedientes de compra;
- histórico y estadísticas;
- motor de recomendaciones;
- automatización ERP.

Estos módulos se desarrollarán progresivamente.

---

## 12. Catálogos disponibles

Se han aportado catálogos de Granell:

- caramelos Granell;
- chocolates Granell;
- goma a granel.

Todavía no se debe asumir cómo deben integrarse con cada flujo de proveedor hasta analizar sus datos y definir el modelo correspondiente.

---

## 13. Terminología

El proyecto ha utilizado anteriormente el término "Expedientes" en la especificación funcional.

Posteriormente se ha planteado utilizar "Pedidos" como terminología habitual cuando corresponda.

No modificar la especificación funcional automáticamente.

Revisar esta terminología cuando se haga una revisión formal de la documentación.

---

## 14. Último punto alcanzado

Último bloque funcional probado:

**Cuadro de preguntas IA en la pantalla principal.**

Prueba realizada:

Pregunta sobre cómo cambiar una película de sala en Cinema Manager.

Resultado:

**Funcionando correctamente.**

Último archivo funcional modificado:

`src/modulos/procedimientos.py`

---

## 15. Siguiente paso

Continuar el desarrollo desde el sistema de preguntas IA que ya funciona.

No empezar todavía nuevos módulos de proveedores sin comprobar primero la arquitectura y el código existente.

El siguiente objetivo es ampliar progresivamente el sistema de conocimiento y procedimientos manteniendo las funcionalidades que ya funcionan.

---

## 16. Regla de continuidad del proyecto

Antes de realizar cambios importantes:

1. comprobar el código existente;
2. conservar las funcionalidades que ya funcionan;
3. evitar duplicar módulos;
4. evitar modificar archivos sin conocer sus dependencias;
5. actualizar este documento cuando se complete un bloque importante.

Este archivo es el punto de continuidad del proyecto si se cambia de conversación.
## 17. Último avance realizado

Se ha mejorado la presentación de las respuestas del asistente.

Anteriormente, la interfaz mostraba el diccionario completo del procedimiento.

Ahora:

Pregunta del usuario
→ búsqueda del procedimiento
→ extracción del campo "respuesta"
→ presentación únicamente de la respuesta útil.

También se ha añadido un mensaje controlado cuando no existe un procedimiento confirmado.

Estado:

FUNCIONANDO Y PROBADO.

---

## 18. Pedidos manuales desde la oficina

Se ha añadido en **Pedidos Conway** la opción **PEDIDO MANUAL** para crear un pedido desde el ordenador sin depender de la tablet o del teléfono Android.

El flujo permite cargar el PDF **Stockable** actual, buscar productos en `data/supermaestro.json` (o, como alternativa, `data/supermaestro_flow.json`), añadirlos y ajustar cantidades. Al confirmar, CineStock localiza cada producto en el Stockable por su `ID VISTA`, escribe la cantidad pedida en la columna **Stocktake Count** (sin alterar existencias ni el resto del informe) y asocia el PDF generado al `orderId` dentro de `data/pdf_pedidos/`. Si posteriormente se corrige una cantidad en pantalla, el Stockable asociado del pedido manual se regenera automáticamente.

Al confirmar, el pedido se guarda con identificador interno `MANUAL-...`, queda en el historial y se muestra como **PENDIENTE** para revisión. No se envía al ERP hasta que la persona pulse **ENVIAR A ERP**; el robot conserva la revisión y el guardado final manual en PeopleSoft. Para evitar sustituir trabajo sin revisar, no se permite crear otro mientras haya un pedido pendiente.

Se ha incorporado `src/recursos/Hoja_de_Pedido_Graneles_Oficial.pdf` y el flujo de pedido manual identifica sus códigos ERP (100 productos únicos), rellena la columna **CANTIDAD** por código ERP y asocia el informe como `{fecha}_pedido_{orderId}_granel.pdf` cuando el pedido incluye productos de esa plantilla. La ventana principal conserva el menú lateral gris oscuro y el área de trabajo blanca. Los artículos repetidos en varias categorías se desambiguan con la descripción del catálogo; si no se puede identificar la fila con seguridad, se cancela la generación con un error claro. Los pedidos sin productos Granel no reciben un PDF Granel vacío. Las líneas Granel se excluyen del Stockable aunque no tengan `ID VISTA`, y ambos informes se regeneran cuando se corrigen cantidades.

La lógica de catálogo, validación, guardado y generación/actualización de ambos informes está cubierta por `tests/test_pedido_manual.py` y `tests/test_informes_pedido.py`. El recurso se incluye en `src/recursos`, carpeta ya recogida por `CineStock.spec`. Para empaquetar o ejecutar la generación PDF se requieren `pypdf` y `reportlab` (`requirements-pdf.txt`). Queda probar las ventanas PySide6 en el entorno Windows habitual de CineStock.
