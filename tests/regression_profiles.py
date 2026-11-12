"""The made-up student profiles from the manual tests (docs/manual_test_2026-11-10*.md).

Every person here is invented, so these can be committed. Fields the notes do not state are left at their
"not given" default (None / empty), the way the Profile form leaves them, so the hard filter must answer
`unknown` for them (invariant 4).

`sam` is not from the notes: it is an eligible SC control student, added so that over-exclusion of LIFE,
Palmetto Fellows and HOPE is caught (round 1 profile 3 is not a U.S. citizen, so she is not eligible for them).
"""
import json
from pathlib import Path

from scholarmatch.models import Scholarship, StudentProfile

CORPUS = Path(__file__).parent / "fixtures" / "regression_listings.jsonl"


def load_corpus(path: Path = CORPUS) -> list[tuple[Scholarship, dict, list[str]]]:
    """(scholarship, expect, ineligible_for) per corpus line. The two sibling keys are stripped before
    validation, so they never end up inside a `Scholarship`."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            expect, inel = d.pop("expect", {}), d.pop("ineligible_for", [])
            out.append((Scholarship.model_validate(d), expect, inel))
    return out


PROFILES: dict[str, StudentProfile] = {
    # round 1, profile 1: NC HS senior, Charlotte, GPA 3.1, applying to ECU, nursing
    "r1p1": StudentProfile(profile_id="r1p1", display_name="Round 1 profile 1", state="NC", county="Mecklenburg",
                           city="Charlotte", level="hs_senior", gpa=3.1, intended_schools=["ecu"], majors=["Nursing"]),
    # round 1, profile 2: VA (Loudoun) VT junior, Computer Science, GPA 2.4, no financial need, male
    "r1p2": StudentProfile(profile_id="r1p2", display_name="Round 1 profile 2", state="VA", county="Loudoun",
                           level="undergrad", class_year="junior", gpa=2.4, current_school="vt",
                           majors=["Computer Science"], financial_need=False, gender="male"),
    # round 1, profile 3: SC (Richland) HS senior, GPA 2.8, not a U.S. citizen, going to USC, Biology
    "r1p3": StudentProfile(profile_id="r1p3", display_name="Round 1 profile 3", state="SC", county="Richland",
                           level="hs_senior", gpa=2.8, intended_schools=["usc"], majors=["Biology"],
                           us_citizen_or_resident=False),
    # round 1, profile 4: VT graduate student, Master of Information Technology, GPA 3.7, financial need
    "r1p4": StudentProfile(profile_id="r1p4", display_name="Round 1 profile 4", state="VA", level="graduate",
                           gpa=3.7, current_school="vt", majors=["Information Technology"], financial_need=True),
    # round 2, profile 1: Maya, NC (Pitt Co.) HS senior -> ECU, Nursing, GPA 3.2, need, first-gen
    "maya": StudentProfile(profile_id="maya", display_name="Maya Testperson", state="NC", county="Pitt",
                           level="hs_senior", gpa=3.2, intended_schools=["ecu"], majors=["Nursing"],
                           financial_need=True, first_generation=True),
    # round 2, profile 2: Derek, SC USC sophomore, Mechanical Engineering, GPA 2.6, no need
    "derek": StudentProfile(profile_id="derek", display_name="Derek Testperson", state="SC", level="undergrad",
                            class_year="sophomore", gpa=2.6, current_school="usc", majors=["Mechanical Engineering"],
                            financial_need=False),
    # round 2, profile 3: Aisha, VA (Henrico) HS senior -> Virginia Tech, Business / Finance, GPA 3.9, need
    "aisha": StudentProfile(profile_id="aisha", display_name="Aisha Testperson", state="VA", county="Henrico",
                            level="hs_senior", gpa=3.9, intended_schools=["vt"], majors=["Business", "Finance"],
                            financial_need=True),
    # control (not from the notes): SC HS senior, citizen, GPA 3.7 -> USC, Biology
    "sam": StudentProfile(profile_id="sam", display_name="Sam Control", state="SC", county="Greenville",
                          level="hs_senior", gpa=3.7, intended_schools=["usc"], majors=["Biology"],
                          us_citizen_or_resident=True),
}
