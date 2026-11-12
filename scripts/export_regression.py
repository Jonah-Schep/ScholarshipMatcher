"""Refresh the regression corpus from a real catalog (docs/fix_plan.md WP0 / WP8 step 2).

    python scripts/export_regression.py --db data/scholarmatch.db \
        --names-file tests/fixtures/regression_names.txt [--out tests/fixtures/regression_listings.jsonl] [--dry-run]

For every award name in the names file (one per line, `#` starts a comment) the script looks for a catalog row
with the same normalised name. When it finds one, that real row replaces the corpus line, and the corpus line's
`expect` and `ineligible_for` are carried over. When it finds none, or only a title-only row
(`extra.detail_fetched` false), the existing (usually synthetic) line is kept.
Names that match more than one row are reported and left unchanged; pick the row by hand.
Corpus lines whose name is not in the names file are kept as they are.

After a refresh, run `python -m pytest -q tests/test_regression.py`: real text can differ from the synthetic
text, so an expectation or an XFAIL entry may need updating. XFAIL entries are keyed by name, so they survive.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scholarmatch.models import Scholarship  # noqa: E402
from scholarmatch.textutil import norm_name  # noqa: E402

DEFAULT_OUT = ROOT / "tests" / "fixtures" / "regression_listings.jsonl"


def read_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--names-file", type=Path, default=ROOT / "tests" / "fixtures" / "regression_names.txt")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    corpus = [json.loads(l) for l in args.out.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if args.out.exists() else []
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows: dict[str, list[dict]] = {}
    for (data,) in con.execute("SELECT data FROM scholarships"):
        d = json.loads(data)
        rows.setdefault(norm_name(d["name"]), []).append(d)
    con.close()

    by_name = {norm_name(d["name"]): i for i, d in enumerate(corpus)}
    replaced, kept, ambiguous, added, title_only = [], [], [], [], []
    for name in read_names(args.names_file):
        key = norm_name(name)
        found = rows.get(key, [])
        if found and all(f.get("extra", {}).get("detail_fetched") is False for f in found):
            title_only.append(name)        # a title-only scrape is worse than the synthetic text
            continue
        if len(found) > 1:
            ambiguous.append(f"{name} ({', '.join(f['source'] + ':' + f['source_id'] for f in found)})")
            continue
        if not found:
            kept.append(name)
            continue
        real = Scholarship.model_validate(found[0]).model_dump(mode="json")
        if key in by_name:
            old = corpus[by_name[key]]
            corpus[by_name[key]] = real | {"expect": old.get("expect", {}), "ineligible_for": old.get("ineligible_for", [])}
            replaced.append(name)
        else:
            corpus.append(real | {"expect": {}, "ineligible_for": []})
            by_name[key] = len(corpus) - 1
            added.append(name)

    print(f"replaced with real rows: {len(replaced)}; added (no expectations yet): {len(added)}; "
          f"not in catalog (kept): {len(kept)}; title only in catalog (kept): {len(title_only)}; "
          f"ambiguous (kept): {len(ambiguous)}")
    for label, items in (("added", added), ("not in catalog", kept), ("title only", title_only),
                         ("ambiguous", ambiguous)):
        for n in items:
            print(f"  {label}: {n}")
    if not args.dry_run:
        with args.out.open("w", encoding="utf-8", newline="\n") as f:
            for d in corpus:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        print(f"wrote {len(corpus)} lines -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
