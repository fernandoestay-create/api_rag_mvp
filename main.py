"""
main.py — Punto de entrada dual:
  - Render (uvicorn main:app)  → sirve la FastAPI
  - CLI    (python main.py)    → comandos ingest/status/query

Render ejecuta: uvicorn main:app --host 0.0.0.0 --port $PORT
CLI ejecuta:    python main.py ingest --project ...
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# ─── FastAPI app — Render busca "main:app" ─────────────────────────────────
from pia_rag.api.main import app  # noqa: F401  ← Render usa esto

# ─── CLI (solo se usa al ejecutar python main.py directamente) ─────────────
import typer
from rich.console import Console
from rich.table import Table

from pia_rag.config import settings

cli = typer.Typer(
    name="pia-rag",
    help="PIA RAG — Sistema RAG para expedientes de evaluación ambiental",
)
console = Console()


# ── Ingest ──────────────────────────────────────────────────────────────────

@cli.command()
def ingest(
    project: Optional[str] = typer.Option(None, help="Ruta a carpeta del proyecto"),
    all: bool = typer.Option(False, "--all", help="Procesar todos los proyectos"),
    resume: bool = typer.Option(True, help="Omitir archivos ya indexados"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="Reintentar archivos fallidos"),
):
    """Ingesta PDFs de un proyecto (o todos) al índice Pinecone."""
    from pia_rag.etl.enriched_pipeline import EnrichedETLPipeline

    pipeline = EnrichedETLPipeline()

    if all:
        # Process all project folders
        docs_dir = settings.documents_dir
        if not docs_dir.exists():
            console.print(f"[red]No se encontró la carpeta de documentos: {docs_dir}[/red]")
            raise typer.Exit(1)

        project_dirs = sorted([
            d for d in docs_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])
        console.print(f"[bold]Procesando {len(project_dirs)} proyectos...[/bold]\n")

        for proj_dir in project_dirs:
            try:
                stats = pipeline.process_project(proj_dir, resume=resume, retry_failed=retry_failed)
                _print_stats(stats)
            except Exception as e:
                console.print(f"[red]Error en {proj_dir.name}: {e}[/red]")

    elif project:
        project_path = Path(project)
        if not project_path.exists():
            # Try as relative to documents_dir
            project_path = settings.documents_dir / project
        if not project_path.exists():
            console.print(f"[red]No se encontró: {project_path}[/red]")
            raise typer.Exit(1)

        stats = pipeline.process_project(project_path, resume=resume, retry_failed=retry_failed)
        _print_stats(stats)

    else:
        console.print("[yellow]Especifica --project o --all[/yellow]")
        raise typer.Exit(1)


def _print_stats(stats: dict):
    """Imprime estadísticas de ingesta."""
    status_color = "green" if stats["status"] == "complete" else "yellow"
    console.print(
        f"[{status_color}]{stats['project_id']}[/{status_color}]  "
        f"{stats['status']}  "
        f"{stats.get('pdfs_ok', 0)}/{stats.get('pdfs_total', 0)} PDFs  "
        f"{stats.get('chunks', 0)} chunks  "
        f"{stats.get('pdfs_failed', 0)} errores"
    )


# ── Status ──────────────────────────────────────────────────────────────────

@cli.command()
def status(
    project: Optional[str] = typer.Option(None, help="ID del proyecto"),
):
    """Muestra el estado de los proyectos."""
    if project:
        # Status de un proyecto específico
        state_file = settings.processed_dir / project / "state.json"
        if not state_file.exists():
            console.print(f"[yellow]No hay estado para: {project}[/yellow]")
            raise typer.Exit(1)
        state = json.loads(state_file.read_text(encoding="utf-8"))
        console.print_json(json.dumps(state, ensure_ascii=False, indent=2))
        return

    # Status global
    table = Table(title="Proyectos PIA RAG")
    table.add_column("Proyecto", style="cyan")
    table.add_column("Estado", style="bold")
    table.add_column("PDFs", justify="right")
    table.add_column("Chunks", justify="right")
    table.add_column("Errores", justify="right")

    # Scan processed dir for state.json files
    processed = settings.processed_dir
    if processed.exists():
        for proj_dir in sorted(processed.iterdir()):
            state_file = proj_dir / "state.json"
            if state_file.exists():
                state = json.loads(state_file.read_text(encoding="utf-8"))
                files = state.get("files", {})
                ok = sum(1 for f in files.values() if f.get("status") == "indexed")
                failed = sum(1 for f in files.values() if f.get("status") == "failed")
                total = ok + failed
                status_str = state.get("status", "pending")
                style = "green" if status_str == "complete" else ("yellow" if status_str == "partial" else "red")
                table.add_row(
                    proj_dir.name,
                    f"[{style}]{status_str}[/{style}]",
                    f"{ok}/{total}",
                    str(state.get("total_chunks", 0)),
                    str(failed),
                )

    # Also list projects that haven't been processed yet
    docs_dir = settings.documents_dir
    if docs_dir.exists():
        existing_ids = {d.name for d in processed.iterdir()} if processed.exists() else set()
        for proj_dir in sorted(docs_dir.iterdir()):
            if proj_dir.is_dir() and not proj_dir.name.startswith("."):
                # Generate project_id same way as pipeline
                import unicodedata
                pid = proj_dir.name.lower().replace(" ", "_").replace("-", "_")
                pid = unicodedata.normalize("NFKD", pid).encode("ascii", "ignore").decode("ascii")
                if pid not in existing_ids:
                    n_pdfs = len(list(proj_dir.rglob("*.pdf")) + list(proj_dir.rglob("*.PDF")))
                    table.add_row(proj_dir.name, "[dim]pending[/dim]", f"0/{n_pdfs}", "0", "—")

    console.print(table)

    # Pinecone stats
    try:
        from pia_rag.storage.pinecone_client import PineconeClient
        pc = PineconeClient()
        stats = pc.get_index_stats()
        console.print(f"\nPinecone {settings.pinecone_index_name}:  {stats['total_vectors']} vectors totales")
    except Exception:
        console.print(f"\nPinecone {settings.pinecone_index_name}:  [dim](no conectado)[/dim]")

    console.print(f"Render {settings.render_service_id}")


# ── Query ───────────────────────────────────────────────────────────────────

@cli.command()
def query(
    question: str = typer.Argument(..., help="Pregunta en lenguaje natural"),
    project: Optional[str] = typer.Option(None, help="Filtrar por proyecto"),
    level: Optional[str] = typer.Option(None, help="Filtrar por nivel (chapter/section/subsection)"),
    top_k: int = typer.Option(8, help="Número de resultados"),
    no_generate: bool = typer.Option(False, "--no-generate", help="Solo buscar, no generar respuesta"),
):
    """Consulta la base de conocimiento."""
    from pia_rag.rag.enriched_engine import EnrichedRAGEngine

    engine = EnrichedRAGEngine()
    result = engine.query(
        question=question,
        project_id=project,
        chunk_level=level,
        top_k=top_k,
        generate=not no_generate,
    )

    if result.get("answer"):
        console.print(f"\n[bold green]Respuesta:[/bold green]\n{result['answer']}\n")

    console.print(f"[dim]Resultados encontrados: {result['total_found']}[/dim]")
    if result.get("filters_applied"):
        console.print(f"[dim]Filtros: {result['filters_applied']}[/dim]")

    for i, r in enumerate(result.get("results", []), 1):
        console.print(
            f"\n[bold]#{i}[/bold] (score: {r['score']}) — "
            f"{r['project_name']} — {r['chapter_title']}"
        )
        if r.get("section_title"):
            console.print(f"  Sección: {r['section_title']}")
        console.print(f"  Páginas: {r['page_start']}-{r['page_end']}  |  Nivel: {r['chunk_level']}")
        console.print(f"  {r['text'][:200]}...")


if __name__ == "__main__":
    cli()
