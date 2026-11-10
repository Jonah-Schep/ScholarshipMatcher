"""SQLite persistence. One short-lived connection per operation (safe across Streamlit threads)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

from . import config
from .models import Criteria, MatchRun, Scholarship, StudentProfile
from .textutil import now_iso, sha

SCHEMA = """
CREATE TABLE IF NOT EXISTS scholarships (
    key TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL, name TEXT NOT NULL,
    school TEXT, geo_scope TEXT, status TEXT, deadline TEXT, amount_min REAL, amount_max REAL,
    aid_type TEXT DEFAULT 'scholarship',
    scam_level TEXT DEFAULT 'ok', scam_reasons TEXT DEFAULT '[]',
    content_hash TEXT NOT NULL, data TEXT NOT NULL, first_seen TEXT, last_seen TEXT);
CREATE INDEX IF NOT EXISTS ix_sch_filter ON scholarships(status, deadline, geo_scope, school);
CREATE TABLE IF NOT EXISTS criteria (
    key TEXT NOT NULL, kind TEXT NOT NULL,          -- 'rules' | 'llm'
    content_hash TEXT NOT NULL, version TEXT NOT NULL, min_gpa REAL, data TEXT NOT NULL,
    PRIMARY KEY (key, kind));
CREATE TABLE IF NOT EXISTS profiles (profile_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS saved (
    profile_id TEXT NOT NULL, key TEXT NOT NULL, status TEXT DEFAULT 'interested', note TEXT DEFAULT '',
    snapshot TEXT, saved_at TEXT, PRIMARY KEY (profile_id, key));
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key TEXT PRIMARY KEY, role TEXT, model TEXT, response TEXT NOT NULL,
    input_tokens INTEGER, output_tokens INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS match_runs (
    run_id TEXT PRIMARY KEY, profile_id TEXT, fingerprint TEXT, data TEXT NOT NULL, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_runs ON match_runs(profile_id, fingerprint);
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id TEXT, key TEXT, role TEXT, content TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS chat_summary (
    profile_id TEXT, key TEXT, summary TEXT, upto_id INTEGER, PRIMARY KEY (profile_id, key));
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, agent TEXT, event TEXT, detail TEXT);
"""

_initialised: set[str] = set()


@contextmanager
def connect(path: Optional[Path] = None):
    path = Path(path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    if str(path) not in _initialised:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        _initialised.add(str(path))
    try:
        yield con
        con.commit()
    finally:
        con.close()


# ----------------------------------------------------------------------------- scholarships
def content_hash(s: Scholarship) -> str:
    """Hash of the fields that matter for eligibility; drives extraction cache invalidation."""
    return sha(s.name, s.description, s.eligibility, s.amount_raw, s.deadline_raw,
               s.extra.get("supplemental_questions", []), s.raw_text)[:32]


def upsert_scholarships(rows: Iterable[Scholarship], scam: dict[str, tuple[str, list[str]]] | None = None) -> int:
    """Insert/update. Rows are never deleted: ended awards stay for history (R11)."""
    n = 0
    now = now_iso()
    with connect() as con:
        for s in rows:
            level, reasons = (scam or {}).get(s.key, ("ok", []))
            con.execute(
                """INSERT INTO scholarships (key, source, source_id, name, school, geo_scope, status, deadline,
                       amount_min, amount_max, aid_type, scam_level, scam_reasons, content_hash, data, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET name=excluded.name, school=excluded.school,
                       geo_scope=excluded.geo_scope, status=excluded.status, deadline=excluded.deadline,
                       amount_min=excluded.amount_min, amount_max=excluded.amount_max, aid_type=excluded.aid_type,
                       scam_level=excluded.scam_level, scam_reasons=excluded.scam_reasons,
                       content_hash=excluded.content_hash, data=excluded.data, last_seen=excluded.last_seen""",
                (s.key, s.source, s.source_id, s.name, s.school, s.geo_scope, s.status, s.deadline,
                 s.amount_min, s.amount_max, s.extra.get("aid_type", "scholarship"), level,
                 json.dumps(reasons), content_hash(s), s.model_dump_json(), now, now))
            n += 1
    return n


def get_scholarship(key: str) -> Optional[Scholarship]:
    with connect() as con:
        r = con.execute("SELECT data FROM scholarships WHERE key=?", (key,)).fetchone()
    return Scholarship.model_validate_json(r["data"]) if r else None


def all_scholarships() -> list[Scholarship]:
    with connect() as con:
        rows = con.execute("SELECT data FROM scholarships ORDER BY key").fetchall()
    return [Scholarship.model_validate_json(r["data"]) for r in rows]


def catalog_stats() -> dict:
    with connect() as con:
        by_source = {r[0]: r[1] for r in con.execute("SELECT source, COUNT(*) FROM scholarships GROUP BY source")}
        by_status = {r[0]: r[1] for r in con.execute("SELECT status, COUNT(*) FROM scholarships GROUP BY status")}
        scam = {r[0]: r[1] for r in con.execute("SELECT scam_level, COUNT(*) FROM scholarships GROUP BY scam_level")}
        llm = con.execute("SELECT COUNT(*) FROM criteria WHERE kind='llm'").fetchone()[0]
        last = con.execute("SELECT MAX(last_seen) FROM scholarships").fetchone()[0]
    return {"total": sum(by_source.values()), "by_source": by_source, "by_status": by_status,
            "scam": scam, "llm_extracted": llm, "last_scraped": last}


def catalog_version() -> str:
    """Changes whenever any scholarship's content changes -> part of the run fingerprint."""
    with connect() as con:
        rows = con.execute("SELECT key, content_hash, status, deadline, scam_level FROM scholarships ORDER BY key").fetchall()
    return sha([tuple(r) for r in rows])[:16]


# ----------------------------------------------------------------------------- criteria
def put_criteria(key: str, kind: str, chash: str, version: str, c: Criteria) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO criteria (key, kind, content_hash, version, min_gpa, data) VALUES (?,?,?,?,?,?)",
                    (key, kind, chash, version, c.min_gpa, c.model_dump_json()))


def get_criteria(key: str, kind: str, chash: str, version: str) -> Optional[Criteria]:
    with connect() as con:
        r = con.execute("SELECT data FROM criteria WHERE key=? AND kind=? AND content_hash=? AND version=?",
                        (key, kind, chash, version)).fetchone()
    return Criteria.model_validate_json(r["data"]) if r else None


# ----------------------------------------------------------------------------- profiles / saved
def save_profile(p: StudentProfile) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO profiles VALUES (?,?,?)", (p.profile_id, p.model_dump_json(), now_iso()))


def load_profile(profile_id: str) -> Optional[StudentProfile]:
    with connect() as con:
        r = con.execute("SELECT data FROM profiles WHERE profile_id=?", (profile_id,)).fetchone()
    return StudentProfile.model_validate_json(r["data"]) if r else None


def list_profiles() -> list[str]:
    with connect() as con:
        return [r[0] for r in con.execute("SELECT profile_id FROM profiles ORDER BY profile_id")]


def delete_profile(profile_id: str) -> None:
    """Student-initiated erase of everything stored about them."""
    with connect() as con:
        for t in ("profiles", "saved", "chat", "chat_summary", "match_runs"):
            con.execute(f"DELETE FROM {t} WHERE profile_id=?", (profile_id,))


def save_scholarship(profile_id: str, key: str, snapshot: dict, status: str = "interested", note: str = "") -> None:
    with connect() as con:
        con.execute("""INSERT INTO saved VALUES (?,?,?,?,?,?)
                       ON CONFLICT(profile_id, key) DO UPDATE SET status=excluded.status, note=excluded.note,
                       snapshot=excluded.snapshot""",
                    (profile_id, key, status, note, json.dumps(snapshot), now_iso()))


def unsave_scholarship(profile_id: str, key: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM saved WHERE profile_id=? AND key=?", (profile_id, key))


def list_saved(profile_id: str) -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT * FROM saved WHERE profile_id=? ORDER BY saved_at", (profile_id,)).fetchall()
    return [dict(r) | {"snapshot": json.loads(r["snapshot"] or "{}")} for r in rows]


# ----------------------------------------------------------------------------- llm cache / runs / audit
def cache_get(cache_key: str) -> Optional[str]:
    with connect() as con:
        r = con.execute("SELECT response FROM llm_cache WHERE cache_key=?", (cache_key,)).fetchone()
    return r["response"] if r else None


def cache_put(cache_key: str, role: str, model: str, response: str, tin: int = 0, tout: int = 0) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?,?)",
                    (cache_key, role, model, response, tin, tout, now_iso()))


