"""Deterministic eligibility filtering (R15). No LLM is involved in anything in this file.

Stage 1 (SQL):    scam-blocked, ended / past-deadline, wrong state or school, loans, GPA below minimum.
Stage 2 (Python): every structured criterion -> pass / fail / unknown.
                  Only a definite `fail` removes a scholarship. `unknown` keeps it and raises a
                  "verify" flag, so missing profile data never silently hides an award.
"""
from __future__ import annotations

import json
import re

from .. import config, db
from ..ingest.extract_rules import (STATE_NAMES, field_match, locality_has_alternative, non_resident_has_alternative,
                                    non_resident_state, rising_years, unit_name)
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
        out.append(locality_check(c.localities, p, c.evidence.get("localities", "")))
    if c.destination_schools and not s.school:
        out.append(college_check(c.destination_schools, p))
    if c.non_resident_required:
        out.append(non_resident_check(s, c, p))
    if c.min_age is not None:
        out.append(age_check(c.min_age, p))
    if c.institution_types and not s.school:
        out.append(institution_check(c.institution_types, p))

    rising = rising_years(c.evidence.get("class_years", ""))
    if c.levels:
        # "rising sophomores" are current college freshmen, so they do not include incoming students either
        incoming_ok = "freshman" in c.class_years and "sophomore" not in rising
        upper_years_only = bool(c.class_years) and not incoming_ok
        if p.level in c.levels:
            add("Education level", "pass", f"Open to: {', '.join(c.levels)}.")
        elif p.level == "hs_senior" and upper_years_only:
            add("Education level", "fail", f"For college {', '.join(c.class_years)}s; you are a high school senior.")
        elif p.level == "hs_senior" and "undergrad" in c.levels and not llm:
            add("Education level", "unknown", "May require current college enrollment; check the listing.")
        elif p.level == "hs_senior" and "undergrad" in c.levels and s.school in student_schools(p):
            add("Education level", "unknown", "For enrolled students; you may qualify once admitted.")
        else:
            add("Education level", "fail", f"Open to: {', '.join(c.levels)}; you are {p.level}.")

    if c.class_years and p.level == "undergrad":
        if not p.class_year:
            add("Class year", "unknown", f"For {', '.join(c.class_years)}; add your class year.")
        elif p.class_year.lower() in rising:
            yr = p.class_year.lower()
            add("Class year", "unknown", f"For rising {yr}s (students who will be {yr}s next year); as a {yr} now, "
                                         f"check whether you still qualify.")
        else:
            add("Class year", "pass" if p.class_year.lower() in c.class_years else "fail",
                f"For {', '.join(c.class_years)}; you are a {p.class_year}.")

    if c.majors:
        if not p.majors:
            add("Field of study", "unknown", f"For {', '.join(c.majors[:4])}; add your major.")
        else:
            # Only "scholarship field inside student's major" counts as a direct hit: "engineering" covers
            # "Mechanical Engineering", but "Mechanical Engineering" does not cover a student who wrote "Engineering".
            direct = any(norm(a) in norm(b) for a in c.majors for b in p.majors if norm(a))
            result = "pass" if direct else field_match(c.majors, p.majors)
            detail = {"pass": f"For {', '.join(c.majors[:4])}; matches {', '.join(p.majors)}.",
                      "unknown": f"For {', '.join(c.majors[:4])}; check that your major ({', '.join(p.majors)}) qualifies.",
                      "fail": f"For {', '.join(c.majors[:4])}; your major is {', '.join(p.majors)}."}[result]
            if result == "pass" and c.majors_from_unit and not direct:
                # the listing names a college or department, not a major: a related field is not proof
                unit = unit_name(c.evidence.get("majors_from_unit", "")) or "the college or department it names"
                result, detail = "unknown", f"For students in the {unit}; check that your major is in that college."
            elif result == "pass" and c.declared_major_required and p.level == "hs_senior":
                result = "unknown"
                detail = (f"Requires a declared major in {', '.join(c.majors[:4])}; incoming students may not "
                          f"qualify yet.")
            add("Field of study", result, detail)

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
    for field, attr, name, label in (
            ("disability_required", "disability", "Disability", "Students with a documented disability."),
            ("first_generation_required", "first_generation", "First-generation", "First-generation college students."),
            ("military_affiliation_required", "military_affiliation", "Military", "Military / veteran affiliation."),
            ("us_citizen_or_resident_required", "us_citizen_or_resident", "Citizenship", "U.S. citizen or permanent resident.")):
        if getattr(c, field):
            add(name, _tri(True, getattr(p, attr)), label)
    if c.restricted_groups:
        out.append(group_check(c.restricted_groups, p))
    return out


