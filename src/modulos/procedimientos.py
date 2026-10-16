from .conocimiento import cargar_conocimiento


_conocimiento = cargar_conocimiento()

PROCEDIMIENTOS = {
    procedimiento["id"]: procedimiento
    for procedimiento in _conocimiento.get("procedimientos", [])
}




def obtener_procedimiento(nombre):
    """Devuelve un procedimiento por su identificador."""
    return PROCEDIMIENTOS.get(nombre)


def generar_respuesta(pregunta, procedimiento):
    """
    Genera una respuesta adaptada al tipo de pregunta.
    """

    if not procedimiento:
        return None

    pregunta = pregunta.lower()

    if any(palabra in pregunta for palabra in [
        "revisar",
        "comprobar",
        "comprobación",
        "comprobaciones",
    ]):
        comprobaciones = procedimiento.get("comprobaciones", [])

        if comprobaciones:
            return "Después del cambio, debes comprobar:\n\n- " + \
                   "\n- ".join(comprobaciones)

    if any(palabra in pregunta for palabra in [
        "advertencia",
        "advertencias",
        "cuidado",
        "precaución",
    ]):
        advertencias = procedimiento.get("advertencias", [])

        if advertencias:
            return "Ten en cuenta:\n\n- " + \
                   "\n- ".join(advertencias)

    if any(palabra in pregunta for palabra in [
        "pasos",
        "cómo",
        "como",
        "hacer",
        "cambiar",
        "mover",
    ]):
        pasos = procedimiento.get("pasos", [])

        if pasos:
            return "Los pasos son:\n\n" + "\n".join(
                f"{i}. {paso}"
                for i, paso in enumerate(pasos, 1)
            )

    return procedimiento.get("respuesta", "")


def buscar_procedimiento(pregunta):
    """
    Busca un procedimiento relacionado con la pregunta.
    Primero intenta encontrar coincidencias en las preguntas clave
    y después busca coincidencias en título, categoría y contenido.
    """

    if not pregunta:
        return None

    pregunta = pregunta.lower().strip()

    # 1. Buscar coincidencias en preguntas clave
    for procedimiento in PROCEDIMIENTOS.values():

        for pregunta_clave in procedimiento.get("pregunta_clave", []):
            if pregunta_clave.lower() in pregunta:
                return procedimiento

    # 2. Buscar palabras relevantes
    palabras_ignoradas = {
        "como",
        "cómo",
        "que",
        "qué",
        "una",
        "un",
        "la",
        "el",
        "de",
        "a",
        "en",
        "por",
        "para",
        "y",
        "me",
        "se",
        "puedo",
        "quiero",
    }

    palabras = [
        palabra
        for palabra in pregunta.split()
        if palabra not in palabras_ignoradas and len(palabra) > 2
    ]

    mejor_procedimiento = None
    mejor_puntuacion = 0

    for procedimiento in PROCEDIMIENTOS.values():

        textos = [
            procedimiento.get("titulo", ""),
            procedimiento.get("categoria", ""),
            procedimiento.get("sistema", ""),
            procedimiento.get("objetivo", ""),
            procedimiento.get("respuesta", ""),
            " ".   join(procedimiento.get("pasos", [])),
            " ".join(procedimiento.get("comprobaciones", [])),
            " ".join(procedimiento.get("advertencias", [])),
        ]

        texto_completo = " ".join(textos).lower()

        puntuacion = 0

        for palabra in palabras:
            if palabra in texto_completo:
                puntuacion += 1

        if puntuacion > mejor_puntuacion:
            mejor_puntuacion = puntuacion
            mejor_procedimiento = procedimiento

    return mejor_procedimiento