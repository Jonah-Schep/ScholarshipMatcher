"""ScholarMatch HTTP API (FastAPI): the same operations as the Streamlit app, as JSON endpoints for a custom front end.

Run:  python -m scholarmatch.api        (http://127.0.0.1:8000, docs at /docs, schema at /openapi.json)

The API serves a student's own profile back to them, including name and location, so it binds to 127.0.0.1 by
default and has no authentication: do not expose it on a network as it stands. What the *agents* see is unchanged
(`StudentProfile.redacted_view()`, enforced in the Supervisor and workers)."""
from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager
from functools import cache
from typing import Annotated, Callable, Optional

from fastapi import FastAPI, File, HTTPException, Path, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from .. import config, db
from ..agents.chat import ChatAgent
from ..agents.supervisor import Supervisor
from ..llm import LLM, get_llm
from ..matching import rubric
from ..models import MatchResult, MatchRun, Scholarship, StudentProfile
from ..profile_io import ESSAY_SUFFIXES, add_essays
from ..run_summary import exclusion_counts, exclusions_for_display, for_display, needs_verification, review_mode_label
from ..textutil import today_iso
from .jobs import JobRunner
from .schemas import (ChatIn, ChatOut, ChatTurn, EssayOut, EssayUploadOut, Health, Job, Meta, ProfileIn, ProfileOut,
                      RunRequest, RunView, SavedIn, SavedOut)

LEVELS = {"hs_senior": "High school senior / incoming freshman", "undergrad": "Current college student",
          "graduate": "Graduate student"}
SAVED_STATUSES = ["interested", "applying", "submitted", "won", "not selected"]
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
PROFILE_ID = r"^[a-z0-9_-]{1,40}$"

ProfileId = Annotated[str, Path(pattern=PROFILE_ID, description="Lower-case letters, digits, '_' or '-'.")]


