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
RULES_VERSION = "rules-3"
PROMPT_VERSION = "p1"
RUBRIC_VERSION = "rubric-4"

# --- Pipeline knobs (all deterministic) --------------------------------------
MAX_LLM_CANDIDATES = int(os.environ.get("SM_MAX_LLM_CANDIDATES", 30))  # cost cap per run
MAX_RESULTS = int(os.environ.get("SM_MAX_RESULTS", 15))                # shown to student
MAX_SUPERVISOR_ROUNDS = 2      # worker retries before the supervisor fails safe
LLM_WORKERS = int(os.environ.get("SM_LLM_WORKERS", 6))
CHAT_KEEP_TURNS = 8            # recent turns kept verbatim; older ones are compacted

# --- LLM ---------------------------------------------------------------------
DEFAULT_MODEL = "claude-opus-5-5"
ROLES = ("extractor", "filter", "scorer", "verifier", "chat")
ROLE_EFFORT = {"extractor": "low", "filter": "low", "verifier": "low",
               "scorer": "medium", "chat": "medium"}


def model_for(role: str) -> str:
    return os.environ.get(f"SM_MODEL_{role.upper()}") or DEFAULT_MODEL


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
