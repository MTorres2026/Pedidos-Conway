"""
Vista limpia de un documento para la pantalla «Conocimiento».

El texto guardado en la biblioteca llega "en bruto": celdas de Excel una
detrás de otra, correos envueltos por Outlook, cascadas de reenvíos, pies
legales y firmas. Volcarlo tal cual lo hace ilegible.

Este módulo (sin Qt) lo convierte en bloques con sentido —párrafos, listas,
tablas, títulos, separadores de hilo— y de ahí a un HTML sencillo, con
aire, que sí se puede leer. También saca un resumen breve («de qué trata»)
con las primeras frases útiles, saltándose saludos y legalismos.
"""

import html
import re
import unicodedata

# ---------------------------------------------------------------------------
# Patrones de ruido
# ---------------------------------------------------------------------------

def _sin_acentos(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    ).casefold()


_PAGINA = re.compile(r"^pagina\s+\d+\s+de\s+\d+$")
_SOLO_SIMBOLOS = re.compile(r"^[\W_]+$")
_SEPARADOR = re.compile(r"^([_\-=\*~#])\1{4,}$")
_VINETA = re.compile(r"^[•·▪◦\-–—\*]\s+(.*)$")
_NUMERADA = re.compile(r"^(\d{1,2})[.)]\s+(.*)$")
_CABECERA = re.compile(
    r"^(de|para|cc|cco|enviado el|enviado|fecha|asunto|from|to|sent|subject)\s*:\s*(.*)$",
    re.IGNORECASE,
)
_URL = re.compile(r"(https?://[^\s<>\"')]+)")

# Frases que delatan un pie legal o una firma automática: no aportan nada
# al leer el documento y ocupan media pantalla.
_LEGAL_FUERTE = (
    "es propiedad de",
    "prohibido el uso de esta informacion",
    "prohibida su reproduccion",
    "prohibido el uso de este documento",
    "si ha recibido este mensaje por error",
    "si usted no es el destinatario",
    "si no es el destinatario",
    "la informacion contenida podra ser utilizada",
    "la información contenida podrá ser utilizada",
    "informacion contenida en este mensaje es confidencial",
    "este mensaje y sus archivos adjuntos van dirigidos",
    "aviso de confidencialidad",
    "enviado desde mi iphone",
    "enviado desde mi android",
    "enviado desde outlook",
    "get outlook for",
    "obtener outlook para",
)

# Palabras típicas del resto del párrafo legal (para seguir descartando
# mientras dure la parrafada, pero sin comerse texto normal).
_LEGAL_SUAVE = (
    "subsidiarias", "afiliadas", "fidedigna", "plasmada", "micrositio",
    "normativos", "establecidos", "destinatario", "confidencial",
    "reproducir", "divulgar", "responsable reconoce",
)

_SALUDOS = re.compile(
    r"^(hola|buenos dias|buenas tardes|buenas noches|buenas|estimad|querid|"
    r"muy buenas|buen dia)\b"
)
_DESPEDIDAS = re.compile(
    r"^(un saludo|saludos|gracias|muchas gracias|atentamente|un abrazo|"
    r"quedo atento|quedamos atentos|recibid|recibe)\b"
)


def _es_legal(linea: str) -> bool:
    limpio = _sin_acentos(linea.strip())
    if _PAGINA.match(limpio):
        return True
    if limpio.startswith("aviso legal"):
        return True
    return any(frase in limpio for frase in _LEGAL_FUERTE)


def _parece_legal(linea: str) -> bool:
    limpio = _sin_acentos(linea)
    return sum(1 for p in _LEGAL_SUAVE if p in limpio) >= 2


# ---------------------------------------------------------------------------
# De texto en bruto a bloques
# ---------------------------------------------------------------------------

def _es_titulo(linea: str) -> bool:
    """Línea corta casi toda en mayúsculas: un epígrafe del documento."""
    if len(linea) > 80 or len(linea.split()) < 2 or linea.endswith((".", ",")):
        return False
    letras = [c for c in linea if c.isalpha()]
    if len(letras) < 6:
        return False
    return sum(1 for c in letras if c.isupper()) / len(letras) >= 0.75


