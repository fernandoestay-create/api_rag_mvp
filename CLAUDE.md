# PIA RAG — Instrucciones para Claude Code

## Estado actual de la infraestructura

> **Todo esto ya está desplegado y funcionando en producción.**
> El trabajo pendiente es: mejorar la ingesta jerárquica y afinar cómo ChatGPT busca y razona.

### Pinecone — índice vectorial (LIVE)

```
Índice:  api-rag-mvp
Host:    https://api-rag-mvp-96gaajy.svc.aped-4627-b74a.pinecone.io
```

El índice ya existe. **No crear uno nuevo.** Todas las operaciones de upsert y query
apuntan a este índice. Variables de entorno requeridas:

```env
PINECONE_INDEX_NAME=api-rag-mvp
PINECONE_HOST=https://api-rag-mvp-96gaajy.svc.aped-4627-b74a.pinecone.io
```

Al inicializar el cliente, conectar por host directo (más rápido que resolver por nombre):

```python
from pinecone import Pinecone

pc    = Pinecone(api_key=settings.pinecone_api_key)
index = pc.Index(
    name="api-rag-mvp",
    host="https://api-rag-mvp-96gaajy.svc.aped-4627-b74a.pinecone.io",
)
```

### API en Render — servicio FastAPI (LIVE)

```
Nombre:     api_rag_mvp
Service ID: srv-d5rr9a63jp1c73e1fibg
```

La API FastAPI ya está corriendo en Render. Render hace redeploy automático
al hacer push a la rama principal. Las variables de entorno ya están configuradas
en Render — no modificarlas desde el código local.

### ChatGPT — integración activa (LIVE)

ChatGPT ya está conectado a la API en Render vía un GPT Action. El flujo actual:

```
Usuario pregunta en ChatGPT
        │
        ▼
ChatGPT llama a la API en Render  (Action ya configurado)
        │
        ▼
API embebe la pregunta → busca en Pinecone api-rag-mvp → devuelve chunks
        │
        ▼
ChatGPT recibe los chunks y genera la respuesta
```

Lo que hay que mejorar es la **capa de razonamiento y búsqueda** de ChatGPT:
cómo descompone preguntas complejas, qué filtros aplica, y el system prompt
que guía su comportamiento. Ver sección **"ChatGPT: base de conocimiento"** al final.

---

## Contexto del proyecto

Sistema RAG sobre expedientes de evaluación ambiental chilenos.
Permite consultar proyectos específicos con precisión semántica, respetando la
estructura jerárquica de los documentos (capítulo → sección → subsección → párrafo).

**Stack completo:**
- Python 3.11+
- OpenAI (`text-embedding-3-small` para embeddings, `gpt-4o` para generación)
- Pinecone serverless (índice `api-rag-mvp`)
- PyMuPDF (parsing PDF + detección de jerarquía)
- FastAPI desplegado en Render (`api_rag_mvp`)
- ChatGPT con GPT Action conectado al endpoint de Render

---

## Estructura del repositorio

```
pia_rag/
├── CLAUDE.md                        ← este archivo
├── config.py                        ← configuración centralizada (pydantic-settings)
├── main.py                          ← CLI principal
├── requirements.txt
├── .env.example
│
├── data/
│   ├── projects/                    ← BASE DE DATOS PRE-DESCARGADA
│   │   ├── proyecto_batuco_eia/     ← un proyecto = una carpeta
│   │   │   ├── cap_01_descripcion.pdf
│   │   │   ├── cap_02_linea_base.pdf
│   │   │   ├── cap_03_impactos.pdf
│   │   │   ├── cap_04_medidas.pdf
│   │   │   ├── anexo_flora.pdf
│   │   │   ├── rca.pdf
│   │   │   └── project.json         ← metadata del proyecto (ver formato abajo)
│   │   ├── proyecto_solar_atacama/
│   │   │   └── ...
│   │   └── ...
│   ├── raw/                         ← PDFs descargados por scrapers
│   ├── processed/                   ← state.json por proyecto (cache de estado)
│   └── logs/
│       ├── extraction/
│       │   ├── proyecto_batuco_eia.log   ← log humano por proyecto
│       │   └── extraction_summary.jsonl  ← resumen global append-only
│       └── indexing/
│
├── etl/
│   ├── document_parser.py           ← árbol jerárquico desde PDF (PyMuPDF)
│   ├── metadata_enricher.py         ← enriquece metadata desde API SEIA
│   ├── enriched_chunker.py          ← chunks a 4 niveles + ~25 campos metadata
│   ├── enriched_pipeline.py         ← orquesta todo el ETL
│   └── extraction_logger.py         ← LOG de extracción (ver sección abajo)
│
├── scrapers/
│   ├── base_scraper.py
│   ├── seia_scraper.py
│   └── other_scrapers.py
│
├── storage/
│   └── pinecone_client.py           ← siempre conectar al host directo de api-rag-mvp
│
├── rag/
│   ├── enriched_engine.py           ← RAG con filtros jerárquicos
│   └── engine.py                    ← RAG base
│
└── api/
    └── main.py                      ← FastAPI — desplegado en Render api_rag_mvp
```