# --------------------------------------------------------------------------- locality, college, out-of-state (WP4)
# The details never repeat the student's county / city / high school (check details reach the chat agent).
def _has_word(name: str, field: str) -> bool:
    return bool(name) and re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", field) is not None


def _loc_names(text: str) -> list[str]:
    """Split "Northampton or Accomack", "Concord/Cabarrus", "A, B and C" into single normalised names."""
    parts = re.split(r",|/|\bor\b|\band\b|&", norm(text))
    return [x for x in (re.sub(r"^(?:the|a|an)\s+", "", p.strip(" .")) for p in parts) if x]


def _parse_locality(loc: str) -> tuple[str, list[str]]:
    """(kind, names) for one locality entry. kind: county | high_school | place (city or school system)."""
    l = norm(loc)
    if re.search(r"charlotte[- ]mecklenburg|\bcms\b", l):
        return "place", ["charlotte", "mecklenburg"]
    if m := re.search(r"(.+?)\s+count(?:y|ies)\b", l):
        return "county", _loc_names(re.sub(r"^.*\b(?:of|from|in)\s+", "", m.group(1)))
    if m := re.search(r"(.+?)\s+high schools?\b", l):
        return "high_school", [m.group(1).strip()]
    if m := re.search(r"(?:city|town) of\s+(.+)", l):
        return "place", _loc_names(m.group(1))
    if m := re.search(r"(.+?)\s+(?:city\s+)?(?:public\s+)?schools?\b", l):
        return "place", _loc_names(m.group(1))
    return "place", _loc_names(l)


def _locality_result(loc: str, county: str, city: str, hs: str) -> str:
    kind, names = _parse_locality(loc)
    if kind == "high_school":
        if hs:
            return "pass" if any(_has_word(n, hs) for n in names) else "fail"
        # No high school given: a school whose name carries the student's city/county may be theirs; any other
        # school is somewhere else (the student told us where they live).
        if county or city:
            mine = [re.sub(r"\s+county$", "", w) for w in _loc_names(f"{county}, {city}")]
            near = any(_has_word(w, n) for n in names for w in mine if w)
            return "unknown" if near else "fail"
        return "unknown"
    if kind == "county":
        if any(_has_word(n, county) or _has_word(n, city) for n in names):
            return "pass"
        return "fail" if county else "unknown"       # a city alone does not say which county
    # a city or school system: independent cities are often given as the county too
    if any(_has_word(n, county) or _has_word(n, city) for n in names):
        return "pass"
    return "fail" if (county or city) else "unknown"


def locality_check(locs: list[str], p: StudentProfile, sentence: str = "") -> HardCheck:
    """"Locality": pass when any listed county / city / school system / high school is the student's; fail when
    the student's location rules all of them out; unknown when the profile does not say enough. When the sentence
    offers another way in after the place ("… Counties or have accessed … programming"), a fail becomes a
    restrictive unknown, as for the out-of-state rule."""
    shown = ", ".join(locs[:4]) + ("…" if len(locs) > 4 else "")
    county, city, hs = norm(p.county), norm(p.city), norm(p.high_school)
    if not (county or city or hs):
        return HardCheck(name="Locality", result="unknown",
                         detail=f"Limited to {shown}; add your county, city or high school to confirm.")
    results = {_locality_result(loc, county, city, hs) for loc in locs}
    if "pass" in results:
        return HardCheck(name="Locality", result="pass", detail=f"Limited to {shown}; this matches your location.")
    if "unknown" in results:
        return HardCheck(name="Locality", result="unknown",
                         detail=f"Limited to {shown}; add your county and high school to confirm.")
    if locality_has_alternative(sentence, locs):
        return HardCheck(name="Locality", result="unknown", weight="restrictive",
                         detail=f"Mainly for {shown}; your location is not one of these, so you would need to "
                                f"qualify another way the listing names.")
    return HardCheck(name="Locality", result="fail", detail=f"Limited to {shown}; your location is not one of these.")


