"""Hand-entered scholarships (`manual_sources/*.csv`).

Used for sources the brief says to copy by hand rather than scrape: SCHEV (blocks
automated clients), NCSEAA (blocks AI crawlers), SC Tuition Grants, and small one-off
local organisations. One CSV per source; the file stem becomes the `source` id.

Columns (header row required; unknown columns go to `extra`):
  name*, url, sponsor, geo_scope, amount_raw, deadline_raw, description, eligibility, aid_type, source_id
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from .. import config
from ..models import Scholarship
from ..textutil import norm_name, now_iso, parse_amounts, parse_date, sha, today_iso

MANUAL_DIR = config.ROOT / "manual_sources"
_KNOWN = {"name", "url", "sponsor", "geo_scope", "amount_raw", "deadline_raw", "description",
          "eligibility", "aid_type", "source_id"}
_SCOPES = {"national", "nc", "va", "sc", "multi_state", "other_state"}


def load_csv(path: Path) -> Iterator[Scholarship]:
    source = norm_name(path.stem).replace(" ", "_")
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            if not row.get("name") or row["name"].startswith("#"):
                continue
            amin, amax = parse_amounts(row.get("amount_raw", ""))
            deadline = parse_date(row.get("deadline_raw", ""))
            scope = row.get("geo_scope", "").lower()
            extra = {k: v for k, v in row.items() if k not in _KNOWN and v}
            if row.get("aid_type"):
                extra["aid_type"] = row["aid_type"].lower()
            yield Scholarship(
                source=source,
                source_id=row.get("source_id") or sha(row["name"], row.get("sponsor", ""))[:12],
                name=row["name"], url=row.get("url", ""), sponsor=row.get("sponsor") or None,
                geo_scope=scope if scope in _SCOPES else "national",
                amount_raw=row.get("amount_raw", ""), amount_min=amin, amount_max=amax,
                deadline_raw=row.get("deadline_raw", ""), deadline=deadline,
                status="ended" if deadline and deadline < today_iso() else ("open" if deadline else "unknown"),
                description=row.get("description", ""), eligibility=row.get("eligibility", ""),
                sources=[row["url"]] if row.get("url") else [], confidence="medium",
                extra=extra, scraped_at=now_iso())


def load_all() -> Iterator[Scholarship]:
    if MANUAL_DIR.exists():
        for path in sorted(MANUAL_DIR.glob("*.csv")):
            yield from load_csv(path)
