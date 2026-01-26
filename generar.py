import os
from reader import load_project_documents
from chunker import chunk_pages
from vector_store import build_vector_store

# --- CONFIGURACIÓN ---
PROYECTO = "HyC"
# Asume que tus PDFs están en una carpeta "docs" y dentro "HyC"
RUTA_DOCS = os.path.join(os.path.dirname(__file__), "docs", PROYECTO)
# Aquí se guardará el cerebro
RUTA_SALIDA = os.path.join(os.path.dirname(__file__), "index", PROYECTO)

def generar_cerebro():
    print(f"🚀 INICIANDO GENERACIÓN PARA: {PROYECTO}")
    print(f"📂 Leyendo documentos