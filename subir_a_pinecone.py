import os
import pickle
import time
import faiss
from pinecone import Pinecone

# ==========================================
# CONFIGURACIÓN (Tus datos actualizados)
# ==========================================

# ⚠️ OJO: Pega aquí la clave de PINECONE (la que está en el menú API Keys de Pinecone)
# NO pongas la de OpenAI (sk-proj...) porque fallará.
PINECONE_API_KEY = "pcsk_vhwvG_B7KVrMbwsy3Y9PFWXj6g5XBsQaDEijpJ1LVsz8Ao4Xhcw8mNjZhT1kEzWPyfVPL"

# El nombre exacto que me diste
INDEX_NAME = "api-rag-mvp"

# Rutas de tus archivos locales (donde está la "mochila" actual)
PATH_FAISS = "Index_global/faiss.index"
PATH_METADATA = "Index_global/metadata.pkl"

def migrar_datos():
    print(f"🚀 Iniciando migración al índice: {INDEX_NAME}...")
    
    # 1. CONEXIÓN A LA NUBE
    # ---------------------------------------------------------
    try:
        # Nos conectamos a Pinecone usando tu clave
        pc = Pinecone(api_key=PINECONE_API_KEY)
        
        # Verificamos si el índice existe en tu cuenta
        lista_indices = [i.name for i in pc.list_indexes()]
        if INDEX_NAME not in lista_indices:
            print(f"❌ Error: No encuentro el índice '{INDEX_NAME}'.")
            print(f"   Índices encontrados: {lista_indices}")
            return
            
        # Conectamos específicamente a tu índice
        index = pc.Index(INDEX_NAME)
        
        # Consultamos el estado actual (debería estar vacío o con 0 vectores)
        stats = index.describe_index_stats()
        print(f"✅ Conectado exitosamente. Estado actual: {stats}")
        
    except Exception as e:
        print(f"❌ Error de conexión: {e}")
        print("   (Verifica que la API KEY sea la de Pinecone y no la de OpenAI)")
        return

    # 2. LEER DATOS LOCALES (Abrir la caja vieja)
    # ---------------------------------------------------------
    print("📂 Leyendo archivos del disco duro...")
    try:
        # Leemos los vectores (la parte matemática)
        faiss_index = faiss.read_index(PATH_FAISS)
        total_vectors = faiss_index.ntotal
        
        # Leemos los textos (la parte humana)
        with open(PATH_METADATA, "rb") as f:
            metadata = pickle.load(f)

        print(f"✅ Archivos leídos correctamente. Total a subir: {total_vectors} registros.")
        
    except Exception as e:
        print(f"❌ Error leyendo archivos locales: {e}")
        return

    # 3. SUBIR A LA NUBE (La mudanza)
    # ---------------------------------------------------------
    # Subimos de 100 en 100 para asegurar que lleguen bien
    BATCH_SIZE = 100  
    
    print("\n⏳ COMENZANDO LA CARGA... Por favor no cierres esta ventana.")
    
    for i in range(0, total_vectors, BATCH_SIZE):
        batch = []
        # Calculamos dónde termina este lote
        fin = min(i + BATCH_SIZE, total_vectors)
        
        # Extraemos los vectores crudos de FAISS (del disco)
        vectores_crudos = faiss_index.reconstruct_n(i, fin - i)
        
        # Preparamos cada carta individualmente
        for k in range(len(vectores_crudos)):
            idx_real = i + k
            
            # Recuperamos el texto asociado
            texto_limpio = metadata[idx_real]["text"]
            # Recortamos preventivamente si es gigantesco (límite técnico de seguridad)
            texto_limpio = texto_limpio[:35000] 
            
            registro = {
                "id": str(idx_real),                 # ID único (0, 1, 2...)
                "values": vectores_crudos[k].tolist(), # El vector matemático
                "metadata": {"text": texto_limpio}     # El texto real
            }
            batch.append(registro)
        
        # Enviamos el camión con 100 registros a Pinecone
        try:
            index.upsert(vectors=batch)
            
            # Mostramos el progreso visualmente
            porcentaje = (fin / total_vectors) * 100
            print(f"Subiendo... {porcentaje:.1f}% completado ({fin}/{total_vectors})", end="\r")
            
        except Exception as e:
            print(f"\n⚠️ Error subiendo el lote {i}: {e}")
            time.sleep(2) # Si falla, esperamos 2 segundos y seguimos

    print("\n\n🎉 ¡MIGRACIÓN COMPLETADA! Tus datos ya están seguros en Pinecone.")
    print("   Ahora tu servidor Render será liviano y rápido.")

if __name__ == "__main__":
    migrar_datos()