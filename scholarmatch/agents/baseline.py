"""Single-agent baseline, for evaluation only (scripts/eval_agents.py). Never used by the app.

The "10 principles" paper warns that multi-agent systems often barely beat one agent given the same budget, so the
supervised pipeline has to be measured against one. This baseline keeps everything that is not "agents":
the same SQL stage, rule checks, shortlist, redacted profile and code rubric. In place of Extractor -> Filter ->
Scorer <-> Verifier and the Supervisor's checks, ONE call per scholarship decides keep/drop/uncertain and rates fit,
and its answer is used as given: no grounding gate, no retries, no Verifier.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..llm import dumps
from ..matching import hard_filter, rubric
from ..models import Exclusion, FitEvidence, MatchResult, Scholarship, ScoreJudgement, StudentProfile
from .supervisor import Supervisor
from .workers import _DATA_RULE, _QUOTE_RULE, _listing_block


class SingleAgentJudgement(BaseModel):
    verdict: Literal["keep", "drop", "uncertain"]
    reason: str
    scholarship_quote: str
    fit: Literal["strong", "moderate", "weak", "none"]
    fit_evidence: list[FitEvidence]
    essay_reuse: Literal["full", "partial", "none", "not_applicable"]
    reusable_essay_title: str
    strengths: list[str]
    gaps: list[str]
    summary: str


SINGLE_AGENT_SYSTEM = f"""You review ONE scholarship for ONE student in a scholarship matcher. A deterministic filter has
already checked the structured rules (GPA, residency, level, school, major and similar). You do the rest in one answer.
{_DATA_RULE}

1. verdict: "drop" if the listing states a requirement the student clearly cannot meet (give the exact sentence in
   scholarship_quote), "uncertain" if there is such a requirement the profile cannot confirm or rule out, else "keep".
   When in doubt choose "uncertain", never "drop". The profile omits name, location and demographics on purpose.
2. fit: "strong" (two or more specific matches between the listing's selection criteria and the profile),
   "moderate" (one match, or no subjective criteria), "weak" (loose overlap), "none" (no sign).
3. fit_evidence: one entry per selection criterion (max 6): criterion, scholarship_quote, student_evidence (copied
   from the profile, or "" if a gap), met. {_QUOTE_RULE}
4. essay_reuse / reusable_essay_title (one of the student's essay titles, or ""), up to 3 strengths and gaps, and a
   one-sentence summary. Never output scores or odds."""


class SingleAgentSupervisor(Supervisor):
    """Same pipeline up to the shortlist; one unchecked agent call per scholarship after it."""

    def fingerprint(self, p: StudentProfile, as_of: str) -> str:
        return "single-" + super().fingerprint(p, as_of)[:13]     # never mistaken for a stored pipeline run

    def _evaluate(self, d: dict, p: StudentProfile, view: dict) -> MatchResult | Exclusion:
        s: Scholarship = d["s"]
        c, checks = d["c"], hard_filter.check(d["s"], d["c"], p)
        flags = list(d["flags"])
        try:
            user = (f"<student_profile>\n{dumps(view)}\n</student_profile>\n{_listing_block(s)}\n"
                    f"<selection_criteria_found>\n{dumps(c.soft_criteria)}\n</selection_criteria_found>")
            a = self.llm.structured("scorer", SINGLE_AGENT_SYSTEM, user, SingleAgentJudgement)
        except Exception as e:  # noqa: BLE001 - same fail-safe as the real pipeline, so failures are not counted as answers
            self._audit("baseline", "llm_failure", {"key": s.key, "error": f"{type(e).__name__}: {e}"[:300]})
            pre: MatchResult = d["pre"]
            pre.flags.append("An AI review step failed for this item; showing the rule-based score instead.")
            return pre
        self._audit("baseline", "answered", {"key": s.key, "verdict": a.verdict, "fit": a.fit})
        if a.verdict == "drop":
            return Exclusion(scholarship_key=s.key, name=s.name, stage="fuzzy_filter",
                             reason=f"{a.reason} (listing says: “{a.scholarship_quote}”)")
        if a.verdict == "uncertain":
            flags.append(f"Check — {a.reason}")
        j = ScoreJudgement(**a.model_dump(exclude={"verdict", "reason", "scholarship_quote"}))
        return rubric.build_result(s, c, p, checks, j, "llm", flags)
