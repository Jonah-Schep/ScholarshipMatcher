"""Scrape -> screen -> extract (rules) -> store -> export.

    python -m scholarmatch.scrapers.runner --sources vt,ecu,sc_che,gsites,manual
    python -m scholarmatch.scrapers.runner --sources usc --max-pages 5      # partial, resumable (cached)

Sources: vt, usc, ecu (AcademicWorks) | sc_che | gsites | manual | bigfuture, careeronestop,
         schev, ncseaa, sc_tuition_grants, cfnc (print why they are not automated)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter

from .. import config, db
from ..ingest import extract_rules, scam
from ..models import Scholarship
from . import academicworks, gsites, manual, pending, state_aid
from .http import Blocked, PoliteFetcher

DEFAULT_SOURCES = "vt,ecu,usc,sc_che,gsites,manual"
DEFAULT_VIEW = ("national", "nc", "va", "sc", "multi_state")
CSV_FIELDS = ["source", "source_id", "name", "url", "school", "region", "geo_scope", "sponsor", "amount_raw",
              "amount_min", "amount_max", "deadline_raw", "deadline", "status", "college_or_department",
              "confidence", "description", "eligibility", "sources", "extra", "scraped_at"]


def ingest(rows: list[Scholarship]) -> dict:
    """Shared by scrapers and tests: scam screen + rules criteria + upsert. Idempotent."""
    seen: dict[str, Scholarship] = {}
    for r in rows:                       # de-dupe within source on (source, source_id)
        seen[r.key] = r
    rows = list(seen.values())
    verdicts = {r.key: scam.screen(r) for r in rows}
    db.upsert_scholarships(rows, verdicts)
    for r in rows:
        db.put_criteria(r.key, "rules", db.content_hash(r), config.RULES_VERSION, extract_rules.extract(r))
    return {"rows": len(rows), "blocked": sum(v[0] == "block" for v in verdicts.values()),
            "warned": sum(v[0] == "warn" for v in verdicts.values())}


def export() -> None:
    config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    rows = db.all_scholarships()
    by_source: dict[str, list[Scholarship]] = {}
    for r in rows:
        by_source.setdefault(r.source, []).append(r)
    for source, items in by_source.items():
        with (config.EXPORT_DIR / f"{source}.jsonl").open("w", encoding="utf-8") as f:
            for r in items:
                f.write(r.model_dump_json() + "\n")
    with (config.EXPORT_DIR / "combined.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            if r.geo_scope not in DEFAULT_VIEW:
                continue
            d = r.model_dump()
            w.writerow({k: json.dumps(d[k]) if isinstance(d[k], (list, dict)) else d[k] for k in CSV_FIELDS})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default=DEFAULT_SOURCES)
    ap.add_argument("--max-pages", type=int, help="AcademicWorks: listing pages per school")
    ap.add_argument("--max-details", type=int, help="AcademicWorks: detail pages per school")
    ap.add_argument("--include-ended", action="store_true", help="also fetch detail pages of ended awards")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    collected: list[Scholarship] = []
    notes: list[str] = []
    fetcher = None
    if any(s in ("vt", "usc", "ecu", "sc_che", "gsites") for s in sources):
        fetcher = PoliteFetcher(use_cache=not args.no_cache)
    for src in sources:
        try:
            if src in academicworks.BASES:
                collected += list(academicworks.scrape(src, fetcher, args.max_pages, args.max_details, args.include_ended))
            elif src == "sc_che":
                collected += list(state_aid.scrape_che(fetcher))
            elif src == "gsites":
                collected += list(gsites.scrape(fetcher))
            elif src == "manual":
                collected += list(manual.load_all())
            elif src in pending.REASONS:
                notes.append(f"{src}: NOT RUN - {pending.REASONS[src]}")
            else:
                notes.append(f"{src}: unknown source")
        except Blocked as e:
            notes.append(f"{src}: STOPPED - {e}")
        except Exception as e:
            notes.append(f"{src}: FAILED - {type(e).__name__}: {e}")
    result = ingest(collected)
    export()

    print("\n=== Run summary ===")
    per = Counter(r.source for r in collected)
    for source in sorted(per):
        items = [r for r in collected if r.source == source]
        st = Counter(r.status for r in items)
        print(f"{source:28s} {len(items):5d} rows | open {st['open']:4d} ended {st['ended']:4d} unknown {st['unknown']:4d}"
              f" | low-confidence {sum(r.confidence == 'low' for r in items)}")
    print(f"geo_scope: {dict(Counter(r.geo_scope for r in collected))}")
    print(f"scam screen: {result['blocked']} blocked, {result['warned']} flagged")
    if fetcher:
        print(f"http: {fetcher.stats['fetched']} fetched, {fetcher.stats['cached']} from cache, "
              f"{len(fetcher.stats['skipped'])} skipped")
        for s in fetcher.stats["skipped"][:20]:
            print("  skipped:", s)
        fetcher.close()
    for n in notes:
        print("NOTE:", n)
    print(f"catalog now holds {db.catalog_stats()['total']} scholarships -> {config.DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
