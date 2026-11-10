"""Shared fixtures. All scholarships below are SYNTHETIC test data, not real awards."""
import pytest

from scholarmatch import config
from scholarmatch.llm import LLM
from scholarmatch.models import (ClaimCheck, Essay, EssayThemes, ExtractedCriteria, FieldEvidence, FilterVerdict,
                                 FitEvidence, Scholarship, ScoreJudgement, StudentProfile, Verification)
from scholarmatch.scrapers.runner import ingest

AS_OF = "2026-11-10"


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    yield


def sch(sid, name, desc, **kw):
    base = dict(source="test", source_id=sid, name=name, description=desc, url=f"https://example.org/{sid}",
                deadline="2027-02-01", deadline_raw="2/1/2027", status="open", geo_scope="national",
                amount_raw="$1,000", amount_min=1000, amount_max=1000)
    base.update(kw)
    return Scholarship(**base)


CATALOG = [
    sch("svc", "Community Service Award",
        "Open to high school seniors who are residents of Virginia. Minimum GPA of 3.0 required. "
        "Applicants must demonstrate leadership and community service. One essay required. "
        "Five scholarships will be awarded.", geo_scope="va", amount_raw="$2,000", amount_min=2000, amount_max=2000),
    sch("gpa", "High Achievers Scholarship", "Applicants must have a minimum GPA of 3.9. Open to high school seniors."),
    sch("nc", "Tar Heel Residents Grant", "Applicants must be residents of North Carolina.", geo_scope="nc"),
    sch("old", "Expired Award", "Open to all students.", deadline="2026-01-15", status="ended"),
    sch("scam", "Easy Money Sweepstakes", "No essay! Winners are selected at random. A $5 processing fee applies."),
    sch("women", "Women in Engineering Scholarship",
        "Awarded to female students majoring in engineering. Preference given to residents of Roanoke County."),
    sch("4h", "4-H Alumni Scholarship", "Applicants must be current members of a 4-H club. Open to high school seniors."),
    sch("vt1", "Hokie Engineering Fund",
        "Supports Virginia Tech students in the College of Engineering who demonstrate financial need.",
        source="academicworks_vt", school="vt", geo_scope="va", amount_raw="Varies", amount_min=None, amount_max=None),
    sch("usc1", "Gamecock Fund", "For University of South Carolina students.",
        source="academicworks_usc", school="usc", geo_scope="sc"),
    sch("loan", "Teacher Forgivable Loan", "Loan forgiven for teaching service.", extra={"aid_type": "forgivable_loan"}),
    sch("open", "General Merit Scholarship",
        "Open to any student pursuing higher education. Two letters of recommendation and a transcript are required.",
        amount_raw="$500", amount_min=500, amount_max=500),
]


@pytest.fixture
def catalog():
    ingest(CATALOG)
    return CATALOG


@pytest.fixture
def profile():
    return StudentProfile(
        profile_id="t1", display_name="Jordan Testcase", state="VA", county="Montgomery", city="Blacksburg",
        level="hs_senior", gpa=3.6, intended_schools=["vt"], majors=["Mechanical Engineering"],
        gender="female", ethnicities=["Hispanic / Latino"], financial_need=True,
        activities=["Robotics team captain", "Food bank volunteer (120 hours)"], interests=["renewable energy"],
        essays=[Essay(title="Why I serve", text="...", themes=["community service", "leadership"])])


class FakeLLM(LLM):
    """Scripted agent outputs. `bad_scorer_rounds` makes the scorer hallucinate N times first."""
    name = "fake"

    def __init__(self, bad_scorer_rounds=0, ungrounded_drop=False, verifier_rejects=False):
        super().__init__()
        self.prompts = []
        self.bad_scorer_rounds = bad_scorer_rounds
        self.ungrounded_drop = ungrounded_drop
        self.verifier_rejects = verifier_rejects
        self.scorer_calls = {}

    def model(self, role):
        return "fake-1"

    def _structured(self, role, system, user, schema):
        self.prompts.append(system + "\n" + user)
        listing = user.split("<scholarship_listing>\n")[-1].split("\n</scholarship_listing>")[0]
        if schema is ExtractedCriteria:
            gpa = 3.0 if "Minimum GPA of 3.0" in listing else 3.9 if "minimum GPA of 3.9" in listing else None
            ev = [FieldEvidence(field="min_gpa", quote=f"inimum GPA of {gpa}")] if gpa else []
            out = ExtractedCriteria(
                min_gpa=gpa, residency_states=[], localities=[], levels=[], class_years=[], majors=[],
                need_based=None, pell_required=None, gender=None, ethnicities=[], disability_required=None,
                first_generation_required=None, military_affiliation_required=None,
                us_citizen_or_resident_required=None, num_awards=None,
                essay_count=1 if "essay required" in listing else None, recommendation_letters=None,
                transcript=None, interview=None, fafsa=None, portfolio_or_audition=None, other_requirements=[],
                soft_criteria=["leadership and community service"] if "leadership" in listing else [],
                scam_signals=[], evidence=ev)
        elif schema is FilterVerdict:
            if "4-H" in listing:
                quote = ("NOT IN THE LISTING" if self.ungrounded_drop
                         else "Applicants must be current members of a 4-H club")
                out = FilterVerdict(verdict="drop", reason="Requires 4-H membership, not in profile.",
                                    scholarship_quote=quote)
            else:
                out = FilterVerdict(verdict="keep", reason="Nothing excludes the student.", scholarship_quote="")
        elif schema is ScoreJudgement:
            n = self.scorer_calls.get(listing, 0)
            self.scorer_calls[listing] = n + 1
            if "leadership" in listing:
                bad = n < self.bad_scorer_rounds
                out = ScoreJudgement(
                    fit="strong",
                    fit_evidence=[
                        FitEvidence(criterion="leadership",
                                    scholarship_quote="Applicants must demonstrate leadership and community service",
                                    student_evidence="Student council president" if bad else "Robotics team captain",
                                    met=True),
                        FitEvidence(criterion="community service",
                                    scholarship_quote="demonstrate leadership and community service",
                                    student_evidence="Food bank volunteer (120 hours)", met=True)],
                    essay_reuse="partial", reusable_essay_title="Why I serve", strengths=["Leadership"], gaps=[],
                    summary="Good fit.")
            else:
                out = ScoreJudgement(fit="moderate", fit_evidence=[], essay_reuse="not_applicable",
                                     reusable_essay_title="", strengths=[], gaps=[], summary="Eligibility-based award.")
        elif schema is Verification:
            n = user.count('"index"')
            ok = not self.verifier_rejects
            out = Verification(checks=[ClaimCheck(index=i, supported=ok, issue="" if ok else "weak link") for i in range(n)],
                               fit_rating_justified=ok, suggested_fit="strong" if ok else "weak")
        elif schema is EssayThemes:
            out = EssayThemes(themes=["service"])
        else:
            raise AssertionError(schema)
        return out, 10, 5

    def _chat(self, role, system, messages, tools, run_tool):
        self.prompts.append(system)
        return "fake answer"
