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


# ----------------------------------------------------------------------------- WP3: specific groups
def _groups(text):
    return extract_rules.extract(sch("g", "Example Scholarship", text)).restricted_groups


def test_group_patterns_positive():
    cases = {   # at least one per pattern family in docs/fix_plan.md (WP3)
        "For children of employees of McGuireWoods LLP.": "children of employees of McGuireWoods LLP",
        "For sons and daughters of Acme Corp employees.": "sons and daughters of Acme Corp employees",
        "For children of tobacco growers in Southside Virginia.": "children of tobacco growers in Southside Virginia",
        "For documented descendants of Martin Luther and Clara B. Vaughan.":
            "documented descendants of Martin Luther and Clara B. Vaughan",
        "Must be a member of Phi Theta Kappa.": "member of Phi Theta Kappa",
        "Applicants must be active USATT members and must join the table tennis team.": "active USATT members",
        "Open to members in good standing of the Elks Lodge.": "members in good standing of the Elks Lodge",
        "For students receiving a Virginia tribal scholarship.": "receiving a Virginia tribal scholarship",
        "Open to recipients of the Gates Millennium Scholarship.": "recipients of the Gates Millennium Scholarship",
        "For seniors who have completed a co-op.": "completed a co-op",
        "For students registered with Services for Students with Disabilities.":
            "registered with Services for Students with Disabilities",
        "For students transferring from the Virginia Community College System (VCCS) who have earned an associate "
        "degree.": "transferring from the Virginia Community College System (VCCS)",
        "For students with diabetes or an autoimmune disease.": "students with diabetes or an autoimmune disease",
    }
    for text, phrase in cases.items():
        assert phrase in _groups(text), (text, _groups(text))
    assert _groups("For students who have earned an associate degree from any accredited school.") == [
        "earned an associate degree from any accredited school"]


def test_group_patterns_negative():
    for text in ("Preference given to children of employees of Acme Corp.",        # preference stays soft
                 "Priority will be given to members of the Elks Lodge.",
                 "Applicants should be active members of the community.",
                 "Open to any member of the graduating class.",
                 "Recipients of this scholarship will be notified in May.",
                 "Recipients of the scholarship will be announced in April.",
                 "Students receiving a scholarship must keep a 3.0 GPA.",
                 "Open to students with a disability.",
                 "Open to transfer students.",
                 "Transfer students from any college may apply.",
                 "Children of employees of the foundation are not eligible.",
                 "Must be enrolled in the College of Engineering.",
                 "Students majoring in Child Development are eligible."):
        assert _groups(text) == [], (text, _groups(text))
    s = sch("g", "Example Scholarship", "Preference given to children of employees of Acme Corp.")
    c = extract_rules.extract(s)
    assert c.restricted_groups == [] and any("children of employees" in x for x in c.soft_criteria)
    # "Recipients of the <this award> Scholarship must ..." is a renewal rule, not a group limit
    own = sch("g", "Smith Family Scholarship", "Each year, recipients of the Smith Family Scholarship must reapply.")
    assert extract_rules.extract(own).restricted_groups == []
    # "students with a disability" stays its own field
    dis = extract_rules.extract(sch("g", "Example Scholarship", "Open to students with a documented disability."))
    assert dis.disability_required and dis.restricted_groups == []


def test_group_evidence_is_grounded():
    s = sch("g", "Example Scholarship", "For dependents of Smithfield Foods employees. Minimum GPA of 3.0.")
    c = extract_rules.extract(s)
    assert c.restricted_groups == ["dependents of Smithfield Foods employees"]
    assert is_grounded(c.evidence["restricted_groups"], s.full_text())


