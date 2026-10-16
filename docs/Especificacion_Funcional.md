1. Objetivo del proyecto

2. Proveedores
   - Conway
   - Coca-Cola
   - Folder
   - Adis

3. Flujo Conway

4. Flujo Coca-Cola

5. Expedientes

6. Recepciones

7. Facturas

8. Motor de recomendaciones

9. Automatización ERP

10. Mejoras futuras

---

# Decisiones de diseño

## Automatización del inventario

Después de analizar el proceso de trabajo del cine, se decide NO automatizar la toma de inventario.

Motivos:

- Es un proceso crítico.
- Un error en el inventario afecta a pedidos, stock e histórico.
- El riesgo es mayor que el beneficio.
- Conway Assistant podrá ayudar a analizar los inventarios, pero nunca modificará automáticamente las existencias del ERP.

Esta decisión podrá revisarse en el futuro, pero no forma parte de la primera versión del proyecto.

---

## Filosofía del proyecto

Conway Assistant no pretende sustituir el trabajo de las personas.

Su objetivo es:

- Reducir tareas repetitivas.
- Disminuir errores administrativos.
- Ahorrar tiempo.
- Ayudar en la toma de decisiones.
- Mantener siempre el control final en manos del usuario.

---

## Arquitectura inicial

El proyecto estará dividido en módulos independientes.

Versión inicial prevista:

- Gestión de pedidos Conway.
- Gestión de pedidos Coca-Cola.
- Recepciones ERP.
- Gestión de facturas.
- Expedientes de compra.
- Histórico y estadísticas.
- Motor de recomendaciones.

Los nuevos módulos podrán añadirse sin modificar la estructura principal del programa.