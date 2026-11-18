"""Request and response models of the HTTP API (what the React front end sees). Stored models stay in `models.py`."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from ..models import Exclusion, MatchResult, StudentProfile

SavedStatus = Literal["interested", "applying", "submitted", "won", "not selected"]
JobState = Literal["queued", "running", "done", "error"]


class EssayOut(BaseModel):
    """An essay on file. The text stays on the server; the front end only needs the title and themes."""
    index: int
    title: str
    themes: list[str]
    chars: int


class ProfileOut(BaseModel):
    profile: StudentProfile = Field(description="The stored profile with essay text removed (see `essays`).")
    essays: list[EssayOut]


class ProfileIn(BaseModel):
    """Every editable profile field except essays, which have their own endpoints. Unset fields take the defaults
    of `StudentProfile`. `state` is a two-letter code; use "XX" for a state outside VA / NC / SC."""
    model_config = {"extra": "forbid"}

    display_name: str = ""
    state: str = Field("VA", pattern=r"^[A-Z]{2}$")
    county: str = ""
    city: str = ""
    high_school: str = ""
    level: Literal["hs_senior", "undergrad", "graduate"] = "hs_senior"
    class_year: str = ""
    gpa: Optional[float] = Field(None, ge=0, le=4.0)
    current_school: str = ""
    intended_schools: list[str] = Field(default_factory=list)
    majors: list[str] = Field(default_factory=list)
    pell_eligible: Optional[bool] = None
    financial_need: Optional[bool] = None
    us_citizen_or_resident: Optional[bool] = True
    gender: str = ""
    ethnicities: list[str] = Field(default_factory=list)
    disability: Optional[bool] = None
    first_generation: Optional[bool] = None
    military_affiliation: Optional[bool] = None
    affiliations: list[str] = Field(default_factory=list)
    no_special_affiliations: bool = False
    age: Optional[int] = Field(None, ge=1, le=120)
    activities: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    awards_won: list[str] = Field(default_factory=list)
    no_essay_only: bool = False
    min_award: float = Field(0, ge=0)
    max_effort_hours: Optional[float] = Field(None, ge=0)


class EssayUploadOut(ProfileOut):
    warnings: list[str]


class Health(BaseModel):
    llm_available: bool
    llm_name: str
    catalog_total: int
    catalog_updated: Optional[str]


class Meta(BaseModel):
    """Option lists for the front end's forms, so it never hard-codes them."""
    levels: dict[str, str]
    schools: dict[str, str]
    sorts: list[str]
    saved_statuses: list[str]
    max_results: int


class RunRequest(BaseModel):
    force: bool = Field(False, description="Ignore the stored result for today and run again.")


class Job(BaseModel):
    job_id: str
    profile_id: str
    state: JobState
    message: str = ""
    fraction: float = 0
    run_id: Optional[str] = None
    error: Optional[str] = None


class RunView(BaseModel):
    run_id: str
    profile_id: str
    as_of: str
    mode: Literal["llm", "heuristic"]
    review_mode: str = Field(description="Plain-language label such as \"AI-reviewed (12 of 15)\".")
    stale: bool = Field(description="The profile, catalog or settings changed since this run.")
    created_at: str
    stats: dict
    total_results: int
    results: list[MatchResult] = Field(description="Sorted and filtered; essay titles shown as the student named them.")
    exclusion_counts: dict[str, int]
    exclusions: list[Exclusion]


class SavedIn(BaseModel):
    status: SavedStatus = "interested"
    note: str = ""


class SavedOut(BaseModel):
    key: str
    status: str
    note: str
    saved_at: Optional[str]
    snapshot: dict
    ended: bool = Field(description="The award has closed since it was saved.")


class ChatTurn(BaseModel):
    id: int
    role: str
    content: str = Field(description="Essay titles shown as the student named them.")


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ChatOut(BaseModel):
    answer: str
