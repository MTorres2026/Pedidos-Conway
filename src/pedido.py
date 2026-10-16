class Pedido:

    def __init__(self, nombre=""):
        self.nombre = nombre
        self.productos = []
        self.estado = "Nuevo"

    def agregar_producto(self, articulo, descripcion, cantidad):
        """Agrega un nuevo producto al pedido con su c�digo, descripci�n y cantidad"""
        self.productos.append({
            "articulo": str(articulo),
            "descripcion": str(descripcion),
            "cantidad": cantidad
        })

    def eliminar_producto(self, articulo):
        """Elimina un producto del pedido por su c�digo"""
        self.productos = [
            producto
            for producto in self.productos
            if producto["articulo"] != str(articulo)
        ]

    def obtener_producto(self, articulo):
        """Busca y devuelve un producto por su c�digo"""
        for producto in self.productos:
            if producto["articulo"] == str(articulo):
                return producto

        return None

    def total_productos(self):
        """Devuelve el n�mero total de productos en el pedido"""
        return len(self.productos)

        return len(self.productos)

    def vaciar(self):
        """Elimina todos los productos del pedido"""
        self.productos.clear()