---

## Formato de `project.json`

Cada carpeta en `data/projects/` tiene un `project.json` con la metadata conocida.
Los campos vacíos los intenta completar `SEIAMetadataEnricher` via API del SEIA.
**`project.json` siempre tiene prioridad sobre lo que devuelva el API.**

```json
{
  "project_id":        "batuco_eia_2026",
  "expedition_id":     "2400123",
  "project_name":      "Proyecto Ecociudad Batuco",
  "instrument_type":   "EIA",
  "project_type":      "Proyectos de desarrollo inmobiliario",
  "titular":           "MODELA SpA",
  "region":            "Metropolitana de Santiago",
  "commune":           "Lampa",
  "evaluation_status": "En calificación",
  "ingreso_date":      "2026-04-01",
  "rca_number":        "",
  "rca_date":          "",
  "coordinates_lat":   -33.28,
  "coordinates_lon":   -70.88,
  "surface_ha":        1240,
  "investment_musd":   850,
  "description":       "Desarrollo urbano integral en la cuenca de Batuco",
  "source":            "seia",
  "notes":             "Capítulos organizados por archivo separado"
}
```

**Convención de nombres de archivos dentro del proyecto:**

| Prefijo      | Contenido esperado                               |
|--------------|--------------------------------------------------|
| `cap_01_`    | Descripción del proyecto                         |
| `cap_02_`    | Línea base ambiental                             |
| `cap_03_`    | Predicción y evaluación de impactos              |
| `cap_04_`    | Medidas de mitigación, compensación y reparación |
| `cap_05_`    | Plan de seguimiento                              |
| `cap_06_`    | Plan de contingencias                            |
| `cap_07_`    | Plan de abandono                                 |
| `cap_08_`    | Compromisos ambientales voluntarios              |
| `anexo_`     | Anexos temáticos (flora, fauna, aire, etc.)      |
| `rca`        | Resolución de calificación ambiental             |
| `icsara_`    | Informes consolidados de aclaraciones            |
| `adenda_`    | Adendas y adendas complementarias                |

---

## Sistema de LOG de extracción

### Por qué existe

Un proyecto tiene 10–30 PDFs. El procesamiento puede fallar parcialmente:
- PDF escaneado sin OCR disponible
- Archivo corrupto o truncado
- Estructura sin headers numerados (no detecta jerarquía)
- Timeout en la API del SEIA
- Error de red con Pinecone

El log permite saber exactamente qué se procesó, qué falló y cuántos chunks generó
cada archivo, y retomar desde donde quedó **sin reprocesar lo que ya está indexado**.

### Los 3 destinos de log

#### 1. Log humano por proyecto: `data/logs/extraction/{project_id}.log`

Muestra progreso en tiempo real, calidad de extracción por archivo, y detalle página a página
cuando hay problemas. Los archivos con OCR muestran exactamente qué páginas se pudieron leer.