def test_specific_group_check():
    from scholarmatch.matching import hard_filter
    from scholarmatch.models import StudentProfile
    s = sch("g", "McGuireWoods LLP Scholarship", "For children of employees of McGuireWoods LLP.")
    c = extract_rules.extract(s)

    def grp(sch_=s, c_=c, **kw):
        return next(h for h in hard_filter.check(sch_, c_, StudentProfile(profile_id="x", **kw))
                    if h.name == "Specific group")

    silent = grp()
    assert silent.result == "unknown" and silent.weight == "restrictive"
    assert "Add this under Affiliations" in silent.detail
    hit = grp(affiliations=["Mom is a paralegal at McGuireWoods"])
    assert hit.result == "pass" and "paralegal" not in hit.detail      # never echo the affiliation text
    assert grp(affiliations=["USATT member"]).result == "unknown"       # no overlap, box not ticked
    assert grp(no_special_affiliations=True).result == "fail"
    assert grp(affiliations=["USATT member"], no_special_affiliations=True).result == "fail"
    assert grp(affiliations=["McGuireWoods"], no_special_affiliations=True).result == "pass"
    # generic words and state names are not an overlap
    leaf = sch("l", "Universal Leaf Scholarship", "For children of tobacco growers in Southside Virginia.")
    lc = extract_rules.extract(leaf)
    assert grp(leaf, lc, affiliations=["Parent is a Virginia Tech employee"]).result == "unknown"
    assert grp(leaf, lc, affiliations=["family tobacco farm"]).result == "pass"


def test_group_limited_award_ranks_below_and_narrows():
    from scholarmatch.matching import hard_filter
    from scholarmatch.models import StudentProfile
    rubric, Criteria, HardCheck, j, p = _rubric_bits()
    group = sch("g", "Smithfield Foundation Scholarship", "For dependents of Smithfield Foods employees. Minimum GPA of 3.0.")
    open_ = sch("o", "Open Merit Scholarship", "Open to all students with a minimum GPA of 3.0.")
    student = StudentProfile(profile_id="x", gpa=3.5)
    rs = []
    for x in (group, open_):
        c = extract_rules.extract(x)
        rs.append(rubric.build_result(x, c, student, hard_filter.check(x, c, student), j, "heuristic"))
    assert "limited to a specific group" in rubric.narrowing(extract_rules.extract(group))
    assert any(f.startswith("Verify — Specific group") for f in rs[0].flags)
    assert rs[1].match_score > rs[0].match_score and rs[1].win_probability > rs[0].win_probability


def test_to_criteria_carries_restricted_groups_from_rules():
    from scholarmatch.agents.workers import to_criteria
    from scholarmatch.models import ExtractedCriteria
    rules = extract_rules.extract(sch("g", "Example Scholarship", "For children of employees of Acme Corp."))
    x = ExtractedCriteria(min_gpa=None, residency_states=[], localities=[], levels=[], class_years=[], majors=[],
                          need_based=None, pell_required=None, gender=None, ethnicities=[], disability_required=None,
                          first_generation_required=None, military_affiliation_required=None,
                          us_citizen_or_resident_required=None, num_awards=None, essay_count=None,
                          recommendation_letters=None, transcript=None, interview=None, fafsa=None,
                          portfolio_or_audition=None, other_requirements=[], soft_criteria=[], scam_signals=[],
                          evidence=[])
    c = to_criteria(x, rules)
    assert c.restricted_groups == ["children of employees of Acme Corp"]
    assert c.evidence["restricted_groups"] == rules.evidence["restricted_groups"]


# ----------------------------------------------------------------------------- WP4: locality, college, out-of-state
def _ex(text, **kw):
    return extract_rules.extract(sch("l", "Example Scholarship", text, **kw))


def test_locality_patterns_positive():
    cases = {
        "For students from Northampton or Accomack counties.": ["Northampton County", "Accomack County"],
        "Open to residents of Mecklenburg, Union and Gaston Counties.":
            ["Mecklenburg County", "Union County", "Gaston County"],
        "Open to residents of the counties of Northampton and Accomack.": ["Northampton County", "Accomack County"],
        "For students from Smyth County in the College of Liberal Arts and Human Sciences.": ["Smyth County"],
        "Smyth County residents are eligible.": ["Smyth County"],
        "For graduates of a Pitt County high school.": ["Pitt County"],
        "Must attend a high school in Mecklenburg County": ["Mecklenburg County"],
        "Resident of Mecklenburg, Union, Iredell Counties in NC or York County, SC":
            ["Mecklenburg County", "Union County", "Iredell County", "York County"],
        "Open to residents of the City of Chesapeake.": ["City of Chesapeake"],
        "Applicants must have graduated from the Chesapeake Public School system and be college seniors.":
            ["Chesapeake Public Schools"],
        "For CMS seniors.": ["Charlotte-Mecklenburg Schools"],
        "Every senior at West Charlotte High School enrolling at Central Piedmont Community College is eligible.":
            ["West Charlotte High School"],
        "For graduates of Radford High School or high schools in Montgomery County.":
            ["Montgomery County", "Radford High School"],
    }
    for text, locs in cases.items():
        assert _ex(text).localities == locs, (text, _ex(text).localities)


