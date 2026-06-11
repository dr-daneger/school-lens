"""Full data build for school-lens: an idempotent clean rebuild of the runtime DB.

Order matters: the CCD spine first (every fact joins to nces_id), then boundaries,
then resolve catchments to the spine, then the school-keyed facts (CRDC, EDFacts),
then per-school completeness. Re-runnable; rebuilds the DB in %LOCALAPPDATA% from
scratch by default. Run with house-hunter's venv interpreter. ASCII only.

    python scripts/build_data.py            # clean rebuild
    python scripts/build_data.py --append   # keep existing DB, refresh in place
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from school_lens.completeness import compute_completeness  # noqa: E402
from school_lens.config import settings  # noqa: E402
from school_lens.db import get_connection, init_db  # noqa: E402
from school_lens.ingest.boundaries import boundary_sources, ingest_source, resolve_boundaries  # noqa: E402
from school_lens.ingest.crdc import ingest_crdc  # noqa: E402
from school_lens.ingest.edfacts import ingest_edfacts  # noqa: E402
from school_lens.ingest.acs import ingest_acs  # noqa: E402
from school_lens.ingest.nces import ingest_ccd  # noqa: E402
from school_lens.ingest.ode import ingest_ode  # noqa: E402
from school_lens.ingest.pss import enrich_pss_survey, ingest_pss, load_private_enrichment  # noqa: E402


def _t(label, fn):
    t0 = time.time()
    out = fn()
    print(f"  {label:<26} {out}   ({time.time() - t0:.1f}s)")
    return out


def main(rebuild: bool = True) -> None:
    db = settings.db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    if rebuild and db.exists():
        db.unlink()
    init_db()
    conn = get_connection()
    try:
        print("== spine ==")
        _t("CCD schools", lambda: ingest_ccd(conn=conn))
        _t("PSS private schools", lambda: ingest_pss(conn=conn))
        _t("PSS survey enrich", lambda: enrich_pss_survey(conn=conn))
        print("== boundaries ==")
        for k in boundary_sources():
            _t(f"boundaries[{k}]", lambda k=k: ingest_source(k, conn=conn))
        _t("resolve -> nces_id", lambda: resolve_boundaries(conn=conn))
        print("== school-keyed facts ==")
        _t("CRDC discipline/staffing", lambda: ingest_crdc(conn=conn))
        _t("EDFacts grad+proficiency", lambda: ingest_edfacts(conn=conn))
        _t("ODE At-A-Glance academics", lambda: ingest_ode(conn=conn))
        print("== geography-keyed context ==")
        _t("ACS/OI tract context", lambda: ingest_acs(conn=conn))
        _t("private tuition/acceptance", lambda: load_private_enrichment(conn=conn))
        print("== derived ==")
        _t("data_completeness", lambda: compute_completeness(conn=conn))

        print("== summary ==")
        for label, q in (
            ("schools", "SELECT COUNT(*) FROM schools"),
            ("  with point", "SELECT COUNT(*) FROM schools WHERE point IS NOT NULL"),
            ("boundaries", "SELECT COUNT(*) FROM boundaries"),
            ("  resolved", "SELECT COUNT(*) FROM boundaries WHERE nces_id IS NOT NULL"),
            ("fact_academics", "SELECT COUNT(*) FROM fact_academics"),
            ("fact_discipline_safety", "SELECT COUNT(*) FROM fact_discipline_safety"),
            ("fact_staffing", "SELECT COUNT(*) FROM fact_staffing"),
        ):
            print(f"  {label:<26} {conn.execute(q).fetchone()[0]}")
    finally:
        conn.close()
    print(f"DB ready at {db}")


if __name__ == "__main__":
    main(rebuild="--append" not in sys.argv)
