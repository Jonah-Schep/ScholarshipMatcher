"""HTTP API (scholarmatch/api) end to end with FastAPI's TestClient. No network: FakeLLM / OfflineLLM, synthetic catalog."""
import time

import pytest
from fastapi.testclient import TestClient

from scholarmatch import db
from scholarmatch.api.app import create_app
from scholarmatch.llm import OfflineLLM
from scholarmatch.profile_io import EssayReadError, read_essay_file, split_lines
from tests.conftest import AS_OF, FakeLLM

PROFILE = {"display_name": "Jordan Testcase", "state": "VA", "county": "Montgomery", "city": "Blacksburg",
           "level": "hs_senior", "gpa": 3.6, "intended_schools": ["vt"], "majors": ["Mechanical Engineering"],
           "gender": "female", "ethnicities": ["Hispanic / Latino"], "financial_need": True,
           "activities": ["Robotics team captain", "Food bank volunteer (120 hours)"], "interests": ["renewable energy"]}
ESSAY = ("Jordan_Testcase_why_I_serve.txt", b"I am Jordan Testcase from Blacksburg. Serving at the food bank taught me "
                                            b"leadership and community service.")


def client(llm=None) -> TestClient:
    return TestClient(create_app(llm=llm or OfflineLLM(), as_of=lambda: AS_OF))


def wait(c: TestClient, job: dict, timeout: float = 30) -> dict:
    end = time.time() + timeout
    while job["state"] in ("queued", "running"):
        assert time.time() < end, job
        time.sleep(0.05)
        job = c.get(f"/api/jobs/{job['job_id']}").json()
    return job


def make_profile(c: TestClient, pid: str = "t1", essay: bool = True) -> None:
    assert c.put(f"/api/profiles/{pid}", json=PROFILE).status_code == 200
    if essay:
        assert c.post(f"/api/profiles/{pid}/essays", files=[("files", ESSAY)]).status_code == 200


def run(c: TestClient, pid: str = "t1", force: bool = False) -> dict:
    r = c.post(f"/api/profiles/{pid}/runs", json={"force": force})
    assert r.status_code == 202, r.text
    job = wait(c, r.json())
    assert job["state"] == "done", job
    return job


# ----------------------------------------------------------------------------- helpers shared with Streamlit
def test_split_lines_and_essay_reading():
    assert split_lines("a\n\n b ; c;") == ["a", "b", "c"]
    assert read_essay_file("x.TXT", "héllo ".encode()) == "héllo"
    with pytest.raises(EssayReadError):
        read_essay_file("x.docx", b"...")
    with pytest.raises(EssayReadError):
        read_essay_file("x.pdf", b"not a pdf")


# ----------------------------------------------------------------------------- status
def test_health_and_meta(catalog):
    c = client()
    h = c.get("/api/health").json()
    assert h == {"llm_available": False, "llm_name": "offline", "catalog_total": len(catalog),
                 "catalog_updated": h["catalog_updated"]}
    m = c.get("/api/meta").json()
    assert "Best overall match" in m["sorts"] and "vt" in m["schools"] and m["saved_statuses"][0] == "interested"
    assert c.get("/openapi.json").status_code == 200


# ----------------------------------------------------------------------------- profiles and essays
def test_profile_crud_and_validation():
    c = client()
    assert c.get("/api/profiles/t1").status_code == 404
    assert c.put("/api/profiles/Bad Name", json=PROFILE).status_code == 422          # profile id pattern
    assert c.put("/api/profiles/t1", json=PROFILE | {"state": "Virginia"}).status_code == 422
    assert c.put("/api/profiles/t1", json=PROFILE | {"essays": []}).status_code == 422   # essays have own endpoints
    assert c.put("/api/profiles/t1", json=PROFILE | {"gpa": 5}).status_code == 422
    make_profile(c)
    assert c.get("/api/profiles").json() == ["t1"]
    got = c.get("/api/profiles/t1").json()
    assert got["profile"]["county"] == "Montgomery" and len(got["essays"]) == 1

    # A PUT replaces the fields but keeps the essays on file.
    out = c.put("/api/profiles/t1", json=PROFILE | {"gpa": 3.9}).json()
    assert out["profile"]["gpa"] == 3.9 and [e["title"] for e in out["essays"]] == [ESSAY[0]]

    assert c.delete("/api/profiles/t1").status_code == 204
    assert c.get("/api/profiles").json() == []
    assert c.delete("/api/profiles/t1").status_code == 404


