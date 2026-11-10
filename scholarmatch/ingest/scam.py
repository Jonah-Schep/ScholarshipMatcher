"""Deterministic scam / spam screen (R7). Runs at ingest, before anything reaches an LLM.

levels:  block -> never shown to students      warn -> shown with a caution flag      ok
Sources vetted upstream (school portals, state agencies, USDOL) can never be *blocked* by a
keyword (e.g. "SC Education Lottery" is not gambling); they can still be flagged `warn`.
"""
from __future__ import annotations

import re

from ..models import Scholarship
from ..textutil import norm

TRUSTED_PREFIXES = ("academicworks_", "sc_che", "schev", "ncseaa", "careeronestop", "sc_tuition_grants")

_BLOCK = [
    ("application or processing fee", r"(?<!no )(?<!without an )\b(application|processing|entry|registration|handling) fee\b(?! (is |are )?(required|waived)? ?(:)? ?(none|no|\$0))"),
    ("pay to apply", r"\b(pay|payment|purchase) (is )?(required )?to (apply|enter|be considered)\b"),
    ("gambling / betting promotion", r"\b(casino|sportsbook|sports betting|online betting|poker|gambling|slot machines?|wager(ing)?)\b"),
    ("sweepstakes / random drawing", r"\b(sweepstakes|random(ly)? (drawing|draw|selected|chosen)|winners? (is|are|will be) (drawn|chosen|selected) at random|prize draw|giveaway)\b"),
    ("guaranteed award", r"\bguaranteed (scholarship|award|winner|money)\b|\byou('ve| have) (been selected|won)\b"),
    ("asks for payment details", r"\b(credit card|debit card|bank account|routing) (number|information|details)\b"),
]
_WARN = [
    ("asks for Social Security number", r"\bsocial security (number|no\.?)\b|\bSSN\b"),
    ("marketing opt-in / lead generation", r"\b(agree to (be contacted|receive)|marketing partners?|sponsors? may contact|opt[- ]in to receive|third[- ]party offers?)\b"),
    ("no-effort monthly drawing", r"\b(monthly|weekly|daily) (drawing|winner|scholarship giveaway)\b|\bno essay\b.*\b(drawing|register to win)\b"),
    ("urgency pressure", r"\b(act now|limited time offer|hurry)\b"),
]
_NEGATED = re.compile(r"\b(no|never|not|without|free of|don't|do not|does not|should not)\b[^.]{0,40}$")


def screen(s: Scholarship) -> tuple[str, list[str]]:
    text = norm(s.full_text() + " " + s.raw_text)
    trusted = s.source.startswith(TRUSTED_PREFIXES)
    block, warn = [], []
    for label, rx in _BLOCK:
        for m in re.finditer(rx, text):
            if _NEGATED.search(text[max(0, m.start() - 60): m.start()]):
                continue      # "there is no application fee", "you should not pay to apply"
            block.append(f"{label}: “{m.group(0)}”")
            break
    for label, rx in _WARN:
        m = re.search(rx, text)
        if m:
            warn.append(f"{label}: “{m.group(0)}”")
    if block and not trusted:
        return "block", block + warn
    if block or warn:
        return "warn", block + warn
    return "ok", []
