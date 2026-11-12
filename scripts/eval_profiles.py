"""Rule-based false-positive count for the manual-test profiles (docs/fix_plan.md, WP0). No network, no LLM.

    python scripts/eval_profiles.py                  # the local catalog (config.DB_PATH)
    python scripts/eval_profiles.py --db path.db     # another catalog, e.g. the full 2,467-row copy
    python scripts/eval_profiles.py --corpus         # the regression corpus only (tests/fixtures/...jsonl)

The catalog is copied to a temporary database first, so match runs and audit rows never touch the original.
For each profile in tests/regression_profiles.py it runs Supervisor(OfflineLLM) and prints:
  eligible   rows that passed the SQL stage and the hard filter (before the LLM cap)
  matches    results shown (the top MAX_LLM_CANDIDATES of those)
  known_inel matches the notes call ineligible for this profile (`ineligible_for` in the corpus)
  inel_top10 how many of those are in the top ten
  elig_lost  corpus awards whose expectation for this profile is pass/unknown but which are not in the
             eligible set (over-exclusion), counted only for awards present in the catalog
Awards are matched to the corpus by normalised name.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scholarmatch import config, db  # noqa: E402
from scholarmatch.agents.supervisor import Supervisor  # noqa: E402
from scholarmatch.llm import OfflineLLM  # noqa: E402
from scholarmatch.scrapers.runner import ingest  # noqa: E402
from scholarmatch.textutil import norm_name  # noqa: E402
from tests.regression_profiles import PROFILES, load_corpus  # noqa: E402

DEFAULT_AS_OF = "2026-11-12"   # fixed so numbers are comparable between sessions


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--db", type=Path, help=f"catalog database (default {config.DB_PATH})")
    src.add_argument("--corpus", action="store_true", help="load the regression corpus into an empty temp DB")
    ap.add_argument("--as-of", default=DEFAULT_AS_OF, help=f"evaluation date (default {DEFAULT_AS_OF})")
    ap.add_argument("--show", action="store_true", help="list the known-ineligible awards in each top ten")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    corpus = load_corpus()
    display = {norm_name(s.name): s.name for s, _, _ in corpus}
    inel = {pid: {norm_name(s.name) for s, _, bad in corpus if pid in bad} for pid in PROFILES}
    want = {pid: {norm_name(s.name) for s, exp, _ in corpus if exp.get(pid, {}).get("result") in ("pass", "unknown")}
            for pid in PROFILES}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "eval.db"
        if args.corpus:
            config.DB_PATH = tmp_db
            ingest([s for s, _, _ in corpus])
            label = f"regression corpus ({len(corpus)} rows)"
        else:
            source = args.db or config.DB_PATH
            if not source.exists():
                print(f"No catalog at {source}. Run scripts/load_sample.py or pass --db / --corpus.")
                return 1
            src_con, dst_con = sqlite3.connect(source), sqlite3.connect(tmp_db)
            with dst_con:
                src_con.backup(dst_con)       # consistent copy, including anything still in the WAL
            src_con.close()
            dst_con.close()
            config.DB_PATH = tmp_db
            label = f"{source} ({db.catalog_stats()['total']} rows)"
        in_catalog = {norm_name(s.name) for s in db.all_scholarships()}

        print(f"Catalog: {label}; as of {args.as_of}; rules {config.RULES_VERSION}, rubric {config.RUBRIC_VERSION}")
        print(f"{'profile':8} {'eligible':>8} {'matches':>7} {'known_inel':>10} {'inel_top10':>10} {'elig_lost':>9}")
        totals = [0, 0, 0, 0, 0]
        details = []
        for pid, p in PROFILES.items():
            run = Supervisor(OfflineLLM()).run(p, args.as_of, force=True)
            names = [norm_name(r.name) for r in run.results]
            eligible = {norm_name(e.name) for e in run.exclusions if e.stage == "cap"} | set(names)
            bad = [r.name for r in run.results if norm_name(r.name) in inel[pid]]
            bad_top = [r.name for r in run.results[:10] if norm_name(r.name) in inel[pid]]
            lost = sorted(display[n] for n in (want[pid] & in_catalog) - eligible)
            row = [run.stats["eligible"], len(run.results), len(bad), len(bad_top), len(lost)]
            totals = [a + b for a, b in zip(totals, row)]
            print(f"{pid:8} {row[0]:8d} {row[1]:7d} {row[2]:10d} {row[3]:10d} {row[4]:9d}")
            details.append((pid, bad_top, lost))
        print(f"{'total':8} {totals[0]:8d} {totals[1]:7d} {totals[2]:10d} {totals[3]:10d} {totals[4]:9d}")
        if args.show:
            for pid, bad_top, lost in details:
                if bad_top or lost:
                    print(f"\n{pid}:")
                    for n in bad_top:
                        print(f"  ineligible in top 10: {n}")
                    for n in lost:
                        print(f"  eligible but excluded: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