def _celdas(linea: str):
    """Si la línea es una fila de tabla (tabuladores o |), sus celdas."""
    if "\t" in linea:
        partes = linea.split("\t")
    elif linea.count("|") >= 1:
        partes = linea.split("|")
    else:
        return None
    partes = [p.strip() for p in partes]
    while partes and not partes[0]:
        partes.pop(0)
    while partes and not partes[-1]:
        partes.pop()
    if sum(1 for p in partes if p) < 2:
        return None
    return partes


_CUELGAN = {
    "de", "del", "la", "el", "los", "las", "un", "una", "en", "con", "por",
    "para", "a", "al", "y", "e", "o", "u", "su", "sus", "que", "se", "lo",
    "como", "entre", "sobre", "desde", "hasta", "sin",
}


def _se_puede_unir(actual: str, siguiente: str, en_vineta: bool = False) -> bool:
    """¿La línea siguiente continúa la frase de la actual? (des-envolver)."""
    if not actual or actual[-1] in ".!?:;…»\"":
        return False
    if not siguiente:
        return False
    # «Reporte de Conteo de / Histórico…»: si la línea se queda colgando de
    # una preposición o un artículo, la siguiente continúa seguro.
    ultima = _sin_acentos(actual.rsplit(" ", 1)[-1]).strip("()«»\"'")
    if ultima in _CUELGAN:
        return True
    primera = siguiente[0]
    if primera.islower() or primera.isdigit() or primera in ")]":
        return True
    # Línea larga cortada por el ancho de Outlook: continúa aunque la
    # siguiente empiece por mayúscula (nombre propio, sigla…).
    if len(actual) >= 55:
        return True
    # Dentro de una viñeta sin cerrar, una línea con forma de frase
    # (no un campo suelto de 1-2 palabras) también es continuación.
    return en_vineta and len(siguiente.split()) >= 3


def _cerrar_texto(pendientes, bloques):
    """Convierte las líneas de texto acumuladas en párrafos o en lista."""
    if not pendientes:
        return
    segmentos = []
    for linea in pendientes:
        if segmentos and _se_puede_unir(segmentos[-1], linea):
            segmentos[-1] += " " + linea
        else:
            segmentos.append(linea)
    cortos = [s for s in segmentos if len(s) <= 64]
    # Cascada de líneas sueltas (celdas de un Excel, campos de un
    # formulario…): como lista se lee; como párrafos es un muro.
    if len(segmentos) >= 3 and len(cortos) == len(segmentos):
        bloques.append(("lista", [(False, s) for s in segmentos]))
    else:
        for s in segmentos:
            bloques.append(("parrafo", s))
    pendientes.clear()