```
2026-03-18 10:23:01 | INFO     | ============================================================
2026-03-18 10:23:01 | INFO     | Iniciando extracción: proyecto_batuco_eia
2026-03-18 10:23:01 | INFO     | PDFs encontrados: 12  |  Pinecone: api-rag-mvp
2026-03-18 10:23:01 | INFO     | ============================================================

2026-03-18 10:23:02 | SUCCESS  | cap_01_descripcion.pdf → 3 cap, 18 sec, 9 sub | 47 chunks | avg 312 tok
2026-03-18 10:23:02 | INFO     |   cap_01_descripcion.pdf | 45 págs | 45 OK / 0 vacías / 0 errores | 58,320 chars | calidad: excellent

2026-03-18 10:23:08 | SUCCESS  | cap_02_linea_base.pdf  → 8 cap, 43 sec, 21 sub | 201 chunks | avg 489 tok
2026-03-18 10:23:08 | INFO     |   cap_02_linea_base.pdf | 120 págs | 117 OK / 2 vacías / 1 errores | 142,880 chars | calidad: good

2026-03-18 10:23:09 | WARNING  | cap_03_impactos.pdf → PDF escaneado, activando OCR
2026-03-18 10:24:15 | SUCCESS  | cap_03_impactos.pdf  → 5 cap, 28 sec, 14 sub | 134 chunks | avg 401 tok
2026-03-18 10:24:15 | INFO     |   cap_03_impactos.pdf | 88 págs | 71 OK / 12 vacías / 0 errores | 89,240 chars | calidad: good [OCR: 17 págs]
2026-03-18 10:24:15 | INFO     |   Calidad de extracción página a página:
2026-03-18 10:24:15 | INFO     |     Pág  Método        Chars  Calidad     Notas
2026-03-18 10:24:15 | INFO     |     -------------------------------------------------------
2026-03-18 10:24:15 | DEBUG    |       1  pymupdf        1240  good
2026-03-18 10:24:15 | DEBUG    |       2  pymupdf         980  good
2026-03-18 10:24:15 | DEBUG    |       3  pymupdf        1102  good
2026-03-18 10:24:15 | WARNING  |    >> 14  tesseract        82  partial     texto escaso
2026-03-18 10:24:15 | WARNING  |    >> 15  tesseract         0  empty       sin texto detectado
2026-03-18 10:24:15 | WARNING  |    >> 16  tesseract         0  empty       sin texto detectado
2026-03-18 10:24:15 | WARNING  |    >> 31  tesseract       234  partial     texto escaso
2026-03-18 10:24:15 | DEBUG    |      32  tesseract        890  good        OCR activado
2026-03-18 10:24:15 | INFO     |   ... y 66 páginas adicionales OK
2026-03-18 10:24:15 | INFO     |   OCR aplicado en páginas: 14, 15, 16, 31, 32, 33, 47, 48, 49, 62, 63, 64, 71, 72, 73, 74, 75
2026-03-18 10:24:15 | WARNING  |   AVISO: OCR parcial en cap_03_impactos.pdf. 12 páginas quedaron sin texto.
                                          Puede haber imágenes de baja resolución o tablas complejas.

2026-03-18 10:24:16 | ERROR    | anexo_hidrogeologia.pdf → PyMuPDF: file truncated at byte 48203
2026-03-18 10:24:16 | INFO     |   anexo_hidrogeologia.pdf | 0 págs | 0 OK / 0 vacías / 0 errores | 0 chars | calidad: unreadable
2026-03-18 10:24:16 | WARNING  |   ALERTA: anexo_hidrogeologia.pdf tiene calidad 'unreadable'.
                                          Solo se leyó el 0% del contenido. Verificar si el PDF
                                          tiene restricciones o está muy deteriorado.
2026-03-18 10:24:16 | DEBUG    | SKIP anexo_hidrogeologia.pdf — marcado FAILED, continúa con siguiente

2026-03-18 10:24:45 | INFO     | Embeddings: 382/382 válidos
2026-03-18 10:24:52 | SUCCESS  | Pinecone api-rag-mvp: 382 vectors upserted
2026-03-18 10:24:52 | INFO     | ============================================================
2026-03-18 10:24:52 | WARNING  | SUMMARY proyecto_batuco_eia → 11/12 OK | 382 chunks | 1 error | 111s
2026-03-18 10:24:52 | WARNING  |   FAILED: anexo_hidrogeologia.pdf — file truncated at byte 48203

2026-03-18 10:24:52 | INFO     |   Calidad de extracción por archivo:
2026-03-18 10:24:52 | INFO     |   Archivo                                   Calidad       Leído  OCR págs
2026-03-18 10:24:52 | INFO     |   ------------------------------------------------------------------------
2026-03-18 10:24:52 | INFO     |   cap_01_descripcion.pdf                    excellent      100%         0
2026-03-18 10:24:52 | INFO     |   cap_02_linea_base.pdf                     good            98%         0
2026-03-18 10:24:52 | INFO     |   cap_03_impactos.pdf                       good            81%        17
2026-03-18 10:24:52 | INFO     |   cap_04_medidas.pdf                        excellent      100%         0
2026-03-18 10:24:52 | INFO     |   cap_05_seguimiento.pdf                    excellent       99%         0
2026-03-18 10:24:52 | INFO     |   anexo_flora.pdf                           partial         62%        31
2026-03-18 10:24:52 | INFO     |   anexo_fauna.pdf                           good            88%        12
2026-03-18 10:24:52 | INFO     |   rca.pdf                                   excellent      100%         0
2026-03-18 10:24:52 | INFO     |   icsara_01.pdf                             excellent      100%         0
2026-03-18 10:24:52 | INFO     |   adenda_01.pdf                             excellent      100%         0
2026-03-18 10:24:52 | INFO     |   adenda_02.pdf                             good            94%         3
2026-03-18 10:24:52 | INFO     |   anexo_hidrogeologia.pdf                   unreadable       0%         0  [FAILED]
2026-03-18 10:24:52 | INFO     | ============================================================
```

