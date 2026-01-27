import os
import pickle
import faiss
import numpy as np
import traceback
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

app = FastAPI(title="API RAG Multi-Proyecto", version="4.0")

# --- CONFIGURACIÓN ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PATH_INDEX = os.path.join(BASE_DIR, "Index") # Carpeta raíz de índices

resources = {
    'model': None,
    'index': None,      # Aquí combinaremos todos los índices
    'metadata': []      # Aquí combinaremos toda la metadata
}

@app.on_event("startup")
def load_resources():
    print(f"🚀 INICIANDO CARGA MULTI-PROYECTO desde: {PATH_INDEX}")
    
    try:
        if not os.path.exists(PATH_INDEX):
            print(f"❌ ERROR: No existe la carpeta {PATH_INDEX}")
            return

        # 1. Cargar Modelo IA
        print("⏳ Cargando modelo IA...")
        resources['model'] = SentenceTransformer('all-MiniLM-L6-v2')
        
        # 2. Buscar carpetas de proyectos (HyC, Maratue, Urbanya, etc.)
        all_metadata = []
        combined_index = None
        
        subcarpetas = [d for d in os.listdir(PATH_INDEX) if os.path.isdir(os.path.join(PATH_INDEX, d))]
        print(f"📂 Proyectos detectados: {subcarpetas}")

        for proyecto in subcarpetas:
            ruta_proy = os.path.join(PATH_INDEX, proyecto)
            faiss_path = os.path.join(ruta_proy, "faiss.index")
            meta_path = os.path.join(ruta_proy, "metadata.pkl")

            if os.path.exists(faiss_path) and os.path.exists(meta_path):
                print(f"   👉 Cargando proyecto: {proyecto}")
                
                # Cargar índice parcial
                idx_part = faiss.read_index(faiss_path)
                
                # Cargar metadata parcial
                with open(meta_path, "rb") as f:
                    meta_part = pickle.load(f)
                    # Opcional: Agregar campo de proyecto si no viene
                    for m in meta_part:
                        if 'project' not in m: m['project'] = proyecto
                
                # FUSIÓN DE ÍNDICES FAISS
                if combined_index is None:
                    # Si es el primero, lo usamos base
                    combined_index = idx_part
                else:
                    # Si ya hay uno, le agregamos el nuevo (Merge)
                    combined_index.merge_from(idx_part, idx_part.ntotal)
                
                # FUSIÓN DE METADATA
                all_metadata.extend(meta_part)
                
            else:
                print(f"   ⚠️ Carpeta {proyecto} vacía o incompleta. Saltando.")

        # Guardar en recursos globales
        if combined_index and len(all_metadata) > 0:
            resources['index'] = combined_index
            resources['metadata'] = all_metadata
            print(f"✅ CARGA COMPLETA. Total documentos: {len(all_metadata)}")
        else:
            print("❌ NO SE CARGÓ NADA. Revisa tus carpetas.")

    except Exception as e:
        print("🔥 ERROR FATAL EN EL ARRANQUE:")
        traceback.print_exc()

class SearchRequest(BaseModel):
    question: str
    project: str = "General" # Ya no es obligatorio filtrar, busca en todo

@app.post("/search")
def search(req: SearchRequest):
    print(f"🔍 SEARCH: '{req.question}'")
    
    try:
        if not resources['index']:
            raise HTTPException(status_code=500, detail="El índice no está cargado.")

        model = resources['model']
        index = resources['index']
        metadata = resources['metadata']

        # Vectorizar y buscar
        vector = model.encode([req.question])
        # Buscamos más resultados (10) para tener variedad de proyectos
        D, I = index.search(vector, 10) 

        results = []
        for i, idx in enumerate(I[0]):
            if idx == -1 or idx >= len(metadata): continue
                
            item = metadata[idx]
            
            # FILTRO OPCIONAL: Si quisieras filtrar por proyecto en el futuro
            # if req.project != "General" and item.get('project') != req.project: continue

            results.append({
                "text": item.get('text', '')[:1000],
                "document": item.get('document', 'Doc'),
                "project": item.get('project', 'Varios'), # Importante saber de qué proyecto vino
                "page": item.get('page', 0),
                "score": float(D[0][i])
            })
        
        # Devolvemos los top 5 mejores después de filtrar
        return {"results": results[:6]}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))