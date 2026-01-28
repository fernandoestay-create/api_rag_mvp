import os
import faiss
import numpy as np
import pickle
from openai import OpenAI
from dotenv import load_dotenv
from pypdf import PdfReader

# --- CONFIGURACIÓN ---
# Carga las claves
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

# RUTAS (Según tu nueva estructura)
BASE_DIR = os.path.dirname(__file__)
CARPETA_PDFS = os.path.join(BASE_DIR, "docs")          # Tus PDFs originales
CARPETA_SALIDA = os.path.join(BASE_DIR, "Index_migración") # Tu nueva carpeta destino

def get_embedding_openai(text):
    # Usamos el modelo "small" que es barato y rápido
    text = text.replace("\n", " ")
    return client.embeddings.create(input=[text], model="text-embedding-3-small").data[0].embedding

def generar_indice_nuevo():
    print("🚀 INICIANDO GENERACIÓN V2 (Formato OpenAI)")
    
    # 1. Verificar carpetas
    if not os.path.exists(CARPETA_PDFS):
        print(f"❌ Error: No encuentro la carpeta 'docs' en: {CARPETA_PDFS}")
        return
    
    if not os.path.exists(CARPETA_SALIDA):
        os.makedirs(CARPETA_SALIDA)
        print(f"📂 Carpeta creada: {CARPETA_SALIDA}")

    # 2. Leer PDFs
    datos_procesados = []
    print(f"📖 Leyendo archivos desde: {CARPETA_PDFS}")

    for archivo in os.listdir(CARPETA_PDFS):
        if archivo.lower().endswith(".pdf"):
            ruta_pdf = os.path.join(CARPETA_PDFS, archivo)
            print(f"   Procesando: {archivo}...")
            
            try:
                reader = PdfReader(ruta_pdf)
                texto_total = ""
                for page in reader.pages:
                    texto_total += page.extract_text() + "\n"
                
                # Cortamos en trozos (chunks) de 1000 caracteres
                # Esto es importante para que la IA no se pierda
                tamano_chunk = 1000
                for i in range(0, len(texto_total), tamano_chunk):
                    chunk = texto_total[i : i + tamano_chunk]
                    if len(chunk) > 100: # Ignoramos trozos muy pequeños
                        datos_procesados.append({
                            "contenido": chunk,
                            "metadata": {"source": archivo}
                        })
            except Exception as e:
                print(f"   ⚠️ Error leyendo {archivo}: {e}")

    if not datos_procesados:
        print("❌ No se extrajo texto. Revisa tus PDFs.")
        return

    print(f"🧠 Generando Embeddings para {len(datos_procesados)} fragmentos (Conectando a OpenAI)...")
    
    # 3. Vectorizar con OpenAI
    lista_vectores = []
    for i, item in enumerate(datos_procesados):
        try:
            vector = get_embedding_openai(item['contenido'])
            lista_vectores.append(vector)
            if i % 5 == 0: print(f"   Progreso: {i}/{len(datos_procesados)}", end="\r")
        except Exception as e:
            print(f"   Error en vector {i}: {e}")

    # 4. Guardar en Index_migración
    print("\n💾 Guardando archivos finales...")
    
    # Guardar Metadata (Texto)
    ruta_pkl = os.path.join(CARPETA_SALIDA, "metadata.pkl")
    with open(ruta_pkl, "wb") as f:
        pickle.dump(datos_procesados, f)

    # Guardar Índice (Vectores)
    if lista_vectores:
        dimension = len(lista_vectores[0]) # 1536
        index = faiss.IndexFlatL2(dimension)
        index.add(np.array(lista_vectores))
        
        ruta_index = os.path.join(CARPETA_SALIDA, "faiss.index")
        faiss.write_index(index, ruta_index)
        
        print(f"✅ ¡ÉXITO! Archivos generados en: {CARPETA_SALIDA}")
        print("   - faiss.index (Vectores OpenAI)")
        print("   - metadata.pkl (Textos)")
    else:
        print("❌ Error crítico: No se generaron vectores.")

if __name__ == "__main__":
    generar_indice_nuevo()