def test_locality_patterns_negative():
    for text in ("Preference given to residents of Roanoke County.",            # preference stays soft
                 "Priority is given to graduates of Radford High School.",
                 "Born in Smyth County, Margaret Skelton established this scholarship for students in CLAHS.",
                 "Born in Smyth County and a graduate of Marion High School, she created this fund for students.",
                 "He graduated from Marion High School in 1950.",
                 "The Skeltons were residents of Smyth County for forty years.",
                 "Administered by the Smyth County Community Foundation, 123 Main Street, Marion.",
                 "Residents of Fairfax County are not eligible.",
                 "Charlotte County Scholarship"):
        assert _ex(text).localities == [], (text, _ex(text).localities)
    c = _ex("Open to students majoring in nursing. Preference given to residents of Roanoke County.")
    assert c.localities == [] and any("Roanoke" in x for x in c.soft_criteria)


def test_locality_evidence_is_grounded():
    s = sch("l", "Watson Scholarship", "For students from Northampton or Accomack counties. Minimum GPA of 3.0.")
    c = extract_rules.extract(s)
    assert is_grounded(c.evidence["localities"], s.full_text())


def _check(s, name, **kw):
    from scholarmatch.matching import hard_filter
    from scholarmatch.models import StudentProfile
    c = extract_rules.extract(s)
    return next((h for h in hard_filter.check(s, c, StudentProfile(profile_id="x", **kw)) if h.name == name), None)


def test_locality_check():
    watson = sch("w", "Watson Scholarship", "For students from Northampton or Accomack counties.")
    assert _check(watson, "Locality", county="Accomack").result == "pass"          # the named county passes
    assert _check(watson, "Locality", county="Northampton County").result == "pass"
    assert _check(watson, "Locality", county="Henrico").result == "fail"
    assert _check(watson, "Locality").result == "unknown"                          # nothing given
    assert _check(watson, "Locality", city="Onley").result == "unknown"           # a city does not name a county
    smyth = sch("s", "Skelton Scholarship", "For students from Smyth County.")
    assert _check(smyth, "Locality", county="Smythe").result == "fail"             # whole words only
    assert _check(smyth, "Locality", county="Smyth").result == "pass"
    gilbert = sch("g", "Gilbert Scholarship", "Applicants must have graduated from the Chesapeake Public School system.")
    assert _check(gilbert, "Locality", city="Chesapeake").result == "pass"
    assert _check(gilbert, "Locality", county="Chesapeake").result == "pass"       # independent city as county
    assert _check(gilbert, "Locality", county="Henrico").result == "fail"
    assert _check(gilbert, "Locality", high_school="Hickory High School").result == "unknown"
    moss = sch("m", "Moss Scholarship", "For graduates of Radford High School or high schools in Montgomery County.")
    assert _check(moss, "Locality", high_school="Radford High School").result == "pass"
    assert _check(moss, "Locality", county="Montgomery").result == "pass"
    assert _check(moss, "Locality", county="Loudoun").result == "fail"
    fox = sch("f", "Fox Scholarship", "Every senior at West Charlotte High School is eligible.")
    assert _check(fox, "Locality", high_school="West Charlotte High").result == "pass"
    assert _check(fox, "Locality", high_school="Myers Park High School").result == "fail"
    assert _check(fox, "Locality", city="Charlotte", county="Mecklenburg").result == "unknown"   # may be theirs
    assert _check(fox, "Locality", county="Pitt").result == "fail"
    cms = sch("c", "CMS Scholarship", "For CMS seniors.")
    assert _check(cms, "Locality", city="Charlotte").result == "pass"
    hit = _check(watson, "Locality", county="Accomack", city="Onancock", high_school="Nandua High School")
    assert not any(x in hit.detail for x in ("Onancock", "Nandua"))               # never echo the student's location


