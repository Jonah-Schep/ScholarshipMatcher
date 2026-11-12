"""Hand-typed eligibility text for programs whose rules are only in linked PDFs (decision D2, docs/fix_plan.md WP6).

One CSV per source in `manual_sources/supplements/` (the file stem is the source id: `sc_che.csv` -> `sc_che`).
Columns (header row required):
  source_id    the row's `Scholarship.source_id` (e.g. `life-scholarship`)
  requirement  the requirement, typed from the PDF as written (the rule extractor and the agents quote from it)
  source_url   the PDF it was typed from (https://...)

`apply` appends every filled-in requirement of a row to its `eligibility` (so normal extraction and grounding see
it), adds the PDF to `sources`, records the rows in `extra["supplement"]` and lowers `confidence` to "medium"
(the text was typed by hand). Rows that are placeholders (`TODO…`), comments (`#…`) or have no http(s) source_url
are ignored. Applying twice changes nothing, so re-ingesting an exported row is safe. No network access.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable, Optional

from .. import config
from ..models import Scholarship

SUPPLEMENT_DIR = config.ROOT / "manual_sources" / "supplements"
PLACEHOLDER = re.compile(r"^\s*(?:TODO|TBD|FIXME)\b", re.I)
_URL = re.compile(r"^https?://\S+$")
_cache: dict[Path, tuple[float, dict[str, list[tuple[str, str]]]]] = {}


def is_placeholder(text: str) -> bool:
    return not (text or "").strip() or bool(PLACEHOLDER.match(text))


def read_file(path: Path) -> dict[str, list[tuple[str, str]]]:
    """source_id -> [(requirement, source_url)] for the usable rows of one supplement file."""
    out: dict[str, list[tuple[str, str]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            sid, text, url = row.get("source_id", ""), row.get("requirement", ""), row.get("source_url", "")
            if not sid or sid.startswith("#") or is_placeholder(text) or not _URL.match(url):
                continue
            out.setdefault(sid, []).append((text, url))
    return out


def load(source: str, directory: Optional[Path] = None) -> dict[str, list[tuple[str, str]]]:
    path = (directory or SUPPLEMENT_DIR) / f"{source}.csv"
    if not path.exists():
        return {}
    mtime = path.stat().st_mtime
    hit = _cache.get(path)
    if not hit or hit[0] != mtime:
        hit = _cache[path] = (mtime, read_file(path))
    return hit[1]


def apply(s: Scholarship, directory: Optional[Path] = None) -> Scholarship:
    """The row with its supplement merged into `eligibility` (unchanged when the source has no filled-in rows)."""
    rows = load(s.source, directory).get(s.source_id)
    if not rows:
        return s
    eligibility = s.eligibility
    for text, _url in rows:
        if text not in eligibility:
            eligibility = f"{eligibility}\n{text}" if eligibility else text
    sources = list(s.sources) + [u for u in dict.fromkeys(u for _t, u in rows) if u not in s.sources]
    extra = dict(s.extra, supplement=[{"requirement": t, "source_url": u} for t, u in rows])
    confidence = "medium" if s.confidence == "high" else s.confidence
    return s.model_copy(update={"eligibility": eligibility, "sources": sources, "extra": extra,
                                "confidence": confidence})


def apply_all(rows: Iterable[Scholarship], directory: Optional[Path] = None) -> list[Scholarship]:
    return [apply(s, directory) for s in rows]
