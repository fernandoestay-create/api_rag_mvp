"""
etl/enriched_pipeline.py — Orquestador del pipeline ETL completo.

Flujo por proyecto:
  1. Lee project.json
  2. Itera sobre cada PDF en la carpeta
  3. Para cada PDF: parse → chunk → embed → upsert
  4. Logging en 3 destinos (humano, JSONL, state.json)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from pia_rag.config import settings
from pia_rag.etl.document_parser import DocumentStructureParser
from pia_rag.etl.enriched_chunker import EnrichedChunk, EnrichedHierarchicalChunker
from pia_rag.etl.extraction_logger import (
    ExtractionLogger,
    FileExtractionResult,
    PageOCRResult,
)
from pia_rag.storage.pinecone_client import PineconeClient


def _load_project_meta(project_dir: Path) -> dict:
    """Carga project.json de la carpeta del proyecto."""
    project_json = project_dir / "project.json"
    if project_json.exists():
        return json.loads(project_json.read_text(encoding="utf-8"))

    # Generar metadata mínima desde el nombre de la carpeta
    folder_name = project_dir.name
    project_id = folder_name.lower().replace(" ", "_").replace("-", "_")
    # Remove accents
    import unicodedata
    project_id = unicodedata.normalize("NFKD", project_id).encode("ascii", "ignore").decode("ascii")

    return {
        "project_id": project_id,
        "project_name": folder_name,
        "instrument_type": "EIA",
        "source": "seia",
        "project_type": "",
        "titular": "",
        "region": "",
        "commune": "",
        "evaluation_status": "",
        "ingreso_date": "",
        "rca_number": "",
        "expedition_id": "",
        "coordinates_lat": None,
        "coordinates_lon": None,
        "surface_ha": None,
        "investment_musd": None,
    }


def _find_pdfs(project_dir: Path) -> list[Path]:
    """Encuentra todos los PDFs en la carpeta del proyecto (recursivo).
    Filtra archivos que no existen realmente (iCloud placeholders)."""
    import os

    seen: set[str] = set()
    pdfs: list[Path] = []
    for ext in ("*.pdf", "*.PDF"):
        for p in sorted(project_dir.rglob(ext)):
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                if p.exists() and os.path.getsize(str(p)) > 500:
                    pdfs.append(p)
            except OSError:
                continue
    return pdfs


def _load_gdrive_map() -> dict[str, dict[str, str]]:
    """Carga el mapeo filename → Google Drive URL desde gdrive_map.json."""
    map_file = settings.base_dir / "data" / "gdrive_map.json"
    if map_file.exists():
        data = json.loads(map_file.read_text(encoding="utf-8"))
        logger.info(f"Google Drive map cargado: {sum(len(v) for v in data.values())} archivos")
        return data
    logger.warning("No se encontró data/gdrive_map.json — URLs quedarán vacías")
    return {}


class EnrichedETLPipeline:
    """
    Pipeline ETL completo: PDF → parse → chunk → embed → Pinecone.

    Uso:
        pipeline = EnrichedETLPipeline()
        stats = pipeline.process_project(Path("data/projects/proyecto_x"))
    """

    def __init__(self):
        self._parser = DocumentStructureParser()
        self._chunker = EnrichedHierarchicalChunker()
        self._pinecone: Optional[PineconeClient] = None
        self._gdrive_map: dict[str, dict[str, str]] = _load_gdrive_map()

    def _get_pinecone(self) -> PineconeClient:
        """Lazy init del cliente Pinecone."""
        if self._pinecone is None:
            self._pinecone = PineconeClient()
        return self._pinecone

    def process_project(
        self,
        project_dir: Path,
        resume: bool = True,
        retry_failed: bool = False,
    ) -> dict:
        """
        Procesa un proyecto completo: todos sus PDFs.

        Args:
            project_dir: Carpeta del proyecto con PDFs
            resume: Omite archivos con status "indexed" en state.json
            retry_failed: Reintenta archivos con status "failed"

        Returns:
            dict con estadísticas del procesamiento
        """
        project_dir = Path(project_dir)
        if not project_dir.exists():
            raise FileNotFoundError(f"Carpeta no encontrada: {project_dir}")

        # Load metadata
        project_meta = _load_project_meta(project_dir)
        project_id = project_meta["project_id"]
        logger.info(f"Procesando proyecto: {project_id} desde {project_dir}")

        # Find PDFs
        pdfs = _find_pdfs(project_dir)
        if not pdfs:
            logger.warning(f"No se encontraron PDFs en {project_dir}")
            return {"project_id": project_id, "status": "empty", "pdfs": 0, "chunks": 0}

        # Init logger
        ext_logger = ExtractionLogger(project_id)
        ext_logger.start_project(total_files=len(pdfs))

        # Init Pinecone
        pinecone = self._get_pinecone()

        # Process each PDF
        total_chunks = 0
        total_ok = 0
        total_failed = 0

        for pdf_path in pdfs:
            filename = pdf_path.name

            # Check skip conditions
            if resume and ext_logger.is_already_indexed(filename):
                ext_logger.file_skipped(filename)
                continue

            if not retry_failed and ext_logger.is_failed(filename):
                ext_logger.file_skipped(filename)
                continue

            # Process single PDF — inject Google Drive URL into project_meta
            start = time.time()
            try:
                # Look up Google Drive URL for this PDF
                pdf_meta = dict(project_meta)  # copy
                gdrive_project = self._gdrive_map.get(project_dir.name, {})
                gdrive_url = gdrive_project.get(filename, "")
                pdf_meta["url"] = gdrive_url

                chunks, structure = self._process_single_pdf(pdf_path, pdf_meta, ext_logger)
                duration = time.time() - start

                if chunks and structure:
                    # Upsert to Pinecone
                    n_indexed = pinecone.upsert_chunks(chunks)

                    avg_tokens = (
                        sum(c.token_count for c in chunks) / len(chunks)
                        if chunks else 0.0
                    )

                    result = FileExtractionResult(
                        filename=filename,
                        project_id=project_id,
                        status="indexed",
                        total_pages=structure.total_pages,
                        pages_pymupdf=structure.pages_pymupdf,
                        pages_ocr=structure.pages_ocr,
                        pages_failed=structure.pages_failed,
                        chapters=structure.n_chapters,
                        sections=structure.n_sections,
                        subsections=structure.n_subsections,
                        chunks=len(chunks),
                        tokens_avg=round(avg_tokens, 1),
                        chars_total=structure.chars_total,
                        ocr_triggered=structure.ocr_triggered,
                        duration_s=round(duration, 1),
                        page_results=[
                            PageOCRResult(
                                page=pr.page_num,
                                method=pr.method,
                                chars_extracted=pr.chars,
                                lines_extracted=len(pr.text.split("\n")) if pr.text else 0,
                                avg_confidence=pr.ocr_confidence,
                                duration_ms=pr.duration_ms,
                                warnings=pr.warnings,
                                error=pr.error,
                            )
                            for pr in structure.page_results
                        ],
                    )
                    ext_logger.file_ok(filename, result)
                    total_chunks += len(chunks)
                    total_ok += 1
                else:
                    ext_logger.file_error(filename, "No se generaron chunks (PDF vacío o sin texto)")
                    total_failed += 1

            except Exception as e:
                duration = time.time() - start
                logger.error(f"Error procesando {filename}: {e}")
                ext_logger.file_error(filename, str(e))
                total_failed += 1

        ext_logger.finish_project()

        return {
            "project_id": project_id,
            "status": "complete" if total_failed == 0 else "partial",
            "pdfs_total": len(pdfs),
            "pdfs_ok": total_ok,
            "pdfs_failed": total_failed,
            "chunks": total_chunks,
        }

    def _process_single_pdf(
        self,
        pdf_path: Path,
        project_meta: dict,
        ext_logger: ExtractionLogger,
    ) -> tuple[list[EnrichedChunk], "DocumentStructure | None"]:
        """Procesa un PDF individual: parse → chunk. No hace upsert.
        Returns (chunks, structure) to avoid double-parsing."""
        logger.info(f"  Procesando: {pdf_path.name}")

        # Parse
        from pia_rag.etl.document_parser import DocumentStructure
        structure = self._parser.parse(pdf_path)
        if not structure.full_text.strip():
            return [], structure

        # Chunk — pass folder_path so doc_type can be inferred from directory structure
        folder_path = str(pdf_path.parent)
        chunks = self._chunker.chunk(structure, project_meta, folder_path=folder_path)
        logger.info(
            f"  {pdf_path.name}: {structure.n_chapters} cap, "
            f"{structure.n_sections} sec → {len(chunks)} chunks"
        )
        return chunks, structure

    def process_pdf_direct(
        self,
        pdf_path: Path,
        project_json: Optional[Path] = None,
    ) -> list[EnrichedChunk]:
        """
        Procesa un PDF individual sin tocar Pinecone (debug/reproceso).

        Args:
            pdf_path: Ruta al PDF
            project_json: Ruta a project.json (opcional)

        Returns:
            Lista de EnrichedChunk
        """
        if project_json and project_json.exists():
            project_meta = json.loads(project_json.read_text(encoding="utf-8"))
        else:
            project_meta = _load_project_meta(pdf_path.parent)

        structure = self._parser.parse(pdf_path)
        return self._chunker.chunk(structure, project_meta, folder_path=str(pdf_path.parent))
