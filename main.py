import os
import pickle
import faiss
import numpy as np
import traceback
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

app = FastAPI(title="API RAG HyC", version="DEBUG")

# Definimos rutas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH_HYC = os.path.join(BASE_DIR, "Index", "HyC")
resources = {}

@app.on_event("startup")
def load_resources():
    print(f"🚀 INICIANDO CARGA. Directorio base: {BASE_DIR}")
    print(f"📂 Buscando archivos en: {PATH_HYC}")
    
    try:
        # Verificamos si la carpeta existe
        if not os.path.exists(PATH_HYC):
            print(f"❌ LA CARPETA {PATH_HYC} NO EXISTE.")
            print(f"   Contenido de {BASE_DIR}: {os.listdir(BASE_DIR)}")
            return

        # Verificamos archivos
        files = os.listdir(PATH_HYC)
        print(f"📄 Archivos encontrados en HyC: {files}")

        faiss_path = os.path.join(PATH_HYC, "faiss.index")
        meta_path = os.path.join(PATH_HYC, "metadata.pkl")

        if "faiss.index" not in files or "metadata.pkl" not in files:
            print("❌ FALTAN ARCHIVOS CRÍTICOS (faiss.index o metadata.pkl)")
            return

        print("⏳ Cargando modelo IA...")
        resources['model'] = SentenceTransformer('all-MiniLM-L6-v2')
        
        print("⏳ Cargando índice FAISS...")
        resources['index'] = faiss.read_index(faiss_path)
        
        print("⏳ Cargando metadatos...")
        with open(meta_path, "rb") as f:
            resources['metadata'] = pickle.load(f)
            
        print("✅ SISTEMA LISTO Y CARGADO CORRECTAMENTE")

    except Exception as e:
        print("🔥 ERROR FATAL EN EL ARRANQUE:")
        traceback.print_exc()

class SearchRequest(BaseModel):
    question: str
    project: str = "HyC"

@app.post("/search")
def search(req: SearchRequest):
    print(f"🔍 SEARCH SOLICITADO: '{req.question}'")
    
    try:
        # 1. Chequeo de Salud
        if 'index' not in resources:
            error_msg = "⛔ El servidor arrancó pero NO CARGÓ el índice. Revisa los logs de inicio."
            print(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)

        # 2. Búsqueda
        model = resources['model']
        index = resources['index']
        metadata = resources['metadata']

        vector = model.encode([req.question])
        D, I = index.search(vector, 5)

        results = []
        for i, idx in enumerate(I[0]):
            if idx == -1: continue
            if idx >= len(metadata):
                print(f"⚠️ Índice {idx} fuera de rango en metadata (Len: {len(metadata)})")
                continue
                
            item = metadata[idx]
            results.append({
                "text": item.get('text', '')[:1000],
                "document": item.get('document', 'Doc'),
                "page": item.get('page', 0),
                "url": item.get('url', ''),
                "score": float(D[0][i])
            })
        
        print(f"✅ Búsqueda exitosa. Retornando {len(results)} resultados.")
        return {"results": results}

    except Exception as e:
        print("🔥 EXCEPCIÓN OCURRIDA DURANTE LA BÚSQUEDA:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")