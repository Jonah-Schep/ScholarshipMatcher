from scholarmatch import db
from scholarmatch.agents.chat import ChatAgent
from scholarmatch.agents.supervisor import Supervisor
from scholarmatch.llm import LLMError, OfflineLLM
from scholarmatch.matching import rubric
from scholarmatch.models import ScoreJudgement
from scholarmatch.run_summary import ai_reviewed_count, review_mode_label
from tests.conftest import AS_OF, FakeLLM


def keys(run):
    return [r.scholarship_key for r in run.results]


def excl(run):
    return {e.scholarship_key: e for e in run.exclusions}


def get(run, key):
    return next(r for r in run.results if r.scholarship_key == key)


def test_hard_filter_is_deterministic_and_explained(catalog, profile):
    run = Supervisor(OfflineLLM()).run(profile, AS_OF)
    assert run.mode == "heuristic"
    assert set(keys(run)) == {"test:svc", "test:women", "test:4h", "academicworks_vt:vt1", "test:open"}
    assert run.stats["sql_excluded"] == {
        "flagged as scam/spam": 1, "deadline passed": 1, "loan or reimbursement, not a scholarship": 1,
        "for students of a school not on your list": 1, "restricted to another state": 1,
        "GPA below stated minimum": 1}
    for r in run.results:
        assert len(r.rubric) == 5 and abs(sum(line.points for line in r.rubric) - r.match_score) < 0.11
        assert all(line.why for line in r.rubric) and 0 < r.win_probability <= 0.9


def test_same_inputs_same_output(catalog, profile):
    sup = Supervisor(OfflineLLM())
    a = sup.run(profile, AS_OF)
    assert sup.run(profile, AS_OF).run_id == a.run_id                    # identical request -> stored run
    b = sup.run(profile, AS_OF, force=True)                              # recomputed from scratch
    assert [r.model_dump() for r in a.results] == [r.model_dump() for r in b.results]
    assert sup.fingerprint(profile, AS_OF) != sup.fingerprint(profile.model_copy(update={"gpa": 3.7}), AS_OF)


def test_unknown_never_excludes(catalog, profile):
    p = profile.model_copy(update={"gender": "", "majors": [], "gpa": None})
    run = Supervisor(OfflineLLM()).run(p, AS_OF)
    assert any(f.startswith("Verify — Gender") for f in get(run, "test:women").flags)
    assert "test:gpa" in keys(run)                                       # GPA unknown -> kept and flagged
    male = Supervisor(OfflineLLM()).run(profile.model_copy(update={"gender": "male"}), AS_OF)
    assert "Gender" in excl(male)["test:women"].reason


def test_preferences(catalog, profile):
    run = Supervisor(OfflineLLM()).run(profile.model_copy(update={"no_essay_only": True, "min_award": 800}), AS_OF)
    ex = excl(run)
    assert ex["test:svc"].stage == "preference" and ex["test:open"].stage == "preference"


def test_llm_pipeline_filter_score_verify(catalog, profile):
    llm = FakeLLM()
    run = Supervisor(llm).run(profile, AS_OF)
    assert run.mode == "llm"
    assert excl(run)["test:4h"].stage == "fuzzy_filter" and "4-H club" in excl(run)["test:4h"].reason
    svc = get(run, "test:svc")
    assert svc.judgement.fit == "strong" and not any("Low confidence" in f for f in svc.flags)
    assert run.results[0].scholarship_key == "test:svc"
    # second, forced run: every agent call is a cache hit and the result is identical
    llm2 = FakeLLM()
    run2 = Supervisor(llm2).run(profile, AS_OF, force=True)
    assert llm2.usage["calls"] == 0 and llm2.usage["cache_hits"] > 0
    assert [r.model_dump() for r in run.results] == [r.model_dump() for r in run2.results]


