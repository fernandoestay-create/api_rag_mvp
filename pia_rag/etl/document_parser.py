"""
etl/document_parser.py — Parser jerárquico de PDFs con PyMuPDF.

Extrae texto de PDFs y construye un árbol de estructura documental:
  capítulo → sección → subsección → párrafos

Si el PDF es escaneado (< OCR_MIN_CHARS promedio por página), activa OCR con pytesseract.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from loguru import logger

from pia_rag.config import settings

# ─── Regex patterns para detectar encabezados numerados ─────────────────────
# Patrones típicos en EIA chilenos:
#   "1. DESCRIPCIÓN DEL PROYECTO"
#   "3.2 Flora y Vegetación"
#   "3.2.1 Metodología de muestreo"
#   "CAPÍTULO 4: MEDIDAS DE MITIGACIÓN"

_RE_CHAPTER = re.compile(
    r"^(?:CAP[ÍI]TULO\s+)?(\d{1,2})[\.\-\s:]+\s*(.{5,120})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_RE_SECTION = re.compile(
    r"^(\d{1,2}\.\d{1,2})[\.\-\s:]+\s*(.{5,120})\s*$",
    re.MULTILINE,
)
_RE_SUBSECTION = re.compile(
    r"^(\d{1,2}\.\d{1,2}\.\d{1,3})[\.\-\s:]+\s*(.{5,120})\s*$",
    re.MULTILINE,
)

# Unidades/patrones que generan falsos positivos en detección de secciones.
# Ej: "1.637 viviendas", "36.050 m2", "4.795 3.849" (tablas numéricas)
_FALSE_POSITIVE_RE = re.compile(
    r"^\d+[.,]\d+\s*(?:m2|m²|m\s|km|ha|viviendas?|unidades?|ton|kg|lt|%|l/s|"
    r"hrs?|días?|meses|años|personas?|habitantes?|vehículos?|construidas?"
    r"|lineales|asociados|de\s)",
    re.IGNORECASE,
)


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class PageResult:
    """Resultado de extracción de una página individual."""
    page_num: int
    text: str
    chars: int
    method: str  # "pymupdf" | "ocr" | "empty"
    quality: str  # "good" | "partial" | "empty" | "failed"
    ocr_confidence: Optional[float] = None
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class StructureNode:
    """Nodo del árbol jerárquico del documento."""
    level: str  # "chapter" | "section" | "subsection" | "paragraph"
    num: str  # "3", "3.2", "3.2.1", ""
    title: str
    text: str
    page_start: int
    page_end: int
    children: list["StructureNode"] = field(default_factory=list)


@dataclass
class DocumentStructure:
    """Estructura completa de un PDF parseado."""
    filename: str
    doc_id: str
    total_pages: int
    chapters: list[StructureNode]
    page_results: list[PageResult]
    full_text: str  # texto concatenado completo

    # Estadísticas
    pages_pymupdf: int = 0
    pages_ocr: int = 0
    pages_failed: int = 0
    pages_empty: int = 0
    ocr_triggered: bool = False
    chars_total: int = 0

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

    @property
    def n_chapters(self) -> int:
        return len(self.chapters)

    @property
    def n_sections(self) -> int:
        return sum(len(ch.children) for ch in self.chapters)

    @property
    def n_subsections(self) -> int:
        return sum(
            len(sec.children)
            for ch in self.chapters
            for sec in ch.children
        )


# ─── OCR helpers ────────────────────────────────────────────────────────────

def _ocr_image(img, lang: str) -> tuple[str, Optional[float]]:
    """OCR a single PIL image. Returns (text, confidence)."""
    import pytesseract

    w, h = img.size
    img_cropped = img.crop((0, int(h * 0.02), w, int(h * 0.92)))

    # Single OCR call — image_to_data gives both text and confidence
    data = pytesseract.image_to_data(
        img_cropped, lang=lang, output_type=pytesseract.Output.DICT
    )
    confidences = [
        int(c) for c, t in zip(data["conf"], data["text"])
        if int(c) > 0 and t.strip()
    ]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    # Reconstruct text from data (avoids a second OCR pass)
    words = [t for t in data["text"] if t.strip()]
    text = " ".join(words)

    return text.strip(), round(avg_conf, 1)


def _ocr_page(pdf_path: Path, page_num: int, dpi: int = 200) -> tuple[str, Optional[float]]:
    """Extrae texto de una página vía OCR. Retorna (text, confidence)."""
    try:
        from pdf2image import convert_from_path
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

        images = convert_from_path(
            str(pdf_path),
            dpi=dpi,
            first_page=page_num,
            last_page=page_num,
            poppler_path=settings.poppler_path,
        )
        if not images:
            return "", None

        return _ocr_image(images[0], settings.ocr_lang)

    except Exception as e:
        logger.warning(f"OCR failed for page {page_num}: {e}")
        return "", None


def _ocr_with_timeout(pdf_path: Path, page_num: int, timeout: int = 60) -> tuple[str, Optional[float], Optional[str]]:
    """Wrapper con timeout para OCR. Retorna (text, confidence, error)."""
    result = {"text": "", "conf": None, "error": None}

    def _worker():
        try:
            result["text"], result["conf"] = _ocr_page(pdf_path, page_num)
        except Exception as e:
            result["error"] = str(e)

    t = threading.Thread(target=_worker)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "", None, f"tesseract timeout after {timeout}s"

    return result["text"], result["conf"], result["error"]


def _ocr_image_with_timeout(
    img,
    lang: str,
    timeout: int = 30,
) -> tuple[str, Optional[float], Optional[str]]:
    """OCR una imagen con timeout por página. Evita que planos gigantes bloqueen."""
    result: dict = {"text": "", "conf": None, "error": None}

    def _worker():
        try:
            result["text"], result["conf"] = _ocr_image(img, lang)
        except Exception as e:
            result["error"] = str(e)

    t = threading.Thread(target=_worker)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "", None, f"ocr timeout after {timeout}s"
    return result["text"], result["conf"], result["error"]


def _ocr_batch(
    pdf_path: Path,
    page_indices: list[int],
    dpi: int = 150,
    batch_size: int = 10,
    page_timeout: int = 30,
) -> dict[int, tuple[str, Optional[float], Optional[str]]]:
    """
    OCR por lotes: convierte `batch_size` páginas a la vez con pdf2image,
    luego procesa cada imagen con tesseract (con timeout por página).
    Retorna {page_idx: (text, confidence, error)}.
    """
    from pdf2image import convert_from_path
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    results: dict[int, tuple[str, Optional[float], Optional[str]]] = {}

    # Sort and process in batches
    sorted_pages = sorted(page_indices)
    for batch_start in range(0, len(sorted_pages), batch_size):
        batch = sorted_pages[batch_start:batch_start + batch_size]
        first_pg = batch[0] + 1  # pdf2image uses 1-based
        last_pg = batch[-1] + 1

        try:
            images = convert_from_path(
                str(pdf_path),
                dpi=dpi,
                first_page=first_pg,
                last_page=last_pg,
                poppler_path=settings.poppler_path,
            )
        except Exception as e:
            for idx in batch:
                results[idx] = ("", None, f"pdf2image error: {e}")
            continue

        # Map images back to page indices
        for idx in batch:
            img_idx = idx - (first_pg - 1)
            if img_idx < 0 or img_idx >= len(images):
                results[idx] = ("", None, "image index out of range")
                continue
            text, conf, error = _ocr_image_with_timeout(
                images[img_idx], settings.ocr_lang, timeout=page_timeout,
            )
            results[idx] = (text, conf, error)

        # Free memory — large images can be heavy
        del images

    return results


# ─── Structure detection ────────────────────────────────────────────────────

def _is_false_positive(num: str, title: str) -> bool:
    """
    Filtra falsos positivos de la detección de secciones.
    Ej: "1.637 viviendas", "36.050 m2", "4.795 3.849" (tablas numéricas).
    """
    full = f"{num} {title}"
    if _FALSE_POSITIVE_RE.match(full):
        return True
    # Números puros (sin texto alfabético significativo en el título)
    alpha_chars = sum(1 for c in title if c.isalpha())
    if alpha_chars < 3:
        return True
    # Título es solo puntos suspensivos (TOC entries)
    if re.match(r"^[.\s…]+$", title):
        return True
    return False


def _detect_headers_by_regex(text: str) -> list[tuple[str, str, str, int]]:
    """
    Detecta encabezados en el texto completo usando regex.
    Retorna lista de (level, num, title, char_pos).
    """
    headers: list[tuple[str, str, str, int]] = []

    for m in _RE_SUBSECTION.finditer(text):
        num, title = m.group(1).strip(), m.group(2).strip()
        if not _is_false_positive(num, title):
            headers.append(("subsection", num, title, m.start()))
    for m in _RE_SECTION.finditer(text):
        num, title = m.group(1).strip(), m.group(2).strip()
        if not any(h[1] == num for h in headers) and not _is_false_positive(num, title):
            headers.append(("section", num, title, m.start()))
    for m in _RE_CHAPTER.finditer(text):
        num, title = m.group(1).strip(), m.group(2).strip()
        if not any(h[1] == num for h in headers) and not _is_false_positive(num, title):
            headers.append(("chapter", num, title, m.start()))

    # Sort by position in text
    headers.sort(key=lambda h: h[3])
    return headers


def _detect_headers_by_font(doc: "fitz.Document") -> list[tuple[str, str, str, int, int]]:
    """
    Detecta encabezados analizando font-size + bold con PyMuPDF.
    Retorna lista de (level, num, title, char_pos_approx, page_num).

    Estrategia:
    1. Determinar el tamaño de fuente del body text (el más frecuente)
    2. Líneas con font-size > body_size Y (bold O match regex numérico) → heading
    3. El nivel se determina por la numeración decimal (2.1 → section, 2.1.1 → subsection)
    """
    from collections import Counter

    # Pass 1: determine body font size
    size_counter: Counter = Counter()
    for page_num in range(min(30, len(doc))):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    size = round(span["size"], 1)
                    text = span["text"].strip()
                    if text and len(text) > 5:
                        size_counter[size] += 1

    if not size_counter:
        return []

    body_size = size_counter.most_common(1)[0][0]

    # Pass 2: find headings
    section_re = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)")
    chapter_re = re.compile(r"^(?:CAP[ÍI]TULO\s+)?(\d{1,2})[\.\-\s:]+\s*(.+)", re.IGNORECASE)
    headers: list[tuple[str, str, str, int, int]] = []
    seen_nums: set[str] = set()

    cumulative_chars = 0
    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        page_text_len = 0

        for block in blocks:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                line_text = "".join(s["text"] for s in line["spans"]).strip()
                if not line_text or len(line_text) < 3:
                    page_text_len += len(line_text) + 1
                    continue

                max_span = max(line["spans"], key=lambda s: s["size"])
                size = round(max_span["size"], 1)
                is_bold = bool(max_span["flags"] & 16)

                # Only consider lines with larger-than-body font or bold
                if size <= body_size and not is_bold:
                    page_text_len += len(line_text) + 1
                    continue

                # Try numbered section match
                m = section_re.match(line_text)
                if not m:
                    m = chapter_re.match(line_text)
                if m:
                    num = m.group(1).strip()
                    title = m.group(2).strip()[:120]

                    if _is_false_positive(num, title) or num in seen_nums:
                        page_text_len += len(line_text) + 1
                        continue

                    # Determine level from numbering
                    dot_count = num.count(".")
                    if dot_count == 0:
                        level = "chapter"
                    elif dot_count == 1:
                        level = "section"
                    else:
                        level = "subsection"

                    char_pos_approx = cumulative_chars + page_text_len
                    headers.append((level, num, title, char_pos_approx, page_num + 1))
                    seen_nums.add(num)

                page_text_len += len(line_text) + 1

        cumulative_chars += page_text_len

    return headers


def _detect_headers(
    text: str,
    doc: "fitz.Document | None" = None,
) -> list[tuple[str, str, str, int]]:
    """
    Detecta encabezados combinando font-analysis y regex fallback.
    Si el doc de PyMuPDF está disponible, usa font-size como método primario.
    """
    # Method 1: font-based detection (preferred, more accurate)
    if doc is not None:
        font_headers = _detect_headers_by_font(doc)
        if len(font_headers) >= 3:
            # Convert to standard format (drop page_num)
            # Map back to char positions in full_text, picking the occurrence
            # closest to the approximate position from font analysis.
            # This avoids matching TOC entries (which appear early in the text).
            result = []
            for level, num, title, char_pos_approx, page_num in font_headers:
                pattern = re.escape(num) + r"\s+" + re.escape(title[:20])
                best_pos = char_pos_approx  # fallback
                best_dist = float("inf")
                for m in re.finditer(pattern, text):
                    dist = abs(m.start() - char_pos_approx)
                    if dist < best_dist:
                        best_dist = dist
                        best_pos = m.start()
                result.append((level, num, title, best_pos))
            result.sort(key=lambda h: h[3])
            logger.debug(f"Font-based detection: {len(result)} headers found")
            return result

    # Method 2: regex fallback
    headers = _detect_headers_by_regex(text)
    logger.debug(f"Regex-based detection: {len(headers)} headers found")
    return headers


def _char_to_page(char_pos: int, page_char_offsets: list[int]) -> int:
    """Convierte posición de carácter a número de página."""
    for i, offset in enumerate(page_char_offsets):
        if char_pos < offset:
            return max(1, i)
    return len(page_char_offsets)


def _build_tree(
    headers: list[tuple[str, str, str, int]],
    full_text: str,
    page_char_offsets: list[int],
    total_pages: int,
) -> list[StructureNode]:
    """Construye el árbol jerárquico desde los encabezados detectados."""
    if not headers:
        # Sin estructura detectada: un solo nodo con todo el texto
        return [StructureNode(
            level="chapter",
            num="1",
            title="Documento completo",
            text=full_text,
            page_start=1,
            page_end=total_pages,
        )]

    chapters: list[StructureNode] = []
    current_chapter: Optional[StructureNode] = None
    current_section: Optional[StructureNode] = None

    for idx, (level, num, title, char_pos) in enumerate(headers):
        # Extract text between this header and the next
        next_pos = headers[idx + 1][3] if idx + 1 < len(headers) else len(full_text)
        segment_text = full_text[char_pos:next_pos].strip()
        page_start = _char_to_page(char_pos, page_char_offsets)
        page_end = _char_to_page(next_pos - 1, page_char_offsets) if next_pos > char_pos else page_start

        node = StructureNode(
            level=level,
            num=num,
            title=title,
            text=segment_text,
            page_start=page_start,
            page_end=page_end,
        )

        if level == "chapter":
            current_chapter = node
            current_section = None
            chapters.append(node)
        elif level == "section":
            if current_chapter is None:
                # Create implicit chapter
                current_chapter = StructureNode(
                    level="chapter", num="0", title="Sin capítulo",
                    text="", page_start=page_start, page_end=page_end,
                )
                chapters.append(current_chapter)
            current_section = node
            current_chapter.children.append(node)
            current_chapter.page_end = max(current_chapter.page_end, page_end)
        elif level == "subsection":
            if current_section is None:
                if current_chapter is None:
                    current_chapter = StructureNode(
                        level="chapter", num="0", title="Sin capítulo",
                        text="", page_start=page_start, page_end=page_end,
                    )
                    chapters.append(current_chapter)
                current_section = StructureNode(
                    level="section", num="0", title="Sin sección",
                    text="", page_start=page_start, page_end=page_end,
                )
                current_chapter.children.append(current_section)
            current_section.children.append(node)
            current_section.page_end = max(current_section.page_end, page_end)
            if current_chapter:
                current_chapter.page_end = max(current_chapter.page_end, page_end)

    return chapters


# ─── Main parser ────────────────────────────────────────────────────────────

class DocumentStructureParser:
    """
    Parser que extrae texto de un PDF y construye un árbol jerárquico.

    Pasos:
    1. Extraer texto con PyMuPDF (rápido, sin OCR)
    2. Si el texto promedio por página es < OCR_MIN_CHARS → activar OCR
    3. Detectar encabezados numerados (capítulos, secciones, subsecciones)
    4. Construir árbol jerárquico
    """

    def parse(self, pdf_path: Path) -> DocumentStructure:
        """Parsea un PDF y retorna su estructura jerárquica."""
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")

        doc_id = hashlib.md5(pdf_path.name.encode()).hexdigest()[:16]
        filename = pdf_path.name

        try:
            doc = fitz.open(str(pdf_path))
        except Exception as e:
            logger.error(f"Error abriendo {filename}: {e}")
            return DocumentStructure(
                filename=filename,
                doc_id=doc_id,
                total_pages=0,
                chapters=[],
                page_results=[],
                full_text="",
            )

        total_pages = len(doc)
        page_texts: list[str] = []
        page_results: list[PageResult] = []
        page_char_offsets: list[int] = []  # cumulative char offset per page

        # ── Pass 1: PyMuPDF extraction ──────────────────────────────────
        cumulative_chars = 0
        for i in range(total_pages):
            page_char_offsets.append(cumulative_chars)
            start_ms = time.time()
            try:
                page = doc[i]
                text = page.get_text("text") or ""
                text = text.strip()
                chars = len(text)
                duration_ms = int((time.time() - start_ms) * 1000)

                if chars >= settings.ocr_min_chars_per_page:
                    page_texts.append(text)
                    page_results.append(PageResult(
                        page_num=i + 1, text=text, chars=chars,
                        method="pymupdf", quality="good", duration_ms=duration_ms,
                    ))
                else:
                    page_texts.append(text)  # keep whatever we got
                    page_results.append(PageResult(
                        page_num=i + 1, text=text, chars=chars,
                        method="pymupdf", quality="empty" if chars == 0 else "partial",
                        duration_ms=duration_ms,
                    ))
                cumulative_chars += len(text) + 1  # +1 for newline join
            except Exception as e:
                page_texts.append("")
                page_results.append(PageResult(
                    page_num=i + 1, text="", chars=0,
                    method="pymupdf", quality="failed",
                    duration_ms=int((time.time() - start_ms) * 1000),
                    error=str(e),
                ))
                cumulative_chars += 1

        # NOTE: do NOT close doc yet — we need it for font-based header detection

        # ── Check if OCR is needed ──────────────────────────────────────
        avg_chars = sum(pr.chars for pr in page_results) / total_pages if total_pages > 0 else 0
        ocr_triggered = avg_chars < settings.ocr_min_chars_per_page

        if ocr_triggered:
            # Adaptive DPI: lower for large PDFs to save time
            ocr_dpi = 150 if total_pages > 50 else 200
            ocr_pages = [idx for idx, pr in enumerate(page_results)
                         if pr.quality in ("empty", "partial")]
            logger.warning(
                f"{filename} → PDF escaneado (avg {avg_chars:.0f} chars/pág), "
                f"activando OCR para {len(ocr_pages)}/{total_pages} páginas a {ocr_dpi} DPI"
            )

            # Batch OCR: converts multiple pages at once (reduces pdf open overhead)
            batch_size = 10 if total_pages > 100 else 5
            start_ocr = time.time()
            ocr_results = _ocr_batch(
                pdf_path, ocr_pages, dpi=ocr_dpi, batch_size=batch_size,
            )
            ocr_elapsed = time.time() - start_ocr
            logger.info(f"{filename} → OCR completado en {ocr_elapsed:.1f}s ({len(ocr_pages)} páginas)")

            for idx in ocr_pages:
                text, conf, error = ocr_results.get(idx, ("", None, "missing from batch"))
                if error:
                    page_results[idx] = PageResult(
                        page_num=idx + 1, text="", chars=0,
                        method="ocr", quality="failed",
                        duration_ms=0, error=error,
                    )
                elif len(text) > 10:
                    page_texts[idx] = text
                    warnings = []
                    if conf is not None and conf < 60:
                        warnings.append("baja_confianza")
                    quality = "good" if len(text) >= settings.ocr_min_chars_per_page else "partial"
                    page_results[idx] = PageResult(
                        page_num=idx + 1, text=text, chars=len(text),
                        method="ocr", quality=quality,
                        ocr_confidence=conf, duration_ms=0,
                        warnings=warnings,
                    )
                else:
                    page_results[idx] = PageResult(
                        page_num=idx + 1, text="", chars=0,
                        method="ocr", quality="empty", duration_ms=0,
                    )

        # ── Stats ───────────────────────────────────────────────────────
        pages_pymupdf = sum(1 for pr in page_results if pr.method == "pymupdf" and pr.quality == "good")
        pages_ocr = sum(1 for pr in page_results if pr.method == "ocr" and pr.quality in ("good", "partial"))
        pages_failed = sum(1 for pr in page_results if pr.quality == "failed")
        pages_empty = sum(1 for pr in page_results if pr.quality == "empty")
        chars_total = sum(pr.chars for pr in page_results)

        # ── Build full text and structure ───────────────────────────────
        full_text = "\n".join(page_texts)

        # Recalculate page_char_offsets based on actual joined text
        page_char_offsets = []
        offset = 0
        for pt in page_texts:
            page_char_offsets.append(offset)
            offset += len(pt) + 1  # +1 for \n

        # Use font-based detection (primary) with regex fallback
        headers = _detect_headers(full_text, doc=doc if not ocr_triggered else None)
        doc.close()
        chapters = _build_tree(headers, full_text, page_char_offsets, total_pages)

        return DocumentStructure(
            filename=filename,
            doc_id=doc_id,
            total_pages=total_pages,
            chapters=chapters,
            page_results=page_results,
            full_text=full_text,
            pages_pymupdf=pages_pymupdf,
            pages_ocr=pages_ocr,
            pages_failed=pages_failed,
            pages_empty=pages_empty,
            ocr_triggered=ocr_triggered,
            chars_total=chars_total,
        )