def bloques(texto: str):
    """Lista de bloques: (tipo, contenido).

    Tipos: "titulo" (str), "parrafo" (str), "lista" ([(numerada, str)]),
    "tabla" ([[celdas]]), "hilo" ({de, fecha} del mensaje anterior).
    Devuelve (bloques, hubo_pie_legal).
    """
    salida = []
    pendientes = []          # líneas de texto seguidas
    filas = []               # filas de tabla seguidas
    puntos = []              # viñetas seguidas
    cabeceras = {}           # cabeceras de un mensaje anterior
    en_legal = False
    legal_cerrado = False    # la última línea legal descartada acababa en «.»
    hubo_legal = False

    def cerrar_tablas():
        if filas:
            salida.append(("tabla", list(filas)))
            filas.clear()

    def cerrar_puntos():
        if puntos:
            salida.append(("lista", list(puntos)))
            puntos.clear()

    def cerrar_cabeceras():
        if cabeceras:
            salida.append(("hilo", dict(cabeceras)))
            cabeceras.clear()

    def cerrar_todo():
        _cerrar_texto(pendientes, salida)
        cerrar_tablas()
        cerrar_puntos()
        cerrar_cabeceras()

    for cruda in (texto or "").split("\n"):
        linea = cruda.strip()

        if en_legal:
            # Dentro de la parrafada legal: se sigue descartando mientras
            # no empiece claramente un texto nuevo (tras un «.», una línea
            # que abra con mayúscula, dígitos o viñeta y no suene legal).
            if not linea:
                en_legal = False
                continue
            nuevo = (
                legal_cerrado
                and not _parece_legal(linea)
                and (linea[0].isupper() or linea[0].isdigit()
                     or _VINETA.match(linea))
            )
            if not nuevo:
                legal_cerrado = linea.endswith(".")
                continue
            en_legal = False

        if not linea:
            cerrar_todo()
            continue
        if _es_legal(linea):
            hubo_legal = True
            en_legal = True
            legal_cerrado = linea.endswith(".")
            continue
        if _SOLO_SIMBOLOS.match(linea) and len(linea) < 40:
            cerrar_todo()
            continue
        if _SEPARADOR.match(linea.replace(" ", "")):
            cerrar_todo()
            continue

        m = _CABECERA.match(linea)
        if m and (cabeceras or m.group(1).casefold() in ("de", "from", "enviado el")):
            _cerrar_texto(pendientes, salida)
            cerrar_tablas()
            cerrar_puntos()
            clave = _sin_acentos(m.group(1))
            if clave in ("de", "from"):
                cabeceras["de"] = m.group(2).strip()
            elif clave.startswith(("enviado", "sent", "fecha")):
                cabeceras["fecha"] = m.group(2).strip()
            continue

        fila = _celdas(linea)
        if fila is not None:
            _cerrar_texto(pendientes, salida)
            cerrar_puntos()
            cerrar_cabeceras()
            filas.append(fila)
            continue

        m = _VINETA.match(linea)
        numerada = _NUMERADA.match(linea)
        if m:
            _cerrar_texto(pendientes, salida)
            cerrar_tablas()
            cerrar_cabeceras()
            puntos.append((False, m.group(1).strip()))
            continue
        if numerada:
            _cerrar_texto(pendientes, salida)
            cerrar_tablas()
            cerrar_cabeceras()
            puntos.append((True, numerada.group(2).strip()))
            continue

        if _es_titulo(linea):
            cerrar_todo()
            salida.append(("titulo", linea))
            continue

        cerrar_tablas()
        cerrar_cabeceras()
        # Línea que continúa la última viñeta (envuelta): se le pega.
        if puntos and _se_puede_unir(puntos[-1][1], linea, en_vineta=True):
            num, txt = puntos[-1]
            puntos[-1] = (num, txt + " " + linea)
            continue
        cerrar_puntos()
        pendientes.append(linea)

    cerrar_todo()
    return salida, hubo_legal


# ---------------------------------------------------------------------------
# Resumen: las primeras frases que de verdad dicen algo
# ---------------------------------------------------------------------------

def _frases(texto: str):
    return [f.strip() for f in re.split(r"(?<=[.!?…])\s+", texto) if f.strip()]


def resumen(texto: str, maximo: int = 320) -> str:
    """Dos o tres frases del principio, sin saludos ni firmas. '' si no hay
    de dónde sacar (documento de celdas sueltas, lista de campos…)."""
    lista_bloques, _ = bloques(texto)
    util = []
    for tipo, contenido in lista_bloques:
        if tipo == "parrafo":
            util.append(contenido)
        if len(util) >= 3:
            break
    if not util:
        return ""

    frases = []
    for parrafo in util:
        limpio = _sin_acentos(parrafo)
        if _SALUDOS.match(limpio) or _DESPEDIDAS.match(limpio):
            # Saludo pegado a la primera frase: quita solo el saludo.
            resto = re.sub(r"^[^.!?…]{0,40}[:,]\s*", "", parrafo)
            if resto != parrafo and len(resto.split()) >= 5:
                parrafo = resto
            else:
                continue
        frases.extend(_frases(parrafo))

    # Solo hay resumen si de verdad hay frases: una larga o dos medianas.
    # (Si no, como en una hoja de cálculo de campos sueltos, saldría un
    # pegote de etiquetas que no resume nada.)
    largas = [f for f in frases if len(f.split()) >= 7]
    if not any(len(f.split()) >= 10 for f in frases) and len(largas) < 2:
        return ""

    salida = ""
    for frase in frases:
        if len(frase.split()) < 5:
            continue
        candidata = (salida + " " + frase).strip()
        if salida and len(candidata) > maximo:
            break
        salida = candidata
        if len(salida) >= maximo * 0.7:
            break
    if len(salida) < 40:
        return ""
    if len(salida) > maximo:  # una única frase larguísima
        salida = salida[:maximo].rsplit(" ", 1)[0] + "…"
    return salida


