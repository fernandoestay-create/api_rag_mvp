"""
storage/pinecone_client.py — Cliente Pinecone para el índice api-rag-mvp.

Siempre conecta por host directo (más rápido que resolver por nombre).
Maneja upsert con batching y retry, y query con filtros metadata.
"""

from __future__ import annotations

import time
from typing import Optional

from loguru import logger
from openai import OpenAI, RateLimitError, APIConnectionError

from pia_rag.config import settings
from pia_rag.etl.enriched_chunker import EnrichedChunk


class PineconeClient:
    """Cliente para operaciones sobre el índice api-rag-mvp en Pinecone."""

    def __init__(self):
        from pinecone import Pinecone

        self._pc = Pinecone(api_key=settings.pinecone_api_key)
        self._index = self._pc.Index(
            name=settings.pinecone_index_name,
            host=settings.pinecone_host,
        )
        self._openai = OpenAI(api_key=settings.openai_api_key)
        logger.info(f"Pinecone conectado: {settings.pinecone_index_name} @ {settings.pinecone_host}")

    # ── Embedding ───────────────────────────────────────────────────────

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Genera embeddings con OpenAI en batches."""
        all_embeddings: list[list[float]] = []
        batch_size = settings.embedding_batch_size

        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            # Clean newlines for better embedding quality
            batch_clean = [t.replace("\n", " ") for t in batch]

            for attempt in range(settings.embedding_max_retries):
                try:
                    response = self._openai.embeddings.create(
                        input=batch_clean,
                        model=settings.openai_embedding_model,
                    )
                    batch_emb = [d.embedding for d in response.data]
                    all_embeddings.extend(batch_emb)
                    break
                except (RateLimitError, APIConnectionError) as e:
                    logger.warning(f"Embedding batch {i} intento {attempt + 1} falló: {e}")
                    if attempt < settings.embedding_max_retries - 1:
                        time.sleep(settings.embedding_retry_delay * (attempt + 1))
                    else:
                        raise
                except Exception as e:
                    logger.error(f"Error fatal en embedding batch {i}: {e}")
                    raise

        return all_embeddings

    # ── Upsert ──────────────────────────────────────────────────────────

    def upsert_chunks(self, chunks: list[EnrichedChunk]) -> int:
        """
        Genera embeddings y hace upsert a Pinecone.
        Retorna el número de vectores indexados.
        """
        if not chunks:
            return 0

        # Generate embeddings
        embed_texts = [c.embed_text for c in chunks]
        logger.info(f"Generando embeddings para {len(chunks)} chunks...")
        embeddings = self.embed_texts(embed_texts)

        if len(embeddings) != len(chunks):
            logger.error(f"Mismatch: {len(embeddings)} embeddings para {len(chunks)} chunks")
            return 0

        # Upsert in batches
        batch_size = settings.pinecone_upsert_batch
        total_upserted = 0

        for i in range(0, len(chunks), batch_size):
            batch_chunks = chunks[i: i + batch_size]
            batch_embs = embeddings[i: i + batch_size]

            vectors = []
            for chunk, emb in zip(batch_chunks, batch_embs):
                vectors.append({
                    "id": chunk.chunk_id,
                    "values": emb,
                    "metadata": chunk.to_pinecone_metadata(),
                })

            try:
                self._index.upsert(vectors=vectors)
                total_upserted += len(vectors)
                logger.debug(f"Upsert batch {i // batch_size + 1}: {len(vectors)} vectors OK")
            except Exception as e:
                logger.error(f"Error en upsert batch {i}: {e}")
                # Continue with next batch
                continue

        logger.info(f"Pinecone {settings.pinecone_index_name}: {total_upserted} vectors upserted")
        return total_upserted

    # ── Query ───────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 8,
        project_id: Optional[str] = None,
        chunk_level: Optional[str] = None,
        chapter_title: Optional[str] = None,
        doc_type: Optional[str] = None,
    ) -> list[dict]:
        """
        Busca en Pinecone con filtros opcionales.
        Retorna lista de resultados con score, text y metadata.
        """
        # Embed query
        response = self._openai.embeddings.create(
            input=query.replace("\n", " "),
            model=settings.openai_embedding_model,
        )
        query_vector = response.data[0].embedding

        # Build filter
        filter_dict: dict = {}
        if project_id:
            filter_dict["project_id"] = {"$eq": project_id}
        if chunk_level:
            filter_dict["chunk_level"] = {"$eq": chunk_level}
        if chapter_title:
            filter_dict["chapter_title"] = {"$eq": chapter_title}
        if doc_type:
            filter_dict["doc_type"] = {"$eq": doc_type}

        # Query
        results = self._index.query(
            vector=query_vector,
            top_k=top_k,
            include_metadata=True,
            filter=filter_dict if filter_dict else None,
        )

        # Format results
        formatted = []
        matches = results.matches if hasattr(results, "matches") else results.get("matches", [])
        for match in matches:
            meta = match.metadata if hasattr(match, "metadata") else match.get("metadata", {})
            score = match.score if hasattr(match, "score") else match.get("score", 0.0)
            if score < settings.min_score:
                continue
            formatted.append({
                "text": meta.get("text", ""),
                "score": round(score, 4),
                "project_id": meta.get("project_id", ""),
                "project_name": meta.get("project_name", ""),
                "chapter_title": meta.get("chapter_title", ""),
                "section_title": meta.get("section_title", ""),
                "subsection_title": meta.get("subsection_title", ""),
                "page_start": meta.get("page_start", 0),
                "page_end": meta.get("page_end", 0),
                "hierarchy_path": meta.get("hierarchy_path", ""),
                "chunk_level": meta.get("chunk_level", ""),
                "doc_type": meta.get("doc_type", ""),
                "url": meta.get("url", ""),
            })

        return formatted

    # ── Stats ───────────────────────────────────────────────────────────

    def get_index_stats(self) -> dict:
        """Retorna estadísticas del índice."""
        try:
            stats = self._index.describe_index_stats()
            total = stats.total_vector_count if hasattr(stats, "total_vector_count") else stats.get("total_vector_count", 0)
            dim = stats.dimension if hasattr(stats, "dimension") else stats.get("dimension", 0)
            fullness = stats.index_fullness if hasattr(stats, "index_fullness") else stats.get("index_fullness", 0)
            return {
                "total_vectors": total,
                "dimension": dim,
                "index_fullness": fullness,
            }
        except Exception as e:
            logger.error(f"Error obteniendo stats: {e}")
            return {"total_vectors": 0, "dimension": 0, "index_fullness": 0}