def _school_names(text: str) -> set[str]:
    t = norm(text).strip()
    if t in config.SCHOOLS:
        return {t, norm(config.SCHOOLS[t][0])}
    return {t} if t else set()


def _acronym(name: str) -> str:
    return "".join(w[0] for w in re.findall(r"[a-z0-9]+", norm(name)) if w not in ("of", "the", "and", "at"))


def college_check(dest: list[str], p: StudentProfile) -> HardCheck:
    """"College attending": the award is only for students enrolling at a named college."""
    shown = " or ".join(dest[:3])
    mine = set().union(*(_school_names(x) for x in [p.current_school, *p.intended_schools]))
    if not mine:
        return HardCheck(name="College attending", result="unknown",
                         detail=f"Only for students attending {shown}; add the colleges you are applying to.")
    for d in dest:
        dn = norm(d)
        if any(_has_word(dn, m) or _has_word(m, dn) or m == _acronym(d) for m in mine):
            return HardCheck(name="College attending", result="pass",
                             detail=f"Only for students attending {shown}; it is on your list of colleges.")
    return HardCheck(name="College attending", result="fail",
                     detail=f"Only for students attending {shown}; it is not on your list of colleges.")


def non_resident_check(s: Scholarship, c: Criteria, p: StudentProfile) -> HardCheck:
    """"Residency" for out-of-state awards: fail for a resident of the school's state, unless the same sentence
    gives another way to qualify ("out-of-state students or descendants of X"), which makes it a restrictive unknown."""
    sentence = c.evidence.get("non_resident_required", "")
    state = config.SCHOOLS[s.school][1] if s.school in config.SCHOOLS else non_resident_state(sentence)
    if not state:
        return HardCheck(name="Residency", result="unknown",
                         detail="For out-of-state students; check which state the award counts you as a resident of.")
    if p.state.upper() != state:
        return HardCheck(name="Residency", result="pass",
                         detail=f"For students from outside {state}; you live in {p.state}.")
    if non_resident_has_alternative(sentence):
        return HardCheck(name="Residency", result="unknown", weight="restrictive",
                         detail=f"Mainly for students from outside {state}; you live in {state}, so you would "
                                f"need to qualify another way the listing names.")
    return HardCheck(name="Residency", result="fail", detail=f"Only for students from outside {state}; you live in {state}.")


# --------------------------------------------------------------------------- age, kind of college (WP6)
# Below this minimum age a missing age is a normal unknown (most students reach 18-21 during college); from it on, the
# award is for adult learners, so a missing age is a restrictive unknown, and a high-school senior is assumed too young.
ADULT_MIN_AGE = 25


def age_check(min_age: int, p: StudentProfile) -> HardCheck:
    """"Age". The detail never repeats the student's age (check details reach the chat agent).

    The one assumption the plan allows (docs/fix_plan.md WP6): with no age given, a high-school senior is taken to be
    under 25, so an award for people 25 or older fails; the detail says so and how to correct it."""
    if p.age is not None:
        if p.age >= min_age:
            return HardCheck(name="Age", result="pass", detail=f"For people {min_age} or older; your age meets this.")
        return HardCheck(name="Age", result="fail", detail=f"For people {min_age} or older; you are younger.")
    if p.level == "hs_senior" and min_age >= ADULT_MIN_AGE:
        return HardCheck(name="Age", result="fail",
                         detail=f"For people {min_age} or older. You have not given your age, so as a high school "
                                f"senior you are assumed to be under {ADULT_MIN_AGE}; add your age on the Profile page "
                                f"if that is wrong.")
    return HardCheck(name="Age", result="unknown", weight="restrictive" if min_age >= ADULT_MIN_AGE else "normal",
                     detail=f"For people {min_age} or older; add your age to confirm.")


_INST_LABEL = {"two_year": "two-year colleges", "technical": "technical colleges",
               "four_year": "four-year colleges and universities"}
_TECH_NAME = re.compile(r"\btechnical (?:community )?college\b|\btechnical institute\b")
_TWO_YEAR_NAME = re.compile(r"\bcommunity college\b|\bjunior college\b|\btwo[- ]year\b")


