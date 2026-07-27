# DOCUMENTO MAESTRO - CINESTOCK

Filosofía CineStock

CineStock no existe para informatizar el trabajo.

Existe para eliminar tareas repetitivas.

La mejor pantalla es la que el trabajador no necesita abrir.

La mejor pregunta es la que la aplicación no necesita hacer.

La mejor IA es la que trabaja mientras el operario sigue trabajando.

> Proyecto de digitalización integral del almacén y automatización de pedidos para cines.

---

# OBJETIVO DEL PROYECTO

Crear una aplicación Android (PDA) que permita gestionar el almacén del cine de una forma mucho más rápida, cómoda y segura que el sistema actual.

La aplicación NO sustituye a Vista, PeopleSoft ni SharePoint.

Su misión es ayudar a los trabajadores.

El objetivo final es ahorrar tiempo, reducir errores y automatizar todo el proceso de pedidos Conway y Coca-Cola.

---

# FILOSOFÍA DEL PROYECTO

La aplicación debe trabajar exactamente igual que trabajan los empleados.

Nunca obligará a cambiar la forma de trabajar.

Debe parecer una herramienta hecha por alguien que conoce perfectamente el cine.

---

# SISTEMAS EXISTENTES

- Vista
- PeopleSoft ERP
- SharePoint
- Conway
- Coca-Cola
- Freshservice

La aplicación actuará como asistente entre todos ellos.

Nunca modificará directamente ninguno.

---

# FLUJO REAL DEL CINE

1. La encargada observa el almacén.

2. Decide cuándo hace falta pedir.

3. Solicita mercancía.

4. Se genera un ID de Solicitud.

5. PeopleSoft crea posteriormente un ID de Pedido.

6. Cuando llega la mercancía se genera un ID de Recepción.

7. Se sube la factura.

8. El pedido queda completamente cerrado.

---

# IDENTIFICADORES IMPORTANTES

Cada pedido tiene tres identificadores distintos.

## ID de Solicitud

Lo genera Vista/ERP al guardar la solicitud.

Es el identificador principal del pedido.

## ID de Pedido

Lo genera automáticamente PeopleSoft cuando recibe la solicitud.

## ID de Recepción

Se genera cuando se recepciona físicamente la mercancía.

---

# INFORMACIÓN QUE GUARDA LA OFICINA

Actualmente los trabajadores mantienen una libreta donde anotan:

- ID Solicitud
- ID Pedido
- ID Recepción
- Factura subida (✔)
- Importe factura

La aplicación sustituirá esta libreta.

---

# PEDIDOS CONWAY

No existe un día fijo obligatorio.

Normalmente se realizan una vez por semana.

Si un martes no se hace el pedido, normalmente se espera unos días antes de realizar el siguiente.

Nunca se pide únicamente un producto.

Siempre se aprovecha para realizar un pedido completo.

---

# PEDIDOS COCA-COLA

Son mucho más flexibles.

Pueden realizarse incluso durante el fin de semana si es necesario.

---

# RECEPCIÓN DE MERCANCÍA

La mercancía Conway y Coca-Cola debe llegar antes de la apertura del cine.

El transportista entrega:

- Albarán
- Mercancía

El trabajador:

- Comprueba los bultos
- Firma
- Introduce recepción en ERP

---

# PRODUCTOS CRÍTICOS

Los productos imprescindibles para el funcionamiento del cine son:

🌽 Maíz dulce

🌽 Maíz salado

🥤 Bag in Box

💧 Aguas

🍿 Cartones

🥤 Vasos

🧂 Sal

🍬 Azúcar

Si alguno de ellos empieza a escasear, normalmente se genera un pedido.

---

# PRODUCTOS IMPORTANTES

Los graneles también son muy importantes.

Una tienda desabastecida genera reclamaciones.

No tienen la prioridad del maíz o los vasos, pero pueden provocar un pedido.

---

# INVENTARIO

El inventario completo será un módulo independiente.

No forma parte inicialmente del flujo de pedidos.

Se desarrollará cuando la aplicación principal esté completamente estable.

---

# DOCUMENTO MAESTRO

Este documento se irá ampliando durante todo el desarrollo del proyecto.

Nunca se eliminará información importante.

Siempre reflejará el funcionamiento real del cine.
