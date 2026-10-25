"""
Pantalla "Registro de pedidos": la matrícula de cada pedido.

Para cada pedido enviado desde FLOW muestra y permite anotar:
  - ID SOLICITUD  (lo lee solo el robot cuando guardas en el ERP; también se puede escribir)
  - ID PEDIDO     (llega por correo: se puede importar el .eml o escribirlo)
  - ID RECEPCIÓN  (al recepcionar en el cine) + factura + fecha/hora de recepción

La lógica está en modulos/registro_pedidos.py (sin Qt).
"""

import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from modulos.registro_pedidos import (
    ErrorRegistro,
    Registro,
    fecha_para_mostrar,
)

COLUMNAS = [
    "Pedido",
    "Enviado",
    "ID solicitud",
    "ID pedido",
    "ID recepción",
    "Factura",
    "Estado",
]


class EditorIdsDialog(QDialog):
    """Formulario para anotar o corregir los números de un pedido."""

    def __init__(self, rec, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Anotar datos · {rec['order_id']}")
        self.resize(500, 0)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Deja en blanco lo que aún no tengas. Si borras un número, se quita."
        ))

        form = QFormLayout()
        self.campos = {}

        def mostrar_fecha(iso):
            return fecha_para_mostrar(iso) if iso else ""

        def campo(clave, etiqueta, valor, ayuda=""):
            caja = QLineEdit(valor or "")
            if ayuda:
                caja.setPlaceholderText(ayuda)
            form.addRow(etiqueta, caja)
            self.campos[clave] = caja

        # 1. Primero va el ID de solicitud (el que lee el robot en el ERP)
        campo("id_solicitud", "ID solicitud (ERP, 6 cifras)", rec["id_solicitud"], "ej. 407128")
        
        # 2. Luego el ID de pedido (el que llega por correo)
        campo("id_pedido", "ID pedido (del correo)", rec["id_pedido"])
        
        # 3. Y por último el de recepción
        campo("id_recepcion", "ID recepción", rec["id_recepcion"])
        
        campo("recepcion_fecha", "Fecha de recepción", mostrar_fecha(rec["recepcion_fecha"]), "dd/mm/aaaa")
        campo("recepcion_hora", "Hora de recepción", rec["recepcion_hora"], "hh:mm")
        
        # --- FACTURA ---
        campo("factura_numero", "Nº de factura (Conway)", rec["factura_numero"])
        campo("factura_fecha", "Fecha de la factura", mostrar_fecha(rec["factura_fecha"]), "dd/mm/aaaa")
        
        # --- NUEVO: Subir archivo de factura con Auto-Lectura ---
        self.campos["factura_archivo"] = QLineEdit(rec.get("factura_archivo", ""))
        self.campos["factura_archivo"].setReadOnly(True)
        self.campos["factura_archivo"].setPlaceholderText("Sin archivo adjunto")
        
        boton_subir = QPushButton("📎 Subir factura…")
        boton_subir.clicked.connect(self.subir_factura)
        
        fila_archivo = QHBoxLayout()
        fila_archivo.addWidget(self.campos["factura_archivo"])
        fila_archivo.addWidget(boton_subir)
        form.addRow("Archivo factura", fila_archivo)
        # ---------------------------------------------------

        campo("notas", "Notas", rec["notas"])
        
        layout.addLayout(form)
        botones = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        layout.addWidget(botones)

    def subir_factura(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self,
            "Elige el archivo de la factura",
            "",
            "Documentos (*.pdf *.jpg *.png *.jpeg);;Todos los archivos (*.*)"
        )
        if not ruta:
            return
            
        self.campos["factura_archivo"].setText(ruta)
        
        # ¡MAGIA! Intentar leer el número y la fecha del archivo
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            texto = self._extraer_texto(ruta)
            numero, fecha = self._buscar_datos_factura(texto)
            
            # Si encuentra el número y el campo está vacío, lo rellena
            if numero and not self.campos["factura_numero"].text().strip():
                self.campos["factura_numero"].setText(numero)
                
            # Si encuentra la fecha y el campo está vacío, la rellena
            if fecha and not self.campos["factura_fecha"].text().strip():
                self.campos["factura_fecha"].setText(fecha)
                
        except Exception as e:
            print(f"No se pudo auto-leer la factura: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def _extraer_texto(self, ruta):
        """Lee el texto de un PDF o de una imagen usando OCR."""
        ruta = Path(ruta)
        if ruta.suffix.lower() == ".pdf":
            try:
                import pypdf
                lector = pypdf.PdfReader(str(ruta))
                texto = ""
                for pagina in lector.pages:
                    texto += (pagina.extract_text() or "") + "\n"
                return texto
            except ImportError:
                print("Falta instalar pypdf: pip install pypdf")
                return ""
        else:
            # Para imágenes (.jpg, .png)
            try:
                from rapidocr_onnxruntime import RapidOCR
                ocr = RapidOCR()
                resultado, _ = ocr(str(ruta))
                if resultado:
                    return "\n".join([linea[1] for linea in resultado])
            except Exception as e:
                print(f"Error con RapidOCR: {e}")
            return ""

    def _buscar_datos_factura(self, texto):
        """Busca patrones típicos de número de factura y fecha en el texto."""
        # Patrones para buscar el número
        patrones_num = [
            # Número de documento / Nº documento (el que usa Conway)
            r"(?i)n[uú]mero\s*de\s*documento\s*[:\-]?\s*([a-z0-9\-\/]{3,25})",
            r"(?i)n[uú]mero\s*documento\s*[:\-]?\s*([a-z0-9\-\/]{3,25})",
            r"(?i)n[º°o.]*\s*documento\s*[:\-]?\s*([a-z0-9\-\/]{3,25})",
            r"(?i)documento\s*(?:n[º°o.]*|numero|num\.?)?\s*[:\-]?\s*([a-z0-9\-\/]{3,25})",
            # Número de factura / Factura Nº (por si acaso)
            r"(?i)factura\s*(?:n[º°o.]*|numero|num\.?|#)?\s*[:\-]?\s*([a-z0-9\-\/]{3,25})",
            r"(?i)n[º°o.]*\s*factura\s*[:\-]?\s*([a-z0-9\-\/]{3,25})",
            r"(?i)invoice\s*(?:no\.?|number|#)?\s*[:\-]?\s*([a-z0-9\-\/]{3,25})",
        ]
        
        # Palabras que NUNCA son un número de factura
        palabras_basura = {
            "fecha", "hora", "importe", "total", "base", "iva", "cliente", 
            "proveedor", "nif", "cif", "direccion", "telefono", "email", 
            "pagina", "original", "copia", "de", "la", "el", "del", "numero", "nº"
        }
        
        numero = ""
        for patron in patrones_num:
            for m in re.finditer(patron, texto):
                candidato = m.group(1).strip()
                
                # 1. Evitamos capturar palabras basura
                if candidato.lower() in palabras_basura:
                    continue
                
                # 2. Un número de factura SIEMPRE tiene al menos un número
                if not any(char.isdigit() for char in candidato):
                    continue
                    
                # Quitamos puntos o comas al final si los hubiera
                candidato = candidato.rstrip(".,;:")
                numero = candidato
                break # Si encontramos uno válido, dejamos de buscar
            
            if numero:
                break # Si ya lo tenemos, no seguimos probando patrones
                
        # Patrón para la fecha (buscamos "Fecha de factura", "Fecha doc", "F.Emitida"...)
        patrones_fecha = [
            r"(?i)fecha\s*(?:de\s*)?(?:factura|emisi[oó]n|documento)?\s*[:\-]?\s*(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{2,4})",
            r"(?i)f\.?\s*(?:emisi[oó]n|factura|doc)\s*[:\-]?\s*(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{2,4})",
            r"(?i)fecha\s*[:\-]?\s*(\d{2}[\/\-\.]\d{2}[\/\-\.]\d{2,4})",
        ]
        
        fecha = ""
        for patron in patrones_fecha:
            m_fecha = re.search(patron, texto)
            if m_fecha:
                fecha = m_fecha.group(1)
                break
                
        # Cambiamos guiones o puntos por barras para que la app lo entienda
        if fecha:
            fecha = fecha.replace("-", "/").replace(".", "/")
            
        return numero, fecha

    def valores(self):
        return {clave: caja.text().strip() for clave, caja in self.campos.items()}


class RegistroPedidosDialog(QDialog):

    def __init__(self, parent=None, registro=None):
        super().__init__(parent)

        self.registro = registro or Registro()
        self._firma = None

        self.setWindowTitle("Registro de pedidos")
        self.resize(1100, 600)

        layout = QVBoxLayout(self)

        titulo = QLabel("🔖 REGISTRO DE PEDIDOS")
        titulo.setStyleSheet("font-size:22px; font-weight:bold; padding:6px;")
        layout.addWidget(titulo)

        ayuda = QLabel(
            "La «matrícula» de cada pedido: ID solicitud (se anota solo al guardar en "
            "el ERP), ID pedido (del correo) e ID recepción. Doble clic en un "
            "pedido para anotar o corregir sus datos."
        )
        ayuda.setWordWrap(True)
        layout.addWidget(ayuda)

        fila_busqueda = QHBoxLayout()
        self.caja_busqueda = QLineEdit()
        self.caja_busqueda.setPlaceholderText(
            "Buscar por cualquier ID, nº de factura o pedido…"
        )
        boton_buscar = QPushButton("Buscar")
        fila_busqueda.addWidget(self.caja_busqueda, 1)
        fila_busqueda.addWidget(boton_buscar)
        layout.addLayout(fila_busqueda)

        self.tabla = QTableWidget()
        self.tabla.setColumnCount(len(COLUMNAS))
        self.tabla.setHorizontalHeaderLabels(COLUMNAS)
        
        # --- NUEVO: Centrar las cabeceras de la tabla ---
        self.tabla.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        cabecera = self.tabla.horizontalHeader()
        cabecera.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        cabecera.setStretchLastSection(True)
        for n, ancho in enumerate((200, 120, 100, 110, 110, 130)):
            self.tabla.setColumnWidth(n, ancho)
        layout.addWidget(self.tabla, 1)

        fila_botones = QHBoxLayout()
        self.boton_editar = QPushButton("✏️ Anotar / editar datos…")
        self.boton_correo = QPushButton("📧 Importar correo de pedido (.eml)…")
        self.boton_cambios = QPushButton("📜 Ver cambios")
        
        # --- NUEVO BOTÓN ELIMINAR ---
        self.boton_eliminar = QPushButton("🗑 Eliminar pedido")
        self.boton_eliminar.setStyleSheet("color: #d9534f;") # Rojo
        # ----------------------------

        boton_cerrar = QPushButton("CERRAR")
        
        for b in (self.boton_editar, self.boton_correo, self.boton_cambios, self.boton_eliminar):
            fila_botones.addWidget(b)
            
        fila_botones.addStretch()
        fila_botones.addWidget(boton_cerrar)
        layout.addLayout(fila_botones)

        self.estado = QLabel("")
        layout.addWidget(self.estado)

        boton_buscar.clicked.connect(self.recargar)
        self.caja_busqueda.returnPressed.connect(self.recargar)
        self.boton_editar.clicked.connect(self.editar)
        self.boton_correo.clicked.connect(self.importar_correo)
        self.boton_cambios.clicked.connect(self.ver_cambios)
        
        # --- NUEVA CONEXIÓN ---
        self.boton_eliminar.clicked.connect(self.eliminar_pedido)
        # ----------------------
        
        boton_cerrar.clicked.connect(self.accept)
        self.tabla.cellDoubleClicked.connect(lambda *_: self.editar())

        # Trae a la lista los pedidos que ya están en el historial.
        try:
            self.registro.sincronizar_historial()
        except Exception:
            pass
        self.recargar()

        # Mientras esta ventana está abierta, se refresca sola (por si el
        # robot acaba de leer un ID pedido).
        self.temporizador = QTimer(self)
        self.temporizador.timeout.connect(lambda: self.recargar(silencioso=True))
        self.temporizador.start(5000)

    # ------------------------------------------------------------------
    def _seleccionado(self):
        fila = self.tabla.currentRow()
        if fila < 0:
            return None
        item = self.tabla.item(fila, 0)
        return self.registro.obtener(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def recargar(self, *_, silencioso=False):
        texto = self.caja_busqueda.text().strip()
        try:
            filas = self.registro.buscar(texto) if texto else self.registro.listar(500)
        except Exception as e:
            if not silencioso:
                QMessageBox.warning(self, "Registro", f"Error al leer el registro:\n{e}")
            return

        firma = tuple(tuple(sorted(f.items())) for f in filas)
        if silencioso and firma == self._firma:
            return
        self._firma = firma

        actual = self._seleccionado()
        order_actual = actual["order_id"] if actual else None

        self.tabla.setRowCount(len(filas))
        fila_a_elegir = -1
        for i, f in enumerate(filas):
            valores = [
                self.registro.nombre_visible(f),
                fecha_para_mostrar(f["fecha_envio"]),
                f["id_solicitud"],
                f["id_pedido"],
                f["id_recepcion"],
                f["factura_numero"],
                self.registro.estado_de(f),
            ]
            for c, valor in enumerate(valores):
                item = QTableWidgetItem(valor)
                
                # --- NUEVO: Centrar el texto de las celdas ---
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, f["order_id"])
                    if f["id_solicitud"] or f["id_pedido"]:
                        item.setToolTip(f"Identificador interno: {f['order_id']}")
                self.tabla.setItem(i, c, item)
            if f["order_id"] == order_actual:
                fila_a_elegir = i

        if fila_a_elegir >= 0:
            self.tabla.selectRow(fila_a_elegir)

        completos = sum(1 for f in filas if self.registro.estado_de(f) == "Completo")
        self.estado.setText(f"{len(filas)} pedidos · {completos} completos")

    # ------------------------------------------------------------------
    def editar(self):
        rec = self._seleccionado()
        if rec is None:
            QMessageBox.information(self, "Registro", "Elige primero un pedido de la lista.")
            return
        dialogo = EditorIdsDialog(rec, self)
        if dialogo.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.registro.fijar(rec["order_id"], origen="manual", **dialogo.valores())
        except ErrorRegistro as e:
            QMessageBox.warning(self, "Registro", str(e))
            return
        self.recargar()

    def eliminar_pedido(self):
        rec = self._seleccionado()
        if rec is None:
            QMessageBox.information(self, "Registro", "Elige primero un pedido de la lista.")
            return
            
        resp = QMessageBox.question(
            self,
            "Eliminar pedido",
            f"¿Seguro que quieres eliminar el pedido {self.registro.nombre_visible(rec)} del registro?\n\n"
            "Esta acción no se puede deshacer.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if resp == QMessageBox.StandardButton.Yes:
            try:
                self.registro.eliminar(rec["order_id"])
                self.recargar()
            except Exception as e:
                QMessageBox.warning(self, "Registro", f"Error al eliminar:\n{e}")

    def importar_correo(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self,
            "Elige el correo (.eml) que trae el ID de pedido",
            "",
            "Correos (*.eml);;Todos los archivos (*.*)",
        )
        if not ruta:
            return
        try:
            analisis = self.registro.analizar_correo(Path(ruta))
        except Exception as e:
            QMessageBox.warning(self, "Registro", f"No se pudo leer el correo:\n{e}")
            return

        # 1) ¿qué número es el ID de pedido?
        opciones = [f"{i}   ({motivo})" for i, motivo in analisis.candidatos]
        manual = "Escribirlo a mano…"
        elegido, ok = QInputDialog.getItem(
            self,
            "ID de pedido",
            f"Correo: {analisis.asunto or '(sin asunto)'}\n\n"
            + ("Números que parecen un ID en este correo. Elige el ID de pedido:"
               if opciones else "No encontré ningún número que parezca un ID."),
            opciones + [manual],
            0,
            False,
        )
        if not ok:
            return
        if elegido == manual:
            texto, ok = QInputDialog.getText(self, "ID de pedido", "Escribe el ID de pedido:")
            if not ok or not texto.strip():
                return
            id_pedido = texto.strip()
        else:
            id_pedido = elegido.split()[0]

        # 2) ¿de qué pedido es?
        pedidos = self.registro.listar(300)
        if not pedidos:
            QMessageBox.information(self, "Registro", "Todavía no hay pedidos en el registro.")
            return
        pedidos.sort(key=lambda p: p["order_id"] != analisis.pedido_sugerido)
        etiquetas = []
        for p in pedidos:
            marca = "  ← sugerido" if p["order_id"] == analisis.pedido_sugerido else ""
            etiquetas.append(
                f"{self.registro.nombre_visible(p)} · {fecha_para_mostrar(p['fecha_envio'])}"
                f" · solicitud: {p['id_solicitud'] or '—'}{marca}"
            )
        texto_ayuda = "¿A qué pedido pertenece este correo?"
        if analisis.pedido_sugerido:
            texto_ayuda += f"\n(Sugerencia: {analisis.motivo_sugerencia}. Compruébalo.)"
        elegido, ok = QInputDialog.getItem(
            self, "Pedido", texto_ayuda, etiquetas, 0, False
        )
        if not ok:
            return
        order_id = pedidos[etiquetas.index(elegido)]["order_id"]

        try:
            self.registro.fijar(order_id, origen="correo", id_pedido=id_pedido)
        except ErrorRegistro as e:
            QMessageBox.warning(self, "Registro", str(e))
            return
        self.recargar()
        QMessageBox.information(
            self, "Registro", f"ID de pedido {id_pedido} anotado en el pedido."
        )

    def ver_cambios(self):
        rec = self._seleccionado()
        if rec is None:
            QMessageBox.information(self, "Registro", "Elige primero un pedido de la lista.")
            return
        lineas = [
            f"{fecha_para_mostrar(e['fecha'])}  ·  {e['tipo']}  ·  {e['detalle']}"
            for e in self.registro.eventos(rec["order_id"])
        ]
        QMessageBox.information(
            self,
            f"Cambios · {self.registro.nombre_visible(rec)}",
            "\n".join(lineas) or "Sin cambios registrados.",
        )

    def done(self, resultado):
        self.temporizador.stop()
        super().done(resultado)