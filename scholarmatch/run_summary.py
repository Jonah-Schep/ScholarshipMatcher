"""Plain-language summary and display helpers for a stored MatchRun, shared by the Streamlit app and the HTTP API.
Pure functions, no UI code, so tests can import them."""
from __future__ import annotations

from .models import Exclusion, MatchResult, MatchRun, StudentProfile


def ai_reviewed_count(run: MatchRun) -> int:
    """Items the agents actually reviewed. Items that fell back to rules carry mode == "heuristic"."""
    return sum(r.mode == "llm" for r in run.results)


def review_mode_label(run: MatchRun) -> str:
    """How the results were produced, for the run header on the matches page."""
    n, m = ai_reviewed_count(run), len(run.results)
    if n > 0:
        return f"AI-reviewed ({n} of {m})"
    if run.mode == "llm" and m == 0:
        return "AI review on (nothing passed the rule checks to review)"     # not an outage (WP8)
    if run.mode == "llm":
        return "rule-based (AI review unavailable)"
    return "rule-based"


def needs_verification(r: MatchResult) -> bool:
    """Items with a "Verify …" or "Check …" flag: the student must confirm something before applying."""
    return any(f.startswith(("Verify", "Check")) for f in r.flags)


def exclusion_counts(run: MatchRun) -> dict[str, int]:
    """Why scholarships were left out: SQL-stage reasons plus individual exclusions by stage, largest first."""
    reasons = dict(run.stats.get("sql_excluded", {}))
    for e in run.exclusions:
        stage = e.stage.replace("_", " ")
        reasons[stage] = reasons.get(stage, 0) + 1
    return dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])))


def for_display(p: StudentProfile, r: MatchResult) -> MatchResult:
    """A copy of `r` with the student's own essay titles in place of the "Essay N" labels the agents saw.
    Display only: the stored run keeps the labels."""
    show = p.show_essay_titles
    update: dict = {"flags": [show(f) for f in r.flags],
                    # rubric reasons quote the Scorer's wording, which can name "Essay N" (live run, 2026-11-18)
                    "rubric": [line.model_copy(update={"why": show(line.why)}) for line in r.rubric]}
    if r.filter_verdict:
        update["filter_verdict"] = r.filter_verdict.model_copy(update={"reason": show(r.filter_verdict.reason)})
    j = r.judgement
    if j:
        title = show(j.reusable_essay_title)
        title = title if title != j.reusable_essay_title or not title else f"“{title}”"   # runs saved before labels
        update["judgement"] = j.model_copy(update={
            "summary": show(j.summary), "strengths": [show(x) for x in j.strengths], "gaps": [show(x) for x in j.gaps],
            "reusable_essay_title": title,
            "fit_evidence": [e.model_copy(update={"criterion": show(e.criterion), "student_evidence": show(e.student_evidence)})
                             for e in j.fit_evidence]})
    return r.model_copy(update=update)


def exclusions_for_display(p: StudentProfile, run: MatchRun) -> list[Exclusion]:
    """The run's exclusions with essay titles shown (a Filter reason can mention "Essay N")."""
    return [e.model_copy(update={"reason": p.show_essay_titles(e.reason)}) for e in run.exclusions]
