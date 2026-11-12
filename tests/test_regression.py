"""Regression corpus from the manual tests (docs/manual_test_2026-11-10*.md, docs/fix_plan.md WP0).

Each line of tests/fixtures/regression_listings.jsonl is a `Scholarship` plus two sibling keys:
  expect:          {profile_id: {"result": "pass" | "unknown" | "fail", "check": <HardCheck name, optional>}}
                   `result` is the WORST check result the hard filter should give for that student (what the notes
                   say is correct, not what the code does today). With `check`, a check of that name must also give
                   that result.
  ineligible_for:  profiles the notes call (almost certainly) ineligible; used by scripts/eval_profiles.py.

Only `extract_rules.extract` + `hard_filter.check` run here: no DB, no LLM.

Cases the code gets wrong today are listed in XFAIL under the work package that fixes them. They are
strict: when a WP makes one pass, the suite fails until that WP deletes the entry.
"""
from __future__ import annotations

import pytest

from scholarmatch.ingest import extract_rules
from scholarmatch.matching import hard_filter
from tests.regression_profiles import PROFILES, load_corpus

SEVERITY = {"pass": 0, "unknown": 1, "fail": 2}

# (scholarship name, profile_id) -> work package that should fix it. Delete entries as they are fixed.
XFAIL: dict[str, list[tuple[str, str]]] = {
    "WP3": [  # awards limited to a specific group -> "Specific group" unknown (restrictive) for a silent profile
        ("Buzz's Bunch Scholarship", "r1p2"),
        ("Faculty-Staff Dependent and Spouse Scholarship", "aisha"),
        ("Martin Luther and Clara B. Vaughan Scholarship", "r1p2"),
        ("Martin Luther and Clara B. Vaughan Scholarship", "aisha"),
        ("The Virginia Tech Tribal Match Scholarship", "r1p2"),
        ("The Virginia Tech Tribal Match Scholarship", "aisha"),
        ("Graham Stedman Neal Diabetes Fund", "aisha"),
        ("Universal Leaf Tobacco Company Scholarship", "aisha"),
        ("McGuireWoods LLP Scholarship", "aisha"),
        ("Smithfield Foundation Scholarship", "aisha"),
        ("VCCS Transfer Achievement Scholarship", "aisha"),
        ("Mossberg Table Tennis Scholarship (Freshman)", "aisha"),
    ],
    "WP4": [  # county lists, school systems, high schools, destination college, out-of-state
        ("Herman C. and Carrie Watson Scholarship", "r1p2"),
        ("Herman C. and Carrie Watson Scholarship", "aisha"),
        ("Harry Bramhall Gilbert Meritorious Scholarship", "r1p2"),
        ("Harry Bramhall Gilbert Meritorious Scholarship", "aisha"),
        ("Anthony Fox Scholarship", "maya"),
        ("O. C. Thompson Scholarship", "aisha"),
    ],
    "WP5": [  # graduate / professional programs, departments and colleges, declared majors
        ("College of Science Dean's Roundtable Scholarship", "r1p2"),
        ("Hallie L. Hughes Scholarship", "r1p2"),
        ("IMPACT Scholarships (Inclusion improving Medicine, Patient outcomes And Community health with Talent)", "r1p2"),
        ("Lena L. Giannotti Scholarship for Family and Child Development", "r1p2"),
        ("MIT Scholars", "r1p2"),
        ("Growing the Future of Clinical Nurse Specialists", "maya"),
        ("Addictions and Rehabilitation Studies Inclusion Scholarship", "maya"),
        ("JB & Vera Congleton Scholarship", "maya"),
        ("Myrtle Westmoreland Scholarship", "maya"),
        ("Pecheles Automotive Scholarship", "maya"),
        ("American Legion Post #39 Scholarship", "maya"),
        ("Justin Bryce Clark Scholarship", "maya"),
    ],
    "WP6": [  # SC CHE: age, institution type, National Guard, enhancement text, HOPE/LIFE supplement
        ("Free Tuition for Residents Over 60", "r1p3"),
        ("Free Tuition for Residents Over 60", "derek"),
        ("South Carolina Workforce & Industry Needs Scholarship (SC•WINS)", "r1p3"),
        ("South Carolina Workforce & Industry Needs Scholarship (SC•WINS)", "derek"),
        ("South Carolina National Guard College Assistance Program (SCNG CAP)", "r1p3"),
        ("South Carolina National Guard College Assistance Program (SCNG CAP)", "derek"),
        ("South Carolina HOPE Scholarship (Freshmen)", "r1p3"),
        ("South Carolina Need-based Grant", "r1p3"),
        ("LIFE Scholarship", "r1p3"),
        ("LIFE Scholarship", "sam"),
        ("Palmetto Fellows Scholarship", "r1p3"),
        ("Palmetto Fellows Scholarship", "sam"),
    ],
}
_XFAIL_BY_CASE = {case: wp for wp, cases in XFAIL.items() for case in cases}


def _cases():
    seen = set()
    for s, expect, _ in load_corpus():
        for pid, exp in expect.items():
            seen.add((s.name, pid))
            marks = [pytest.mark.xfail(strict=True, reason=_XFAIL_BY_CASE[(s.name, pid)])] \
                if (s.name, pid) in _XFAIL_BY_CASE else []
            yield pytest.param(s, pid, exp, id=f"{pid}-{s.source}:{s.source_id}", marks=marks)
    missing = set(_XFAIL_BY_CASE) - seen
    assert not missing, f"XFAIL entries with no corpus case: {sorted(missing)}"


@pytest.mark.parametrize("s, pid, exp", list(_cases()))
def test_hard_filter_matches_manual_test_notes(s, pid, exp):
    checks = hard_filter.check(s, extract_rules.extract(s), PROFILES[pid])
    got = {c.name: c.result for c in checks}
    worst = max(got.values(), key=SEVERITY.__getitem__, default="pass")
    assert worst == exp["result"], f"{s.name} / {pid}: worst check {worst!r}, expected {exp['result']!r}; checks {got}"
    if exp.get("check"):
        assert got.get(exp["check"]) == exp["result"], \
            f"{s.name} / {pid}: check {exp['check']!r} gave {got.get(exp['check'])!r}; checks {got}"


def test_corpus_is_well_formed():
    rows = load_corpus()
    assert len({s.key for s, _, _ in rows}) == len(rows)
    for s, expect, inel in rows:
        assert set(expect) <= set(PROFILES) and set(inel) <= set(PROFILES), s.name
        assert all(e["result"] in SEVERITY for e in expect.values()), s.name
        if s.source.startswith("academicworks_"):
            assert s.school and s.geo_scope == {"vt": "va", "ecu": "nc", "usc": "sc"}[s.school], s.name
