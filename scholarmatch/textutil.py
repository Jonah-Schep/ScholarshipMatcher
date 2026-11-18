"""Small deterministic text helpers: parsing amounts/dates, normalising, hashing, grounding."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Optional

_WS = re.compile(r"\s+")
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?\s*([kK])?")
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9


def squash(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def norm(text: str) -> str:
    """Normalise for comparison: lowercase, straight quotes, collapsed whitespace."""
    t = (text or "").lower()
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), (" ", " "), (" ", " ")):
        t = t.replace(a, b)
    return _WS.sub(" ", t).strip()


def norm_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", norm(text)).strip()


def is_grounded(quote: str, source: str, min_len: int = 8) -> bool:
    """True if `quote` appears verbatim (modulo case/whitespace/quote style) in `source`.
    This is the anti-hallucination gate used by every supervisor check."""
    q = norm(quote).strip(" .\"'")
    return len(q) >= min_len and q in norm(source)


def sha(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        if not isinstance(p, str):
            p = json.dumps(p, sort_keys=True, default=str, ensure_ascii=False)
        h.update(p.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def parse_amounts(raw: str) -> tuple[Optional[float], Optional[float]]:
    """Return (min, max) only from explicit dollar figures; else (None, None).
    Figures under $50 (including "$0.00", which AcademicWorks shows for program-support funds) are
    not award amounts, so a listing whose only figure is $0 parses as unknown (None, None)."""
    vals = []
    for m in _MONEY.finditer(raw or ""):
        v = float(m.group(1).replace(",", ""))
        if m.group(3):
            v *= 1000
        per_unit = re.match(r"\s*(?:per|/|a)\s*(?:credit|hour|unit)", (raw or "")[m.end():m.end() + 20], re.I)
        if v >= 50 and not per_unit:      # "$80 per credit hour" is a rate, not an award amount
            vals.append(v)
    if not vals:
        return None, None
    return min(vals), max(vals)


def is_zero_amount(raw: str) -> bool:
    """True when the amount text names dollar figures and every one of them is zero ("$0.00", "$0 - $0")."""
    figures = [float(m.group(1).replace(",", "")) for m in _MONEY.finditer(raw or "")]
    return bool(figures) and all(v == 0 for v in figures)


def parse_date(raw: str, default_year: Optional[int] = None) -> Optional[str]:
    """Parse common US date spellings to ISO. Returns None when unsure (never guesses a day)."""
    t = squash(raw)
    if not t:
        return None
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", t)
    if m:
        y = int(m.group(3))
        return _iso(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", t)
    if m and m.group(1).lower() in _MONTHS:
        y = int(m.group(3)) if m.group(3) else default_year
        if y:
            return _iso(y, _MONTHS[m.group(1).lower()], int(m.group(2)))
    return None


def _iso(y: int, mo: int, d: int) -> Optional[str]:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    return date.today().isoformat()


def money(v: Optional[float]) -> str:
    return "—" if v is None else f"${v:,.0f}"


_PII = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"), "[phone]"),
    (re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), "[ssn]"),
]


def redact_pii(text: str, names: tuple[str, ...] = (), places: tuple[str, ...] = ()) -> str:
    """Strip direct identifiers before any text leaves the machine.

    names: the student's name. The full name is always removed. A single part ("Priya") is removed too, unless the
      text also uses it as an ordinary lowercase word: a student called "Test One" or "Grace" must not turn every
      "one" or "grace" into "[name]" (live test 2026-11-18). In that case only the full name is removed.
    places: the student's county, city and high school, removed as whole phrases, longest first ("Blacksburg High
      School" before "Blacksburg"), so ordinary words such as "high" or "school" elsewhere are left alone."""
    out = text or ""
    for rx, repl in _PII:
        out = rx.sub(repl, out)
    for place in sorted({squash(p) for p in places if len(squash(p)) > 2}, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(place)}\b", "[place]", out, flags=re.I)
    for n in names:
        n = squash(n)
        if not n:
            continue
        out = re.sub(rf"\b{re.escape(n)}\b", "[name]", out, flags=re.I)
        for part in n.split():
            if len(part) > 2 and not re.search(rf"\b{re.escape(part.lower())}\b", out):
                out = re.sub(rf"\b{re.escape(part)}\b", "[name]", out, flags=re.I)
    return out