**Niveles de calidad de extracción:**

| Label        | Significado                                        | Acción recomendada                          |
|--------------|----------------------------------------------------|---------------------------------------------|
| `excellent`  | ≥ 90% de páginas con texto                        | Ninguna                                     |
| `good`       | 70–89% de páginas con texto                       | Revisar páginas vacías si son críticas      |
| `partial`    | 40–69% de páginas con texto                       | Verificar calidad del PDF original          |
| `poor`       | 1–39% de páginas con texto                        | Obtener PDF de mayor calidad                |
| `unreadable` | 0% de páginas con texto                           | PDF corrupto, protegido o imagen pura       |

**Qué muestra el log de páginas:**

- `>>` al inicio de línea = página con problema (partial, empty o failed)
- Páginas `good` solo se muestran las primeras 5 para no saturar el log
- Columna `Método`: `pymupdf` (texto nativo), `tesseract` (OCR), `text_direct` (texto ya extraído), `none` (no se intentó)
- La lista de páginas OCR permite identificar exactamente dónde están las imágenes escaneadas

#### 2. Resumen global: `data/logs/extraction/extraction_summary.jsonl`

Append-only. Una línea JSON por evento. Permite auditar todos los proyectos:

```jsonl
{"ts":"2026-03-18T10:24:52","project_id":"proyecto_batuco_eia","event":"project_complete","pdfs_total":12,"pdfs_ok":11,"pdfs_failed":1,"chunks":382,"vectors_indexed":382,"pinecone_index":"api-rag-mvp","duration_s":111.3}
{"ts":"2026-03-18T10:25:01","project_id":"proyecto_batuco_eia","event":"file_error","file":"anexo_hidrogeologia.pdf","error":"file truncated at byte 48203","ocr_attempted":false}
{"ts":"2026-03-18T10:23:02","project_id":"proyecto_batuco_eia","event":"file_ok","file":"cap_01_descripcion.pdf","chapters":3,"sections":18,"subsections":9,"chunks":47,"tokens_avg":312}
```

#### 3. Estado por proyecto: `data/processed/{project_id}/state.json`

Permite `--resume` y `--retry-failed` sin tocar lo ya indexado.
Ahora incluye métricas de calidad OCR por archivo:

```json
{
  "project_id":     "proyecto_batuco_eia",
  "status":         "partial",
  "last_updated":   "2026-03-18T10:24:52",
  "pinecone_index": "api-rag-mvp",
  "files": {
    "cap_01_descripcion.pdf":  {
      "status": "indexed", "chunks": 47, "indexed_at": "2026-03-18T10:23:04",
      "total_pages": 42, "pages_pymupdf": 42, "pages_ocr": 0,
      "pages_failed": 0, "ocr_triggered": false, "extraction_rate": 100.0
    },
    "cap_03_impactos.pdf": {
      "status": "indexed", "chunks": 134, "indexed_at": "2026-03-18T10:26:44",
      "total_pages": 88, "pages_pymupdf": 0, "pages_ocr": 76,
      "pages_failed": 12, "ocr_triggered": true, "extraction_rate": 86.4
    },
    "anexo_hidrogeologia.pdf": {
      "status": "failed", "chunks": 0, "failed_at": "2026-03-18T10:24:16",
      "error": "PyMuPDF: file truncated at byte 48203", "ocr_attempted": false
    }
  },
  "total_chunks":  382,
  "total_indexed": 382
}
```

#### 4. Detalle OCR página a página: `data/logs/extraction/{project_id}_ocr.jsonl`

Solo se genera si un archivo usó OCR o tuvo páginas con errores.
Una línea de cabecera por archivo + una línea por cada página problemática.
Las páginas con texto directo sin errores **no se escriben** (no inflan el log).

```jsonl
{"type":"file_summary","file":"cap_03_impactos.pdf","project_id":"proyecto_batuco_eia","total_pages":88,"pages_pymupdf":0,"pages_ocr":76,"pages_failed":12,"ocr_avg_conf":79.3,"extraction_rate":86.4,"quality":"BUENA","ts":"2026-03-18T10:26:44Z"}
{"type":"page_detail","file":"cap_03_impactos.pdf","project_id":"proyecto_batuco_eia","page":14,"method":"ocr","chars":82,"lines":4,"confidence":52.1,"duration_ms":1380,"warnings":["baja_confianza"]}
{"type":"page_detail","file":"cap_03_impactos.pdf","project_id":"proyecto_batuco_eia","page":15,"method":"ocr","chars":0,"lines":0,"confidence":null,"duration_ms":30000,"error":"tesseract timeout after 30s"}
{"type":"page_detail","file":"cap_03_impactos.pdf","project_id":"proyecto_batuco_eia","page":16,"method":"ocr","chars":0,"lines":0,"confidence":null,"duration_ms":890,"error":"image too dark, binarization failed"}
{"type":"page_detail","file":"cap_03_impactos.pdf","project_id":"proyecto_batuco_eia","page":31,"method":"ocr","chars":234,"lines":11,"confidence":61.4,"duration_ms":1210,"warnings":["baja_confianza"]}
```

