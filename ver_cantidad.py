import os
import pickle
from pathlib import Path

# Configuración de ruta
BASE_DIR = Path(__file__).parent
INDEX_DIR = BASE_DIR / "Index"

def contar_todo():
    print("\n📊 INFORME DE TAMAÑO DE PROYECTOS")
    print("=" * 50)
    print(f"{'PROYECTO (CARPETA)':<30} | {'CANTIDAD FRAGMENTOS'}")
    print("-" * 50)

    total_global = 0
    archivos_encontrados = []

    if not INDEX_DIR.exists():
        print(f"❌ Error: No existe la carpeta {INDEX_DIR}")
        return

    # Buscar archivos
    for root, dirs, files in os.walk(INDEX_DIR):
        for file in files:
            if file == "metadata.pkl":
                ruta_completa = os.path.join(root, file)
                nombre_carpeta = os.path.basename(root)
                
                try:
                    with open(ruta_completa, "rb") as f:
                        datos = pickle.load(f)
                        cantidad = len(datos)
                        total_global += cantidad
                        # Guardamos tupla para ordenar después
                        archivos_encontrados.append((cantidad, nombre_carpeta))
                except Exception as e:
                    print(f"⚠️ Error en {nombre_carpeta}: {e}")

    # Ordenar del más pequeño al más grande (Ideal para tu MVP)
    archivos_encontrados.sort() # Orden ascendente

    for cantidad, nombre in archivos_encontrados:
        emoji = "✅" if cantidad < 18000 else "🐢"
        print(f"{emoji} {nombre:<27} | {cantidad:,}".replace(",", "."))

    print("-" * 50)
    print(f"📚 TOTAL GLOBAL: {total_global:,} fragmentos".replace(",", "."))
    print("=" * 50)
    print("💡 SUGERENCIA MVP: Mueve fuera las carpetas marcadas con 🐢")

if __name__ == "__main__":
    contar_todo()
