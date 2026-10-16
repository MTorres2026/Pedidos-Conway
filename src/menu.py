def mostrar_menu():
    print()
    print("=" * 50)
    print("        CONWAY ASSISTANT")
    print("    Asistente de Compras del Cine")
    print("=" * 50)
    print()
    print("1. Pedidos Conway")
    print("2. Pedidos Coca-Cola")
    print("3. Recepciones ERP")
    print("4. Facturas")
    print("5. Expedientes")
    print("6. Historial")
    print("7. Configuración")
    print("8. Borrar Historial y Pedido Actual")
    print()
    print("0. Salir")
    print("=" * 50)


def leer_opcion():
    opcion = input("Seleccione una opción: ")
    return opcion