Para ver el reporte OCR en consola:

```python
from etl.extraction_logger import ExtractionLogger

log = ExtractionLogger("proyecto_batuco_eia")
log.print_ocr_report()
```

Salida:

```
==============================================================
REPORTE OCR — proyecto_batuco_eia
==============================================================

  Archivo: cap_03_impactos.pdf
  Paginas OCR:      76/88
  Paginas fallidas: 12
  Conf. promedio:   79%
  Calidad:          BUENA (86.4%)

    Pag   14:  ocr        chars=   82  conf=  52%  1380ms  [baja_confianza]
    Pag   15:  ocr        chars=    0  conf=   —  30000ms  ERROR: tesseract timeout after 30s
    Pag   16:  ocr        chars=    0  conf=   —    890ms  ERROR: image too dark, binarization failed
    Pag   31:  ocr        chars=  234  conf=  61%  1210ms  [baja_confianza]
==============================================================
```

---

## Módulos a implementar / modificar

### Nuevo: `etl/extraction_logger.py`

**Ya implementado.** Interfaz pública:

```python
from etl.extraction_logger import ExtractionLogger, FileExtractionResult, PageOCRResult

log = ExtractionLogger(project_id="proyecto_batuco_eia")
log.start_project(total_files=12)

# Resultado por archivo (se construye durante el procesamiento)
result = FileExtractionResult(
    filename="cap_03_impactos.pdf",
    project_id="proyecto_batuco_eia",
    status="indexed",
    total_pages=88,
    pages_pymupdf=0,
    pages_ocr=76,
    pages_failed=12,
    chapters=5, sections=28, subsections=14,
    chunks=134, tokens_avg=401.0, chars_total=89240,
    ocr_triggered=True,
    ocr_avg_confidence=79.3,
    duration_s=219.4,
    page_results=[...],  # lista de PageOCRResult
)

# Llamar durante OCR en tiempo real (por página)
log.ocr_page_start("cap_03.pdf", page=14, total=88)
log.ocr_page_result("cap_03.pdf", PageOCRResult(
    page=14, method="ocr", chars_extracted=82, lines_extracted=4,
    avg_confidence=52.1, warnings=["baja_confianza"], duration_ms=1380,
))

# Al terminar el archivo
log.file_ok("cap_03.pdf", result)

# Para archivos que fallan
log.file_error("anexo_hidro.pdf", error="file truncated at byte 48203", ocr_attempted=False)

# Consultas de estado
log.is_already_indexed("cap_01.pdf")   # → True
log.get_failed_files()                  # → ["anexo_hidro.pdf"]
log.get_project_status()               # → {"status": "partial", "ok": 11, "failed": 1}
log.print_ocr_report()                 # imprime tabla OCR en consola
```

### Modificar: `etl/enriched_pipeline.py`

```python
pipeline = EnrichedETLPipeline()

# Proyecto completo desde carpeta
stats = pipeline.process_project(
    project_dir=Path("data/projects/proyecto_batuco_eia"),
    resume=True,         # omite archivos con status "indexed" en state.json
    retry_failed=False,  # no reintenta archivos con status "failed"
)

# PDF individual (debug / reproceso manual)
chunks = pipeline.process_pdf_direct(
    pdf_path=Path("data/projects/proyecto_batuco_eia/cap_02_linea_base.pdf"),
    project_json=Path("data/projects/proyecto_batuco_eia/project.json"),
)
```

### Modificar: `storage/pinecone_client.py`

Usar siempre el host directo de producción:

```python
index = pc.Index(
    name="api-rag-mvp",
    host="https://api-rag-mvp-96gaajy.svc.aped-4627-b74a.pinecone.io",
)
```

### Modificar: `main.py` (CLI)

```bash
python main.py ingest --project data/projects/proyecto_batuco_eia
python main.py ingest --all
python main.py ingest --project data/projects/proyecto_batuco_eia --resume
python main.py ingest --project data/projects/proyecto_batuco_eia --retry-failed
python main.py status
python main.py status --project proyecto_batuco_eia
python main.py query "medidas de mitigación para flora" --project proyecto_batuco_eia
```

