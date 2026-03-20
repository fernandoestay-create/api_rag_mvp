"""
Configuración centralizada del sistema PIA RAG.
Usa pydantic-settings para cargar desde .env o variables de entorno.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── OpenAI ──────────────────────────────────────────────
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o"

    # ── Pinecone (índice productivo — NO cambiar) ──────────
    pinecone_api_key: str = ""
    pinecone_index_name: str = "api-rag-mvp"
    pinecone_host: str = "https://api-rag-mvp-96gaajy.svc.aped-4627-b74a.pinecone.io"

    # ── Chunking (in characters — ~5 chars/word in Spanish) ─
    chunk_size: int = 1200
    chunk_overlap: int = 200

    # ── RAG ─────────────────────────────────────────────────
    top_k: int = 8
    min_score: float = 0.30

    # ── API (Render) ────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    render_service_id: str = "srv-d5rr9a63jp1c73e1fibg"

    # ── Embedding batch ─────────────────────────────────────
    embedding_batch_size: int = 50
    embedding_max_retries: int = 3
    embedding_retry_delay: float = 5.0

    # ── Pinecone upsert ─────────────────────────────────────
    pinecone_upsert_batch: int = 100

    # ── OCR ─────────────────────────────────────────────────
    ocr_lang: str = "spa"
    ocr_timeout: int = 60
    ocr_min_chars_per_page: int = 100
    poppler_path: str = r"C:\poppler\Library\bin"
    tesseract_cmd: str = r"C:\Users\FernandoEstay\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"

    # ── Paths ───────────────────────────────────────────────
    @property
    def base_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def documents_dir(self) -> Path:
        """Carpeta con los PDFs originales descargados."""
        return self.base_dir / "00.InformaciónBase" / "Documento_proyectos"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def extraction_logs_dir(self) -> Path:
        return self.logs_dir / "extraction"

    @property
    def indexing_logs_dir(self) -> Path:
        return self.logs_dir / "indexing"


# Singleton
settings = Settings()