def test_essay_upload_keeps_text_local_and_warns():
    llm = FakeLLM()
    c = client(llm)
    make_profile(c, essay=False)
    r = c.post("/api/profiles/t1/essays", files=[("files", ESSAY), ("files", ("notes.docx", b"x")),
                                                  ("files", ("empty.txt", b"  "))])
    body = r.json()
    assert r.status_code == 200
    assert [e["title"] for e in body["essays"]] == [ESSAY[0]]
    assert body["essays"][0]["themes"] == ["service"] and body["essays"][0]["chars"] > 0
    assert len(body["warnings"]) == 2
    # The essay text never comes back over the API, and the themes call got no name, place or title.
    assert all(e["text"] == "" for e in body["profile"]["essays"])
    sent = "\n".join(llm.prompts)
    for secret in ("Jordan", "Testcase", "Blacksburg", "why_I_serve"):
        assert secret not in sent
    assert db.load_profile("t1").essays[0].text.startswith("I am Jordan")

    again = c.post("/api/profiles/t1/essays", files=[("files", ESSAY)]).json()
    assert len(again["essays"]) == 1 and "already on file" in again["warnings"][0]
    assert c.delete("/api/profiles/t1/essays/3").status_code == 404
    assert c.delete("/api/profiles/t1/essays/0").json()["essays"] == []


# ----------------------------------------------------------------------------- runs
def test_run_needs_profile_and_catalog():
    c = client()
    assert c.post("/api/profiles/t1/runs").status_code == 404
    make_profile(c, essay=False)
    assert c.post("/api/profiles/t1/runs").status_code == 409                       # empty catalog
    assert c.get("/api/profiles/t1/runs/latest").status_code == 404


def test_background_run_with_agents(catalog):
    llm = FakeLLM()
    c = client(llm)
    make_profile(c)
    job = run(c)
    view = c.get("/api/profiles/t1/runs/latest").json()
    assert view["run_id"] == job["run_id"] and view["mode"] == "llm" and view["stale"] is False
    assert view["review_mode"].startswith("AI-reviewed")
    keys = {r["scholarship_key"] for r in view["results"]}
    assert "test:svc" in keys and "test:gpa" not in keys
    assert view["exclusion_counts"]["GPA below stated minimum"] == 1

    # The agents saw "Essay 1"; the API shows the student's own title. No PII reached any prompt.
    svc = next(r for r in view["results"] if r["scholarship_key"] == "test:svc")
    assert svc["judgement"]["reusable_essay_title"] == f"“{ESSAY[0]}”"
    blob = "\n".join(llm.prompts)
    for secret in ("Jordan", "Testcase", "Montgomery", "Blacksburg", "Hispanic", "why_I_serve"):
        assert secret not in blob

    # Sorting, limits and the verify filter work on the stored run; a bad sort is a 422.
    by_amount = c.get("/api/profiles/t1/runs/latest", params={"sort": "Largest award", "limit": 2}).json()
    assert len(by_amount["results"]) == 2 and by_amount["total_results"] == len(view["results"])
    hidden = c.get("/api/profiles/t1/runs/latest", params={"hide_unverified": True}).json()["results"]
    assert all(not f.startswith(("Verify", "Check")) for r in hidden for f in r["flags"])
    assert c.get("/api/profiles/t1/runs/latest", params={"sort": "nope"}).status_code == 422

    # Same profile and catalog: the stored run is reused. A profile change marks the run stale.
    calls = len(llm.prompts)
    assert run(c)["run_id"] == job["run_id"] and len(llm.prompts) == calls
    c.put("/api/profiles/t1", json=PROFILE | {"gpa": 3.95})
    assert c.get("/api/profiles/t1/runs/latest").json()["stale"] is True

    audit = c.get(f"/api/runs/{job['run_id']}/audit").json()
    assert any(e["event"] == "sql_stage" for e in audit)