Salida de `python main.py status`:

```
Proyectos en data/projects/:
  proyecto_batuco_eia       partial   11/12 PDFs  382 chunks   1 error
  proyecto_solar_atacama    complete  18/18 PDFs  891 chunks   0 errores
  proyecto_ruta_5_norte     pending    0/8  PDFs    0 chunks   —

Pinecone api-rag-mvp:  1273 vectors totales
Render api_rag_mvp:    srv-d5rr9a63jp1c73e1fibg
```

### Modificar: `api/main.py`

Agregar para que Render pueda recibir trabajos de ingesta y ChatGPT pueda buscar con filtros:

```
POST /search          búsqueda con filtros opcionales (ver abajo)
POST /ingest/project  body: {"project_id": "...", "resume": true}
GET  /projects        lista todos los proyectos con estado (lee state.json)
GET  /projects/{id}   estado detallado de un proyecto
GET  /health          {"status":"ok","pinecone":"api-rag-mvp","vectors":1273}
```

---

## Pipeline de extracción por proyecto (flujo completo)

```
data/projects/{project_id}/
    project.json  +  cap_01.pdf, cap_02.pdf, ...
        │
        ▼
ExtractionLogger.start_project(total_files=N)
        │
        ▼  por cada PDF, ordenado por nombre
┌───────────────────────────────────────────────┐
│  state.json dice "indexed"?                   │
│    SÍ  → logger.file_skipped() → siguiente   │
│    NO  → procesar                             │
└───────────────────────────────────────────────┘
        │
        ▼
DocumentStructureParser.parse(pdf)
  → árbol: capítulos, secciones, subsecciones + página de cada bloque
  → si < 100 chars/página: activar OCR con pytesseract
  → si falla: logger.file_error() → siguiente PDF  ← nunca abortar el proyecto
        │
        ▼
SEIAMetadataEnricher.enrich(project.json)
  → consulta API SEIA por expedition_id
  → fusiona: project.json tiene prioridad sobre API
  → si falla API: continúa con project.json solamente
        │
        ▼
EnrichedHierarchicalChunker.chunk(structure, meta)
  → Nivel 1 (chapter):    intro del capítulo (~200 tokens)
  → Nivel 2 (section):    sección completa
  → Nivel 3 (subsection): subsección
  → Nivel 4 (paragraph):  párrafos densos (>300 tokens)
  → embed_text = prefijo_contexto + texto del chunk
  → ~25 campos de metadata por chunk
        │
        ▼
Embedder.embed(chunks)
  → embed_text → OpenAI text-embedding-3-small → vector 1536d
  → batch de 50 por request
        │
        ▼
PineconeClient.upsert → api-rag-mvp
  → id:       {doc_id}__{level}{idx:05d}
  → values:   vector 1536d
  → metadata: 25 campos
        │
        ▼
logger.file_ok(...)  +  state.json actualizado → status: "indexed"
        │
        ▼  (fin del proyecto)
logger.finish_project(...)
extraction_summary.jsonl ← nueva línea appended
```

---

## Campos de metadata en Pinecone (`api-rag-mvp`)

### Posición en el documento
| Campo             | Tipo   | Ejemplo              |
|-------------------|--------|----------------------|
| `chunk_level`     | string | `"subsection"`       |
| `chunk_idx`       | int    | `47`                 |
| `page_start`      | int    | `87`                 |
| `page_end`        | int    | `92`                 |
| `total_pages`     | int    | `342`                |
| `position_in_doc` | float  | `0.254`              |
| `hierarchy_path`  | string | `"3 > 3.2 > 3.2.1"` |

### Jerarquía documental
| Campo              | Tipo   | Ejemplo                     |
|--------------------|--------|-----------------------------|
| `chapter_num`      | string | `"3"`                       |
| `chapter_title`    | string | `"Línea Base Ambiental"`    |
| `section_num`      | string | `"3.2"`                     |
| `section_title`    | string | `"Flora y Vegetación"`      |
| `subsection_num`   | string | `"3.2.1"`                   |
| `subsection_title` | string | `"Metodología de muestreo"` |

### Fuente
| Campo      | Tipo   | Ejemplo                         |
|------------|--------|---------------------------------|
| `source`   | string | `"seia"`                        |
| `doc_type` | string | `"EIA"`                         |
| `doc_id`   | string | `"a3f9c812b7e04d1a"`            |
| `title`    | string | `"Capítulo 3 - Línea Base"`     |
| `date`     | string | `"2026-04-01"`                  |
| `url`      | string | `"https://seia.sea.gob.cl/..."` |