def school_kinds(name: str) -> set[str]:
    """Kinds of college a profile school is: the portals in `config.SCHOOLS` are four-year universities; a name with
    "Technical College" / "Community College" is two-year. Anything else is not known (empty set)."""
    n = norm(name).strip()
    if n in config.SCHOOLS or n in {norm(v[0]) for v in config.SCHOOLS.values()}:
        return {"four_year"}
    if _TECH_NAME.search(n):
        return {"technical", "two_year"}
    if _TWO_YEAR_NAME.search(n):
        return {"two_year"}
    return set()


def institution_check(kinds: list[str], p: StudentProfile) -> HardCheck:
    """"Type of college": pass when a school on the profile is of a listed kind; fail when every school on the profile
    is known and none is; unknown when the profile names no school or a school whose kind is not known."""
    shown = " or ".join(_INST_LABEL[k] for k in kinds)
    schools = [x for x in [p.current_school, *p.intended_schools] if x and x.strip()]
    if not schools:
        return HardCheck(name="Type of college", result="unknown",
                         detail=f"Only for students at {shown}; add the colleges you attend or are applying to.")
    found = [school_kinds(x) for x in schools]
    if any(k & set(kinds) for k in found):
        return HardCheck(name="Type of college", result="pass",
                         detail=f"Only for students at {shown}; a college on your list is one.")
    if all(found):
        return HardCheck(name="Type of college", result="fail",
                         detail=f"Only for students at {shown}; none of the colleges on your list is one.")
    return HardCheck(name="Type of college", result="unknown",
                     detail=f"Only for students at {shown}; check whether the college you attend counts.")


# Words that say what KIND of group it is, not WHICH one: they never count as an overlap on their own
# (so "my mom is a Virginia Tech employee" does not match "children of tobacco growers in Southside Virginia").
_GROUP_STOP = set(
    "the and for with from that this who have has are was were its into than our your their his her a an of or in on "
    "at to by as is be "
    "child children son sons daughter daughters dependent dependents spouse spouses parent parents family relative "
    "employee employees employed staff faculty associate associates member members membership active current "
    "good standing descendant descendants direct lineal documented student students applicant applicants recipient "
    "recipients receiving received scholarship scholarships award awards program programs completed earned degree "
    "transfer transferring college colleges community university school system club registered services diagnosed "
    "living disease disorder condition conditions company inc llc llp corp corporation foundation".split()) | {
    n for name in STATE_NAMES for n in name.split()}


def _group_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9'-]*[a-z0-9]", norm(text)) if w not in _GROUP_STOP}


_NATIONAL_GUARD = re.compile(r"\bnational guard\b", re.I)


def group_check(groups: list[str], p: StudentProfile) -> HardCheck:
    """"Specific group": pass when an affiliation shares a distinctive word with a group phrase; fail only when the
    student ticked "none of these apply to me"; otherwise a restrictive unknown (decision D1 in docs/fix_plan.md).
    The detail never repeats the student's affiliation text (it reaches the chat agent)."""
    shown = "; ".join(groups[:2])
    mine = set().union(*(_group_tokens(a) for a in p.affiliations)) if p.affiliations else set()
    if any(_group_tokens(g) & mine for g in groups):
        return HardCheck(name="Specific group", result="pass",
                         detail=f"Only for a specific group: “{shown}”. This matches something you listed under Affiliations.")
    if p.no_special_affiliations:
        return HardCheck(name="Specific group", result="fail",
                         detail=f"Only for a specific group: “{shown}”. You said no special group applies to you.")
    if p.military_affiliation is False and any(_NATIONAL_GUARD.search(g) for g in groups):
        return HardCheck(name="Specific group", result="fail",
                         detail=f"Only for a specific group: “{shown}”. You said you have no military tie.")
    return HardCheck(name="Specific group", result="unknown", weight="restrictive",
                     detail=f"Only for a specific group: “{shown}”. Add this under Affiliations if it applies to you.")


def failures(checks: list[HardCheck]) -> list[str]:
    return [f"{c.name}: {c.detail}" for c in checks if c.result == "fail"]


def preference_exclusion(c: Criteria, s: Scholarship, p: StudentProfile) -> str:
    if p.no_essay_only and (c.requirements.essay_count or 0) > 0:
        return f"You asked for no-essay awards; this needs {c.requirements.essay_count} essay(s)."
    top = s.amount_max or s.amount_min
    if p.min_award and top is not None and top < p.min_award:
        return f"Award (${top:,.0f}) is below your minimum of ${p.min_award:,.0f}."
    return ""
