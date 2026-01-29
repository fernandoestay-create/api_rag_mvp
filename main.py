import os
import pickle
import numpy as np
import faiss
import gdown  # Librería clave para descargar desde Drive
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import OpenAI

# ==========================================
# 1. CONFIGURACIÓN INICIAL Y SECRETOS
# ==========================================
# Cargamos las variables de entorno (aquí vive tu API KEY de OpenAI).
# En tu PC busca el archivo .env, en Render buscará en su configuración interna.
load_dotenv()

# Inicializamos el cliente de OpenAI con la clave segura
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Creamos la aplicación web (FastAPI)
app = FastAPI(
    title="API RAG EIA", 
    description="API inteligente que descarga su propia memoria desde Drive y responde preguntas sobre documentos."
)

# ==========================================
# 2. SISTEMA DE AUTO-DESCARGA (CRÍTICO)
# ==========================================
# Esta función es el "truco" para que Render funcione.
# Como GitHub no tiene los archivos pesados, esta función va a Google Drive
# y se los trae antes de que la App empiece a funcionar.
def descargar_datos_si_no_existen():
    """
    Verifica si la carpeta 'Index_global' existe. 
    Si no existe (como pasa en Render al iniciar), la crea y descarga los archivos.
    """
    carpeta_destino = "Index_global"
    
    # DICCIONARIO DE ENLACES: Aquí están tus archivos reales de Drive
    archivos = {
        # El archivo pesado con los vectores (la "memoria matemática")
        "faiss.index": "https://drive.google.com/file/d/1qay74jcAFWIKmyGKKgTTKGQO4pdX7-1K/view?usp=sharing",
        # El archivo con los textos originales (para que sepamos qué dice cada vector)
        "metadata.pkl": "https://drive.google.com/file/d/1nTNxM-hmmBQ7eI1DsgDf5jaasKb7FaRS/view?usp=sharing"
    }

    # 1. ¿Existe la carpeta? Si no, la creamos.
    if not os.path.exists(carpeta_destino):
        os.makedirs(carpeta_destino)
        print(f"📂 Carpeta creada: {carpeta_destino}")

    # 2. Revisamos cada archivo uno por uno
    for nombre_archivo, url in archivos.items():
        ruta_completa = os.path.join(carpeta_destino, nombre_archivo)
        
        # Si el archivo NO está en el disco, lo descargamos
        if not os.path.exists(ruta_completa):
            print(f"📥 Descargando {nombre_archivo} desde Google Drive...")
            
            # Pequeño ajuste técnico para convertir enlaces de 'vista' a 'descarga directa'
            file_id = url.split('/')[-2]
            download_url = f'https://drive.google.com/uc?id={file_id}'
            
            # Usamos gdown para bajar el archivo
            gdown.download(download_url, ruta_completa, quiet=False)
            print(f"✅ {nombre_archivo} descargado exitosamente.")
        else:
            # Si ya existe (ej. en tu PC local), no perdemos tiempo bajándolo de nuevo
            print(f"ℹ️ El archivo {nombre_archivo} ya existe. Saltando descarga.")

# ¡IMPORTANTE! Ejecutamos esta función AHORA MISMO, antes de seguir.
# Si esto falla, la app se detiene aquí.
descargar_datos_si_no_existen()

# ==========================================
# 3. CARGA DE LA "MEMORIA" EN RAM
# ==========================================
# Definimos dónde quedaron guardados los archivos
INDEX_PATH = "Index_global/faiss.index"
METADATA_PATH = "Index_global/metadata.pkl"

print("🧠 Cargando cerebro digital (FAISS y Metadata)...")
try:
    # Leemos el índice FAISS (búsqueda rápida)
    index = faiss.read_index(INDEX_PATH)
    
    # Leemos la metadata (textos reales)
    with open(METADATA_PATH, "rb") as f:
        metadata = pickle.load(f)
    print("🚀 ¡Sistema listo! Memoria cargada correctamente.")

except Exception as e:
    print(f"❌ Error fatal cargando los archivos: {e}")
    # Si no podemos leer la memoria, lanzamos un error y cerramos.
    raise e

# ==========================================
# 4. DEFINICIÓN DE DATOS (VALIDACIÓN)
# ==========================================
# Esto asegura que el usuario nos mande un JSON correcto: {"question": "texto"}
class Query(BaseModel):
    question: str

# ==========================================
# 5. LÓGICA DE INTELIGENCIA (EL CEREBRO)
# ==========================================

def get_embedding(text, model="text-embedding-3-small"):
    """
    Toma un texto y le pide a OpenAI que lo convierta en una lista de números (vector).
    Usamos el mismo modelo que usaste para crear el índice.
    """
    text = text.replace("\n", " ")
    return client.embeddings.create(input=[text], model=model).data[0].embedding

@app.post("/ask")
async def ask_question(query: Query):
    """
    ENDPOINT PRINCIPAL: Aquí llega la pregunta del usuario.
    Flujo: Pregunta -> Vector -> Búsqueda en FAISS -> Contexto -> ChatGPT -> Respuesta
    """
    try:
        # PASO A: Convertir la pregunta del usuario en números
        question_embedding = np.array([get_embedding(query.question)]).astype('float32')

        # PASO B: Buscar en FAISS los 5 fragmentos más parecidos
        k = 5  # Número de fragmentos a recuperar
        distances, indices = index.search(question_embedding, k)

        # PASO C: Recuperar el TEXTO real de esos fragmentos encontrados
        retrieved_docs = []
        for i, idx in enumerate(indices[0]):
            if idx != -1:  # Si idx es -1 significa que no encontró nada
                doc_meta = metadata[idx]
                retrieved_docs.append(f"Fragmento {i+1}: {doc_meta['text']}")

        # Unimos todos los fragmentos en un solo bloque de texto
        context_text = "\n\n".join(retrieved_docs)

        # PASO D: Crear el "Prompt" para ChatGPT (RAG)
        # Le damos las instrucciones estrictas de solo usar el contexto
        prompt = f"""
        Actúa como un experto en análisis ambiental y legal de proyectos EIA.
        Usa EXCLUSIVAMENTE la siguiente información de contexto para responder la pregunta del usuario.
        Si la respuesta no está en el contexto, di "No tengo información suficiente en los documentos proporcionados".
        
        Contexto (Información recuperada):
        {context_text}

        Pregunta del usuario:
        {query.question}
        """

        # PASO E: Enviar todo a ChatGPT para que redacte la respuesta final
        response = client.chat.completions.create(
            model="gpt-4o-mini", # Modelo rápido y económico
            messages=[
                {"role": "system", "content": "Eres un asistente útil y preciso basado en evidencia técnica."},
                {"role": "user", "content": prompt}
            ],
            temperature=0 # Temperatura 0 para que sea muy objetivo y no invente
        )

        # Devolvemos la respuesta limpia y también las fuentes (útil para debug)
        return {
            "answer": response.choices[0].message.content,
            "source_docs": retrieved_docs
        }

    except Exception as e:
        # Si algo falla, avisamos al usuario con un error 500
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# 6. ENDPOINT DE SALUD (PING)
# ==========================================
@app.get("/")
def read_root():
    """
    Simplemente para probar si el servidor está encendido.
    Si entras a la web principal, verás este mensaje.
    """
    return {"status": "API RAG Activa", "service": "Análisis de EIA v2"}