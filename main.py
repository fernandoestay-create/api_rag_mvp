import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pinecone import Pinecone
from openai import OpenAI

# ============================================================
# 1. CONFIGURACIÓN E INICIALIZACIÓN
# ============================================================
app = FastAPI(
    title="API RAG con Pinecone (EIA)",
    version="2.0",
    description="API ligera que consulta Pinecone Cloud en lugar de usar memoria local."
)

# Cargamos las claves desde las Variables de Entorno (Configuradas en Render)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY") # <--- Esta es nueva
INDEX_NAME = "api-rag-mvp" # El nombre de tu índice en la nube

# Verificación de seguridad: Si faltan claves, el servidor no arranca.
if not OPENAI_API_KEY:
    raise RuntimeError("❌ ERROR CRÍTICO: No encontré la variable OPENAI_API_KEY.")
if not PINECONE_API_KEY:
    raise RuntimeError("❌ ERROR CRÍTICO: No encontré la variable PINECONE_API_KEY.")

# Conectamos los clientes
try:
    # Cliente para generar texto y embeddings
    client_openai = OpenAI(api_key=OPENAI_API_KEY)
    
    # Cliente para buscar en la base de datos vectorial
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(INDEX_NAME)
    print("✅ Conexión exitosa con OpenAI y Pinecone.")
except Exception as e:
    print(f"❌ Error al conectar con servicios externos: {e}")

# Definimos el modelo de datos para recibir preguntas (formato JSON)
class Question(BaseModel):
    question: str

# ============================================================
# 2. ENDPOINTS (Rutas de la API)
# ============================================================

@app.get("/")
def home():
    """Ruta de prueba para verificar que el servidor está vivo."""
    return {
        "status": "Online 🟢",
        "architecture": "Serverless (Pinecone + OpenAI)",
        "index_connected": INDEX_NAME
    }

@app.post("/ask")
def ask(q: Question):
    """
    Recibe una pregunta, busca contexto en Pinecone y genera respuesta con GPT-4o.
    """
    try:
        print(f"🔎 Procesando pregunta: {q.question}")

        # PASO A: Convertir la pregunta del usuario en números (Embedding)
        # Usamos el mismo modelo con el que creaste los datos (text-embedding-3-small)
        response_emb = client_openai.embeddings.create(
            input=q.question,
            model="text-embedding-3-small"
        )
        question_vector = response_emb.data[0].embedding

        # PASO B: Consultar a Pinecone (Búsqueda Semántica)
        # Le pedimos los 5 fragmentos más parecidos al vector de la pregunta
        search_response = index.query(
            vector=question_vector,
            top_k=5,             # Trae los 5 mejores resultados
            include_metadata=True # ¡Importante! Para recuperar el texto real
        )

        # PASO C: Construir el contexto (Recuperar el texto)
        context_text = ""
        for match in search_response['matches']:
            # Verificamos que el resultado tenga texto guardado
            if match['metadata'] and 'text' in match['metadata']:
                context_text += match['metadata']['text'] + "\n\n---\n\n"

        # Si Pinecone no devuelve nada relevante
        if not context_text:
            return {"answer": "Lo siento, no encontré información relevante en la base de datos sobre este tema."}

        # PASO D: Generar la respuesta con Inteligencia Artificial (GPT-4o)
        prompt = f"""
        Actúa como un experto consultor en análisis de documentos ambientales (EIA).
        Utiliza EXCLUSIVAMENTE la siguiente información de contexto para responder la pregunta del usuario de forma profesional y precisa.
        Si la respuesta no se encuentra en el contexto, indícalo claramente.
        
        CONTEXTO RECUPERADO DE LA BASE DE DATOS:
        {context_text}

        PREGUNTA DEL USUARIO:
        {q.question}
        """

        completion = client_openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Eres un asistente útil y preciso basado en evidencia."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2 # Baja creatividad para ser fiel a los documentos
        )

        respuesta_final = completion.choices[0].message.content
        
        return {
            "answer": respuesta_final,
            "sources_count": len(search_response['matches']) # Opcional: decir cuántas fuentes usó
        }

    except Exception as e:
        print(f"❌ Error procesando la solicitud: {e}")
        raise HTTPException(status_code=500, detail=str(e))