### Proyecto
| Campo               | Tipo   | Ejemplo                                  |
|---------------------|--------|------------------------------------------|
| `project_id`        | string | `"proyecto_batuco_eia"`                  |
| `project_name`      | string | `"Ecociudad Batuco"`                     |
| `project_type`      | string | `"Proyectos de desarrollo inmobiliario"` |
| `titular`           | string | `"MODELA SpA"`                           |
| `region`            | string | `"Metropolitana de Santiago"`            |
| `commune`           | string | `"Lampa"`                                |
| `instrument_type`   | string | `"EIA"`                                  |
| `evaluation_status` | string | `"En calificación"`                      |
| `rca_number`        | string | `""`                                     |
| `expedition_id`     | string | `"2400123"`                              |
| `coordinates_lat`   | float  | `-33.28`                                 |
| `coordinates_lon`   | float  | `-70.88`                                 |
| `surface_ha`        | float  | `1240.0`                                 |
| `investment_musd`   | float  | `850.0`                                  |

### Estadísticas del chunk
| Campo            | Tipo   | Ejemplo                           |
|------------------|--------|-----------------------------------|
| `word_count`     | int    | `312`                             |
| `token_count`    | int    | `487`                             |
| `has_tables`     | bool   | `false`                           |
| `has_figures`    | bool   | `true`                            |
| `text`           | string | texto limpio del chunk (≤3800 ch) |
| `context_prefix` | string | `"[EIA] [Proyecto: Batuco] ..."`  |

---

## Configuración (`.env`)

```env
# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-4o

# Pinecone — índice productivo, no cambiar
PINECONE_API_KEY=pcsk_...
PINECONE_INDEX_NAME=api-rag-mvp
PINECONE_HOST=https://api-rag-mvp-96gaajy.svc.aped-4627-b74a.pinecone.io

# Chunking
CHUNK_SIZE=800
CHUNK_OVERLAP=150

# RAG
TOP_K=8
MIN_SCORE=0.70

# API (Render)
API_HOST=0.0.0.0
API_PORT=8000
RENDER_SERVICE_ID=srv-d5rr9a63jp1c73e1fibg
```

---

## ChatGPT: base de conocimiento y razonamiento

ChatGPT ya está conectado a la API. Lo que hay que mejorar es **cómo piensa y busca**.

### Problema actual

ChatGPT busca en Pinecone con la pregunta cruda del usuario. Esto falla en:
- Preguntas ambiguas ("¿cuánto mide el proyecto?" → ¿superficie? ¿longitud? ¿potencia?)
- Preguntas multi-parte ("compara las medidas de mitigación de flora entre el cap. 4 y el anexo")
- Preguntas sobre el estado del trámite ("¿ya se respondió el ICSARA?")
- Preguntas que necesitan jerarquía ("explícame la metodología de la línea base de flora")

### Mejora 1: system prompt del GPT

El system prompt que recibe ChatGPT debe instruirlo para que:

```
Eres PIA, asistente experto en evaluación ambiental de proyectos chilenos.
Tienes acceso a una base de conocimiento con los documentos completos de cada
proyecto (EIA, DIA, RCA, anexos, adendas, ICSARAs) indexados en una base vectorial.

ESTRATEGIA DE BÚSQUEDA — aplícala antes de responder:

1. Identifica qué tipo de información necesitas según la pregunta:
   - Descripción del proyecto      → busca en cap_01, chunk_level: section
   - Línea base / componente       → busca en cap_02 + anexos temáticos relevantes
   - Impactos ambientales          → busca en cap_03
   - Medidas de mitigación         → busca en cap_04
   - Compromisos voluntarios       → busca en cap_08 o rca
   - Estado del proceso (ICSARA)   → busca en icsara_ o adenda_
   - Normativa aplicable           → busca en cap_01 o rca, doc_type: RCA

2. Si la pregunta abarca más de un tema, divide en 2–3 búsquedas específicas.
   Ejemplo: "¿Cuáles son los impactos sobre flora y qué medidas propone?"
   → búsqueda 1: impactos flora (cap_03, section "flora")
   → búsqueda 2: medidas flora (cap_04, section "flora" o "vegetación")

3. Usa siempre los filtros disponibles en el endpoint /search:
   - project_id:    para limitar al proyecto consultado
   - chunk_level:   "subsection" para precisión, "section" para contexto amplio
   - chapter_title: si sabes en qué capítulo está la información

4. Cita siempre la fuente con formato: (Proyecto X — Cap. Y, Secc. Z, pág. N-M)

TONO Y FORMATO:
- Responde en español, lenguaje técnico ambiental
- Usa bullet points para listas de medidas o impactos
- Si no encuentras la información, dilo explícitamente — no inventes datos
- Si hay información en varios documentos, sintetiza y distingue las fuentes
- Si el usuario no especifica un proyecto, pregunta antes de buscar
```