def test_supervisor_rejects_hallucinated_evidence_then_accepts(catalog, profile):
    run = Supervisor(FakeLLM(bad_scorer_rounds=1)).run(profile, AS_OF)
    svc = get(run, "test:svc")
    assert svc.judgement.fit == "strong"
    assert all(e.student_evidence != "Student council president" for e in svc.judgement.fit_evidence)
    events = [(a["agent"], a["event"]) for a in db.audit_for(run.run_id)]
    assert ("scorer", "rejected") in events and ("scorer", "accepted") in events


def test_supervisor_fails_safe_when_retries_exhausted(catalog, profile):
    run = Supervisor(FakeLLM(bad_scorer_rounds=99)).run(profile, AS_OF)
    svc = get(run, "test:svc")
    assert svc.judgement.fit == "moderate"                               # 1 grounded match left -> downgraded
    assert any("Low confidence" in f for f in svc.flags)


def test_verifier_rejection_downgrades_fit(catalog, profile):
    run = Supervisor(FakeLLM(verifier_rejects=True)).run(profile, AS_OF)
    assert get(run, "test:svc").judgement.fit == "weak"                  # verifier's conservative rating wins


def test_ungrounded_drop_fails_open(catalog, profile):
    run = Supervisor(FakeLLM(ungrounded_drop=True)).run(profile, AS_OF)
    r = get(run, "test:4h")
    assert r.filter_verdict.verdict == "uncertain" and any(f.startswith("Check —") for f in r.flags)


def test_no_pii_reaches_agents(catalog, profile):
    llm = FakeLLM(ungrounded_drop=True)                                  # keeps test:4h in the results
    profile = profile.model_copy(update={"affiliations": ["Parent works at Zyxwv Holdings", "member of 4-H club"]})
    run = Supervisor(llm).run(profile, AS_OF)
    assert next(h for h in get(run, "test:4h").hard_checks if h.name == "Specific group").result == "pass"
    ChatAgent(llm, profile, run).ask("test:svc", "What should I do first?")
    ChatAgent(llm, profile, run).ask("test:4h", "Am I eligible?")      # the chat sees its "Specific group" check
    blob = "\n".join(llm.prompts)
    for secret in ("Jordan", "Testcase", "Montgomery", "Blacksburg", "Hispanic", '"gender":', '"ethnicities"',
                   "Zyxwv", "Parent works at", '"affiliations"', "no_special_affiliations"):
        assert secret not in blob, secret
    assert "Robotics team captain" in blob
    assert "affiliations" not in profile.redacted_view() and "no_special_affiliations" not in profile.redacted_view()


def test_group_limited_award_kept_with_verify_unless_student_says_none_apply(catalog, profile):
    run = Supervisor(OfflineLLM()).run(profile, AS_OF)
    r = get(run, "test:4h")                                              # no affiliations listed: kept, flagged
    assert any(f.startswith("Verify — Specific group") for f in r.flags)
    assert next(h for h in r.hard_checks if h.name == "Specific group").weight == "restrictive"
    member = Supervisor(OfflineLLM()).run(profile.model_copy(update={"affiliations": ["4-H club member"]}), AS_OF)
    assert next(h for h in get(member, "test:4h").hard_checks if h.name == "Specific group").result == "pass"
    none = Supervisor(OfflineLLM()).run(profile.model_copy(update={"no_special_affiliations": True}), AS_OF)
    assert "Specific group" in excl(none)["test:4h"].reason and "test:svc" in keys(none)


def test_sorting_and_saved(catalog, profile):
    run = Supervisor(OfflineLLM()).run(profile, AS_OF)
    big = rubric.sort_results(run.results, "Largest award")
    assert big[0].scholarship_key == "test:svc" and big[-1].amount_value is None
    assert rubric.sort_results(run.results, "Easiest to apply")[0].effort_hours == min(r.effort_hours for r in run.results)
    db.save_profile(profile)
    db.save_scholarship("t1", "test:svc", run.results[0].model_dump())
    assert db.list_saved("t1")[0]["key"] == "test:svc" and db.load_profile("t1").gpa == 3.6
    db.delete_profile("t1")
    assert db.load_profile("t1") is None and db.list_saved("t1") == []


