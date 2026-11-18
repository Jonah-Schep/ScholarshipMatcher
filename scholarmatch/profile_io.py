"""Profile input helpers shared by the Streamlit app and the HTTP API: parsing free-text lists and reading essays.

No UI code here, so both front ends behave the same and tests can import it."""
from __future__ import annotations

import io
import re

from .agents import workers
from .llm import LLM
from .models import Essay, StudentProfile

ESSAY_SUFFIXES = (".txt", ".pdf")


class EssayReadError(ValueError):
    """An uploaded essay could not be turned into text."""


def split_lines(text: str) -> list[str]:
    """One item per line (or per ';'), blanks dropped. Used for activities, interests, awards, affiliations."""
    return [x.strip() for x in re.split(r"[\n;]+", text or "") if x.strip()]


def read_essay_file(name: str, data: bytes) -> str:
    """Text of an uploaded .txt or .pdf essay. Raises EssayReadError for other types or an unreadable PDF."""
    lower = name.lower()
    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages).strip()
        except Exception as e:                                     # pypdf raises many types for broken files
            raise EssayReadError(f"Could not read {name}: {e}") from e
    if lower.endswith(".txt"):
        return data.decode("utf-8", errors="replace").strip()
    raise EssayReadError(f"{name}: only .txt and .pdf essays are supported.")


def add_essays(llm: LLM, p: StudentProfile, files: list[tuple[str, bytes]]) -> tuple[StudentProfile, list[str]]:
    """Return a copy of `p` with the uploaded essays added, plus plain-language warnings.

    Each new essay's themes come from `workers.essay_themes`, the one call that sends essay text to a provider; it
    removes the student's name, county, city and high school first and never sends the title. A file whose title is
    already on the profile, or that has no text, is skipped with a warning. A themes failure keeps the essay without
    themes."""
    essays = list(p.essays)
    warnings: list[str] = []
    for name, data in files:
        try:
            text = read_essay_file(name, data)
        except EssayReadError as e:
            warnings.append(str(e))
            continue
        if not text:
            warnings.append(f"{name} has no readable text; it was not added.")
            continue
        if name in [e.title for e in essays]:
            warnings.append(f"An essay called {name} is already on file; it was not added again.")
            continue
        try:
            themes = workers.essay_themes(llm, name, text, (p.display_name,), (p.county, p.city, p.high_school))
        except Exception as e:                                     # a provider outage must not lose the upload
            themes = []
            warnings.append(f"Couldn't summarise {name} ({e}); saved without themes.")
        essays.append(Essay(title=name, text=text, themes=themes))
    return p.model_copy(update={"essays": essays}), warnings