def test_job_events_stream(catalog):
    c = client()
    make_profile(c, essay=False)
    job = c.post("/api/profiles/t1/runs").json()
    with c.stream("GET", f"/api/jobs/{job['job_id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [line.removeprefix("event: ") for line in r.iter_lines() if line.startswith("event: ")]
    assert events[-1] == "done"
    assert c.get("/api/jobs/nope").status_code == 404


# ----------------------------------------------------------------------------- saved list and chat
def test_saved_list(catalog):
    c = client()
    make_profile(c, essay=False)
    run(c)
    r = c.put("/api/profiles/t1/saved/test:svc")
    assert r.status_code == 200 and r.json()["snapshot"]["name"] == "Community Service Award"
    upd = c.put("/api/profiles/t1/saved/test:svc", json={"status": "applying", "note": "ask counselor"}).json()
    assert (upd["status"], upd["note"], upd["ended"]) == ("applying", "ask counselor", False)
    assert c.put("/api/profiles/t1/saved/test:svc", json={"status": "maybe"}).status_code == 422

    # Not in the latest run but in the catalog: saved from the catalog row, and flagged as ended.
    old = c.put("/api/profiles/t1/saved/test:old").json()
    assert old["snapshot"]["name"] == "Expired Award" and old["ended"] is True
    assert c.put("/api/profiles/t1/saved/test:none").status_code == 404

    assert {x["key"] for x in c.get("/api/profiles/t1/saved").json()} == {"test:svc", "test:old"}  # same-second saves
    assert c.delete("/api/profiles/t1/saved/test:svc").status_code == 204
    assert [x["key"] for x in c.get("/api/profiles/t1/saved").json()] == ["test:old"]


def test_saved_snapshot_keeps_labels_and_shows_titles(catalog):
    c = client(FakeLLM())
    make_profile(c)
    run(c)
    shown = c.put("/api/profiles/t1/saved/test:svc").json()["snapshot"]
    assert shown["judgement"]["reusable_essay_title"] == f"“{ESSAY[0]}”"
    stored = db.list_saved("t1")[0]["snapshot"]                       # stored like Streamlit stores it
    assert stored["judgement"]["reusable_essay_title"] == "Essay 1"


def test_upload_strips_client_paths_and_delete_waits_for_run():
    c = client()
    make_profile(c, essay=False)
    body = c.post("/api/profiles/t1/essays", files=[("files", ("C:\\Users\\me\\essay.txt", b"text")),
                                                     ("files", ("dir/other.txt", b"more"))]).json()
    assert [e["title"] for e in body["essays"]] == ["essay.txt", "other.txt"]

    c.app.state.runner.active = lambda pid: True                       # a run is still going for this profile
    assert c.delete("/api/profiles/t1").status_code == 409
    assert c.get("/api/profiles/t1").status_code == 200


def test_scholarship_and_chat(catalog):
    c = client()
    make_profile(c, essay=False)
    run(c)
    assert c.get("/api/scholarships/test:svc").json()["name"] == "Community Service Award"
    assert c.get("/api/scholarships/test:none").status_code == 404
    answer = c.post("/api/profiles/t1/chat/test:svc", json={"question": "How hard is this?"}).json()["answer"]
    assert "Community Service Award" in answer                                     # offline answer from the listing
    hist = c.get("/api/profiles/t1/chat/test:svc").json()
    assert [t["role"] for t in hist] == ["user", "assistant"]
    assert c.post("/api/profiles/t1/chat/test:svc", json={"question": ""}).status_code == 422
    assert c.post("/api/profiles/t1/chat/test:none", json={"question": "hi"}).status_code == 404


def test_cors_allows_the_dev_front_end():
    r = client().options("/api/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_for_display_maps_every_agent_text_field():
    """Live run 2026-11-18: a rubric reason quoted the Scorer's "Essay 1"; every agent-worded field must be mapped."""
    from scholarmatch.models import (Essay, Exclusion, FilterVerdict, FitEvidence, MatchResult, MatchRun, RubricLine,
                                     ScoreJudgement, StudentProfile)
    from scholarmatch.run_summary import exclusions_for_display, for_display
    p = StudentProfile(profile_id="x", essays=[Essay(title="mine.txt", text="t")])
    j = ScoreJudgement(fit="moderate", fit_evidence=[FitEvidence(criterion="reuse Essay 1", scholarship_quote="q",
                                                                 student_evidence="Essay 1", met=True)],
                       essay_reuse="partial", reusable_essay_title="Essay 1", strengths=["Essay 1"], gaps=["Essay 1"],
                       summary="Adapt Essay 1.")
    r = MatchResult(scholarship_key="k", name="n", url="", source="s", judgement=j, flags=["Essay 1 fits"],
                    filter_verdict=FilterVerdict(verdict="keep", reason="Essay 1 is relevant", scholarship_quote=""),
                    rubric=[RubricLine(dimension="Fit", rating="ok", points=1, max_points=2, why="adapt Essay 1")])
    shown = for_display(p, r).model_dump_json()
    assert "Essay 1" not in shown and shown.count("mine.txt") == 9
    assert "Essay 1" in r.model_dump_json()                                   # the stored result is untouched
    run = MatchRun(run_id="r", profile_id="x", as_of="2026-11-18", fingerprint="f", mode="llm", results=[r],
                   exclusions=[Exclusion(scholarship_key="k2", name="m", stage="fuzzy_filter", reason="Essay 1 off")],
                   stats={}, created_at="")
    assert exclusions_for_display(p, run)[0].reason == "“mine.txt” off"