class ScorerDownLLM(FakeLLM):
    """Every Scorer call fails, as on 2026-11-10 when the endpoint returned 502 for most calls."""
    name = "fake-scorer-down"

    def _structured(self, role, system, user, schema):
        if schema is ScoreJudgement:
            raise LLMError("502 backend unavailable")
        return super()._structured(role, system, user, schema)


def test_run_header_counts_items_actually_reviewed_by_agents(catalog, profile):
    down = Supervisor(ScorerDownLLM()).run(profile, AS_OF)
    assert down.mode == "llm" and down.results
    assert all(r.mode == "heuristic" for r in down.results)
    assert ai_reviewed_count(down) == 0
    assert review_mode_label(down) == "rule-based (AI review unavailable)"
    assert all(any("AI review step failed" in f for f in r.flags) for r in down.results)


def test_run_header_labels_for_working_and_offline_runs(catalog, profile):
    ok = Supervisor(FakeLLM()).run(profile, AS_OF)
    n = len(ok.results)
    assert n and review_mode_label(ok) == f"AI-reviewed ({n} of {n})"
    partial = ok.model_copy(update={"results": [ok.results[0].model_copy(update={"mode": "heuristic"}), *ok.results[1:]]})
    assert review_mode_label(partial) == f"AI-reviewed ({n - 1} of {n})"
    assert review_mode_label(Supervisor(OfflineLLM()).run(profile, AS_OF)) == "rule-based"


def test_progress_reports_agent_stage_before_first_item_finishes(catalog, profile):
    msgs = []
    Supervisor(FakeLLM()).run(profile, AS_OF, progress=lambda msg, frac: msgs.append((msg, frac)))
    texts = [m for m, _ in msgs]
    assert texts[0].startswith("Filtering the catalog")
    assert any(t.startswith("Shortlisted") for t in texts)
    first = next(i for i, t in enumerate(texts) if t.startswith("Agents reviewing scholarships… 0/"))
    assert first < next(i for i, t in enumerate(texts) if t.startswith("Agents reviewing scholarships… 1/"))
    assert [f for _, f in msgs] == sorted(f for _, f in msgs) and texts[-1] == "Done."


def test_zero_award_amount_shows_as_unknown(profile):
    from scholarmatch.scrapers.runner import ingest
    from tests.conftest import sch
    ingest([sch("prog", "Andrew and Margaret Little Real Estate Fund", "Supports the real estate program.",
                amount_raw="$0.00", amount_min=None, amount_max=None)])
    r = get(Supervisor(OfflineLLM()).run(profile, AS_OF), "test:prog")
    assert r.amount_display == "Varies" and r.amount_value is None and r.expected_value is None
    assert rubric.ZERO_AMOUNT_FLAG in r.flags
    assert next(line for line in r.rubric if line.dimension == "Award value").rating == "varies"


def test_chat_persists_and_works_offline(catalog, profile):
    run = Supervisor(OfflineLLM()).run(profile, AS_OF)
    out = ChatAgent(OfflineLLM(), profile, run).ask("test:svc", "How hard is this?")
    assert "Competition" in out
    assert [t["role"] for t in db.chat_history("t1", "test:svc")] == ["user", "assistant"]


def test_title_only_rows_fall_in_the_pre_rank(catalog, profile):
    from scholarmatch.scrapers.runner import ingest
    from tests.conftest import sch
    ingest([sch("vt_title", "Smith Family Endowed Scholarship", "", source="academicworks_vt", school="vt",
                geo_scope="va", amount_raw="Varies", amount_min=None, amount_max=None,
                extra={"detail_fetched": False})])
    run = Supervisor(OfflineLLM()).run(profile, AS_OF)
    title = get(run, "academicworks_vt:vt_title")
    cert = next(line for line in title.rubric if line.dimension == "Eligibility certainty")
    assert cert.rating == "30%" and "no eligibility details" in cert.why
    assert any("Only the listing title" in f for f in title.flags)
    assert keys(run).index("academicworks_vt:vt_title") > keys(run).index("test:svc")
    assert keys(run).index("academicworks_vt:vt_title") > keys(run).index("academicworks_vt:vt1")
