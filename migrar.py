import os
import pickle
import faiss
import numpy as np
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path

# --- 1. CONFIGURACIÓN ---
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("❌ ERROR CRÍTICO: No se encontró OPENAI_API_KEY")
    exit()

client = OpenAI(api_key=api_key)

# --- 2. RUTAS ---
BASE_DIR = Path(__file__).parent
CARPETA_ORIGEN = BASE_DIR / "Index"
# CAMBIO CLAVE: Usamos nombre sin tildes para evitar error en Windows
CARPETA_DESTINO = BASE_DIR / "Index_global"

# --- 3. FUNCIONES ---

def get_embedding_openai(text):
    text = text.replace("\n", " ")
    # Usamos text-embedding-3-small (rápido y barato)
    return client.embeddings.create(input=[text], model="text-embedding-3-small").data[0].embedding

def buscar_archivos_metadata(carpeta):
    """
    Busca archivos y extrae el nombre del proyecto basado en la carpeta contenedora.
    """
    archivos = []
    for root, dirs, files in os.walk(carpeta):
        for file in files:
            if file == "metadata.pkl":
                ruta_completa = os.path.join(root, file)
                nombre_carpeta = os.path.basename(root) # Capturamos "HyC" o "Maratue"
                archivos.append((ruta_completa, nombre_carpeta))
    return archivos

def ejecutar_migracion():
    print("🚀 INICIANDO MIGRACIÓN MVP (HyC + Maratue)")
    print(f"   Destino seguro: {CARPETA_DESTINO}")
    
    if not os.path.exists(CARPETA_DESTINO):
        os.makedirs(CARPETA_DESTINO)

    # 1. Identificar archivos
    lista_origen = buscar_archivos_metadata(CARPETA_ORIGEN)
    
    if not lista_origen:
        print(f"❌ ERROR: No encontré metadata.pkl en {CARPETA_ORIGEN}")
        return

    todos_los_items = []
    print(f"🔍 Cargando datos antiguos...")
    
    for ruta, nombre_proyecto in lista_origen:
        print(f"   📂 Procesando carpeta: '{nombre_proyecto}'")
        try:
            with open(ruta, "rb") as f:
                datos = pickle.load(f)
                
                # Pre-procesamos para asegurar que el proyecto viaje con el dato
                for item in datos:
                    if isinstance(item, dict):
                        item['proyecto_forzado'] = nombre_proyecto
                    else:
                        item = {'text': str(item), 'proyecto_forzado': nombre_proyecto}
                    todos_los_items.append(item)
                    
        except Exception as e:
            print(f"   ⚠️ Error leyendo {ruta}: {e}")

    total_fragmentos = len(todos_los_items)
    print(f"⚡ Total acumulado: {total_fragmentos} fragmentos")
    print("-" * 50)
    print("🧠 Generando vectores 'tatuados'...")

    nuevos_vectores = []
    nuevos_datos_limpios = []
    errores = 0

    # 2. Bucle Principal
    for i, item in enumerate(todos_los_items):
        # Extraer info base
        if isinstance(item, dict):
            texto_puro = item.get('text') or item.get('contenido') or ""
            source = item.get('document') or item.get('source') or "Desconocido"
            page = item.get('page') or 0
            proyecto = item.get('proyecto_forzado', 'General')
        else:
            texto_puro = str(item)
            source = "Texto plano"
            page = 0
            proyecto = "General"

        if texto_puro and len(texto_puro.strip()) > 10:
            try:
                # --- EL TATUAJE ---
                texto_para_vectorizar = f"Proyecto: {proyecto} \n Archivo: {source} \n Contenido: {texto_puro}"
                
                # Vectorizamos
                vector = get_embedding_openai(texto_para_vectorizar)
                nuevos_vectores.append(vector)
                
                # Guardamos los datos LIMPIOS
                nuevos_datos_limpios.append({
                    "contenido": texto_puro,
                    "metadata": {
                        "source": source,
                        "page": page,
                        "project": proyecto
                    }
                })
            except Exception as e:
                errores += 1
        
        # Barra de progreso simple
        if i % 10 == 0 or i == total_fragmentos - 1:
            print(f"   Procesando... {i + 1}/{total_fragmentos}", end="\r")

    print("\n" + "-" * 50)

    # 3. Guardado
    if nuevos_vectores:
        try:
            dimension = len(nuevos_vectores[0])
            index = faiss.IndexFlatL2(dimension)
            index.add(np.array(nuevos_vectores).astype('float32'))
            
            # Guardar FAISS (Ruta segura sin tildes)
            faiss.write_index(index, str(CARPETA_DESTINO / "faiss.index"))
            
            # Guardar Metadata
            with open(CARPETA_DESTINO / "metadata.pkl", "wb") as f:
                pickle.dump(nuevos_datos_limpios, f)

            print("✅ ¡MIGRACIÓN EXITOSA!")
            print(f"   - Index guardado en: {CARPETA_DESTINO}")
        except Exception as e:
            print(f"❌ ERROR FINAL GUARDANDO: {e}")
    else:
        print("❌ Falló: No se generaron vectores.")

if __name__ == "__main__":
    ejecutar_migracion()