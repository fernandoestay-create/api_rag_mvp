"""
etl/enriched_chunker.py — Chunker híbrido: cobertura total + metadata jerárquica.

Estrategia "zero-loss":
  1. Chunkea PÁGINA POR PÁGINA (nunca se pierde texto)
  2. Detecta la jerarquía (capítulo → sección → subsección) como overlay
  3. Cada chunk recibe la metadata de la sección a la que pertenece por posición

Esto combina:
  - La cobertura 100% del chunking por página (como el código original)
  - La metadata enriquecida de 25+ campos para filtrado preciso en Pinecone
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

from pia_rag.config import settings
from pia_rag.etl.document_parser import DocumentStructure, StructureNode


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class EnrichedChunk:
    """Un chunk listo para embedding + upsert a Pinecone."""

    # Identificación
    chunk_id: str
    doc_id: str
    chunk_idx: int

    # Texto
    text: str              # texto limpio (≤3800 chars para metadata Pinecone)
    embed_text: str        # context_prefix + text (se usa para generar el embedding)
    context_prefix: str    # "[EIA] [Proyecto: X] [Cap 3 > Sec 3.2 > ...]"

    # Posición en el documento
    chunk_level: str       # "chapter" | "section" | "subsection" | "paragraph"
    page_start: int
    page_end: int
    total_pages: int
    position_in_doc: float  # 0.0 a 1.0
    hierarchy_path: str     # "3 > 3.2 > 3.2.1"

    # Jerarquía documental
    chapter_num: str
    chapter_title: str
    section_num: str
    section_title: str
    subsection_num: str
    subsection_title: str

    # Fuente
    source: str            # "seia"
    doc_type: str          # "EIA" | "DIA" | "RCA" | etc
    title: str             # "Capítulo 3 - Línea Base"
    filename: str          # nombre del archivo PDF original
    date: str
    url: str

    # Proyecto
    project_id: str
    project_name: str
    project_type: str
    titular: str
    region: str
    commune: str
    instrument_type: str
    evaluation_status: str
    rca_number: str
    expedition_id: str
    coordinates_lat: Optional[float] = None
    coordinates_lon: Optional[float] = None
    surface_ha: Optional[float] = None
    investment_musd: Optional[float] = None

    # Estadísticas
    word_count: int = 0
    token_count: int = 0
    has_tables: bool = False
    has_figures: bool = False

    def to_pinecone_metadata(self) -> dict:
        """Convierte a dict para Pinecone metadata (text truncado a 3800 chars)."""
        meta = {
            "text": self.text[:3800],
            "context_prefix": self.context_prefix,
            "chunk_level": self.chunk_level,
            "chunk_idx": self.chunk_idx,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "total_pages": self.total_pages,
            "position_in_doc": self.position_in_doc,
            "hierarchy_path": self.hierarchy_path,
            "chapter_num": self.chapter_num,
            "chapter_title": self.chapter_title,
            "section_num": self.section_num,
            "section_title": self.section_title,
            "subsection_num": self.subsection_num,
            "subsection_title": self.subsection_title,
            "source": self.source,
            "doc_type": self.doc_type,
            "doc_id": self.doc_id,
            "title": self.title,
            "filename": self.filename,
            "date": self.date,
            "url": self.url,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_type": self.project_type,
            "titular": self.titular,
            "region": self.region,
            "commune": self.commune,
            "instrument_type": self.instrument_type,
            "evaluation_status": self.evaluation_status,
            "rca_number": self.rca_number,
            "expedition_id": self.expedition_id,
            "word_count": self.word_count,
            "token_count": self.token_count,
            "has_tables": self.has_tables,
            "has_figures": self.has_figures,
        }
        # Add optional numeric fields
        if self.coordinates_lat is not None:
            meta["coordinates_lat"] = self.coordinates_lat
        if self.coordinates_lon is not None:
            meta["coordinates_lon"] = self.coordinates_lon
        if self.surface_ha is not None:
            meta["surface_ha"] = self.surface_ha
        if self.investment_musd is not None:
            meta["investment_musd"] = self.investment_musd
        return meta


# ─── Helpers ────────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Limpia texto: colapsa espacios, quita líneas vacías excesivas."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _estimate_tokens(text: str) -> int:
    """Estimación rápida de tokens (~0.75 words/token para español)."""
    words = len(text.split())
    return int(words * 1.33)


def _has_table_markers(text: str) -> bool:
    """Detecta si hay indicadores de tabla en el texto."""
    patterns = [r"Tabla\s+\d", r"Cuadro\s+\d", r"\|\s*\w+\s*\|", r"─{3,}"]
    return any(re.search(p, text) for p in patterns)


def _has_figure_markers(text: str) -> bool:
    """Detecta si hay indicadores de figuras en el texto."""
    patterns = [r"Figura\s+\d", r"Imagen\s+\d", r"Ilustración\s+\d", r"Foto\s+\d"]
    return any(re.search(p, text) for p in patterns)


def _safe_id(text: str) -> str:
    """Genera un ID seguro para Pinecone (sin tildes ni caracteres especiales)."""
    text_norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9_]", "_", text_norm)


def _infer_doc_type(filename: str, folder_path: str = "") -> str:
    """
    Infiere el tipo de documento desde la estructura de carpetas (prioridad)
    y el nombre del archivo (fallback).
    """
    path_lower = folder_path.lower().replace("\\", "/")

    folder_rules = [
        ("capitulos eia", "EIA"),
        ("capitulo", "EIA"),
        ("cap_", "EIA"),
        ("icsara", "ICSARA"),
        ("adenda", "ADENDA"),
        ("informe consolidado", "ICE"),
        ("ice", "ICE"),
        ("rca", "RCA"),
        ("resolucion", "RESOLUCION"),
        ("admisibilidad", "RESOLUCION"),
        ("suspension", "SUSPENSION"),
        ("desistimiento", "DESISTIMIENTO"),
        ("dia", "DIA"),
    ]

    for keyword, dtype in folder_rules:
        if keyword in unicodedata.normalize("NFKD", path_lower).encode("ascii", "ignore").decode("ascii"):
            if dtype == "ADENDA":
                fname_lower = filename.lower()
                if "icsara" in fname_lower:
                    return "ICSARA"
            return dtype

    fname_lower = filename.lower()
    if "eia" in fname_lower or "cap" in fname_lower:
        return "EIA"
    if "dia" in fname_lower:
        return "DIA"
    if "rca" in fname_lower:
        return "RCA"
    if "icsara" in fname_lower:
        return "ICSARA"
    if "ice" in fname_lower:
        return "ICE"
    if "adenda" in fname_lower:
        return "ADENDA"
    if "anexo" in fname_lower:
        return "ANEXO"
    if "resolucion" in fname_lower or "admisibilidad" in fname_lower:
        return "RESOLUCION"
    return "EIA"


def _infer_chapter_from_folder(folder_path: str) -> tuple[str, str]:
    """
    Intenta inferir el número y título del capítulo desde el nombre de la carpeta.
    """
    parts = folder_path.replace("\\", "/").split("/")
    for part in reversed(parts):
        m = re.match(r"(?:Cap[ií]tulo|Cap)\s*(\d+)\s*[-:.\s]+\s*(.+)", part, re.IGNORECASE)
        if m:
            return m.group(1), m.group(2).strip()
        m = re.match(r"^0*(\d{1,2})\s+([A-Z].+)", part)
        if m and int(m.group(1)) <= 20:
            return m.group(1), m.group(2).strip()
    return "", ""


# ─── Hierarchy map builder ──────────────────────────────────────────────────

@dataclass
class _HierarchyInfo:
    """Info jerárquica para una posición del documento."""
    chapter_num: str = ""
    chapter_title: str = ""
    section_num: str = ""
    section_title: str = ""
    subsection_num: str = ""
    subsection_title: str = ""
    chunk_level: str = "section"
    page_start: int = 1
    page_end: int = 1


def _build_page_hierarchy_map(
    structure: DocumentStructure,
    folder_path: str = "",
) -> dict[int, _HierarchyInfo]:
    """
    Construye un mapa: página → información jerárquica.

    Para cada página del documento, determina en qué capítulo/sección/subsección cae.
    Esto permite asignar metadata jerárquica a chunks basados en páginas.
    """
    total_pages = structure.total_pages
    if total_pages == 0:
        return {}

    # Initialize all pages with default info
    page_map: dict[int, _HierarchyInfo] = {}
    for pg in range(1, total_pages + 1):
        page_map[pg] = _HierarchyInfo(page_start=pg, page_end=pg)

    # Try folder-level chapter info as fallback
    folder_chap_num, folder_chap_title = _infer_chapter_from_folder(folder_path)

    if not structure.chapters:
        # No structure detected — use folder info or generic
        for pg in range(1, total_pages + 1):
            page_map[pg].chapter_num = folder_chap_num or "1"
            page_map[pg].chapter_title = folder_chap_title or "Documento completo"
        return page_map

    # Walk the structure tree and map page ranges to hierarchy
    for chapter in structure.chapters:
        ch_num = chapter.num
        ch_title = chapter.title

        # Override generic titles with folder info
        if ch_title == "Documento completo" and folder_chap_title:
            ch_num = folder_chap_num or ch_num
            ch_title = folder_chap_title

        ch_start = chapter.page_start
        ch_end = chapter.page_end

        # Paint all pages in chapter range
        for pg in range(ch_start, min(ch_end + 1, total_pages + 1)):
            page_map[pg].chapter_num = ch_num
            page_map[pg].chapter_title = ch_title

        for section in chapter.children:
            sec_start = section.page_start
            sec_end = section.page_end

            for pg in range(sec_start, min(sec_end + 1, total_pages + 1)):
                page_map[pg].section_num = section.num
                page_map[pg].section_title = section.title
                page_map[pg].chunk_level = "section"

            for subsection in section.children:
                sub_start = subsection.page_start
                sub_end = subsection.page_end

                for pg in range(sub_start, min(sub_end + 1, total_pages + 1)):
                    page_map[pg].subsection_num = subsection.num
                    page_map[pg].subsection_title = subsection.title
                    page_map[pg].chunk_level = "subsection"

    # Fill in pages that weren't covered by any chapter
    # (usually pages before the first detected header)
    last_known = _HierarchyInfo(
        chapter_num=folder_chap_num or "0",
        chapter_title=folder_chap_title or structure.filename,
    )
    for pg in range(1, total_pages + 1):
        info = page_map[pg]
        if info.chapter_title:
            last_known = info
        else:
            # Inherit from nearest known section above
            page_map[pg].chapter_num = last_known.chapter_num
            page_map[pg].chapter_title = last_known.chapter_title

    return page_map


# ─── Main Chunker ───────────────────────────────────────────────────────────

class EnrichedHierarchicalChunker:
    """
    Chunker híbrido zero-loss: chunkea página por página, enriquece con metadata
    jerárquica por overlay.

    Garantías:
      - TODO el texto extraído del PDF se convierte en chunks (0% pérdida)
      - Cada chunk tiene página exacta y sección/subsección a la que pertenece
      - Metadata completa de 25+ campos para filtrado en Pinecone
    """

    def __init__(self):
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    def chunk(
        self,
        structure: DocumentStructure,
        project_meta: dict,
        folder_path: str = "",
    ) -> list[EnrichedChunk]:
        """
        Genera chunks enriquecidos para un documento parseado.

        Estrategia:
          1. Construye mapa página → jerarquía desde el árbol de estructura
          2. Para cada página con texto, divide en chunks con RecursiveCharacterTextSplitter
          3. Cada chunk recibe metadata jerárquica según la página a la que pertenece

        Args:
            structure: Resultado de DocumentStructureParser.parse()
            project_meta: Diccionario con metadata del proyecto (de project.json)
            folder_path: Ruta de la carpeta del PDF (para inferir doc_type)

        Returns:
            Lista de EnrichedChunk listos para embedding + upsert
        """
        if not structure.page_results:
            return []

        doc_type = _infer_doc_type(structure.filename, folder_path)
        page_hierarchy = _build_page_hierarchy_map(structure, folder_path)

        chunks: list[EnrichedChunk] = []
        chunk_idx = 0

        for page_result in structure.page_results:
            page_num = page_result.page_num
            page_text = page_result.text

            # Skip empty pages
            if not page_text or len(page_text.strip()) < 20:
                continue

            # Clean the page text
            cleaned = _clean_text(page_text)
            if len(cleaned) < 20:
                continue

            # Get hierarchy info for this page
            hier = page_hierarchy.get(page_num, _HierarchyInfo())

            # Split page text into chunks
            if len(cleaned) <= settings.chunk_size:
                # Page fits in one chunk — keep it whole
                parts = [cleaned]
            else:
                # Page is long — split it
                parts = self._splitter.split_text(cleaned)

            for part in parts:
                part = part.strip()
                if len(part) < 20:
                    continue

                # Determine chunk level based on hierarchy granularity
                if hier.subsection_num:
                    level = "subsection"
                elif hier.section_num:
                    level = "section"
                else:
                    level = "chapter"

                ch = self._make_chunk(
                    text=part,
                    level=level,
                    page_num=page_num,
                    hier=hier,
                    structure=structure,
                    project_meta=project_meta,
                    doc_type=doc_type,
                    chunk_idx=chunk_idx,
                )
                chunks.append(ch)
                chunk_idx += 1

        return chunks

    def _make_chunk(
        self,
        text: str,
        level: str,
        page_num: int,
        hier: _HierarchyInfo,
        structure: DocumentStructure,
        project_meta: dict,
        doc_type: str,
        chunk_idx: int,
    ) -> EnrichedChunk:
        """Construye un EnrichedChunk con toda su metadata."""

        # Hierarchy path
        parts = []
        if hier.chapter_num:
            parts.append(hier.chapter_num)
        if hier.section_num:
            parts.append(hier.section_num)
        if hier.subsection_num:
            parts.append(hier.subsection_num)
        hierarchy_path = " > ".join(parts) if parts else ""

        # Context prefix (embedded in the embedding for better semantic search)
        instrument = project_meta.get("instrument_type", doc_type)
        project_name = project_meta.get("project_name", project_meta.get("project_id", ""))
        ctx_parts = [f"[{instrument}]", f"[Proyecto: {project_name}]"]

        if hier.chapter_title:
            ctx_parts.append(f"[{hier.chapter_title}")
            if hier.section_title:
                ctx_parts.append(f"> {hier.section_title}")
                if hier.subsection_title:
                    ctx_parts.append(f"> {hier.subsection_title}")
            ctx_parts[-1] += "]"
        context_prefix = " ".join(ctx_parts)

        # Tatuaje: inject identity into the text for embedding
        tatuaje = (
            f"[PROYECTO: {project_name} | "
            f"DOC: {structure.filename} | "
            f"PÁG: {page_num}]\n"
        )
        embed_text = f"{context_prefix}\n{tatuaje}{text}"

        position = page_num / structure.total_pages if structure.total_pages > 0 else 0.0

        # Chunk ID: unique per document + chunk index
        safe_doc = _safe_id(structure.doc_id)
        chunk_id = f"{safe_doc}__{level}{chunk_idx:05d}"

        # Title for display
        title = hier.chapter_title or structure.filename
        if hier.section_title:
            title += f" - {hier.section_title}"

        return EnrichedChunk(
            chunk_id=chunk_id,
            doc_id=structure.doc_id,
            chunk_idx=chunk_idx,
            text=text,
            embed_text=embed_text,
            context_prefix=context_prefix,
            chunk_level=level,
            page_start=page_num,
            page_end=page_num,
            total_pages=structure.total_pages,
            position_in_doc=round(position, 3),
            hierarchy_path=hierarchy_path,
            chapter_num=hier.chapter_num,
            chapter_title=hier.chapter_title,
            section_num=hier.section_num,
            section_title=hier.section_title,
            subsection_num=hier.subsection_num,
            subsection_title=hier.subsection_title,
            source=project_meta.get("source", "seia"),
            doc_type=doc_type,
            title=title,
            filename=structure.filename,
            date=project_meta.get("ingreso_date", ""),
            url=project_meta.get("url", ""),
            project_id=project_meta.get("project_id", ""),
            project_name=project_meta.get("project_name", ""),
            project_type=project_meta.get("project_type", ""),
            titular=project_meta.get("titular", ""),
            region=project_meta.get("region", ""),
            commune=project_meta.get("commune", ""),
            instrument_type=project_meta.get("instrument_type", ""),
            evaluation_status=project_meta.get("evaluation_status", ""),
            rca_number=project_meta.get("rca_number", ""),
            expedition_id=project_meta.get("expedition_id", ""),
            coordinates_lat=project_meta.get("coordinates_lat"),
            coordinates_lon=project_meta.get("coordinates_lon"),
            surface_ha=project_meta.get("surface_ha"),
            investment_musd=project_meta.get("investment_musd"),
            word_count=len(text.split()),
            token_count=_estimate_tokens(text),
            has_tables=_has_table_markers(text),
            has_figures=_has_figure_markers(text),
        )