### Mejora 2: endpoint `/search` con filtros

En lugar de que ChatGPT haga una sola búsqueda plana, la API expone un endpoint
que recibe filtros precisos. ChatGPT decide qué filtros aplicar según la pregunta:

```
POST /search
{
  "query":         "metodología de muestreo de flora",
  "project_id":    "proyecto_batuco_eia",
  "chunk_level":   "subsection",
  "chapter_title": "Línea Base",
  "top_k":         8
}

Respuesta:
{
  "results": [
    {
      "text":             "...",
      "score":            0.91,
      "project_name":     "Ecociudad Batuco",
      "chapter_title":    "Línea Base Ambiental",
      "section_title":    "Flora y Vegetación",
      "subsection_title": "Metodología de muestreo",
      "page_start":       87,
      "page_end":         92,
      "hierarchy_path":   "3 > 3.2 > 3.2.1",
      "url":              "https://seia.sea.gob.cl/..."
    }
  ],
  "total_found":     8,
  "filters_applied": {"project_id":"proyecto_batuco_eia","chunk_level":"subsection"}
}
```

### Mejora 3: OpenAPI schema del GPT Action

El `openapi.json` que ChatGPT usa debe describir el endpoint `/search` con todos
sus parámetros opcionales y sus descripciones en inglés (ChatGPT lo lee mejor en inglés
aunque responda en español):

```yaml
/search:
  post:
    operationId: searchDocuments
    summary: Search the environmental project knowledge base
    parameters:
      query:         "Natural language search query"
      project_id:    "Filter by specific project folder ID (e.g. proyecto_batuco_eia)"
      chunk_level:   "Granularity: chapter | section | subsection | paragraph"
      chapter_title: "Filter by chapter name (partial match)"
      doc_type:      "Filter by document type: EIA | DIA | RCA | ICSARA | ADENDA"
      top_k:         "Number of results (default 8, max 20)"
```

---

## Tareas pendientes (en orden de prioridad)

1. **`etl/extraction_logger.py`** — clase `ExtractionLogger` con los 3 destinos de log descritos arriba

2. **`etl/enriched_pipeline.py`** — método `process_project(project_dir, resume, retry_failed)` con lectura de `project.json`

3. **`storage/pinecone_client.py`** — asegurar conexión por host directo `api-rag-mvp-96gaajy.svc.aped-4627-b74a.pinecone.io`

4. **`main.py`** — comandos `ingest`, `status`, `query` con flags `--project`, `--all`, `--resume`, `--retry-failed`

5. **`api/main.py`** — endpoint `POST /search` con filtros; `GET /projects` con estado desde logs; `POST /ingest/project`

6. **`openapi.json`** — actualizar schema del GPT Action con el endpoint `/search` y sus parámetros

7. **System prompt de ChatGPT** — reemplazar con el prompt de la sección anterior

8. **Testing end-to-end** — procesar 3–4 PDFs de un proyecto, verificar `state.json`, log generado,
   vectors en `api-rag-mvp` con metadata completa, y búsqueda desde ChatGPT con jerarquía en la respuesta

---

## Convenciones de código

- Python 3.11+, type hints en todo
- `loguru` para logging (no `logging` estándar)
- `pydantic-settings` para config
- `dataclasses` para estructuras de datos internas
- Nunca abortar el pipeline por un archivo fallido: capturar, loggear, continuar
- Todo archivo procesado queda en `state.json`: `indexed`, `failed` o `skipped`
- El índice Pinecone es `api-rag-mvp`. **No crear índices nuevos.**
- Las variables de entorno de Render no se modifican desde el código local

---

## Notas importantes

- `data/projects/` contiene los PDFs ya descargados. **No borrar ni mover.**
- `project.json` tiene prioridad sobre la API del SEIA en todos los campos.
- El embedding se genera sobre `embed_text` = prefijo de contexto + texto del chunk. En Pinecone se guarda el texto limpio en `text` y el prefijo en `context_prefix`.
- Pinecone limita metadata a ~40KB por vector. El campo `text` está truncado a 3800 chars.
- La separación entre proyectos en Pinecone se hace por filtro `project_id`, no por namespaces separados.
- PDFs con < 100 chars promedio por página se clasifican como escaneados → OCR con pytesseract (requiere `tesseract-ocr-spa` instalado en el sistema).
- Render redeploya automáticamente al hacer push. Verificar que el health check pase antes de probar desde ChatGPT.
