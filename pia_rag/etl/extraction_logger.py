"""
etl/extraction_logger.py — Sistema de logging de extracción con 3 destinos.

Destinos:
  1. Log humano por proyecto:  data/logs/extraction/{project_id}.log
  2. Resumen global (JSONL):   data/logs/extraction/extraction_summary.jsonl
  3. Estado por proyecto:       data/processed/{project_id}/state.json

También genera un log OCR detallado si aplica:
  4. OCR detalle:              data/logs/extraction/{project_id}_ocr.jsonl
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from pia_rag.config import settings


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class PageOCRResult:
    """Resultado de OCR de una página individual."""
    page: int
    method: str  # "ocr" | "pymupdf"
    chars_extracted: int
    lines_extracted: int
    avg_confidence: Optional[float] = None
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class FileExtractionResult:
    """Resultado completo de extracción de un archivo PDF."""
    filename: str
    project_id: str
    status: str  # "indexed" | "failed" | "skipped"
    total_pages: int = 0
    pages_pymupdf: int = 0
    pages_ocr: int = 0
    pages_failed: int = 0
    chapters: int = 0
    sections: int = 0
    subsections: int = 0
    chunks: int = 0
    tokens_avg: float = 0.0
    chars_total: int = 0
    ocr_triggered: bool = False
    ocr_avg_confidence: Optional[float] = None
    duration_s: float = 0.0
    error: Optional[str] = None
    page_results: list[PageOCRResult] = field(default_factory=list)

    @property
    def extraction_rate(self) -> float:
        if self.total_pages == 0:
            return 0.0
        ok = self.pages_pymupdf + self.pages_ocr
        return round(ok / self.total_pages * 100, 1)

    @property
    def quality_label(self) -> str:
        rate = self.extraction_rate
        if rate >= 90:
            return "excellent"
        if rate >= 70:
            return "good"
        if rate >= 40:
            return "partial"
        if rate >= 1:
            return "poor"
        return "unreadable"


# ─── Logger class ───────────────────────────────────────────────────────────

class ExtractionLogger:
    """
    Gestiona logging de extracción para un proyecto.

    Uso:
        log = ExtractionLogger("proyecto_batuco_eia")
        log.start_project(total_files=12)
        log.file_ok("cap_01.pdf", result)
        log.file_error("anexo.pdf", error="corrupted")
        log.finish_project()
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._start_time: Optional[float] = None
        self._total_files = 0
        self._results: list[FileExtractionResult] = []
        self._errors: list[dict] = []

        # Paths
        self._log_dir = settings.extraction_logs_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self._log_dir / f"{project_id}.log"
        self._summary_file = self._log_dir / "extraction_summary.jsonl"
        self._ocr_file = self._log_dir / f"{project_id}_ocr.jsonl"

        self._state_dir = settings.processed_dir / project_id
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self._state_dir / "state.json"

        # Load existing state
        self._state = self._load_state()

    # ── State management ────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "project_id": self.project_id,
            "status": "pending",
            "last_updated": "",
            "pinecone_index": settings.pinecone_index_name,
            "files": {},
            "total_chunks": 0,
            "total_indexed": 0,
        }

    def _save_state(self):
        self._state["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._state_file.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── Public interface ────────────────────────────────────────────────

    def start_project(self, total_files: int):
        """Inicia el logging para un proyecto."""
        self._start_time = time.time()
        self._total_files = total_files
        self._results = []
        self._errors = []

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"{ts} | INFO     | {'=' * 60}",
            f"{ts} | INFO     | Iniciando extracción: {self.project_id}",
            f"{ts} | INFO     | PDFs encontrados: {total_files}  |  Pinecone: {settings.pinecone_index_name}",
            f"{ts} | INFO     | {'=' * 60}",
            "",
        ]
        self._write_log("\n".join(lines))

    def file_ok(self, filename: str, result: FileExtractionResult):
        """Registra un archivo procesado exitosamente."""
        self._results.append(result)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Log humano
        line = (
            f"{ts} | SUCCESS  | {filename} → "
            f"{result.chapters} cap, {result.sections} sec, {result.subsections} sub | "
            f"{result.chunks} chunks | avg {result.tokens_avg:.0f} tok"
        )
        detail = (
            f"{ts} | INFO     |   {filename} | "
            f"{result.total_pages} págs | "
            f"{result.pages_pymupdf + result.pages_ocr} OK / "
            f"{result.total_pages - result.pages_pymupdf - result.pages_ocr - result.pages_failed} vacías / "
            f"{result.pages_failed} errores | "
            f"{result.chars_total} chars | calidad: {result.quality_label}"
        )
        if result.ocr_triggered:
            ocr_pages = result.pages_ocr
            detail += f" [OCR: {ocr_pages} págs]"

        self._write_log(f"{line}\n{detail}\n")

        # State
        self._state["files"][filename] = {
            "status": "indexed",
            "chunks": result.chunks,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "total_pages": result.total_pages,
            "pages_pymupdf": result.pages_pymupdf,
            "pages_ocr": result.pages_ocr,
            "pages_failed": result.pages_failed,
            "ocr_triggered": result.ocr_triggered,
            "extraction_rate": result.extraction_rate,
        }
        self._save_state()

        # Summary JSONL
        self._append_jsonl(self._summary_file, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "project_id": self.project_id,
            "event": "file_ok",
            "file": filename,
            "chapters": result.chapters,
            "sections": result.sections,
            "subsections": result.subsections,
            "chunks": result.chunks,
            "tokens_avg": result.tokens_avg,
        })

        # OCR detail log
        if result.ocr_triggered and result.page_results:
            self._write_ocr_detail(result)

    def file_error(self, filename: str, error: str, ocr_attempted: bool = False):
        """Registra un archivo que falló en el procesamiento."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._write_log(
            f"{ts} | ERROR    | {filename} → {error}\n"
            f"{ts} | DEBUG    | SKIP {filename} — marcado FAILED, continúa con siguiente\n"
        )

        self._errors.append({"file": filename, "error": error})

        self._state["files"][filename] = {
            "status": "failed",
            "chunks": 0,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
            "ocr_attempted": ocr_attempted,
        }
        self._save_state()

        self._append_jsonl(self._summary_file, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "project_id": self.project_id,
            "event": "file_error",
            "file": filename,
            "error": error,
            "ocr_attempted": ocr_attempted,
        })

    def file_skipped(self, filename: str):
        """Registra un archivo omitido (ya indexado)."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._write_log(f"{ts} | INFO     | SKIP {filename} — ya indexado\n")

    def finish_project(self):
        """Cierra el logging del proyecto y escribe resumen."""
        duration_s = time.time() - self._start_time if self._start_time else 0.0
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        total_ok = sum(1 for r in self._results if r.status == "indexed")
        total_failed = len(self._errors)
        total_chunks = sum(r.chunks for r in self._results)

        # Update global state
        self._state["total_chunks"] = sum(
            f.get("chunks", 0) for f in self._state["files"].values()
        )
        self._state["total_indexed"] = self._state["total_chunks"]

        if total_failed == 0 and total_ok > 0:
            self._state["status"] = "complete"
        elif total_ok > 0:
            self._state["status"] = "partial"
        else:
            self._state["status"] = "failed"
        self._save_state()

        # Summary log
        lines = [
            f"{ts} | INFO     | {'=' * 60}",
            f"{ts} | WARNING  | SUMMARY {self.project_id} → "
            f"{total_ok}/{total_ok + total_failed} OK | "
            f"{total_chunks} chunks | {total_failed} error{'es' if total_failed != 1 else ''} | "
            f"{duration_s:.0f}s",
        ]
        for err in self._errors:
            lines.append(
                f"{ts} | WARNING  |   FAILED: {err['file']} — {err['error']}"
            )

        # Quality table
        lines.append(f"{ts} | INFO     |")
        lines.append(f"{ts} | INFO     |   Calidad de extracción por archivo:")
        lines.append(f"{ts} | INFO     |   {'Archivo':<45} {'Calidad':<14} {'Leído':>6}  {'OCR págs':>8}")
        lines.append(f"{ts} | INFO     |   {'-' * 75}")
        for r in self._results:
            ocr_str = str(r.pages_ocr) if r.ocr_triggered else "0"
            lines.append(
                f"{ts} | INFO     |   {r.filename:<45} {r.quality_label:<14} "
                f"{r.extraction_rate:>5.0f}%  {ocr_str:>8}"
            )
        for err in self._errors:
            lines.append(
                f"{ts} | INFO     |   {err['file']:<45} {'unreadable':<14} {'0':>6}%  {'0':>8}  [FAILED]"
            )
        lines.append(f"{ts} | INFO     | {'=' * 60}")

        self._write_log("\n".join(lines) + "\n")

        # Global summary JSONL
        self._append_jsonl(self._summary_file, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "project_id": self.project_id,
            "event": "project_complete",
            "pdfs_total": total_ok + total_failed,
            "pdfs_ok": total_ok,
            "pdfs_failed": total_failed,
            "chunks": total_chunks,
            "vectors_indexed": total_chunks,
            "pinecone_index": settings.pinecone_index_name,
            "duration_s": round(duration_s, 1),
        })

    # ── Query methods ───────────────────────────────────────────────────

    def is_already_indexed(self, filename: str) -> bool:
        """Verifica si un archivo ya está indexado."""
        file_state = self._state.get("files", {}).get(filename, {})
        return file_state.get("status") == "indexed"

    def is_failed(self, filename: str) -> bool:
        """Verifica si un archivo falló previamente."""
        file_state = self._state.get("files", {}).get(filename, {})
        return file_state.get("status") == "failed"

    def get_failed_files(self) -> list[str]:
        """Retorna lista de archivos con status 'failed'."""
        return [
            fn for fn, info in self._state.get("files", {}).items()
            if info.get("status") == "failed"
        ]

    def get_project_status(self) -> dict:
        """Retorna estado resumido del proyecto."""
        files = self._state.get("files", {})
        ok = sum(1 for f in files.values() if f.get("status") == "indexed")
        failed = sum(1 for f in files.values() if f.get("status") == "failed")
        return {
            "status": self._state.get("status", "pending"),
            "ok": ok,
            "failed": failed,
            "total_chunks": self._state.get("total_chunks", 0),
        }

    def get_state(self) -> dict:
        """Retorna state.json completo."""
        return self._state.copy()

    # ── OCR progress (real-time) ────────────────────────────────────────

    def ocr_page_start(self, filename: str, page: int, total: int):
        """Log de inicio de OCR en una página."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._write_log(
            f"{ts} | DEBUG    |   OCR {filename} pág {page}/{total}\r"
        )

    def ocr_page_result(self, filename: str, result: PageOCRResult):
        """Log del resultado de OCR de una página."""
        if result.error or result.warnings:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if result.error:
                self._write_log(
                    f"{ts} | WARNING  |   >> {result.page:>4}  {result.method:<12} "
                    f"chars={result.chars_extracted:>5}  ERROR: {result.error}\n"
                )
            elif result.warnings:
                self._write_log(
                    f"{ts} | WARNING  |   >> {result.page:>4}  {result.method:<12} "
                    f"chars={result.chars_extracted:>5}  conf={result.avg_confidence or 0:.0f}%  "
                    f"[{', '.join(result.warnings)}]\n"
                )

    def print_ocr_report(self):
        """Imprime reporte OCR en consola."""
        if not self._ocr_file.exists():
            print(f"No hay datos OCR para {self.project_id}")
            return

        print(f"\n{'=' * 60}")
        print(f"REPORTE OCR — {self.project_id}")
        print(f"{'=' * 60}\n")

        with open(self._ocr_file, "r", encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                if entry["type"] == "file_summary":
                    print(f"  Archivo: {entry['file']}")
                    print(f"  Paginas OCR:      {entry['pages_ocr']}/{entry['total_pages']}")
                    print(f"  Paginas fallidas: {entry['pages_failed']}")
                    print(f"  Conf. promedio:   {entry.get('ocr_avg_conf', 0):.0f}%")
                    print(f"  Calidad:          {entry['quality']} ({entry['extraction_rate']}%)\n")
                elif entry["type"] == "page_detail":
                    conf_str = f"{entry.get('confidence', 0):.0f}%" if entry.get("confidence") else "  —"
                    error_str = f"ERROR: {entry['error']}" if entry.get("error") else ""
                    warn_str = f"[{','.join(entry.get('warnings', []))}]" if entry.get("warnings") else ""
                    print(
                        f"    Pag {entry['page']:>4}:  {entry['method']:<10} "
                        f"chars={entry['chars']:>5}  conf={conf_str}  "
                        f"{entry.get('duration_ms', 0)}ms  {error_str}{warn_str}"
                    )

        print(f"{'=' * 60}")

    # ── Private helpers ─────────────────────────────────────────────────

    def _write_log(self, text: str):
        """Append to log file."""
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(text)

    def _append_jsonl(self, path: Path, data: dict):
        """Append one JSON line."""
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _write_ocr_detail(self, result: FileExtractionResult):
        """Escribe detalle OCR página a página."""
        # File summary
        self._append_jsonl(self._ocr_file, {
            "type": "file_summary",
            "file": result.filename,
            "project_id": self.project_id,
            "total_pages": result.total_pages,
            "pages_pymupdf": result.pages_pymupdf,
            "pages_ocr": result.pages_ocr,
            "pages_failed": result.pages_failed,
            "ocr_avg_conf": result.ocr_avg_confidence or 0,
            "extraction_rate": result.extraction_rate,
            "quality": result.quality_label.upper(),
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        # Page details (only problematic pages)
        for pr in result.page_results:
            if pr.error or pr.warnings or (pr.method == "ocr" and pr.chars_extracted < settings.ocr_min_chars_per_page):
                self._append_jsonl(self._ocr_file, {
                    "type": "page_detail",
                    "file": result.filename,
                    "project_id": self.project_id,
                    "page": pr.page,
                    "method": pr.method,
                    "chars": pr.chars_extracted,
                    "lines": pr.lines_extracted,
                    "confidence": pr.avg_confidence,
                    "duration_ms": pr.duration_ms,
                    "warnings": pr.warnings,
                    "error": pr.error,
                })
