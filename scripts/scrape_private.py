"""Phase 1 of the private-school enrichment: scrape PrivateSchoolReview per-school
profiles for tuition / acceptance / ratio / enrollment, into a resumable JSONL
cache (school_lens.ingest.pss.CACHE). Phase 2 (load_private_enrichment) reads the
cache into the DB. Two-phase like house-hunter's Redfin worker, so a DB rebuild
never forces a re-scrape. Run with house-hunter's venv. ASCII only.
"""
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import httpx  # noqa: E402

from school_lens.db import get_connection  # noqa: E402
from school_lens.ingest.pss import CACHE  # noqa: E402

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}


def slug(name: str) -> str:
    s = name.lower().replace("&", "and").replace("'", "").replace(".", "")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s + "-profile"


def parse(html: str) -> dict:
    clean = re.sub(r"<[^>]+>", " ", html)
    out: dict = {}
    m = re.search(r"tuition[^$]{0,160}\$([\d,]+)", clean, re.I)
    if m:
        out["tuition"] = float(m.group(1).replace(",", ""))
    m = re.search(r"Acceptance rate:?\s*(\d+)\s*%", clean, re.I)
    if m:
        out["acceptance"] = float(m.group(1))
    m = re.search(r"Student-?Teacher Ratio\D{0,30}(\d+)\s*:\s*1", clean, re.I)
    if m:
        out["ratio"] = float(m.group(1))
    m = re.search(r"Total Students\D{0,30}([\d,]+)", clean, re.I)
    if m:
        out["enrollment"] = float(m.group(1).replace(",", ""))
    return out


def main() -> None:
    conn = get_connection(read_only=True)
    privs = conn.execute(
        "SELECT nces_id, name FROM schools WHERE school_type LIKE 'private%' ORDER BY name"
    ).fetchall()
    conn.close()

    done = set()
    if CACHE.exists():
        for ln in CACHE.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(ln)["ppin"])
            except (ValueError, KeyError):
                pass
    CACHE.parent.mkdir(parents=True, exist_ok=True)

    hit = 0
    with httpx.Client(headers=UA, timeout=30, follow_redirects=True) as c, \
            open(CACHE, "a", encoding="utf-8") as f:
        for ppin, name in privs:
            if ppin in done:
                continue
            url = f"https://www.privateschoolreview.com/{slug(name)}"
            rec = {"ppin": ppin, "name": name, "url": url}
            try:
                r = c.get(url)
                rec["status"] = r.status_code
                if r.status_code == 200:
                    rec.update(parse(r.text))
            except Exception as e:
                rec["status"] = "err"
                rec["error"] = str(e)[:80]
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if rec.get("tuition") is not None:
                hit += 1
            print(f"{str(rec.get('status')):>4}  tuition={str(rec.get('tuition')):>8}  {name[:38]}")
            time.sleep(0.4)
    print(f"--- done; {hit} with tuition (cache: {CACHE})")


if __name__ == "__main__":
    main()
