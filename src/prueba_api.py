import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")

if not api_key:
    print("ERROR: No se ha encontrado OPENAI_API_KEY")
else:
    print("Clave encontrada correctamente.")

    client = OpenAI(api_key=api_key)

    respuesta = client.responses.create(
        model="gpt-5-mini",
        input="Responde únicamente: API funcionando correctamente"
    )

    print(respuesta.output_text)