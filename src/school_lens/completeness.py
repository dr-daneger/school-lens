"""Per-school data_completeness: the fraction of each domain's metrics present.

Writes schools.data_completeness as JSON keyed by domain (0..1). The comparison
engine and UI use it to show how much is actually known about a school, which is
the honest counterpart to the public/charter/private data asymmetry (CLAUDE.md
3.2): a private school with mostly empty cells reads as low-completeness, not
low-quality.
"""
from __future__ import annotations

import json

import duckdb

from school_lens.db import get_connection
from school_lens.domain import metrics as M

# Domains backed by a school-keyed fact table (tract-based outcomes/demographics
# are joined spatially at query time, not stored per school, so they are omitted).
_DOMAIN_TABLE = {
    "academics": "fact_academics",
    "discipline_safety": "fact_discipline_safety",
    "staffing": "fact_staffing",
    "private_signals": "fact_private_signals",
}


def compute_completeness(conn: duckdb.DuckDBPyConnection | None = None) -> int:
    """Recompute schools.data_completeness for every school. Returns rows updated."""
    own = conn is None
    conn = conn or get_connection()
    try:
        schools = [r[0] for r in conn.execute("SELECT nces_id FROM schools").fetchall()]
        comp: dict[str, dict[str, float]] = {s: {d: 0.0 for d in _DOMAIN_TABLE} for s in schools}
        for domain, table in _DOMAIN_TABLE.items():
            cols = [m.id for m in M.by_domain(domain)]
            if not cols:
                continue
            sel = ", ".join(f"arg_max({c}, school_year) AS {c}" for c in cols)
            for row in conn.execute(
                f"SELECT nces_id, {sel} FROM {table} GROUP BY nces_id"
            ).fetchall():
                nces, vals = row[0], row[1:]
                if nces in comp:
                    present = sum(1 for v in vals if v is not None)
                    comp[nces][domain] = round(present / len(cols), 3)
        n = 0
        for nces, dd in comp.items():
            conn.execute(
                "UPDATE schools SET data_completeness = ? WHERE nces_id = ?",
                [json.dumps(dd), nces],
            )
            n += 1
        return n
    finally:
        if own:
            conn.close()
