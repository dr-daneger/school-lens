"""school-lens command-line interface (mirrors house-hunter's Click CLI shape).

Output is ASCII only (Windows console is cp1252); no box-drawing characters.
"""
from __future__ import annotations

import click

from school_lens.config import settings
from school_lens.db import get_connection, house_hunter_attached, init_db

_TABLES = (
    "schools", "source_crosswalk", "boundaries", "fact_academics",
    "fact_discipline_safety", "fact_staffing", "fact_finance",
    "tract_outcomes", "tract_acs", "school_mentions",
)


@click.group()
def main() -> None:
    """school-lens: local school data engine."""


@main.command("init-db")
def init_db_cmd() -> None:
    """Create the canonical schema."""
    init_db()
    click.echo(f"Initialized schema at {settings.db_path}")


@main.command("ingest-boundaries")
@click.argument("source", default="all")
@click.option("--year", default=None, help="override the school_year tag for ingested rows")
def ingest_boundaries_cmd(source: str, year: str | None) -> None:
    """Ingest attendance-area boundaries. SOURCE: pps | beaverton | hillsboro | all."""
    from school_lens.ingest.boundaries import boundary_sources, ingest_source

    known = boundary_sources()
    keys = list(known) if source == "all" else [source]
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise click.ClickException(
            f"unknown source(s) {unknown}; choose: {', '.join(known)}, all"
        )
    total = 0
    for k in keys:
        n = ingest_source(k, school_year=year)
        click.echo(f"  {k:<10} {n} features  [{known[k].label}]")
        total += n
    click.echo(f"Ingested {total} boundary features (run resolve-boundaries to map to nces_id)")


@main.command("resolve-boundaries")
def resolve_boundaries_cmd() -> None:
    """Resolve unresolved boundary raw_name -> nces_id via the crosswalk."""
    from school_lens.ingest.boundaries import resolve_boundaries

    n = resolve_boundaries()
    click.echo(f"Resolved {n} boundaries to nces_id")


@main.command("status")
def status_cmd() -> None:
    """Show table row counts and whether house_hunter is attached."""
    if not settings.db_path.exists():
        click.echo("No database yet. Run: school-lens init-db")
        return
    conn = get_connection(read_only=True)
    try:
        click.echo(f"DB: {settings.db_path}")
        click.echo(f"house_hunter attached: {house_hunter_attached(conn)}")
        click.echo("-" * 40)
        for t in _TABLES:
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                n = "missing"
            click.echo(f"  {t:<24} {n}")
    finally:
        conn.close()


@main.command("serve")
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8011, type=int)
def serve_cmd(host: str, port: int) -> None:
    """Run the FastAPI app + map UI."""
    import uvicorn

    uvicorn.run("school_lens.api.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
