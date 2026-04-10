"""
build_gdrive_map.py — Construye mapeo filename → Google Drive URL para todos los PDFs.

Recorre recursivamente la carpeta Documento_proyectos en Google Drive,
obtiene el fileId de cada PDF, y guarda el mapeo en data/gdrive_map.json.

Estructura del resultado:
{
  "Ampliacion Parque del Recuerdo - Los Parques": {
    "Previsualización EIA.pdf": "https://drive.google.com/file/d/XXX/view",
    "subfolder/otro.pdf": "https://drive.google.com/file/d/YYY/view"
  },
  ...
}

Usa Google Drive API v3 con API key (carpeta pública) o service account.
Como fallback, recorre el filesystem local y busca por nombre.
"""

import sys
import os
import json
import time
import codecs
from pathlib import Path
from urllib.parse import quote

if sys.platform == "win32":
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

# Add project root
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))


# ─── Config ────────────────────────────────────────────────────────────────

ROOT_FOLDER_ID = "19ANyOghPtXc-sPqMbbYmObZN4-4NmTtn"  # Documento_proyectos
BASE_URL = "https://drive.google.com/file/d/{file_id}/view?usp=sharing"
OUTPUT_FILE = project_root / "data" / "gdrive_map.json"

# Local docs path (for matching)
LOCAL_DOCS = project_root / "00.InformaciónBase" / "Documento_proyectos"


def build_map_from_local():
    """
    Construye el mapeo recorriendo el filesystem local de Google Drive.

    Usa la utilidad de Google Drive for Desktop que almacena los fileIds
    en los atributos extendidos del archivo. Como fallback, construye
    el mapeo por ruta relativa (necesita completarse con fileIds después).
    """
    print(f"\n{'='*60}")
    print(f"BUILD GOOGLE DRIVE URL MAP")
    print(f"{'='*60}")
    print(f"Carpeta local: {LOCAL_DOCS}")
    print(f"Carpeta Drive: https://drive.google.com/drive/folders/{ROOT_FOLDER_ID}")

    if not LOCAL_DOCS.exists():
        print(f"ERROR: No se encuentra {LOCAL_DOCS}")
        return

    gdrive_map = {}
    total_files = 0

    for project_dir in sorted(LOCAL_DOCS.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue

        project_name = project_dir.name
        project_files = {}

        # Find all PDFs recursively
        pdfs = sorted(
            list(project_dir.rglob("*.pdf")) + list(project_dir.rglob("*.PDF")),
            key=lambda p: str(p)
        )

        for pdf in pdfs:
            try:
                if not pdf.exists() or os.path.getsize(str(pdf)) < 500:
                    continue
            except OSError:
                continue

            # Get relative path from project dir
            rel_path = pdf.relative_to(project_dir)
            filename = pdf.name

            # Key is just the filename (what the chunker uses)
            # If there are duplicates, use relative path
            if filename in project_files:
                key = str(rel_path).replace("\\", "/")
            else:
                key = filename

            # Placeholder URL - needs fileId
            project_files[key] = {
                "filename": filename,
                "relative_path": str(rel_path).replace("\\", "/"),
                "gdrive_url": "",  # To be filled
                "size_kb": round(os.path.getsize(str(pdf)) / 1024, 1),
            }
            total_files += 1

        gdrive_map[project_name] = project_files
        print(f"  {project_name}: {len(project_files)} PDFs")

    print(f"\nTotal: {total_files} PDFs mapeados")

    # Save
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(gdrive_map, f, ensure_ascii=False, indent=2)

    print(f"Mapeo guardado en: {OUTPUT_FILE}")
    print(f"\nSiguiente paso: ejecutar fill_gdrive_ids.py para completar las URLs")

    return gdrive_map


def try_google_drive_api():
    """
    Intenta usar la Google Drive API directamente para obtener fileIds.
    Requiere google-api-python-client instalado.
    """
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials

        # Try to use existing credentials from gcloud or Drive for Desktop
        # This is a best-effort approach
        print("Intentando usar Google Drive API...")

        # For a public folder, we can use API key
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            print("No se encontró GOOGLE_API_KEY. Usando modo local.")
            return None

        service = build("drive", "v3", developerKey=api_key)
        return service
    except ImportError:
        print("google-api-python-client no instalado. Usando modo local.")
        return None
    except Exception as e:
        print(f"Error con Google Drive API: {e}. Usando modo local.")
        return None


def fill_ids_from_api(service, gdrive_map):
    """
    Recorre Google Drive recursivamente y llena los fileIds en el mapeo.
    """
    def list_files_recursive(folder_id, path_prefix=""):
        """Lista todos los archivos en una carpeta recursivamente."""
        files = []
        page_token = None

        while True:
            query = f"'{folder_id}' in parents and trashed = false"
            response = service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageSize=1000,
                pageToken=page_token,
            ).execute()

            for item in response.get("files", []):
                if item["mimeType"] == "application/vnd.google-apps.folder":
                    # Recurse into subfolder
                    sub_path = f"{path_prefix}{item['name']}/" if path_prefix else f"{item['name']}/"
                    files.extend(list_files_recursive(item["id"], sub_path))
                elif item["name"].lower().endswith(".pdf"):
                    files.append({
                        "id": item["id"],
                        "name": item["name"],
                        "path": f"{path_prefix}{item['name']}",
                    })

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return files

    print("\nRecorriendo Google Drive recursivamente...")

    # List project folders
    query = f"'{ROOT_FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    response = service.files().list(q=query, fields="files(id, name)", pageSize=100).execute()

    for project_folder in response.get("files", []):
        project_name = project_folder["name"]
        if project_name not in gdrive_map:
            continue

        print(f"\n  Escaneando: {project_name}...")
        files = list_files_recursive(project_folder["id"])

        # Match files to local map
        matched = 0
        for file_info in files:
            for key, entry in gdrive_map[project_name].items():
                if entry["filename"] == file_info["name"] and not entry["gdrive_url"]:
                    entry["gdrive_url"] = BASE_URL.format(file_id=file_info["id"])
                    matched += 1
                    break

        print(f"    {matched}/{len(gdrive_map[project_name])} PDFs mapeados")

    return gdrive_map


def main():
    # Step 1: Build map from local filesystem
    gdrive_map = build_map_from_local()

    if not gdrive_map:
        return

    # Step 2: Try to fill IDs using Google Drive API
    service = try_google_drive_api()
    if service:
        gdrive_map = fill_ids_from_api(service, gdrive_map)

        # Save updated map
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(gdrive_map, f, ensure_ascii=False, indent=2)

        # Count how many have URLs
        total = 0
        with_url = 0
        for project_files in gdrive_map.values():
            for entry in project_files.values():
                total += 1
                if entry.get("gdrive_url"):
                    with_url += 1

        print(f"\n{'='*60}")
        print(f"RESULTADO: {with_url}/{total} PDFs con URL de Google Drive")
        print(f"Guardado en: {OUTPUT_FILE}")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print(f"MODO LOCAL: Mapeo creado sin URLs (falta GOOGLE_API_KEY)")
        print(f"Para completar las URLs, ejecuta con GOOGLE_API_KEY:")
        print(f"  set GOOGLE_API_KEY=tu_api_key")
        print(f"  python build_gdrive_map.py")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
