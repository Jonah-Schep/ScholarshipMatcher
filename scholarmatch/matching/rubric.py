"""The scoring rubric (R9, R10, R16). Pure functions: same inputs -> same numbers, always.

Agents supply categorical judgements (fit, essay reuse) with evidence; this module turns
them into points, an estimated win chance, effort hours and expected value. Every line
carries a plain-language "why" so each score is defensible and auditable.

IMPORTANT: win_probability is a transparent heuristic, not a calibrated statistic. No public
data exists on applicant counts for these awards, so the estimate is built from pool size
(who can apply), number of awards, and fit. The UI labels it as an estimate.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from ..models import Criteria, HardCheck, MatchResult, RubricLine, Scholarship, ScoreJudgement, StudentProfile
from ..textutil import is_zero_amount, money, norm

FIT_POINTS = {"strong": 35, "moderate": 24, "weak": 10, "none": 0}
FIT_ODDS = {"strong": 1.6, "moderate": 1.0, "weak": 0.5, "none": 0.15}
REUSE_FACTOR = {"full": 0.25, "partial": 0.6, "none": 1.0, "not_applicable": 1.0}
#            pool tier: (competition points /20, base win rate, label)
POOLS = {"department": (20, 0.20, "one department/college at one school"),
         "local": (17, 0.15, "one city / county / high-school area"),
         "school": (16, 0.10, "students at one school"),
         "state": (12, 0.06, "one state"),
         "multi_state": (9, 0.04, "a few states"),
         "national": (5, 0.01, "nationwide")}
HOURS = {"base": 0.5, "essay": 2.5, "letter": 1.0, "transcript": 0.25, "interview": 1.5, "portfolio": 3.0}
_STOP = set("the and for with that this from have will your their they them what when where which about into "
            "been were must also such each other more than then only over some very most student students "
            "scholarship scholarships award awards applicant applicants school college university program".split())


def pool_tier(s: Scholarship, c: Criteria) -> str:
    if s.school:
        return "department" if (c.majors or s.college_or_department) else "school"
    if c.localities or s.region:
        return "local"
    if len(c.residency_states) == 1 or s.geo_scope in ("nc", "va", "sc"):
        return "state"
    if c.residency_states or s.geo_scope == "multi_state":
        return "multi_state"
    return "national"


def narrowing(c: Criteria) -> list[str]:
    """Criteria that shrink the applicant pool beyond geography/school."""
    out = []
    if c.majors: out.append("specific field of study")
    if c.gender: out.append("gender-specific")
    if c.ethnicities: out.append("background-specific")
    if c.first_generation_required: out.append("first-generation only")
    if c.military_affiliation_required: out.append("military affiliation")
    if c.disability_required: out.append("disability-specific")
    if c.pell_required or c.need_based: out.append("need-based")
    if c.class_years: out.append("specific class year")
    return out


def effort_hours(c: Criteria, essay_reuse: str) -> tuple[float, list[str]]:
    r = c.requirements
    if c.application_method == "automatic":
        return 0.25, ["awarded automatically / via FAFSA or your school"]
    hours, parts = HOURS["base"], ["form 0.5h"]
    if c.application_method == "general_application":
        return 0.5, ["covered by the school's general scholarship application"]
    if r.essay_count:
        h = r.essay_count * HOURS["essay"] * REUSE_FACTOR.get(essay_reuse, 1.0)
        hours += h
        parts.append(f"{r.essay_count} essay(s) {h:.1f}h" + (f" ({essay_reuse} reuse of a past essay)" if essay_reuse in ("full", "partial") else ""))
    if r.recommendation_letters:
        hours += r.recommendation_letters * HOURS["letter"]
        parts.append(f"{r.recommendation_letters} recommendation(s) {r.recommendation_letters * HOURS['letter']:.1f}h")
    for flag, key, label in ((r.transcript, "transcript", "transcript"), (r.interview, "interview", "interview"),
                             (r.portfolio_or_audition, "portfolio", "portfolio/audition")):
        if flag:
            hours += HOURS[key]
            parts.append(f"{label} {HOURS[key]}h")
    return round(hours, 2), parts


ZERO_AMOUNT_FLAG = "No award amount is listed; this may be program support rather than a student scholarship."


def amount_value(s: Scholarship) -> Optional[float]:
    """Midpoint of the stated range. A zero (or missing) amount is unknown, never $0."""
    if s.amount_min is None and s.amount_max is None:
        return None
    lo, hi = s.amount_min or s.amount_max, s.amount_max or s.amount_min
    if not lo or not hi:
        return None
    v = round((lo + hi) / 2, 2)
    return v if v > 0 else None


def amount_display(s: Scholarship) -> str:
    """The amount text shown to the student; "$0.00" and empty text become "Varies"."""
    raw = (s.amount_raw or "").strip()
    return "Varies" if not raw or is_zero_amount(raw) else raw


def _value_points(v: Optional[float]) -> tuple[float, str]:
    if v is None:
        return 4, "Amount not stated (“varies”); scored as unknown."
    for cap, pts in ((500, 3), (1000, 5), (2500, 7), (5000, 8), (10000, 9)):
        if v < cap:
            return pts, f"About {money(v)}."
    return 10, f"About {money(v)}."


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", norm(text)) if w not in _STOP}


def heuristic_judgement(s: Scholarship, c: Criteria, p: StudentProfile) -> ScoreJudgement:
    """Offline stand-in for the Scorer agent: keyword overlap between what the student lists and
    what the scholarship says. Deliberately never returns 'strong' - that needs real reading."""
    text_tokens = _tokens(" ".join(c.soft_criteria) + " " + s.full_text())
    hits = []
    items = p.activities + p.interests + p.majors + [t for e in p.essays for t in e.themes]
    for item in dict.fromkeys(items):
        common = _tokens(item) & text_tokens
        if common:
            hits.append((item, sorted(common)[0]))
    if not c.soft_criteria:
        fit, summary = "moderate", "No subjective criteria stated; selection appears to rest on eligibility."
    elif len(hits) >= 2:
        fit, summary = "moderate", f"Your profile mentions {len(hits)} things this award talks about."
    elif hits:
        fit, summary = "weak", "One point of overlap with what this award looks for."
    else:
        fit, summary = "weak", "No clear overlap found by keyword matching."
    reuse = "not_applicable" if not c.requirements.essay_count else "none"
    return ScoreJudgement(fit=fit, fit_evidence=[], essay_reuse=reuse, reusable_essay_title="",
                          strengths=[f"{item} (matches “{word}”)" for item, word in hits[:4]],
                          gaps=[], summary=summary)


def build_result(s: Scholarship, c: Criteria, p: StudentProfile, checks: list[HardCheck],
                 j: ScoreJudgement, mode: str, extra_flags: list[str] | None = None) -> MatchResult:
    lines: list[RubricLine] = []
    flags = list(extra_flags or [])

    # 1. Eligibility certainty (25)
    n = len(checks)
    passed = sum(ch.result == "pass" for ch in checks)
    unknown = [ch for ch in checks if ch.result == "unknown"]
    certainty = 0.8 if n == 0 else (passed + 0.5 * len(unknown)) / n
    why = ("No eligibility restrictions were found in the listing." if n == 0 else
           f"{passed} of {n} stated requirements confirmed" + (f"; {len(unknown)} need your confirmation." if unknown else "."))
    lines.append(RubricLine(dimension="Eligibility certainty", rating=f"{certainty:.0%}", points=round(25 * certainty, 1), max_points=25, why=why))
    flags += [f"Verify — {ch.name}: {ch.detail}" for ch in unknown]

    # 2. Profile fit (35)
    met = [e for e in j.fit_evidence if e.met]
    why = j.summary + (f" Evidence: {'; '.join(f'{e.criterion} ← {e.student_evidence}' for e in met[:3])}." if met else "")
    lines.append(RubricLine(dimension="Profile fit", rating=j.fit, points=FIT_POINTS[j.fit], max_points=35, why=why))

    # 3. Competition (20)
    tier = pool_tier(s, c)
    pts, base, label = POOLS[tier]
    narrow = narrowing(c)
    bonus = min(4, 2 * len(narrow)) + (2 if (c.num_awards or 0) >= 5 else 0)
    comp = min(20, pts + bonus)
    why = f"Applicant pool: {label}" + (f", narrowed by {', '.join(narrow)}" if narrow else "") + \
          (f"; {c.num_awards} award(s) given." if c.num_awards else "; number of awards not stated.")
    lines.append(RubricLine(dimension="Competition", rating=tier.replace("_", " "), points=comp, max_points=20, why=why))

    # 4. Effort (10)
    hours, parts = effort_hours(c, j.essay_reuse)
    lines.append(RubricLine(dimension="Effort", rating=f"~{hours:g} h", points=round(10 * max(0.0, 1 - hours / 10), 1),
                            max_points=10, why="; ".join(parts) + "."))

    # 5. Award value (10)
    value = amount_value(s)
    vp, vwhy = _value_points(value)
    lines.append(RubricLine(dimension="Award value", rating=money(value) if value else "varies", points=vp, max_points=10, why=vwhy))

    # --- derived numbers
    if c.application_method == "automatic" and tier == "state":
        prob = 0.9 if not unknown else 0.6     # entitlement-style state programs: meet the rules, get the aid
    else:
        prob = base * min(2.0, 1.25 ** len(narrow))
        prob *= 1 + min(2.0, math.log10(c.num_awards)) if c.num_awards and c.num_awards > 1 else 1
        prob *= FIT_ODDS[j.fit] * (0.5 + 0.5 * certainty)
    prob = round(min(0.9, max(0.005, prob)), 3)

    if s.confidence == "low":
        flags.append("Listing came from a hand-edited page and may be out of date; confirm details at the source.")
    if is_zero_amount(s.amount_raw) and ZERO_AMOUNT_FLAG not in flags:
        flags.append(ZERO_AMOUNT_FLAG)
    return MatchResult(
        scholarship_key=s.key, name=s.name, url=s.url, source=s.source, sponsor=s.sponsor or "",
        amount_display=amount_display(s), amount_value=value, deadline=s.deadline, open_date=s.open_date,
        hard_checks=checks, judgement=j, rubric=lines, match_score=round(sum(l.points for l in lines), 1),
        win_probability=prob, effort_hours=hours, ease=round(100 * max(0.0, 1 - hours / 10), 0),
        expected_value=round(prob * value, 2) if value is not None else None,
        requirements=c.requirements, application_method=c.application_method, flags=flags, mode=mode)


SORTS = {  # label -> (key function, reverse). Ties always break on name then key => stable ordering.
    "Best overall match": (lambda r: r.match_score, True),
    "Highest chance of winning": (lambda r: r.win_probability, True),
    "Lowest chance of winning": (lambda r: r.win_probability, False),
    "Easiest to apply": (lambda r: r.effort_hours, False),
    "Hardest to apply": (lambda r: r.effort_hours, True),
    "Largest award": (lambda r: r.amount_value if r.amount_value is not None else -1, True),
    "Smallest award": (lambda r: r.amount_value if r.amount_value is not None else float("inf"), False),
    "Highest expected value": (lambda r: r.expected_value if r.expected_value is not None else -1, True),
    "Lowest expected value": (lambda r: r.expected_value if r.expected_value is not None else float("inf"), False),
    "Soonest deadline": (lambda r: r.deadline or "9999", False),
}


def sort_results(results: list[MatchResult], order: str) -> list[MatchResult]:
    key, reverse = SORTS[order]
    ordered = sorted(results, key=lambda r: (r.name.lower(), r.scholarship_key))     # deterministic tie-break
    return sorted(ordered, key=key, reverse=reverse)                                  # stable sort keeps it
