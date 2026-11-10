"""Part 1: Blackbaud AcademicWorks portals (VT, USC, ECU). One scraper, per-site base URL.

Selectors verified against vt.academicworks.com on 2026-11-10:
  listing  {base}/?page=N  -> <table> rows: td (award) | th[scope=row] a[href=/opportunities/ID] | td (deadline)
  detail   {base}/opportunities/ID -> header h3 (name) + p (description), <dl><dt>label</dt><dd>value</dd></dl>
"""
from __future__ import annotations

import re
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from .. import config
from ..models import Scholarship
from ..textutil import now_iso, parse_amounts, parse_date, squash, today_iso
from .http import Blocked, PoliteFetcher, SignInRequired

BASES = {"vt": "https://vt.academicworks.com", "usc": "https://sc.academicworks.com",
         "ecu": "https://ecu.academicworks.com"}
_DEPT = re.compile(r"\b((?:Pamplin\s+)?(?:College|School|Department|Division) of [A-Z][\w&,' -]+?)(?=\s+(?:Apply|Annual|Scholarship|Fund|Award)\b|[.,;:(]|$)")


def parse_listing(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for a in soup.select('th[scope="row"] a[href*="/opportunities/"]'):
        m = re.search(r"/opportunities/(\d+)", a.get("href", ""))
        tr = a.find_parent("tr")
        if not m or tr is None:
            continue
        tds = tr.find_all("td")
        award = squash(tds[0].get_text(" ")) if tds else ""
        dl = squash(tds[-1].get_text(" ")) if len(tds) > 1 else ""
        rows.append({"id": m.group(1), "name": squash(a.get_text(" ")),
                     "amount_raw": award, "deadline_raw": re.sub(r"^Deadline\s*", "", dl)})
    return rows


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("section.js-opportunity") or soup.select_one("#main") or soup
    out: dict = {"fields": {}, "questions": []}
    header = main.find("header")
    if header:
        h = header.find(["h1", "h2", "h3"])
        out["name"] = squash(h.get_text(" ")) if h else ""
        if h:
            h.extract()
        out["description"] = squash(header.get_text(" "))
    for dl in main.find_all("dl"):
        dt, dd = dl.find("dt"), dl.find("dd")
        if not dt or not dd:
            continue
        label = squash(dt.get_text(" "))
        if label.lower().startswith("supplemental"):
            out["questions"] = [squash(li.get_text(" ")) for li in dd.find_all("li")]
        else:
            out["fields"][label] = squash(dd.get_text(" "))
    return out


def to_scholarship(school: str, row: dict, detail: Optional[dict]) -> Scholarship:
    base = BASES[school]
    d = detail or {"fields": {}, "questions": []}
    amount_raw = d["fields"].get("Award") or row["amount_raw"]
    deadline_raw = d["fields"].get("Deadline") or row["deadline_raw"]
    deadline = parse_date(deadline_raw)
    if "ended" in deadline_raw.lower() or (deadline and deadline < today_iso()):
        status = "ended"
    elif deadline:
        status = "open"
    else:
        status = "unknown"
    amin, amax = parse_amounts(amount_raw)
    name = d.get("name") or row["name"]
    desc = d.get("description", "")
    dept = _DEPT.search(name) or _DEPT.search(desc)
    extra_fields = {k: v for k, v in d["fields"].items() if k not in ("Award", "Deadline")}
    url = f"{base}/opportunities/{row['id']}"
    return Scholarship(
        source=f"academicworks_{school}", source_id=row["id"], school=school, name=name, url=url,
        geo_scope=config.SCHOOLS[school][1].lower(), sponsor=config.SCHOOLS[school][0],
        amount_raw=amount_raw, amount_min=amin, amount_max=amax,
        deadline_raw=deadline_raw, deadline=deadline, status=status,
        description=desc, eligibility="; ".join(f"{k}: {v}" for k, v in extra_fields.items()),
        college_or_department=dept.group(1).strip() if dept else None,
        sources=[url], confidence="high" if detail else "medium",
        extra={"supplemental_questions": d["questions"], "fields": extra_fields,
               "enrolled_students_only": True, "detail_fetched": bool(detail)},
        scraped_at=now_iso())


def scrape(school: str, fetcher: PoliteFetcher, max_pages: Optional[int] = None,
           max_details: Optional[int] = None, include_ended: bool = False,
           log=print) -> Iterator[Scholarship]:
    base = BASES[school]
    seen: dict[str, dict] = {}
    page, prev_ids = 1, None
    while max_pages is None or page <= max_pages:
        try:
            rows = parse_listing(fetcher.get(f"{base}/?page={page}", refresh=True))
        except Blocked as e:
            log(f"[{school}] stopped: {e}")
            break
        ids = [r["id"] for r in rows]
        if not rows or ids == prev_ids:
            break
        for r in rows:
            seen.setdefault(r["id"], r)
        prev_ids, page = ids, page + 1
    log(f"[{school}] {len(seen)} opportunities on {page - 1} listing page(s)")
    fetched = 0
    for oid, row in seen.items():
        ended = "ended" in row["deadline_raw"].lower()
        detail = None
        if (include_ended or not ended) and (max_details is None or fetched < max_details):
            try:
                detail = parse_detail(fetcher.get(f"{base}/opportunities/{oid}"))
                fetched += 1
            except SignInRequired:
                fetcher.stats["skipped"].append(f"{base}/opportunities/{oid} (sign-in)")
                continue
            except Blocked as e:
                log(f"[{school}] stopped: {e}")
                break
            except Exception as e:  # parse/network failure on one page must not kill the run
                fetcher.stats["skipped"].append(f"{base}/opportunities/{oid} ({type(e).__name__})")
        yield to_scholarship(school, row, detail)
