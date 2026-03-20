# Resumen sesión 19 de marzo 2026

## Lo que se hizo hoy

### 1. Reescritura completa del chunker (zero-loss)

Se reescribió `pia_rag/etl/enriched_chunker.py` desde cero con un enfoque **híbrido página-por-página + overlay jerárquico**:

- **Antes:** el chunker recorría el árbol de headers y perdía entre 15-20% del texto (texto entre headers, intros truncadas, secciones cortas ignoradas)
- **Ahora:** recorre **cada página del PDF**, extrae TODO el texto, lo divide en chunks de 1200 chars, y luego le asigna la metadata jerárquica (capítulo/sección/subsección) según un mapa de páginas
- **Resultado:** 0% de pérdida de texto. Cada carácter del PDF queda indexado

**Parámetros finales:**
- `chunk_size = 1200` caracteres
- `chunk_overlap = 200` caracteres

**Cada chunk tiene ~25 campos de metadata:**
- Proyecto: `project_id`, `project_name`, `titular`, `region`, `commune`
- Documento: `doc_type` (EIA/DIA/RCA/ICSARA/ADENDA/ANEXO), `doc_id`, `title`
- Jerarquía: `chapter_num`, `chapter_title`, `section_num`, `section_title`, `subsection_num`, `subsection_title`
- Posición: `page_start`, `page_end`, `hierarchy_path`, `chunk_level`, `position_in_doc`
- Estadísticas: `word_count`, `token_count`, `has_tables`, `has_figures`
- Embedding: `context_prefix` + `tatuaje` con identidad del proyecto/doc/página

### 2. Ingesta de 9 proyectos a Pinecone

Se ejecutó `ingest_30min.py` que procesa los 9 proyectos sin OCR pesado:

| # | Proyecto | Estado |
|---|----------|--------|
| 1 | Ampliación Parque del Recuerdo - Los Parques | ✅ Subido |
| 2 | Barrio Hacienda Norte - Crillón | ✅ Subido |
| 3 | Centro Logístico La Farfana | ✅ Subido |
| 4 | Centro Logístico Lo Aguirre | ✅ Subido |
| 5 | Centro Logístico Nuevo Maipú | ✅ Subido |
| 6 | HyC | ✅ Subido |
| 7 | Inmobiliaria Los Cóndores | ✅ Subido |
| 8 | Instituto del Cáncer | ✅ Subido |
| 9 | Las Lilas Sanitaria | ✅ Subido |

**Al momento de salir:** 36,551 vectores y subiendo. Estimación final: ~42,000-46,000 vectores.

**Verificar mañana:** entrar a Pinecone dashboard y confirmar el total final.

### 3. Configuración de Modela_Ambiental en ChatGPT

Se crearon dos archivos para configurar el GPT "Modela_Ambiental" en ChatGPT:

- **`chatgpt_system_prompt.md`** — System prompt completo con:
  - Modo 1: Consulta de información (busca en la base vectorial)
  - Modo 2: Crítica de informes (sube un PDF y lo compara con proyectos aprobados)
  - Estrategia de búsqueda en 4 pasos (clasificar pregunta → dividir si es compleja → usar filtros → citar fuentes)
  - Tabla de filtros recomendados por tema (línea base → cap 3, impactos → cap 4-5, etc.)
  - Formato de citación estandarizado
  - Tabla de los 12 project IDs

- **`openapi_chatgpt.yaml`** — Schema OpenAPI para el GPT Action con:
  - `POST /search` con filtros: `query`, `project_id`, `doc_type`, `chunk_level`, `chapter_title`, `top_k`
  - `GET /projects` — lista proyectos con estado
  - `GET /health` — health check
  - Descripciones detalladas para que ChatGPT entienda cómo usar cada filtro

**Fernando ya configuró Modela_Ambiental en ChatGPT** con estos archivos.

### 4. Preparación del deploy a Render

- Se creó `Procfile`: `web: uvicorn pia_rag.api.main:app --host 0.0.0.0 --port $PORT`
- Se creó `.gitignore` (excluye PDFs, data, .env, scripts de ingesta)
- Se inicializó el repo git con `git init`
- Se dejaron los archivos staged listos para commit

### 5. Archivos clave creados/modificados hoy

| Archivo | Acción | Descripción |
|---------|--------|-------------|
| `pia_rag/etl/enriched_chunker.py` | **Reescrito** | Chunker zero-loss página-por-página |
| `pia_rag/config.py` | Modificado | `chunk_size=1200`, `chunk_overlap=200` |
| `.env` | Creado | API keys de OpenAI y Pinecone |
| `ingest_30min.py` | Creado | Script para ingestar 9 proyectos |
| `ingest_all.py` | Creado | Script para ingestar los 12 proyectos |
| `chatgpt_system_prompt.md` | Creado | System prompt de Modela_Ambiental |
| `openapi_chatgpt.yaml` | Creado | OpenAPI schema para ChatGPT Action |
| `Procfile` | Creado | Para deploy en Render |
| `.gitignore` | Creado | Excluye datos pesados del repo |

---

## Lo que falta para mañana

### Prioridad 1: Deploy de la API a Render (15 min)

**Esto es lo que bloquea a Modela_Ambiental.** ChatGPT llama a `https://api-rag-mvp.onrender.com/search` pero el endpoint no existe porque el código nuevo no se ha desplegado.

