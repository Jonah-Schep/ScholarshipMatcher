"""Part 2B: counselor-maintained Google Sites lists (Charlotte, NC).

These are hand-edited pages with no schema, so parsing is heuristic by design:
  1. flatten the page to ordered text lines (+ outbound link, + current section heading)
  2. "labelled" pages (Queen City 2 College style: NAME / DUE DATE: / AWARD AMOUNT: / ELIGIBILITY:)
     are segmented on the deadline label -> confidence medium
  3. anything else falls back to one low-confidence row per scholarship-looking link
Every row keeps its raw text block; stale or uncertain rows are flagged, never dropped.
sites.google.com is a single host, so all pages share one rate limit (PoliteFetcher is per-host).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterator
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from ..models import Scholarship
from ..textutil import norm_name, now_iso, parse_amounts, parse_date, sha, squash, today_iso
from .http import Blocked, PoliteFetcher

PAGES = {
    "queencity2college": "https://sites.google.com/view/queencity2college/scholarships",
    "westcharlotte": "https://sites.google.com/cms.k12.nc.us/wcstudentsupport/scholarships-financial-aid",
    "nwsa": "https://sites.google.com/cms.k12.nc.us/nwsastudentservices/scholarships",
    "hawthorne": "https://sites.google.com/cms.k12.nc.us/hawthornestudentservicesdept/scholarships-2324",
    "northmeck": "https://sites.google.com/cms.k12.nc.us/north-meck-student-services/college-corner/scholarships",
    "independence": "https://sites.google.com/cms.k12.nc.us/cdcindependencehighschool/college-and-career-info/scholarships",
}
_DUE = re.compile(r"^(due date|deadline|due)\s*:\s*(.*)$", re.I)
_AMT = re.compile(r"^(award amount|amount|award)\s*:\s*(.*)$", re.I)
_ELIG = re.compile(r"^(eligibility|requirements|criteria)\s*:\s*(.*)$", re.I)
_LINK = re.compile(r"^(link|website|apply|application)\s*:\s*(.*)$", re.I)
_YEAR = re.compile(r"\b(20\d{2})\b")
_SEARCH_ENGINES = ("chegg.", "fastweb.", "scholarships.com", "collegeboard.org", "cfnc.org", "goingmerry.",
                   "raise.me", "nccommunityfoundation.org/apply", "fftc.org/scholarships")


def _unwrap(href: str) -> str:
    if "google.com/url" in href:
        q = parse_qs(urlsplit(href).query).get("q")
        if q:
            return q[0]
    return href


def flatten(html: str) -> list[dict]:
    """Ordered leaf text lines: {text, link, section, heading}."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    lines, section, last = [], "", None
    for el in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        if el.name == "li" and el.find("p"):
            continue  # Google Sites wraps li text in <p>; take the <p>
        text = squash(el.get_text(""))      # "" not " ": Sites splits words across inline spans
        if not text or text == last:
            continue
        last = text
        if el.name in ("h1", "h2", "h3"):
            section = text
        a = el.find("a", href=True)
        link = _unwrap(a["href"]) if a else ""
        lines.append({"text": text, "link": link if link.startswith("http") else "",
                      "section": section, "heading": el.name != "p" and el.name != "li"})
    return lines


def _labelled_blocks(lines: list[dict]) -> list[list[dict]]:
    due_idx = [i for i, l in enumerate(lines) if _DUE.match(l["text"]) and not l["heading"] and i > 0]
    starts = []
    for i in due_idx:                        # the name is the nearest line above that is not a bare URL
        j = i - 1
        while j > 0 and i - j < 3 and re.match(r"^https?://\S+$", lines[j]["text"]):
            j -= 1
        starts.append(j)
    return [lines[s:(starts[n + 1] if n + 1 < len(starts) else len(lines))] for n, s in enumerate(starts)]


