import os
import pickle
import faiss
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path

# --- 1. CONFIGURACIÓN ---
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

app = FastAPI()

# --- 2. CARGA DE DATOS ---
BASE_DIR = Path(__file__).parent
# ¡AQUÍ ESTÁ EL CAMBIO! Apunta a la carpeta sin tildes
CARPETA_INDICE = BASE_DIR / "Index_global" 

print("⏳ Cargando cerebro (Index + Metadata)...")

try:
    if (CARPETA_INDICE / "faiss.index").exists():
        index = faiss.read_index(str(CARPETA_INDICE / "faiss.index"))
        with open(CARPETA_INDICE / "metadata.pkl", "rb") as f:
            metadata_store = pickle.load(f)
        print(f"✅ ¡Cerebro cargado! {len(metadata_store)} fragmentos listos.")
    else:
        print("⚠️ Aún no existe la carpeta Index_global. Esperando migración...")
        index = None
        metadata_store = []

except Exception as e:
    print(f"❌ Error cargando índices: {e}")
    metadata_store = []
    index = None

# --- 3. MODELOS ---
class QueryRequest(BaseModel):
    question: str

# --- 4. ENDPOINTS ---
def get_embedding(text):
    text = text.replace("\n", " ")
    return client.embeddings.create(input=[text], model="text-embedding-3-small").data[0].embedding

@app.get("/")
def home():
    return {"status": "online", "message": "API RAG v2 (Global) funcionando 🚀"}

@app.post("/chat")
def chat_endpoint(request: QueryRequest):
    if not index or not metadata_store:
        raise HTTPException(status_code=500, detail="El índice no está cargado.")

    # 1. Vectorizar pregunta
    try:
        query_vector = np.array([get_embedding(request.question)]).astype('float32')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error vectorizando: {e}")

    # 2. Buscar
    k = 5
    D, I = index.search(query_vector, k)

    # 3. Recuperar texto
    contexto_encontrado = ""
    fuentes = []

    for i in range(k):
        idx = I[0][i]
        if idx < len(metadata_store):
            item = metadata_store[idx]
            texto = item.get('contenido', '')
            meta = item.get('metadata', {})
            proyecto = meta.get('project', 'Desconocido')
            archivo = meta.get('source', 'Desconocido')
            
            fragmento = f"\n[Fuente: Proyecto {proyecto} | Archivo: {archivo}]\n{texto}\n"
            contexto_encontrado += fragmento
            fuentes.append(f"{proyecto} - {archivo}")

    # 4. Generar respuesta GPT
    prompt_sistema = """
    Eres un asistente experto en proyectos de ingeniería. Responde BASADO SOLO en el contexto.
    Si no sabes, di "No tengo información". Cita siempre Proyecto y Archivo.
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": f"Contexto:\n{contexto_encontrado}\n\nPregunta: {request.question}"}
            ],
            temperature=0
        )
        respuesta_final = response.choices[0].message.content
    except Exception as e:
        respuesta_final = f"Error GPT: {e}"

    return {
        "respuesta": respuesta_final,
        "fuentes_consultadas": fuentes
    }