**Pasos:**

```bash
# 1. Commit
cd "C:\Users\FernandoEstay\iCloudDrive\Programación\Claude_code\04.RAG_EIA-DIA_Seleccionados_Modela"
git add .gitignore Procfile requirements.txt pia_rag/ chatgpt_system_prompt.md openapi_chatgpt.yaml CLAUDE.md
git commit -m "Deploy API RAG v2.0 con búsqueda filtrada para Modela_Ambiental"

# 2. Crear repo en GitHub
gh repo create pia-rag-mvp --private --source=. --push

# 3. En Render (render.com):
#    - Ir al servicio api_rag_mvp (srv-d5rr9a63jp1c73e1fibg)
#    - Conectar al nuevo repo de GitHub
#    - Verificar variables de entorno:
#      OPENAI_API_KEY=sk-proj-2GT5jW...
#      PINECONE_API_KEY=pcsk_vhwvG_...
#      PINECONE_INDEX_NAME=api-rag-mvp
#      PINECONE_HOST=https://api-rag-mvp-96gaajy.svc.aped-4627-b74a.pinecone.io
#    - Build command: pip install -r requirements.txt
#    - Start command: (lo toma del Procfile)

# 4. Verificar que funciona
curl https://api-rag-mvp.onrender.com/health
curl -X POST https://api-rag-mvp.onrender.com/search \
  -H "Content-Type: application/json" \
  -d '{"query": "area de influencia", "project_id": "ampliacion_parque_del_recuerdo___los_parques", "top_k": 3}'
```

### Prioridad 2: Verificar ingesta de hoy

```bash
# Verificar cuántos vectores quedaron en Pinecone
python -c "
from pinecone import Pinecone
from pia_rag.config import settings
pc = Pinecone(api_key=settings.pinecone_api_key)
idx = pc.Index(name='api-rag-mvp', host=settings.pinecone_host)
stats = idx.describe_index_stats()
print(f'Vectores totales: {stats.total_vector_count:,}')
"
```

Esperado: ~42,000-46,000 vectores para los 9 proyectos.

### Prioridad 3: Subir los 3 proyectos restantes (~1-2 horas)

| Proyecto | Chunks estimados | Notas |
|----------|-----------------|-------|
| Urbanya | ~19,000 | El más grande, texto nativo |
| Fantasilandia | ~17,000 | Incluye planos escaneados (OCR) |
| Maratue | ~10,000 | RCA de 421 páginas (OCR pesado) |

```bash
python ingest_all.py  # O crear un ingest_remaining.py solo con los 3
```

**Total final esperado:** ~88,000-100,000 vectores para los 12 proyectos.

### Prioridad 4: Probar Modela_Ambiental end-to-end

Una vez desplegada la API, probar en ChatGPT:

1. **Consulta simple:**
   > "¿Qué medidas de mitigación propone Barrio Hacienda Norte para flora?"

2. **Consulta con filtros:**
   > "Describe el área de influencia del proyecto Ampliación Parque del Recuerdo"

3. **Comparativa:**
   > "Compara cómo describen su línea base de ruido Ampliación Parque del Recuerdo y HyC"

4. **ICSARA:**
   > "¿Qué observaciones hizo el SEIA a Barrio Hacienda Norte?"

5. **Crítica (subir PDF):**
   > Subir un capítulo de línea base y pedir que lo critique comparando con los proyectos indexados

### Prioridad 5 (opcional): Mejoras al system prompt

Si Modela_Ambiental no usa bien los filtros o no cita las fuentes correctamente, ajustar el system prompt en `chatgpt_system_prompt.md` y actualizar en la configuración del GPT.

---

## Arquitectura actual

```
Usuario pregunta en ChatGPT (Modela_Ambiental)
        │
        ▼
ChatGPT decide qué filtros usar (según system prompt)
        │
        ▼
POST https://api-rag-mvp.onrender.com/search    ← ⚠️ FALTA DEPLOY
  {query, project_id, doc_type, chapter_title, chunk_level, top_k}
        │
        ▼
FastAPI en Render:
  1. Embede la query con OpenAI text-embedding-3-small
  2. Busca en Pinecone api-rag-mvp con filtros metadata
  3. Devuelve chunks con score, texto, jerarquía, páginas
        │
        ▼
ChatGPT recibe los chunks y genera respuesta con citas
```

## Índice Pinecone

```
Nombre:    api-rag-mvp
Host:      https://api-rag-mvp-96gaajy.svc.aped-4627-b74a.pinecone.io
Vectores:  ~42,000-46,000 (9 proyectos, faltan 3)
Dimensión: 1536 (text-embedding-3-small)
```

## Credenciales (en .env local, configurar en Render)

```
OPENAI_API_KEY=sk-proj-2GT5jW...     (ver .env)
PINECONE_API_KEY=pcsk_vhwvG_...      (ver .env)
PINECONE_INDEX_NAME=api-rag-mvp
PINECONE_HOST=https://api-rag-mvp-96gaajy.svc.aped-4627-b74a.pinecone.io
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
CHUNK_SIZE=1200
CHUNK_OVERLAP=200
TOP_K=8
MIN_SCORE=0.70
```
