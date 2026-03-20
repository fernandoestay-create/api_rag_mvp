"""
rag/enriched_engine.py — Motor RAG con filtros jerárquicos y generación con GPT-4o.
"""

from __future__ import annotations

from typing import Optional

from openai import OpenAI
from loguru import logger

from pia_rag.config import settings
from pia_rag.storage.pinecone_client import PineconeClient


SYSTEM_PROMPT = """Eres PIA, asistente experto en evaluación ambiental de proyectos chilenos.
Tienes acceso a una base de conocimiento con los documentos completos de cada
proyecto (EIA, DIA, RCA, anexos, adendas, ICSARAs) indexados en una base vectorial.

REGLAS:
- Responde en español, lenguaje técnico ambiental
- Usa bullet points para listas de medidas o impactos
- Si no encuentras la información, dilo explícitamente — no inventes datos
- Si hay información en varios documentos, sintetiza y distingue las fuentes
- Cita siempre la fuente con formato: (Proyecto X — Cap. Y, Secc. Z, pág. N-M)
"""


class EnrichedRAGEngine:
    """Motor RAG que busca en Pinecone y genera respuestas con GPT-4o."""

    def __init__(self):
        self._pinecone = PineconeClient()
        self._openai = OpenAI(api_key=settings.openai_api_key)

    def query(
        self,
        question: str,
        project_id: Optional[str] = None,
        chunk_level: Optional[str] = None,
        chapter_title: Optional[str] = None,
        doc_type: Optional[str] = None,
        top_k: int = 8,
        generate: bool = True,
    ) -> dict:
        """
        Ejecuta una consulta RAG completa.

        Args:
            question: Pregunta en lenguaje natural
            project_id: Filtrar por proyecto
            chunk_level: Filtrar por nivel de chunk
            chapter_title: Filtrar por título de capítulo
            doc_type: Filtrar por tipo de documento
            top_k: Número de resultados
            generate: Si True, genera respuesta con GPT-4o

        Returns:
            dict con results, answer, filters_applied
        """
        # Search
        results = self._pinecone.search(
            query=question,
            top_k=top_k,
            project_id=project_id,
            chunk_level=chunk_level,
            chapter_title=chapter_title,
            doc_type=doc_type,
        )

        filters_applied = {}
        if project_id:
            filters_applied["project_id"] = project_id
        if chunk_level:
            filters_applied["chunk_level"] = chunk_level
        if chapter_title:
            filters_applied["chapter_title"] = chapter_title
        if doc_type:
            filters_applied["doc_type"] = doc_type

        response = {
            "results": results,
            "total_found": len(results),
            "filters_applied": filters_applied,
            "answer": None,
        }

        if not generate or not results:
            return response

        # Build context
        context_parts = []
        for r in results:
            source = f"({r['project_name']} — {r['chapter_title']}"
            if r.get("section_title"):
                source += f", {r['section_title']}"
            source += f", pág. {r['page_start']}-{r['page_end']})"
            context_parts.append(f"{r['text']}\n{source}")

        context_text = "\n\n---\n\n".join(context_parts)

        # Generate answer
        try:
            completion = self._openai.chat.completions.create(
                model=settings.openai_chat_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"CONTEXTO RECUPERADO:\n{context_text}\n\n"
                            f"PREGUNTA: {question}"
                        ),
                    },
                ],
                temperature=0.2,
            )
            response["answer"] = completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Error generando respuesta: {e}")
            response["answer"] = f"Error al generar respuesta: {e}"

        return response
