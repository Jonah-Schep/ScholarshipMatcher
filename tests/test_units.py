from pathlib import Path

from scholarmatch.ingest import extract_rules, scam
from scholarmatch.scrapers import academicworks as aw
from scholarmatch.scrapers import gsites, state_aid
from scholarmatch.scrapers.pending import classify_geo_scope
from scholarmatch.textutil import is_grounded, parse_amounts, parse_date, redact_pii
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
