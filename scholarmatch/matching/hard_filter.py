"""Deterministic eligibility filtering (R15). No LLM is involved in anything in this file.

Stage 1 (SQL):    scam-blocked, ended / past-deadline, wrong state or school, loans, GPA below minimum.
Stage 2 (Python): every structured criterion -> pass / fail / unknown.
                  Only a definite `fail` removes a scholarship. `unknown` keeps it and raises a
                  "verify" flag, so missing profile data never silently hides an award.
"""
from __future__ import annotations

import json

from .. import config, db
from ..ingest.extract_rules import field_groups
from ..models import Criteria, HardCheck, Scholarship, StudentProfile
from ..textutil import norm

EXCLUDED_AID_TYPES = ("loan", "forgivable_loan", "loan_repayment", "reimbursement", "fiscal_agent")
_URM = {"Black / African American", "Hispanic / Latino", "Native American"}


def student_schools(p: StudentProfile) -> list[str]:
    names = [p.current_school, *p.intended_schools]
    return sorted({n.strip().lower() for n in names if n and n.strip().lower() in config.SCHOOLS})


def sql_stage(p: StudentProfile, as_of: str) -> tuple[list[dict], dict[str, int]]:
    """Returns candidate rows and a count of exclusions by reason (for the audit trail)."""
    schools = student_schools(p) or ["__none__"]
    marks = ",".join("?" * len(schools))
    aid = ",".join("?" * len(EXCLUDED_AID_TYPES))
    reason_sql = f"""
        CASE
          WHEN s.scam_level = 'block' THEN 'flagged as scam/spam'
          WHEN s.status = 'ended' OR (s.deadline IS NOT NULL AND s.deadline < ?) THEN 'deadline passed'
          WHEN s.aid_type IN ({aid}) THEN 'loan or reimbursement, not a scholarship'
          WHEN s.school IS NOT NULL AND s.school NOT IN ({marks}) THEN 'for students of a school not on your list'
          WHEN s.school IS NULL AND s.geo_scope NOT IN ('national', 'multi_state', ?) THEN 'restricted to another state'
          WHEN c.min_gpa IS NOT NULL AND ? IS NOT NULL AND c.min_gpa > ? THEN 'GPA below stated minimum'
          ELSE NULL
        END"""
    params = [as_of, *EXCLUDED_AID_TYPES, *schools, p.state.lower(), p.gpa, p.gpa]
    with db.connect() as con:
        rows = con.execute(
            f"""SELECT s.key, s.data, s.scam_level, s.scam_reasons, s.content_hash, {reason_sql} AS reason
                FROM scholarships s LEFT JOIN criteria c
                  ON c.key = s.key AND c.kind = 'rules' AND c.content_hash = s.content_hash
                ORDER BY s.key""", params).fetchall()
    keep, counts = [], {}
    for r in rows:
        if r["reason"]:
            counts[r["reason"]] = counts.get(r["reason"], 0) + 1
        else:
            keep.append({"scholarship": Scholarship.model_validate_json(r["data"]), "content_hash": r["content_hash"],
                         "scam_level": r["scam_level"], "scam_reasons": json.loads(r["scam_reasons"])})
    return keep, counts


def _tri(required, have) -> str:
    if have is None:
        return "unknown"
    return "pass" if have else "fail"


