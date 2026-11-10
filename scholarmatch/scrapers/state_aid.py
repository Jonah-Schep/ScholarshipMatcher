"""Part 4: state aid programs.

Implemented:   4D SC Commission on Higher Education (one server-rendered page).
Manual import: 4A SCHEV (site returns 403 to automated clients - brief says stop, copy by hand),
               4B NCSEAA (robots.txt blocks AI crawlers; brief recommends transcribing),
               4E SC Tuition Grants (single record). Use `manual_sources/*.csv` for these.
Excluded:      4C CFNC (Terms of Use prohibit automated collection).

SC CHE structure verified 2026-11-10: <dl class="ckeditor-accordion"> with <dt>program</dt><dd>body</dd>.
"""
from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Scholarship
from ..textutil import norm_name, now_iso, parse_amounts, squash
from .http import PoliteFetcher

CHE_URL = "https://che.sc.gov/students-families-and-military/scholarships-and-grants-sc-residents"
CHE_EXPECTED = ["Palmetto Fellows", "LIFE", "HOPE", "Need-based Grant", "Lottery Tuition Assistance",
                "National Guard", "WINS", "over 60", "Nursing"]
_AID_TYPES = [("waiver", r"free tuition|waiver|over 60|age 60"), ("reimbursement", r"reimburse"),
              ("tuition_assistance", r"lottery tuition|tuition assistance|college assistance program"),
              ("grant", r"\bgrant\b|\bwins\b"), ("scholarship", r"scholarship|fellows")]
_STACKING = re.compile(r"[^.]*\b(?:may not (?:also )?receive|cannot (?:also )?receive|not eligible to receive|"
                       r"in addition to|in conjunction with)\b[^.]*\.", re.I)


def _aid_type(name: str) -> str:
    low = name.lower()
    for kind, rx in _AID_TYPES:
        if re.search(rx, low):
            return kind
    return "scholarship"


def parse_che(html: str) -> tuple[list[Scholarship], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    out, problems = [], []
    for dl in soup.select("dl.ckeditor-accordion"):
        for dt in dl.find_all("dt", recursive=False):
            dd = dt.find_next_sibling("dd")
            name = squash(dt.get_text(" "))
            if not dd or not name:
                continue
            docs = [{"title": squash(a.get_text(" ")) or "document", "url": urljoin(CHE_URL, a["href"])}
                    for a in dd.find_all("a", href=True) if ".pdf" in a["href"].lower()]
            paras = [squash(p.get_text(" ")) for p in dd.find_all(["p", "li"])]
            paras = [p for p in paras if p and not p.startswith("CHE Contact")]
            text = "\n".join(dict.fromkeys(paras))          # de-dupe nested li/p repeats, keep order
            amin, amax = parse_amounts(text)
            amount_raw = "; ".join(dict.fromkeys(
                squash(m.group(0)) for m in re.finditer(r"[^.\n]*\$\s?\d[^.\n]*\.?", text)))[:400]
            out.append(Scholarship(
                source="sc_che", source_id=norm_name(name).replace(" ", "-"), name=name,
                url=CHE_URL, geo_scope="sc", sponsor="SC Commission on Higher Education",
                amount_raw=amount_raw or "See program rules", amount_min=amin, amount_max=amax,
                status="open", description=text, sources=[CHE_URL], confidence="high",
                extra={"aid_type": _aid_type(name), "documents": docs,
                       "notes": [squash(m.group(0)) for m in _STACKING.finditer(text)][:5],
                       "application_method": "automatic"},
                scraped_at=now_iso()))
    found = " | ".join(s.name.lower() for s in out)
    for exp in CHE_EXPECTED:
        if exp.lower() not in found:
            problems.append(f"sc_che: expected program not found on page: {exp}")
    return out, problems


def scrape_che(fetcher: PoliteFetcher, log=print) -> Iterator[Scholarship]:
    rows, problems = parse_che(fetcher.get(CHE_URL, refresh=True))
    for p in problems:
        log(p)
        fetcher.stats["skipped"].append(p)
    yield from rows