def cors_origins() -> list[str]:
    raw = os.environ.get("SM_API_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app(llm: Optional[LLM] = None, as_of: Optional[Callable[[], str]] = None) -> FastAPI:
    """`llm` defaults to the provider configured in `.env`, resolved on first use. `as_of` overrides "today" (tests)."""

    @cache
    def get_model() -> LLM:
        return llm or get_llm()

    today = as_of or today_iso
    runner = JobRunner(lambda: Supervisor(get_model()))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        runner.shutdown()

    app = FastAPI(title="ScholarMatch API", version="1.0", lifespan=lifespan,
                  description="Scholarship matching for VA / NC / SC students. Local use only (no authentication).")
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins(), allow_methods=["*"], allow_headers=["*"])
    app.state.runner = runner

    # ------------------------------------------------------------------ helpers
    def load(pid: str) -> StudentProfile:
        p = db.load_profile(pid)
        if not p:
            raise HTTPException(404, f"No profile called {pid!r}.")
        return p

    def latest(pid: str) -> MatchRun:
        run = db.latest_run(pid)
        if not run:
            raise HTTPException(404, "No match run yet for this profile. Start one with POST …/runs.")
        return run

    def profile_out(p: StudentProfile) -> ProfileOut:
        return ProfileOut(
            profile=p.model_copy(update={"essays": [e.model_copy(update={"text": ""}) for e in p.essays]}),
            essays=[EssayOut(index=i, title=e.title, themes=e.themes, chars=len(e.text)) for i, e in enumerate(p.essays)])

    # ------------------------------------------------------------------ app status
    @app.get("/api/health", response_model=Health, tags=["status"])
    def health():
        stats = db.catalog_stats()
        m = get_model()
        return Health(llm_available=m.available, llm_name=m.name, catalog_total=stats["total"],
                      catalog_updated=stats["last_scraped"])

    @app.get("/api/meta", response_model=Meta, tags=["status"])
    def meta():
        return Meta(levels=LEVELS, schools={k: v[0] for k, v in config.SCHOOLS.items()}, sorts=list(rubric.SORTS),
                    saved_statuses=SAVED_STATUSES, max_results=config.MAX_RESULTS)

    @app.get("/api/catalog/stats", tags=["status"])
    def catalog_stats() -> dict:
        return db.catalog_stats()

    # ------------------------------------------------------------------ profiles
    @app.get("/api/profiles", tags=["profiles"])
    def list_profiles() -> list[str]:
        return db.list_profiles()

    @app.get("/api/profiles/{pid}", response_model=ProfileOut, tags=["profiles"])
    def get_profile(pid: ProfileId):
        return profile_out(load(pid))

    @app.put("/api/profiles/{pid}", response_model=ProfileOut, tags=["profiles"])
    def put_profile(pid: ProfileId, body: ProfileIn):
        """Create or replace a profile. Essays already on file are kept (manage them with the essay endpoints)."""
        old = db.load_profile(pid)
        p = StudentProfile(profile_id=pid, essays=old.essays if old else [], **body.model_dump())
        db.save_profile(p)
        return profile_out(p)

    @app.delete("/api/profiles/{pid}", status_code=204, tags=["profiles"])
    def delete_profile(pid: ProfileId):
        """Erase the profile, saved scholarships, match history and chats from this computer."""
        load(pid)
        if runner.active(pid):
            raise HTTPException(409, "A match run for this profile is still going; delete it when the run finishes.")
        db.delete_profile(pid)

    @app.post("/api/profiles/{pid}/essays", response_model=EssayUploadOut, tags=["profiles"])
    def upload_essays(pid: ProfileId, files: list[UploadFile] = File(...)):
        """Add .txt / .pdf essays. Each one's text is sent to the AI once, with the student's name, county, city and
        high school removed, to pull out themes. Problems with single files come back as `warnings`."""
        p = load(pid)
        items, warnings = [], []
        for f in files:
            name = re.split(r"[\\/]", f.filename or "")[-1] or "essay.txt"
            data = f.file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                warnings.append(f"{name} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB; it was not added.")
            elif not name.lower().endswith(ESSAY_SUFFIXES):
                warnings.append(f"{name}: only .txt and .pdf essays are supported.")
            else:
                items.append((name, data))
        p, more = add_essays(get_model(), p, items)
        db.save_profile(p)
        return EssayUploadOut(**profile_out(p).model_dump(), warnings=warnings + more)

    @app.delete("/api/profiles/{pid}/essays/{index}", response_model=ProfileOut, tags=["profiles"])
    def delete_essay(pid: ProfileId, index: int):
        p = load(pid)
        if not 0 <= index < len(p.essays):
            raise HTTPException(404, f"No essay at index {index}.")
        p = p.model_copy(update={"essays": [e for i, e in enumerate(p.essays) if i != index]})
        db.save_profile(p)
        return profile_out(p)

    # ------------------------------------------------------------------ match runs
    @app.post("/api/profiles/{pid}/runs", response_model=Job, status_code=202, tags=["runs"])
    def start_run(pid: ProfileId, body: RunRequest = RunRequest()):
        """Start a match run in the background (or return the one already running for this profile). If the profile,
        catalog and settings are unchanged since today's stored run, that run is reused and the job finishes at once
        (unless `force`)."""
        p = load(pid)
        if db.catalog_stats()["total"] == 0:
            raise HTTPException(409, "The scholarship catalog is empty. Run `python scripts/load_sample.py` or the "
                                     "scrapers first (see README).")
        return runner.start(p, force=body.force, as_of=today())

    @app.get("/api/jobs/{job_id}", response_model=Job, tags=["runs"])
    def get_job(job_id: str):
        job = runner.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job (jobs are kept in memory and reset when the server restarts).")
        return job

    @app.get("/api/jobs/{job_id}/events", tags=["runs"])
    async def job_events(job_id: str, request: Request):
        """Server-sent events: one `progress` event per change, then a final `done` or `error` event. Each event's
        data is the Job as JSON."""
        if not runner.get(job_id):
            raise HTTPException(404, "Unknown job.")

        async def stream():
            last = None
            while not await request.is_disconnected():
                job = runner.get(job_id)
                if job is None:
                    return
                data = job.model_dump_json()
                if data != last:
                    event = job.state if job.state in ("done", "error") else "progress"
                    yield f"event: {event}\ndata: {data}\n\n"
                    last = data
                    if event != "progress":
                        return
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/profiles/{pid}/runs/latest", response_model=RunView, tags=["runs"])
    def latest_run(pid: ProfileId,
                   sort: str = Query("Best overall match", description="One of `GET /api/meta` → sorts."),
                   limit: Optional[int] = Query(None, ge=1, description="Default: SM_MAX_RESULTS."),
                   hide_unverified: bool = Query(False, description="Leave out items with a Verify / Check flag.")):
        p, run = load(pid), latest(pid)
        if sort not in rubric.SORTS:
            raise HTTPException(422, f"Unknown sort {sort!r}. Options: {list(rubric.SORTS)}.")
        results = rubric.sort_results(run.results, sort)
        if hide_unverified:
            results = [r for r in results if not needs_verification(r)]
        results = results[: limit or config.MAX_RESULTS]
        return RunView(
            run_id=run.run_id, profile_id=run.profile_id, as_of=run.as_of, mode=run.mode,
            review_mode=review_mode_label(run), created_at=run.created_at, stats=run.stats,
            stale=run.fingerprint != Supervisor(get_model()).fingerprint(p, today()),
            total_results=len(run.results), results=[for_display(p, r) for r in results],
            exclusion_counts=exclusion_counts(run), exclusions=exclusions_for_display(p, run))

    @app.get("/api/runs/{run_id}/audit", tags=["runs"])
    def run_audit(run_id: str) -> list[dict]:
        """Every Supervisor accept / reject decision for one run (the "Data & audit" page)."""
        return db.audit_for(run_id)

    # ------------------------------------------------------------------ scholarships and saved list
    @app.get("/api/scholarships/{key:path}", response_model=Scholarship, tags=["scholarships"])
    def get_scholarship(key: str):
        s = db.get_scholarship(key)
        if not s:
            raise HTTPException(404, f"No scholarship {key!r}.")
        return s

    def shown_snapshot(p: Optional[StudentProfile], snap: dict) -> dict:
        """A saved match result with the student's essay titles shown; a catalog-row snapshot unchanged."""
        if p is None:
            return snap
        try:
            return for_display(p, MatchResult.model_validate(snap)).model_dump()
        except ValidationError:
            return snap

    @app.get("/api/profiles/{pid}/saved", response_model=list[SavedOut], tags=["saved"])
    def list_saved(pid: ProfileId):
        p = db.load_profile(pid)
        out = []
        for it in db.list_saved(pid):
            live = db.get_scholarship(it["key"])
            out.append(SavedOut(key=it["key"], status=it["status"], note=it["note"], saved_at=it.get("saved_at"),
                                snapshot=shown_snapshot(p, it["snapshot"]), ended=bool(live and live.status == "ended")))
        return out

    @app.put("/api/profiles/{pid}/saved/{key:path}", response_model=SavedOut, tags=["saved"])
    def save(pid: ProfileId, key: str, body: SavedIn = SavedIn()):
        """Save a scholarship or change its status / note. A new save keeps a snapshot of the latest match result
        (or of the catalog row if it is not in the latest run)."""
        load(pid)                                                  # 404 for an unknown profile
        current = next((it for it in db.list_saved(pid) if it["key"] == key), None)
        if current:
            snapshot = current["snapshot"]
        else:
            run = db.latest_run(pid)
            hit = next((r for r in (run.results if run else []) if r.scholarship_key == key), None)
            if hit:
                snapshot = hit.model_dump()                        # stored as Streamlit stores it: agent labels
            else:
                s = db.get_scholarship(key)
                if not s:
                    raise HTTPException(404, f"No scholarship {key!r}.")
                snapshot = {"scholarship_key": key, "name": s.name, "url": s.url, "source": s.source,
                            "amount_display": s.amount_raw, "deadline": s.deadline}
        db.save_scholarship(pid, key, snapshot, body.status, body.note)
        return next(x for x in list_saved(pid) if x.key == key)

    @app.delete("/api/profiles/{pid}/saved/{key:path}", status_code=204, tags=["saved"])
    def unsave(pid: ProfileId, key: str):
        db.unsave_scholarship(pid, key)

    # ------------------------------------------------------------------ chat
    @app.get("/api/profiles/{pid}/chat/{key:path}", response_model=list[ChatTurn], tags=["chat"])
    def chat_history(pid: ProfileId, key: str):
        p = load(pid)
        return [ChatTurn(id=t["id"], role=t["role"], content=p.show_essay_titles(t["content"]))
                for t in db.chat_history(pid, key)]

    @app.post("/api/profiles/{pid}/chat/{key:path}", response_model=ChatOut, tags=["chat"])
    def chat(pid: ProfileId, key: str, body: ChatIn):
        """Ask the advisor about one scholarship. Answers are grounded in the listing and the stored match analysis."""
        p = load(pid)
        if not db.get_scholarship(key):
            raise HTTPException(404, f"No scholarship {key!r}.")
        answer = ChatAgent(get_model(), p, db.latest_run(pid)).ask(key, body.question)
        return ChatOut(answer=p.show_essay_titles(answer))

    return app


app = create_app()
