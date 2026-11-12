"""Plain-language summary text for a stored MatchRun. Pure functions, no Streamlit, so tests can import them."""
from __future__ import annotations

from .models import MatchRun


def ai_reviewed_count(run: MatchRun) -> int:
    """Items the agents actually reviewed. Items that fell back to rules carry mode == "heuristic"."""
    return sum(r.mode == "llm" for r in run.results)


def review_mode_label(run: MatchRun) -> str:
    """How the results were produced, for the run header on the matches page."""
    n, m = ai_reviewed_count(run), len(run.results)
    if n > 0:
        return f"AI-reviewed ({n} of {m})"
    if run.mode == "llm":
        return "rule-based (AI review unavailable)"
    return "rule-based"