def check(s: Scholarship, c: Criteria, p: StudentProfile) -> list[HardCheck]:
    out: list[HardCheck] = []
    add = lambda name, result, detail: out.append(HardCheck(name=name, result=result, detail=detail))
    llm = c.extracted_by == "llm+rules"

    if s.school:
        school_name = config.SCHOOLS[s.school][0]
        if p.current_school.strip().lower() == s.school:
            add("Enrollment", "pass", f"You attend {school_name}.")
        elif s.school in student_schools(p):
            add("Enrollment", "pass", f"Requires admission to / enrollment at {school_name} (on your list).")
        else:
            add("Enrollment", "fail", f"Only for {school_name} students.")

    if c.min_gpa is not None:
        if p.gpa is None:
            add("GPA", "unknown", f"Requires GPA ≥ {c.min_gpa:.2f}; add your GPA to confirm.")
        else:
            add("GPA", "pass" if p.gpa >= c.min_gpa else "fail", f"Requires ≥ {c.min_gpa:.2f}; yours is {p.gpa:.2f}.")

    if c.residency_states:
        ok = p.state.upper() in [x.upper() for x in c.residency_states]
        add("Residency", "pass" if ok else "fail", f"Residents of {', '.join(c.residency_states)}; you live in {p.state}.")

    if c.localities:
        mine = norm(" | ".join([p.county, p.city, p.high_school]))
        if not mine.replace("|", "").strip():
            add("Locality", "unknown", f"Limited to {', '.join(c.localities[:3])}; add your county/city/high school.")
        else:
            hit = False
            for loc in c.localities:
                l = norm(loc).replace(" county", "")
                charlotte = any(k in l for k in ("charlotte", "mecklenburg", "cms"))
                if (l and l in mine) or (charlotte and ("mecklenburg" in mine or "charlotte" in mine)):
                    hit = True
            add("Locality", "pass" if hit else "fail", f"Limited to {', '.join(c.localities[:3])}.")

    if c.levels:
        if p.level in c.levels:
            add("Education level", "pass", f"Open to: {', '.join(c.levels)}.")
        elif p.level == "hs_senior" and "undergrad" in c.levels and not llm:
            add("Education level", "unknown", "May require current college enrollment; check the listing.")
        elif p.level == "hs_senior" and "undergrad" in c.levels and s.school in student_schools(p):
            add("Education level", "unknown", "For enrolled students; you may qualify once admitted.")
        else:
            add("Education level", "fail", f"Open to: {', '.join(c.levels)}; you are {p.level}.")

    if c.class_years and p.level == "undergrad":
        if not p.class_year:
            add("Class year", "unknown", f"For {', '.join(c.class_years)}; add your class year.")
        else:
            add("Class year", "pass" if p.class_year.lower() in c.class_years else "fail",
                f"For {', '.join(c.class_years)}; you are a {p.class_year}.")

    if c.majors:
        if not p.majors:
            add("Field of study", "unknown", f"For {', '.join(c.majors[:4])}; add your major.")
        else:
            want, have = field_groups(c.majors), field_groups(p.majors)
            direct = any(norm(a) in norm(b) or norm(b) in norm(a) for a in c.majors for b in p.majors)
            if direct or (want & have):
                add("Field of study", "pass", f"For {', '.join(c.majors[:4])}; matches {', '.join(p.majors)}.")
            elif not want or not have:
                add("Field of study", "unknown", f"For {', '.join(c.majors[:4])}; could not map to your major.")
            else:
                add("Field of study", "fail", f"For {', '.join(c.majors[:4])}; your major is {', '.join(p.majors)}.")

    if c.pell_required:
        add("Pell eligibility", _tri(True, p.pell_eligible), "Requires Pell Grant eligibility.")
    if c.need_based:
        need = True if (p.financial_need or p.pell_eligible) else (None if p.financial_need is None and p.pell_eligible is None else False)
        add("Financial need", _tri(True, need), "Requires demonstrated financial need (usually via FAFSA).")
    if c.gender:
        g = p.gender.strip().lower()
        add("Gender", "unknown" if not g or g == "prefer not to say" else ("pass" if g == c.gender else "fail"),
            f"For {c.gender} students.")
    if c.ethnicities:
        if not p.ethnicities:
            add("Background", "unknown", f"For {', '.join(c.ethnicities)} students.")
        else:
            mine = set(p.ethnicities)
            ok = bool(mine & set(c.ethnicities)) or ("Underrepresented minority" in c.ethnicities and bool(mine & _URM))
            add("Background", "pass" if ok else "fail", f"For {', '.join(c.ethnicities)} students.")
    for field, attr, label in (("disability_required", "disability", "Students with a documented disability."),
                               ("first_generation_required", "first_generation", "First-generation college students."),
                               ("military_affiliation_required", "military_affiliation", "Military / veteran affiliation."),
                               ("us_citizen_or_resident_required", "us_citizen_or_resident", "U.S. citizen or permanent resident.")):
        if getattr(c, field):
            add(label.split(".")[0].split(" /")[0][:28], _tri(True, getattr(p, attr)), label)
    return out


def failures(checks: list[HardCheck]) -> list[str]:
    return [f"{c.name}: {c.detail}" for c in checks if c.result == "fail"]


def preference_exclusion(c: Criteria, s: Scholarship, p: StudentProfile) -> str:
    if p.no_essay_only and (c.requirements.essay_count or 0) > 0:
        return f"You asked for no-essay awards; this needs {c.requirements.essay_count} essay(s)."
    top = s.amount_max or s.amount_min
    if p.min_award and top is not None and top < p.min_award:
        return f"Award (${top:,.0f}) is below your minimum of ${p.min_award:,.0f}."
    return ""