def _block_to_row(short: str, page_url: str, block: list[dict]) -> Scholarship:
    name = re.sub(r"\s*\((?:scholarship open|deadline|due)[^)]*\)\s*$", "", block[0]["text"], flags=re.I)
    fields = {"due": [], "amt": [], "elig": [], "other": []}
    cur, link = "other", block[0]["link"]
    for l in block[1:]:
        if l["heading"]:
            break                             # next section heading ends the block
        t = l["text"]
        link = link or l["link"]
        for key, rx in (("due", _DUE), ("amt", _AMT), ("elig", _ELIG), ("link", _LINK)):
            m = rx.match(t)
            if m and len(t) < 200:
                cur = key if key != "link" else "other"
                if m.group(2) and key != "link":
                    fields[cur].append(m.group(2))
                break
        else:
            fields[cur].append(t)
    raw = "\n".join(l["text"] for l in block)
    due_raw, amt_raw = " ".join(fields["due"])[:200], " ".join(fields["amt"])[:200]
    years = [int(y) for y in _YEAR.findall(raw)]
    stale = bool(years) and max(years) < date.today().year
    deadline = parse_date(due_raw)
    amin, amax = parse_amounts(amt_raw)
    if stale or (deadline and deadline < today_iso()):
        status = "ended"          # stale listings are retained but never shown as open
    else:
        status = "open" if deadline else "unknown"
    return Scholarship(
        source=f"gsite_{short}", source_id=sha(norm_name(name))[:12], name=name, url=link or page_url,
        region="charlotte", geo_scope="nc", amount_raw=amt_raw, amount_min=amin, amount_max=amax,
        deadline_raw=due_raw, deadline=deadline, status=status,
        eligibility="\n".join(fields["elig"]), description="\n".join(fields["other"]),
        sources=[page_url], confidence="low" if stale or not fields["elig"] else "medium", raw_text=raw,
        extra={"section": block[0]["section"], "stale": stale}, scraped_at=now_iso())


def _link_rows(short: str, page_url: str, lines: list[dict]) -> list[Scholarship]:
    rows = []
    for l in lines:
        if not l["link"] or any(s in l["link"] for s in _SEARCH_ENGINES):
            continue
        if not re.search(r"scholar|award|grant|fellow", l["text"], re.I) or len(l["text"]) > 400:
            continue
        rows.append(Scholarship(
            source=f"gsite_{short}", source_id=sha(norm_name(l["text"][:80]))[:12],
            name=l["text"][:120], url=l["link"], region="charlotte", geo_scope="nc",
            sources=[page_url], confidence="low", raw_text=l["text"],
            extra={"section": l["section"], "parse": "link_only"}, scraped_at=now_iso()))
    return rows


def parse_page(short: str, page_url: str, html: str) -> list[Scholarship]:
    lines = flatten(html)
    blocks = _labelled_blocks(lines)
    if len(blocks) >= 3:
        return [_block_to_row(short, page_url, b) for b in blocks if len(b[0]["text"]) < 200]
    return _link_rows(short, page_url, lines)


def dedupe(rows: list[Scholarship]) -> list[Scholarship]:
    """Across Part 2 sources: merge on normalised name + sponsor, keep every source URL."""
    merged: dict[str, Scholarship] = {}
    for r in rows:
        k = norm_name(r.name) + "|" + norm_name(r.sponsor or "")
        if k in merged:
            keep = merged[k]
            keep.sources = sorted(set(keep.sources) | set(r.sources))
            if keep.confidence == "low" and r.confidence != "low":   # prefer the better parse
                r.sources = keep.sources
                merged[k] = r
        else:
            merged[k] = r
    return list(merged.values())


def scrape(fetcher: PoliteFetcher, log=print) -> Iterator[Scholarship]:
    rows: list[Scholarship] = []
    for short, url in PAGES.items():
        try:
            html = fetcher.get(url, refresh=True)
        except Blocked as e:
            log(f"[gsites] stopped: {e}")
            break
        except Exception as e:      # 404 / stale URL: log and skip, never guess a replacement
            log(f"[gsite_{short}] skipped: {type(e).__name__} {e}")
            fetcher.stats["skipped"].append(f"{url} ({type(e).__name__})")
            continue
        page_rows = parse_page(short, url, html)
        log(f"[gsite_{short}] {len(page_rows)} rows ({sum(r.confidence == 'low' for r in page_rows)} low-confidence)")
        rows += page_rows
    yield from dedupe(rows)