def save_run(run: MatchRun) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO match_runs VALUES (?,?,?,?,?)",
                    (run.run_id, run.profile_id, run.fingerprint, run.model_dump_json(), run.created_at))


def find_run(profile_id: str, fingerprint: str) -> Optional[MatchRun]:
    with connect() as con:
        r = con.execute("SELECT data FROM match_runs WHERE profile_id=? AND fingerprint=? ORDER BY created_at DESC LIMIT 1",
                        (profile_id, fingerprint)).fetchone()
    return MatchRun.model_validate_json(r["data"]) if r else None


def latest_run(profile_id: str) -> Optional[MatchRun]:
    with connect() as con:
        r = con.execute("SELECT data FROM match_runs WHERE profile_id=? ORDER BY created_at DESC LIMIT 1",
                        (profile_id,)).fetchone()
    return MatchRun.model_validate_json(r["data"]) if r else None


def audit(run_id: str, agent: str, event: str, detail: dict | str = "") -> None:
    with connect() as con:
        con.execute("INSERT INTO audit_log (ts, run_id, agent, event, detail) VALUES (?,?,?,?,?)",
                    (now_iso(), run_id, agent, event, detail if isinstance(detail, str) else json.dumps(detail, default=str)))


def audit_for(run_id: str) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT ts, agent, event, detail FROM audit_log WHERE run_id=? ORDER BY id", (run_id,))]


# ----------------------------------------------------------------------------- chat
def chat_add(profile_id: str, key: str, role: str, content: str) -> None:
    with connect() as con:
        con.execute("INSERT INTO chat (profile_id, key, role, content, created_at) VALUES (?,?,?,?,?)",
                    (profile_id, key, role, content, now_iso()))


def chat_history(profile_id: str, key: str) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, role, content FROM chat WHERE profile_id=? AND key=? ORDER BY id", (profile_id, key))]


def chat_summary_get(profile_id: str, key: str) -> tuple[str, int]:
    with connect() as con:
        r = con.execute("SELECT summary, upto_id FROM chat_summary WHERE profile_id=? AND key=?", (profile_id, key)).fetchone()
    return (r["summary"], r["upto_id"]) if r else ("", 0)


def chat_summary_put(profile_id: str, key: str, summary: str, upto_id: int) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO chat_summary VALUES (?,?,?,?)", (profile_id, key, summary, upto_id))
