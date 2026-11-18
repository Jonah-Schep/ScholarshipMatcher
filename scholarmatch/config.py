"""Central configuration. Everything tunable lives here so runs are reproducible."""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SM_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("SM_DB_PATH", DATA_DIR / "scholarmatch.db"))
CACHE_DIR = DATA_DIR / "http_cache"      # raw HTML / JSON from scrapers
EXPORT_DIR = DATA_DIR / "export"         # per-source JSONL + combined CSV

# --- Scope -------------------------------------------------------------------
STATES_IN_SCOPE = ("VA", "NC", "SC")
SCHOOLS = {  # AcademicWorks portals we scrape -> display name, state
    "vt": ("Virginia Tech", "VA"),
    "usc": ("University of South Carolina", "SC"),
    "ecu": ("East Carolina University", "NC"),
}

# --- Versions: bump when prompts / rules change so caches invalidate cleanly ---
RULES_VERSION = "rules-7"
PROMPT_VERSION = "p5"
RUBRIC_VERSION = "rubric-6"
# Supervisor acceptance checks (quote length, criterion relevance, the held-out drop check). They change which agent
# answers are accepted, so they are part of the run fingerprint too.
CHECKS_VERSION = "checks-2"

# --- Pipeline knobs (all deterministic) --------------------------------------
MAX_LLM_CANDIDATES = int(os.environ.get("SM_MAX_LLM_CANDIDATES", 30))  # cost cap per run
MAX_RESULTS = int(os.environ.get("SM_MAX_RESULTS", 15))                # shown to student
MAX_SUPERVISOR_ROUNDS = 2      # worker retries before the supervisor fails safe
LLM_WORKERS = int(os.environ.get("SM_LLM_WORKERS", 6))
# Provider calls in flight at once across ALL runs in this process (two users matching at the same time share it).
# VT ARC answers "429 concurrent session limit … max 10 in flight per user" above 10 (WP8, 2026-11-14).
LLM_MAX_INFLIGHT = int(os.environ.get("SM_LLM_MAX_INFLIGHT", 6))
# Scorer evidence quotes and Filter "drop" quotes must be at least this long: an 8-character span such as
# "students" matches almost any listing and proves nothing.
MIN_EVIDENCE_QUOTE_CHARS = 20
CHAT_KEEP_TURNS = 8            # recent turns kept verbatim; older ones are compacted

# --- LLM ---------------------------------------------------------------------
DEFAULT_MODEL = "claude-opus-5-5"
ROLES = ("extractor", "filter", "scorer", "verifier", "chat")
# The Verifier checks the Scorer, so by default it runs on a different model: a checker on the same model shares the
# author's blind spots (correlated errors). Two Claude models are still one family; a different provider for the
# Verifier would need a per-role provider setting (not built). Applies to the Anthropic provider; the
# OpenAI-compatible provider has its own default (SM_OPENAI_MODEL). SM_MODEL_<ROLE> overrides either.
ROLE_DEFAULT_MODELS = {"verifier": "claude-sonnet-5-5"}
ROLE_EFFORT = {"extractor": "low", "filter": "low", "verifier": "low",
               "scorer": "medium", "chat": "medium"}


def model_for(role: str) -> str:
    return os.environ.get(f"SM_MODEL_{role.upper()}") or ROLE_DEFAULT_MODELS.get(role, DEFAULT_MODEL)


def llm_provider() -> str:
    explicit = (os.environ.get("SM_LLM_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if os.environ.get("SM_OPENAI_BASE_URL"):
        return "openai_compat"
    return "offline"


# --- Scraping politeness -----------------------------------------------------
REQUEST_DELAY_SECONDS = 2.0
HOST_DELAYS = {"www.ncseaa.edu": 10.0}   # robots.txt Crawl-delay: 10
MAX_RETRIES = 3


def user_agent() -> str:
    email = os.environ.get("SCRAPER_CONTACT_EMAIL", "").strip()
    if not email:
        raise RuntimeError(
            "Set SCRAPER_CONTACT_EMAIL (in .env) before scraping: the brief requires a "
            "descriptive User-Agent with contact info."
        )
    return f"ScholarshipResearchBot/0.1 (student course project; contact: {email})"
