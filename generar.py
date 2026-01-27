import os
import pickle
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from pypdf import PdfReader

# --- CONFIGURACIÓN ---
# Asume que tienes una carpeta "docs" al lado de este script
RUTA_DOCS = os.path.join(os.path.dirname(__file__), "docs") 

# GUARDAMOS EN LA MISMA RUTA QUE USA MAIN.PY
# Así no tienes que cambiar el servidor
RUTA_SALIDA = os.path.join(os.path.dirname(__file__), "Index", "HyC")

MODEL_NAME = 'all-MiniLM-L6-v2'

def generar_cerebro_maestro():
    print(f"🚀 INICIANDO GENERACIÓN MAESTRA")
    print(f"📂 Leyendo desde: {RUTA_DOCS}")
    
    # 1. Crear carpeta de salida si no existe
    if not os.path.exists(RUTA_SALIDA):
        os.makedirs(RUTA_SALIDA)

    datos = []
    
    # Recorremos todas las subcarpetas (los 4 proyectos)
    for root, dirs, files in os.walk(RUTA_DOCS):
        for file in files:
            if file.lower().endswith(".pdf"):
                ruta_completa = os.path.join(root, file)
                # El nombre del proyecto es la carpeta inmediata dentro de docs
                try:
                    rel_path = os.path.relpath(root, RUTA_DOCS)
                    proyecto = rel_path.split(os.sep)[0] 
                    if proyecto == ".": proyecto = "General"
                except:
                    proyecto = "Desconocido"

                print(f"   📖 Procesando: {file} | Proyecto: {proyecto}")
                
                try:
                    reader = PdfReader(ruta_completa)
                    for i, page in enumerate(reader.pages):
                        texto = page.extract_text()
                        if texto and len(texto) > 50:
                            datos.append({
                                "text": texto.replace('\n', ' ').strip(),
                                "document": file,
                                "page": i + 1,
                                "project": proyecto,
                                "url": f"file://{file}" 
                            })
                except Exception as e:
                    print(f"   ❌ Error en {file}: {e}")

    print(f"📚 Total fragmentos extraídos: {len(datos)}")
    
    if len(datos) == 0:
        print("⚠️ NO SE ENCONTRARON DATOS. Revisa tu carpeta 'docs'.")
        return

    # 2. Vectorizar
    print("🧠 Vectorizando con IA (Espere un momento)...")
    model = SentenceTransformer(MODEL_NAME)
    texts = [d['text'] for d in datos]
    embeddings = model.encode(texts, show_progress_bar=True)

    # 3. Guardar
    print("💾 Guardando archivos en Index/HyC...")
    index = faiss.IndexFlatL2(embeddings.shape[1])
    index.add(np.array(embeddings).astype('float32'))

    faiss.write_index(index, os.path.join(RUTA_SALIDA, "faiss.index"))
    with open(os.path.join(RUTA_SALIDA, "metadata.pkl"), "wb") as f:
        pickle.dump(datos, f)

    print("✅ ¡LISTO! Archivos faiss.index y metadata.pkl actualizados con TODOS los proyectos.")

if __name__ == "__main__":
    generar_cerebro_maestro()