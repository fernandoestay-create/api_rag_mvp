import os
import pickle
import faiss
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

# ==============================================================================
# CONFIGURACIÓN (SOLO HyC)
# ==============================================================================

app = FastAPI(title="API RAG HyC", version="3.0")

# Definimos la ruta FIJA a tu carpeta HyC
# Esto busca en: /app/index/HyC/faiss.index
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH_HYC = os.path.join(BASE_DIR, "index", "HyC")

print(f"📂 Ruta configurada para HyC: {PATH_HYC}")

# Variables globales para guardar en memoria
resources = {}

@app.on_event("startup")
def load_resources():
    print("⏳ Iniciando carga de recursos HyC...")
    
    # 1. Cargar Modelo (El cerebro)
    resources['model'] = SentenceTransformer('all-MiniLM-L6-v2')
    
    # 2. Cargar Índice y Metadata desde la carpeta HyC
    faiss_file = os.path.join(PATH_HYC, "faiss.index")
    meta_file = os.path.join(PATH_HYC, "metadata.pkl")
    
    if os.path.exists(faiss_file) and os.path.exists(meta_file):
        resources['index'] = faiss.read_index(faiss_file)
        with open(meta_file, "rb") as f:
            resources['metadata'] = pickle.load(f)
        print("✅ ¡Base de datos HyC cargada exitosamente!")
    else:
        print(f"❌ ERROR CRÍTICO: No encuentro los archivos en {PATH_HYC}")
        # No detenemos el server para que puedas ver el log en Render, 
        # pero la búsqueda fallará si esto no carga.

# ==============================================================================
# MODELO DE DATOS (Lo que ChatGPT envía)
# ==============================================================================

class SearchRequest(BaseModel):
    # CORRECCIÓN CLAVE: Usamos 'question' porque eso es lo que envía ChatGPT.
    # Antes tenías 'text' y por eso daba Error 422.
    question: str
    
    # Opcional: Aunque sea solo HyC, lo dejamos para que no falle si GPT envía el campo.
    project: str = "HyC"

# ==============================================================================
# ENDPOINT DE BÚSQUEDA
# ==============================================================================

@app.post("/search")
def search(req: SearchRequest):
    
    # Verificación de seguridad
    if 'index' not in resources or 'metadata' not in resources:
        raise HTTPException(status_code=500, detail="La base de datos HyC no está cargada en el servidor.")
        
    # 1. Vectorizar la pregunta
    query_vector = resources['model'].encode([req.question])
    
    # 2. Buscar en el índice (Top 5 resultados)
    D, I = resources['index'].search(query_vector, 5)
    
    results = []
    indices = I[0] # Lista de IDs encontrados
    scores = D[0]  # Lista de puntuaciones (distancia)
    
    for i, idx in enumerate(indices):
        if idx == -1: continue # Resultado vacío
        
        # Recuperar la info real del archivo metadata
        meta = resources['metadata'][idx]
        
        results.append({
            "text": meta.get('text', '')[:1500],
            "document": meta.get('document', 'Desconocido'),
            "page": meta.get('page', 0),
            "url": meta.get('url', 'Sin Link'), # <--- AQUÍ VIAJA TU LINK
            "score": float(scores[i])
        })
        
    return {"results": results}