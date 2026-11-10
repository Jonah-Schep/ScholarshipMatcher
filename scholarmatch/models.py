"""Pydantic data contracts shared by scrapers, agents, the pipeline and the UI.

Design rule: LLM agents only ever emit *categorical judgements + verbatim evidence*.
Every number the student sees (odds, effort, expected value) is computed by code
in matching/rubric.py from these contracts.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

GeoScope = Literal["national", "nc", "va", "sc", "multi_state", "other_state"]
Level = Literal["hs_senior", "undergrad", "graduate"]
Tri = Literal["pass", "fail", "unknown"]


# ----------------------------------------------------------------------------- catalog
class Scholarship(BaseModel):
    """Shared output schema from scraper-brief.md (one row per award)."""
    source: str
    source_id: str
    name: str
    url: str = ""
    school: Optional[str] = None            # vt | usc | ecu for AcademicWorks rows
    region: Optional[str] = None            # e.g. "charlotte"
    geo_scope: GeoScope = "national"
    sponsor: Optional[str] = None
    amount_raw: str = ""
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    deadline_raw: str = ""
    deadline: Optional[str] = None          # ISO date
    open_date: Optional[str] = None
    status: Literal["open", "ended", "unknown"] = "unknown"
    description: str = ""
    eligibility: str = ""
    college_or_department: Optional[str] = None
    sources: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "high"
    raw_text: str = ""
    extra: dict = Field(default_factory=dict)
    scraped_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    def full_text(self) -> str:
        """The only text agents may quote from (grounding source)."""
        parts = [self.name, self.description, self.eligibility]
        q = self.extra.get("supplemental_questions")
        if q:
            parts.append("Supplemental questions: " + " | ".join(q))
        if not (self.description or self.eligibility):
            parts.append(self.raw_text)
        return "\n".join(p for p in parts if p).strip()


class Requirements(BaseModel):
    essay_count: Optional[int] = None       # None = not stated
    recommendation_letters: Optional[int] = None
    transcript: Optional[bool] = None
    interview: Optional[bool] = None
    fafsa: Optional[bool] = None
    portfolio_or_audition: Optional[bool] = None
    other: list[str] = Field(default_factory=list)


class Criteria(BaseModel):
    """Structured eligibility. Empty list / None always means 'not restricted or not stated'."""
    min_gpa: Optional[float] = None
    residency_states: list[str] = Field(default_factory=list)   # 2-letter codes
    localities: list[str] = Field(default_factory=list)         # counties / cities / high schools
    levels: list[Level] = Field(default_factory=list)
    class_years: list[str] = Field(default_factory=list)        # freshman, sophomore, junior, senior
    majors: list[str] = Field(default_factory=list)             # fields of study
    need_based: Optional[bool] = None
    pell_required: Optional[bool] = None
    gender: Optional[Literal["female", "male"]] = None
    ethnicities: list[str] = Field(default_factory=list)
    disability_required: Optional[bool] = None
    first_generation_required: Optional[bool] = None
    military_affiliation_required: Optional[bool] = None
    us_citizen_or_resident_required: Optional[bool] = None
    num_awards: Optional[int] = None
    application_method: Literal["direct", "general_application", "automatic", "unknown"] = "unknown"
    requirements: Requirements = Field(default_factory=Requirements)
    soft_criteria: list[str] = Field(default_factory=list)      # leadership, service, interests ...
    scam_signals: list[str] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)      # field -> verbatim quote
    extracted_by: Literal["rules", "llm+rules"] = "rules"
    notes: list[str] = Field(default_factory=list)              # supervisor / validation notes


# ----------------------------------------------------------------------------- student
class Essay(BaseModel):
    title: str
    text: str
    themes: list[str] = Field(default_factory=list)   # derived once; only themes go to match agents


class StudentProfile(BaseModel):
    profile_id: str                       # local handle, e.g. "jonah"
    display_name: str = ""
    # hard-filter facts
    state: str = "VA"                     # state of residence (2 letters)
    county: str = ""
    city: str = ""
    high_school: str = ""
    level: Level = "hs_senior"
    class_year: str = ""                  # for undergrads
    gpa: Optional[float] = None           # unweighted 4.0 scale
    current_school: str = ""              # vt | usc | ecu | other text
    intended_schools: list[str] = Field(default_factory=list)
    majors: list[str] = Field(default_factory=list)
    pell_eligible: Optional[bool] = None
    financial_need: Optional[bool] = None
    us_citizen_or_resident: Optional[bool] = True
    # optional demographics: used ONLY by the local deterministic filter, never sent to an LLM
    gender: str = ""
    ethnicities: list[str] = Field(default_factory=list)
    disability: Optional[bool] = None
    first_generation: Optional[bool] = None
    military_affiliation: Optional[bool] = None
    # soft facts (sent to agents, after redaction)
    activities: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    awards_won: list[str] = Field(default_factory=list)
    essays: list[Essay] = Field(default_factory=list)
    # preferences
    no_essay_only: bool = False
    min_award: float = 0
    max_effort_hours: Optional[float] = None

    def redacted_view(self) -> dict:
        """Minimum-necessary profile for LLM agents: no name, contact, location below state,
        or protected demographics (those are matched locally in hard_filter)."""
        return {
            "level": self.level, "class_year": self.class_year, "state": self.state,
            "majors": self.majors, "activities": self.activities, "interests": self.interests,
            "awards_won": self.awards_won,
            "essay_themes": [{"title": e.title, "themes": e.themes} for e in self.essays],
        }


# ----------------------------------------------------------------------------- agent outputs
class FieldEvidence(BaseModel):
    field: str
    quote: str          # verbatim from the scholarship text


class ExtractedCriteria(BaseModel):
    """Extractor agent output. Mirrors Criteria but flat + evidence list (schema-friendly)."""
    min_gpa: Optional[float]
    residency_states: list[str]
    localities: list[str]
    levels: list[Level]
    class_years: list[str]
    majors: list[str]
    need_based: Optional[bool]
    pell_required: Optional[bool]
    gender: Optional[Literal["female", "male"]]
    ethnicities: list[str]
    disability_required: Optional[bool]
    first_generation_required: Optional[bool]
    military_affiliation_required: Optional[bool]
    us_citizen_or_resident_required: Optional[bool]
    num_awards: Optional[int]
    essay_count: Optional[int]
    recommendation_letters: Optional[int]
    transcript: Optional[bool]
    interview: Optional[bool]
    fafsa: Optional[bool]
    portfolio_or_audition: Optional[bool]
    other_requirements: list[str]
    soft_criteria: list[str]
    scam_signals: list[str]
    evidence: list[FieldEvidence]


class FilterVerdict(BaseModel):
    """Fuzzy-filter agent output for ONE scholarship."""
    verdict: Literal["keep", "drop", "uncertain"]
    reason: str
    scholarship_quote: str      # verbatim; required for 'drop'


class FitEvidence(BaseModel):
    criterion: str              # what the scholarship is looking for
    scholarship_quote: str      # verbatim from scholarship text
    student_evidence: str       # verbatim item from the student's profile, or "" if a gap
    met: bool


class ScoreJudgement(BaseModel):
    """Scorer agent output for ONE scholarship: categorical only, no numbers."""
    fit: Literal["strong", "moderate", "weak", "none"]
    fit_evidence: list[FitEvidence]
    essay_reuse: Literal["full", "partial", "none", "not_applicable"]
    reusable_essay_title: str
    strengths: list[str]
    gaps: list[str]
    summary: str


class ClaimCheck(BaseModel):
    index: int
    supported: bool
    issue: str


class Verification(BaseModel):
    """Verifier (chain-of-verification) output."""
    checks: list[ClaimCheck]
    fit_rating_justified: bool
    suggested_fit: Literal["strong", "moderate", "weak", "none"]


class EssayThemes(BaseModel):
    themes: list[str]


# ----------------------------------------------------------------------------- results
class HardCheck(BaseModel):
    name: str
    result: Tri
    detail: str


class RubricLine(BaseModel):
    dimension: str
    rating: str
    points: float
    max_points: float
    why: str


class MatchResult(BaseModel):
    scholarship_key: str
    name: str
    url: str
    source: str
    sponsor: str = ""
    amount_display: str = ""
    amount_value: Optional[float] = None
    deadline: Optional[str] = None
    open_date: Optional[str] = None
    hard_checks: list[HardCheck] = Field(default_factory=list)
    filter_verdict: Optional[FilterVerdict] = None
    judgement: Optional[ScoreJudgement] = None
    rubric: list[RubricLine] = Field(default_factory=list)
    match_score: float = 0            # 0-100
    win_probability: float = 0        # heuristic estimate, 0-1
    effort_hours: float = 0
    ease: float = 0                   # 0-100, higher = easier
    expected_value: Optional[float] = None
    requirements: Requirements = Field(default_factory=Requirements)
    application_method: str = "unknown"
    flags: list[str] = Field(default_factory=list)   # e.g. "verify eligibility: GPA not stated"
    mode: Literal["llm", "heuristic"] = "llm"


class Exclusion(BaseModel):
    scholarship_key: str
    name: str
    stage: Literal["sql", "hard_filter", "fuzzy_filter", "preference", "cap"]
    reason: str


class MatchRun(BaseModel):
    run_id: str
    profile_id: str
    as_of: str                        # date the run is evaluated against
    fingerprint: str                  # hash(profile, catalog, versions, as_of)
    mode: Literal["llm", "heuristic"]
    results: list[MatchResult]
    exclusions: list[Exclusion]
    stats: dict
    created_at: str
