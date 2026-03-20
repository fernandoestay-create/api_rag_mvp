"""
api/main.py — FastAPI desplegado en Render (api_rag_mvp).

Endpoints:
  POST /search         — búsqueda con filtros opcionales (ChatGPT lo usa)
  POST /ingest/project — ingestar un proyecto
  GET  /projects       — lista proyectos con estado
  GET  /projects/{id}  — estado detallado de un proyecto
  GET  /health         — health check
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from pia_rag.config import settings

app = FastAPI(
    title="PIA RAG API",
    version="2.0",
    description="API de consulta para la base de conocimiento ambiental PIA",
)

# CORS — permite que ChatGPT GPT Actions llame a la API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Models ─────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = Field(..., description="Natural language search query")
    project_id: Optional[str] = Field(None, description="Filter by project folder ID")
    chunk_level: Optional[str] = Field(None, description="Granularity: chapter | section | subsection | paragraph")
    chapter_title: Optional[str] = Field(None, description="Filter by chapter name")
    doc_type: Optional[str] = Field(None, description="Filter by document type: EIA | DIA | RCA | ICSARA | ADENDA")
    top_k: int = Field(8, ge=1, le=20, description="Number of results (default 8, max 20)")


class SearchResult(BaseModel):
    text: str
    score: float
    project_id: str = ""
    project_name: str = ""
    chapter_title: str = ""
    section_title: str = ""
    subsection_title: str = ""
    page_start: int = 0
    page_end: int = 0
    hierarchy_path: str = ""
    chunk_level: str = ""
    doc_type: str = ""
    url: str = ""


class SearchResponse(BaseModel):
    results: list[SearchResult]
    total_found: int
    filters_applied: dict


class IngestRequest(BaseModel):
    project_id: str = Field(..., description="Nombre de la carpeta del proyecto")
    resume: bool = Field(True, description="Omitir archivos ya indexados")
    retry_failed: bool = Field(False, description="Reintentar archivos fallidos")


# ─── Lazy init ──────────────────────────────────────────────────────────────

_pinecone_client = None
_rag_engine = None


def _get_pinecone():
    global _pinecone_client
    if _pinecone_client is None:
        from pia_rag.storage.pinecone_client import PineconeClient
        _pinecone_client = PineconeClient()
    return _pinecone_client


def _get_rag():
    global _rag_engine
    if _rag_engine is None:
        from pia_rag.rag.enriched_engine import EnrichedRAGEngine
        _rag_engine = EnrichedRAGEngine()
    return _rag_engine


# ─── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Health check."""
    try:
        pc = _get_pinecone()
        stats = pc.get_index_stats()
        return {
            "status": "ok",
            "pinecone": settings.pinecone_index_name,
            "vectors": stats.get("total_vectors", 0),
        }
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    """
    Busca en la base de conocimiento con filtros opcionales.
    Este endpoint es llamado por ChatGPT vía GPT Action.
    """
    try:
        pc = _get_pinecone()
        results = pc.search(
            query=req.query,
            top_k=req.top_k,
            project_id=req.project_id,
            chunk_level=req.chunk_level,
            chapter_title=req.chapter_title,
            doc_type=req.doc_type,
        )

        filters = {}
        if req.project_id:
            filters["project_id"] = req.project_id
        if req.chunk_level:
            filters["chunk_level"] = req.chunk_level
        if req.chapter_title:
            filters["chapter_title"] = req.chapter_title
        if req.doc_type:
            filters["doc_type"] = req.doc_type

        return SearchResponse(
            results=[SearchResult(**r) for r in results],
            total_found=len(results),
            filters_applied=filters,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ask")
def ask(req: SearchRequest):
    """
    Busca + genera respuesta con GPT-4o.
    Compatibilidad con el endpoint anterior.
    """
    try:
        engine = _get_rag()
        result = engine.query(
            question=req.query,
            project_id=req.project_id,
            chunk_level=req.chunk_level,
            chapter_title=req.chapter_title,
            doc_type=req.doc_type,
            top_k=req.top_k,
            generate=True,
        )
        return {
            "answer": result.get("answer", ""),
            "sources_count": result.get("total_found", 0),
            "results": result.get("results", []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/projects")
def list_projects():
    """Lista todos los proyectos con su estado."""
    projects = []
    processed = settings.processed_dir

    if processed.exists():
        for proj_dir in sorted(processed.iterdir()):
            state_file = proj_dir / "state.json"
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    files = state.get("files", {})
                    ok = sum(1 for f in files.values() if f.get("status") == "indexed")
                    failed = sum(1 for f in files.values() if f.get("status") == "failed")
                    projects.append({
                        "project_id": state.get("project_id", proj_dir.name),
                        "status": state.get("status", "unknown"),
                        "pdfs_ok": ok,
                        "pdfs_failed": failed,
                        "total_chunks": state.get("total_chunks", 0),
                        "last_updated": state.get("last_updated", ""),
                    })
                except Exception:
                    pass

    return {"projects": projects, "total": len(projects)}


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    """Estado detallado de un proyecto."""
    state_file = settings.processed_dir / project_id / "state.json"
    if not state_file.exists():
        raise HTTPException(status_code=404, detail=f"Proyecto no encontrado: {project_id}")

    return json.loads(state_file.read_text(encoding="utf-8"))


@app.post("/ingest/project")
def ingest_project(req: IngestRequest):
    """Inicia la ingesta de un proyecto."""
    from pia_rag.etl.enriched_pipeline import EnrichedETLPipeline

    # Find project directory
    project_dir = settings.documents_dir / req.project_id
    if not project_dir.exists():
        # Try fuzzy match
        docs_dir = settings.documents_dir
        if docs_dir.exists():
            for d in docs_dir.iterdir():
                if d.is_dir() and req.project_id.lower() in d.name.lower():
                    project_dir = d
                    break

    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Proyecto no encontrado: {req.project_id}")

    try:
        pipeline = EnrichedETLPipeline()
        stats = pipeline.process_project(
            project_dir,
            resume=req.resume,
            retry_failed=req.retry_failed,
        )
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Root ───────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "name": "PIA RAG API",
        "version": "2.0",
        "status": "Online",
        "index": settings.pinecone_index_name,
        "endpoints": ["/search", "/ask", "/projects", "/health"],
    }
