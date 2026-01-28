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
    return {"status": "online", "message": "API RAG v2 (Global) con Trazabilidad Full 🚀"}

@app.post("/chat")
def chat_endpoint(request: QueryRequest):
    if not index or not metadata_store:
        raise HTTPException(status_code=500, detail="El índice no está cargado.")

    # 1. Vectorizar pregunta
    try:
        query_vector = np.array([get_embedding(request.question)]).astype('float32')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error vectorizando: {e}")

    # 2. Buscar (Top 5 fragmentos)
    k = 5
    D, I = index.search(query_vector, k)

    # 3. Recuperar texto + METADATA DETALLADA
    contexto_encontrado = ""
    fuentes = []

    for i in range(k):
        idx = I[0][i]
        if idx < len(metadata_store):
            item = metadata_store[idx]
            
            # --- CORRECCIÓN CRÍTICA ---
            # Tu chunker.py guarda los datos planos (no dentro de 'metadata' ni 'contenido')
            # Extraemos directamente las llaves que definiste en chunker.py
            
            texto = item.get('text', '') 
            proyecto = item.get('project', 'General')
            archivo = item.get('document', 'Desconocido')
            pagina = item.get('page', 'N/A')    # <--- AQUÍ RECUPERAMOS LA PÁGINA
            url = item.get('url', 'No disp.')   # <--- AQUÍ RECUPERAMOS EL LINK
            
            # Construimos un bloque de texto muy claro para GPT
            fragmento = f"""
            [DOCUMENTO: {archivo} | PÁGINA: {pagina} | LINK: {url}]
            {texto}
            --------------------------------------------------
            """
            contexto_encontrado += fragmento
            fuentes.append(f"{archivo} (Pág. {pagina})")

    # 4. Generar respuesta GPT
    prompt_sistema = """
    Eres un asistente experto técnico (Asesor HyC).
    
    TU OBJETIVO: Responder basado EXCLUSIVAMENTE en el contexto proporcionado.
    
    REGLA DE ORO DE CITAS:
    Cada vez que uses información, DEBES citar la fuente inmediatamente con este formato:
    "El impacto es alto [Fuente: NombreArchivo.pdf | Pág: X]"
    
    Si el contexto incluye un LINK, inclúyelo también.
    Si no encuentras la respuesta en el contexto, di "No tengo información en los documentos".
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