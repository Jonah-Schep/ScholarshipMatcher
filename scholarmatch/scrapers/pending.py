"""Sources from scraper-brief.md that are deliberately NOT automated yet, and why.

The brief says to stop and report back in each of these situations rather than guess or
work around a restriction. `run()` raises with the reason so the CLI prints it.
"""
from __future__ import annotations

import re

from ..config import STATES_IN_SCOPE

REASONS = {
    "bigfuture": (
        "College Board BigFuture: the brief requires confirming College Board's site terms allow "
        "automated access before building. Those terms could not be retrieved from this environment, "
        "so the scraper is not enabled. Read the terms yourself; if they permit it, implement "
        "scrapers/bigfuture.py (sitemap -> /scholarships/{slug} -> Charlotte filter)."),
    "careeronestop": (
        "CareerOneStop: needs COS_USER_ID / COS_API_TOKEN (the owner must register) and the current API "
        "docs to confirm the scholarship endpoint - the brief forbids guessing endpoint URLs, and the docs "
        "site returned 403 from this environment. `classify_geo_scope` below is ready for when it is wired up."),
    "schev": "SCHEV returns 403 to automated clients (robots.txt included). Per the brief: stop, copy the two "
             "tables by hand into manual_sources/schev.csv.",
    "ncseaa": "NCSEAA's robots.txt blocks AI crawlers and asks for a 10 s crawl delay; the brief recommends "
              "transcribing the handful of program pages into manual_sources/ncseaa.csv.",
    "sc_tuition_grants": "Single program: enter it once in manual_sources/sc_tuition_grants.csv.",
    "cfnc": "CFNC Terms of Use prohibit automated collection. Excluded permanently.",
}

_STATE_RX = {"NC": r"north carolina|\bN\.?C\.?\b", "VA": r"(?<!west )virginia|\bV\.?A\.?\b", "SC": r"south carolina|\bS\.?C\.?\b"}


def classify_geo_scope(residence_states: list[str], college_states: list[str]) -> tuple[str, list[str]]:
    """Brief Part 3 rule: geo_scope from residence + college location restrictions."""
    restricted = {s.strip().upper() for s in residence_states + college_states if s.strip()}
    if not restricted:
        return "national", []
    ours = sorted(restricted & set(STATES_IN_SCOPE))
    if not ours:
        return "other_state", []
    if len(ours) == 1:
        return ours[0].lower(), ours
    return "multi_state", ours


def mentions_charlotte(text: str) -> bool:
    return bool(re.search(r"charlotte|mecklenburg|\bCMS\b", text or "", re.I))


def run(source: str):
    raise NotImplementedError(REASONS[source])