def test_destination_college():
    fox = sch("f", "Anthony Fox Scholarship",
              "Every senior at West Charlotte High School enrolling at Central Piedmont Community College is eligible.")
    assert extract_rules.extract(fox).destination_schools == ["Central Piedmont Community College"]
    assert _check(fox, "College attending").result == "unknown"
    assert _check(fox, "College attending", intended_schools=["ecu"]).result == "fail"
    assert _check(fox, "College attending", intended_schools=["Central Piedmont"]).result == "pass"
    assert _check(fox, "College attending", intended_schools=["ecu", "CPCC"]).result == "pass"
    two = _ex("Open to students planning to attend East Carolina University or the University of South Carolina.")
    assert two.destination_schools == ["East Carolina University", "University of South Carolina"]
    s2 = sch("u", "X", "Open to students accepted to the University of South Carolina.")
    assert _check(s2, "College attending", intended_schools=["usc"]).result == "pass"
    for text in ("Why are you interested in attending Central Piedmont Community College?",
                 "Must be enrolled in the College of Nursing.",
                 "Students must be admitted to the College of Engineering.",
                 "Planning to enroll at a 4-year college or university."):
        assert _ex(text).destination_schools == [], text
    # on a school's own portal the Enrollment check covers the college
    aw = sch("a", "X", "For students attending Virginia Western Community College.", source="academicworks_vt",
             school="vt", geo_scope="va")
    assert extract_rules.extract(aw).destination_schools == []


def test_out_of_state():
    for text in ("Open to out-of-state students.", "Open to non-residents of Virginia.",
                 "For non-Virginia residents."):
        c = _ex(text)
        assert c.non_resident_required is True and c.residency_states == [], text
    for text in ("Both residents and non-residents may apply.", "Covers nonresident tuition for one year.",
                 "Out-of-state students are not eligible.", "Preference given to out-of-state students.",
                 "Students transferring from an out-of-state institution must reapply."):
        assert _ex(text).non_resident_required is None, text
    vt = dict(source="academicworks_vt", school="vt", geo_scope="va")
    only = sch("o", "X", "Open to out-of-state students.", **vt)
    assert _check(only, "Residency", state="VA").result == "fail"
    assert _check(only, "Residency", state="NC").result == "pass"
    thompson = sch("t", "O. C. Thompson Scholarship", "For out-of-state students or descendants of O. C. Thompson.", **vt)
    alt = _check(thompson, "Residency", state="VA")
    assert alt.result == "unknown" and alt.weight == "restrictive"
    assert _check(sch("n", "X", "Open to non-residents of Virginia."), "Residency", state="VA").result == "fail"
    assert _check(sch("n", "X", "Open to out-of-state students."), "Residency", state="VA").result == "unknown"


def test_wp4_fields_narrow_and_carry_over():
    from scholarmatch.agents.workers import to_criteria
    from scholarmatch.matching import rubric
    from scholarmatch.models import ExtractedCriteria
    fox = sch("f", "X", "Open to seniors enrolling at Central Piedmont Community College.")
    cf = extract_rules.extract(fox)
    assert rubric.pool_tier(fox, cf) == "school"
    out = sch("o", "X", "Open to out-of-state students.", source="academicworks_vt", school="vt", geo_scope="va")
    co = extract_rules.extract(out)
    assert "out-of-state students only" in rubric.narrowing(co)
    x = ExtractedCriteria(min_gpa=None, residency_states=[], localities=[], levels=[], class_years=[], majors=[],
                          need_based=None, pell_required=None, gender=None, ethnicities=[], disability_required=None,
                          first_generation_required=None, military_affiliation_required=None,
                          us_citizen_or_resident_required=None, num_awards=None, essay_count=None,
                          recommendation_letters=None, transcript=None, interview=None, fafsa=None,
                          portfolio_or_audition=None, other_requirements=[], soft_criteria=[], scam_signals=[],
                          evidence=[])
    c1, c2 = to_criteria(x, cf), to_criteria(x, co)
    assert c1.destination_schools == ["Central Piedmont Community College"]
    assert c1.evidence["destination_schools"] == cf.evidence["destination_schools"]
    assert c2.non_resident_required is True
    assert c2.evidence["non_resident_required"] == co.evidence["non_resident_required"]
