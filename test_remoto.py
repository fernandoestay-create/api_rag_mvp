import requests
import time
import json

# ==========================================
# CONFIGURACIÓN
# ==========================================
# Pega aquí la URL que te dio Render (sin la barra final /)
URL_RENDER = "https://api-rag-hyc.onrender.com" 

# Endpoint de chat (la puerta de entrada)
url = f"{URL_RENDER}/chat"

# La pregunta de prueba (sobre tu proyecto HyC)
payload = {
    "question": "¿Cuáles son los principales impactos ambientales del proyecto?"
}

# ==========================================
# EJECUCIÓN DEL TEST
# ==========================================
print(f"📡 Conectando a: {url}")
print(f"❓ Preguntando: '{payload['question']}'")
print("⏳ Esperando respuesta del servidor en la nube... (esto puede tardar unos segundos)")

try:
    start_time = time.time()
    
    # Enviamos la petición POST
    response = requests.post(url, json=payload)
    
    end_time = time.time()
    duration = end_time - start_time

    # Verificamos si salió bien (Código 200 significa OK)
    if response.status_code == 200:
        data = response.json()
        
        print("\n" + "✅" * 20)
        print(" ¡ÉXITO! EL CEREBRO DIGITAL RESPONDIÓ")
        print("✅" * 20 + "\n")
        
        print(f"🤖 RESPUESTA:\n{data['respuesta']}\n")
        
        print("-" * 40)
        print("📚 FUENTES UTILIZADAS:")
        if data.get('fuentes_consultadas'):
            for fuente in data['fuentes_consultadas']:
                print(f"   📄 {fuente}")
        else:
            print("   (La IA respondió sin citar fuentes específicas)")
            
        print("-" * 40)
        print(f"⚡ Tiempo total: {duration:.2f} segundos")
        
    else:
        print("\n❌ ALGO FALLÓ EN EL SERVIDOR")
        print(f"Código de error: {response.status_code}")
        print(f"Detalle: {response.text}")

except Exception as e:
    print(f"\n❌ ERROR DE CONEXIÓN (Tu PC no pudo llegar a Render)")
    print(f"Detalle: {e}")