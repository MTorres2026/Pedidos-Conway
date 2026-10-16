class Expediente:

    def __init__(self):
        self.numero = ""
        self.proveedor = ""
        self.fecha = ""
        self.estado = "Nuevo"

        self.pedido = ""
        self.facturas = []
        self.abonos = []
        self.incidencias = []
        self.comunicaciones = []

        self.observaciones = ""