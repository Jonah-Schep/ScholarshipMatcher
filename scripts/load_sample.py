"""Load a small REAL snapshot (pages fetched 2026-11-10, stored in tests/fixtures) into the catalog
so the app can be tried before running the scrapers. No network access.

    python scripts/load_sample.py

Contents: Virginia Tech listing page 1 (50 awards, listing-level data only + 1 full detail page),
SC Commission on Higher Education state programs (9), Queen City 2 College list (stale 2022 list:
kept for history, never shown as open). Run the real scrapers for the full, current catalog.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scholarmatch import db  # noqa: E402
from scholarmatch.scrapers import academicworks as aw, gsites, state_aid  # noqa: E402
from scholarmatch.scrapers.runner import ingest  # noqa: E402

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def main() -> None:
    rd = lambda n: (FIX / n).read_text(encoding="utf-8")
    listing = aw.parse_listing(rd("aw_listing.html"))
    detail = aw.parse_detail(rd("aw_detail.html"))
    rows = [aw.to_scholarship("vt", r, detail if i == 0 else None) for i, r in enumerate(listing)]
    rows += state_aid.parse_che(rd("sc_che.html"))[0]
    rows += gsites.dedupe(gsites.parse_page("queencity2college", gsites.PAGES["queencity2college"], rd("gsite_qc2c.html")))
    print(ingest(rows), db.catalog_stats())


if __name__ == "__main__":
    main()
