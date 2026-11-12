from pathlib import Path

from scholarmatch.ingest import extract_rules, scam
from scholarmatch.scrapers import academicworks as aw
from scholarmatch.scrapers import gsites, state_aid
from scholarmatch.scrapers.pending import classify_geo_scope
from scholarmatch.textutil import is_grounded, is_zero_amount, parse_amounts, parse_date, redact_pii
from tests.conftest import sch

FIX = Path(__file__).parent / "fixtures"


def rd(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_grounding_gate():
    src = "Applicants must have a minimum GPA of 3.0 and live in “Wake County”."
    assert is_grounded("minimum gpa  of 3.0", src)
    assert is_grounded('live in "Wake County"', src)
    assert not is_grounded("minimum GPA of 3.5", src)
    assert not is_grounded("GPA", src)            # too short to count as evidence


def test_amounts_and_dates():
    assert parse_amounts("$3,000 per year for 4 years") == (3000, 3000)
    assert parse_amounts("$500-$5,000") == (500, 5000)
    assert parse_amounts("Varies") == (None, None)
    assert parse_amounts("$80 per credit hour") == (None, None)
    assert parse_date("11/16/2026") == "2026-11-16"
    assert parse_date("March 1, 2027") == "2027-03-01"
    assert parse_date("Feb. 6") is None           # no year -> do not guess
    assert parse_date("Ended") is None


def test_zero_amount_is_unknown_not_zero():
    from scholarmatch.matching import rubric
    assert parse_amounts("$0.00") == (None, None)
    assert parse_amounts("$0 - $0") == (None, None)
    assert is_zero_amount("$0.00") and is_zero_amount("$0 - $0")
    assert not is_zero_amount("$0 - $1,000") and not is_zero_amount("Varies") and not is_zero_amount("")
    zero = sch("z", "Program Fund", "Supports the program.", amount_raw="$0.00", amount_min=0, amount_max=0)
    assert rubric.amount_value(zero) is None and rubric.amount_display(zero) == "Varies"
    assert rubric.amount_display(sch("v", "V", "x", amount_raw="")) == "Varies"
    real = sch("r", "Real", "x", amount_raw="$500-$5,000", amount_min=500, amount_max=5000)
    assert rubric.amount_value(real) == 2750 and rubric.amount_display(real) == "$500-$5,000"


def test_redaction():
    out = redact_pii("I am Jordan Testcase, jt@example.com, 540-555-1212.", ("Jordan Testcase",))
    assert "Jordan" not in out and "example.com" not in out and "555" not in out


def test_academicworks_parsers():
    rows = aw.parse_listing(rd("aw_listing.html"))
    assert len(rows) == 50 and rows[0]["id"].isdigit() and rows[0]["name"]
    s = aw.to_scholarship("vt", rows[0], aw.parse_detail(rd("aw_detail.html")))
    assert s.source == "academicworks_vt" and s.school == "vt" and s.description
    assert len(s.extra["supplemental_questions"]) == 3
    c = extract_rules.extract(s)
    assert c.requirements.essay_count == 3 and c.application_method == "direct" and c.need_based


def test_sc_che_parser():
    rows, problems = state_aid.parse_che(rd("sc_che.html"))
    assert not problems and len(rows) == 9
    by = {r.name: r for r in rows}
    assert by["Free Tuition for Residents Over 60"].extra["aid_type"] == "waiver"
    assert all(r.geo_scope == "sc" for r in rows)
    assert extract_rules.extract(rows[0]).residency_states == ["SC"]


def test_gsites_parser_flags_stale_rows():
    rows = gsites.parse_page("qc", "https://sites.google.com/x", rd("gsite_qc2c.html"))
    assert len(rows) >= 10
    assert all(r.raw_text and r.region == "charlotte" for r in rows)
    assert all(r.status == "ended" for r in rows if r.extra.get("stale"))     # 2022 list: kept, never shown as open
    assert not any(r.name.startswith("http") for r in rows)
    a, b = rows[0].model_copy(), rows[0].model_copy(update={"sources": ["https://other"]})
    merged = gsites.dedupe([a, b])
    assert len(merged) == 1 and len(merged[0].sources) == 2


def test_scam_screen():
    assert scam.screen(sch("a", "X", "A $5 processing fee applies. Winners are selected at random."))[0] == "block"
    assert scam.screen(sch("b", "X", "There is no application fee. Essay required."))[0] == "ok"
    assert scam.screen(sch("c", "Lottery Tuition Assistance", "Funded by the SC Education Lottery.", source="sc_che"))[0] == "ok"
    assert scam.screen(sch("d", "X", "Sponsored by BigWin Casino."))[0] == "block"
    # trusted source: flag, never hide
    assert scam.screen(sch("e", "X", "Sponsored by BigWin Casino.", source="academicworks_vt"))[0] == "warn"


def test_rules_preference_is_soft_not_hard():
    c = extract_rules.extract(sch(
        "w", "Women in Engineering Scholarship",
        "Awarded to female students majoring in engineering. Preference given to residents of Roanoke County."))
    assert c.gender == "female" and "engineering" in c.majors
    assert c.localities == [] and c.residency_states == [] and any("Roanoke" in x for x in c.soft_criteria)
    c2 = extract_rules.extract(sch("g", "G", "Applicants must have a minimum GPA of 3.25. Open to residents of Virginia."))
    assert c2.min_gpa == 3.25 and c2.residency_states == ["VA"] and "min_gpa" in c2.evidence


def test_cos_geo_scope_rule():
    assert classify_geo_scope([], []) == ("national", [])
    assert classify_geo_scope(["NC"], []) == ("nc", ["NC"])
    assert classify_geo_scope(["NC", "SC", "GA"], []) == ("multi_state", ["NC", "SC"])
    assert classify_geo_scope(["TX"], ["TX"]) == ("other_state", [])


def test_field_match_uses_specific_fields_not_groups():
    fm = extract_rules.field_match
    assert fm(["engineering"], ["Mechanical Engineering"]) == "pass"           # broad award, specific student
    assert fm(["mechanical engineering"], ["Mechanical Engineering"]) == "pass"
    assert fm(["Computer Engineering", "Computer Science"], ["Mechanical Engineering"]) == "fail"
    assert fm(["Dental Medicine"], ["Nursing"]) == "fail"                     # same "health" group, other field
    assert fm(["Physician Assistant Studies"], ["Nursing"]) == "fail"
    assert fm(["health sciences"], ["Nursing"]) == "pass"
    assert fm(["Clinical Nurse Specialist"], ["Nursing"]) == "unknown"        # student gave only the broad field
    assert fm(["mechanical engineering"], ["Engineering"]) == "unknown"
    assert fm(["Fashion Merchandising"], ["Nursing"]) == "fail"
    assert fm(["Underwater basket weaving"], ["Nursing"]) == "unknown"         # unmapped -> never a fail
    # A sentence naming a sub-field does not also count as the broad field.
    assert extract_rules.field_keys("majoring in Computer Engineering or Computer Science") == {
        "computer engineering", "computer science"}


def test_hard_filter_major_and_level_checks():
    from scholarmatch.matching import hard_filter
    from scholarmatch.models import StudentProfile
    nurse = StudentProfile(profile_id="n", state="NC", level="hs_senior", intended_schools=["ecu"], majors=["Nursing"])
    dental = sch("d", "Griffin Endowed", "This School of Dental Medicine scholarship is for students enrolled in "
                 "the dental medicine program with demonstrated financial need.",
                 source="academicworks_ecu", school="ecu", geo_scope="nc")
    res = {h.name: h.result for h in hard_filter.check(dental, extract_rules.extract(dental), nurse)}
    assert res["Field of study"] == "fail"

    coop = sch("w", "Kimberly Ware Award", "Recognizes an outstanding graduating senior who completed one or more "
               "work terms through the Cooperative Education and Internship Program.",
               source="academicworks_vt", school="vt", geo_scope="va")
    c = extract_rules.extract(coop)
    assert "hs_senior" not in c.levels and "undergrad" in c.levels and "senior" in c.class_years
    hs = StudentProfile(profile_id="h", state="VA", level="hs_senior", intended_schools=["vt"])
    res = {h.name: h.result for h in hard_filter.check(coop, c, hs)}
    assert res["Education level"] == "fail"

    # On a high-school counselor page "graduating senior" still means a high-school senior.
    hs_page = sch("g", "Local Award", "Graduating senior of a North Carolina high school.", source="gsite_test")
    assert "hs_senior" in extract_rules.extract(hs_page).levels


def test_check_names_are_readable():
    from scholarmatch.matching import hard_filter
    from scholarmatch.models import StudentProfile
    s = sch("c", "Citizens Award", "Applicants must be a U.S. citizen or permanent resident.")
    c = extract_rules.extract(s)
    c.us_citizen_or_resident_required = True
    names = [h.name for h in hard_filter.check(s, c, StudentProfile(profile_id="x", state="VA"))]
    assert "Citizenship" in names and "U" not in names


def test_rule_majors_keep_the_specific_field():
    s = sch("wil", "Wilson Scholarship", "Awarded to an undergraduate student majoring in Computer Engineering, "
            "Computer Information Systems, or Computer Science. Supports students in the College of Engineering.")
    c = extract_rules.extract(s)
    assert "engineering" not in c.majors and "computer engineering" in c.majors
    assert extract_rules.field_match(c.majors, ["Mechanical Engineering"]) == "fail"
    cns = sch("cns", "CNS Award", "The scholarship is intended for students enrolled in the Clinical Nurse "
              "Specialists programs within the College of Nursing.")
    assert extract_rules.field_match(extract_rules.extract(cns).majors, ["Nursing"]) == "unknown"


# ----------------------------------------------------------------------------- WP2: certainty when info is missing
def _rubric_bits():
    from scholarmatch.matching import rubric
    from scholarmatch.models import Criteria, HardCheck, ScoreJudgement, StudentProfile
    j = ScoreJudgement(fit="moderate", fit_evidence=[], essay_reuse="not_applicable", reusable_essay_title="",
                       strengths=[], gaps=[], summary="Eligibility-based award.")
    p = StudentProfile(profile_id="u", state="VA", level="undergrad", current_school="vt", gpa=3.5,
                       majors=["Nursing"])
    return rubric, Criteria, HardCheck, j, p


def _certainty(r):
    return next(line for line in r.rubric if line.dimension == "Eligibility certainty")


def test_title_only_row_is_not_certain():
    rubric, Criteria, HardCheck, j, p = _rubric_bits()
    s = sch("t", "Smith Family Endowed Scholarship", "", source="academicworks_vt", school="vt", geo_scope="va",
            extra={"detail_fetched": False})
    c = extract_rules.extract(s)
    assert rubric.info_level(s, c) == "none"
    checks = [HardCheck(name="Enrollment", result="pass", detail="You attend Virginia Tech.")]
    line = _certainty(rubric.build_result(s, c, p, checks, j, "heuristic"))
    assert line.rating == "30%" and line.points == 7.5            # was 100% (Enrollment 1 of 1) before rubric-3
    assert "no eligibility details" in line.why
    # a few words of text with no detail_fetched flag is also "none"
    short = sch("t2", "Smith Fund", "Supports students.")
    assert rubric.info_level(short, extract_rules.extract(short)) == "none"


def test_thin_row_is_capped_at_sixty_percent():
    rubric, Criteria, HardCheck, j, p = _rubric_bits()
    s = sch("t", "Generous Donor Scholarship", "This fund was established in 1987 by a grateful alumnus to "
            "honor his parents, who believed deeply in the value of an education.")
    c = extract_rules.extract(s)
    assert rubric.info_level(s, c) == "thin"
    line = _certainty(rubric.build_result(s, c, p, [], j, "heuristic"))
    assert line.rating == "60%" and "no specific eligibility requirements" in line.why
    # Enrollment is a property of the source, so it does not lift a thin listing either
    vt = s.model_copy(update={"source": "academicworks_vt", "school": "vt"})
    enr = [HardCheck(name="Enrollment", result="pass", detail="You attend Virginia Tech.")]
    assert _certainty(rubric.build_result(vt, c, p, enr, j, "heuristic")).rating == "60%"


def test_row_with_real_requirements_keeps_the_ratio():
    rubric, Criteria, HardCheck, j, p = _rubric_bits()
    s = sch("t", "Nursing Merit Scholarship", "Open to undergraduate nursing majors with a minimum GPA of 3.0.")
    c = extract_rules.extract(s)
    assert rubric.info_level(s, c) == "ok"
    passes = [HardCheck(name="GPA", result="pass", detail="ok"), HardCheck(name="Field of study", result="pass", detail="ok")]
    assert _certainty(rubric.build_result(s, c, p, passes, j, "heuristic")).rating == "100%"
    mixed = [HardCheck(name="Enrollment", result="pass", detail="ok"), HardCheck(name="GPA", result="pass", detail="ok"),
             HardCheck(name="Field of study", result="unknown", detail="check")]
    assert _certainty(rubric.build_result(s, c, p, mixed, j, "heuristic")).rating == "75%"   # Enrollment not counted


def test_restrictive_unknown_counts_zero_and_cuts_chance():
    rubric, Criteria, HardCheck, j, p = _rubric_bits()
    s = sch("t", "Nursing Merit Scholarship", "Open to undergraduate nursing majors with a minimum GPA of 3.0.")
    c = extract_rules.extract(s)
    assert HardCheck(name="x", result="unknown", detail="").weight == "normal"
    normal = [HardCheck(name="GPA", result="pass", detail="ok"),
              HardCheck(name="Specific group", result="unknown", detail="Only for members.")]
    restrictive = [normal[0], normal[1].model_copy(update={"weight": "restrictive"})]
    a = rubric.build_result(s, c, p, normal, j, "heuristic")
    b = rubric.build_result(s, c, p, restrictive, j, "heuristic")
    assert _certainty(a).rating == "75%" and _certainty(b).rating == "50%"
    assert "specific group" in _certainty(b).why
    # chance: certainty effect (0.75 vs 0.875) times the 0.25 restrictive factor
    expected = a.win_probability * (0.5 + 0.5 * 0.5) / (0.5 + 0.5 * 0.75) * rubric.RESTRICTIVE_CHANCE_FACTOR
    assert abs(b.win_probability - max(0.005, expected)) <= 0.002
    assert b.match_score < a.match_score
    assert any(f.startswith("Verify — Specific group") for f in b.flags)


def test_info_level_tolerates_fields_added_by_later_work_packages():
    from types import SimpleNamespace
    from scholarmatch.matching import rubric
    s = sch("t", "Employee Family Scholarship", "For the children of employees of Example Manufacturing Company.")
    assert "restricted_groups" in rubric.HARD_CRITERIA_FIELDS
    assert rubric.info_level(s, SimpleNamespace(restricted_groups=["children of employees"])) == "ok"
    assert rubric.info_level(s, SimpleNamespace()) == "thin"


def test_title_only_row_ranks_below_confirmed_requirements():
    rubric, Criteria, HardCheck, j, p = _rubric_bits()
    title = sch("a", "Smith Family Endowed Scholarship", "", source="academicworks_vt", school="vt", geo_scope="va",
                extra={"detail_fetched": False})
    real = sch("b", "Nursing Merit Scholarship", "Open to nursing majors with a minimum GPA of 3.0.",
               source="academicworks_vt", school="vt", geo_scope="va")
    enr = HardCheck(name="Enrollment", result="pass", detail="ok")
    r_title = rubric.build_result(title, extract_rules.extract(title), p, [enr], j, "heuristic")
    r_real = rubric.build_result(real, extract_rules.extract(real), p,
                                 [enr, HardCheck(name="GPA", result="pass", detail="ok"),
                                  HardCheck(name="Field of study", result="pass", detail="ok")], j, "heuristic")
    assert r_real.match_score > r_title.match_score and r_real.win_probability > r_title.win_probability
    for order in ("Best overall match", "Highest chance of winning"):
        assert rubric.sort_results([r_title, r_real], order)[0].scholarship_key == r_real.scholarship_key