# ---------------------------------------------------------------------------
# De bloques a HTML legible (con resaltado de la búsqueda y enlaces)
# ---------------------------------------------------------------------------

_GRIS = "#6f6450"  # texto secundario sobre el fondo papel (#f6f1e7)


def _con_estilo(texto: str, palabras) -> str:
    """Escapa, enlaza las URL y marca en amarillo las palabras buscadas."""
    # Primero las URL (antes de resaltar, para no romper los enlaces).
    trozos = []
    ultimo = 0
    for m in _URL.finditer(texto):
        trozos.append((False, texto[ultimo:m.start()]))
        url = m.group(1).rstrip(".,;")
        trozos.append((True, url))
        ultimo = m.end() - (len(m.group(1)) - len(url))
    trozos.append((False, texto[ultimo:]))

    patron = None
    if palabras:
        patron = re.compile(
            "(" + "|".join(re.escape(p) for p in palabras) + ")", re.IGNORECASE
        )

    salida = []
    for es_url, trozo in trozos:
        if not trozo:
            continue
        if es_url:
            seguro = html.escape(trozo, quote=True)
            salida.append(f'<a href="{seguro}" style="color:#1f6fb4">{seguro}</a>')
            continue
        if patron is None:
            salida.append(html.escape(trozo))
            continue
        for i, parte in enumerate(patron.split(trozo)):
            if not parte:
                continue
            seguro = html.escape(parte)
            if i % 2 == 1:
                salida.append(
                    f'<span style="background-color:#ffe08a;color:#000">{seguro}</span>'
                )
            else:
                salida.append(seguro)
    return "".join(salida)


def a_html(lista_bloques, palabras=()) -> str:
    partes = []
    for tipo, contenido in lista_bloques:
        if tipo == "titulo":
            partes.append(
                f'<p style="margin:16px 0 6px 0;font-size:15px">'
                f'<b>{_con_estilo(contenido, palabras)}</b></p>'
            )
        elif tipo == "parrafo":
            partes.append(
                f'<p style="margin:0 0 10px 0;line-height:150%">'
                f'{_con_estilo(contenido, palabras)}</p>'
            )
        elif tipo == "lista":
            numerada = any(n for n, _ in contenido)
            apertura = "<ol>" if numerada else "<ul>"
            cierre = "</ol>" if numerada else "</ul>"
            items = "".join(
                f'<li style="margin-bottom:4px;line-height:140%">'
                f'{_con_estilo(txt, palabras)}</li>'
                for _, txt in contenido
            )
            partes.append(f"{apertura}{items}{cierre}")
        elif tipo == "tabla":
            filas = []
            for fila in contenido:
                celdas = "".join(
                    f'<td style="padding:3px 8px">{_con_estilo(c, palabras)}</td>'
                    for c in fila
                )
                filas.append(f"<tr>{celdas}</tr>")
            partes.append(
                '<table border="1" cellspacing="0" '
                'style="border-color:#c4b28a;margin:6px 0 12px 0">'
                + "".join(filas) + "</table>"
            )
        elif tipo == "hilo":
            detalle = " · ".join(
                v for v in (contenido.get("de"), contenido.get("fecha")) if v
            )
            partes.append(
                f'<p style="margin:14px 0 8px 0;color:{_GRIS}">'
                f"↩ <i>Mensaje anterior{' — ' + html.escape(detalle) if detalle else ''}</i></p>"
            )
    return "".join(partes)
