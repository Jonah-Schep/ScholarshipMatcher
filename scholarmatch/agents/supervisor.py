"""The Supervisor: a deterministic controller that owns the pipeline and gates every agent.

Why the supervisor is code, not an LLM: routing here is fixed (filter -> extract -> screen ->
score -> verify), so an LLM "manager" would only add variance. What does need judgement -
"is this worker's output acceptable?" - is split in two:
  1. mechanical checks in this file (schema, verbatim-quote grounding, evidence present in profile)
  2. the independent Verifier agent (chain-of-verification) for semantic support
A rejected worker gets specific feedback and at most MAX_SUPERVISOR_ROUNDS retries, then the
supervisor FAILS SAFE: keep the scholarship, downgrade the claim, and flag it for the student.

Consistency: a run is identified by a fingerprint of (profile, catalog, date, versions, models).
Same fingerprint -> the stored run is returned unchanged. New fingerprint -> each LLM call is
still served from the per-(profile, scholarship) response cache wherever inputs are unchanged.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from .. import config, db
from ..ingest import extract_rules
from ..llm import LLM, LLMError, dumps, get_llm
from ..matching import hard_filter, rubric
from ..models import (Criteria, Exclusion, ExtractedCriteria, FilterVerdict, MatchResult, MatchRun, Scholarship,
                      ScoreJudgement, StudentProfile)
from ..textutil import is_grounded, norm, now_iso, sha, today_iso
from . import workers

FIT_ORDER = ["none", "weak", "moderate", "strong"]
Progress = Optional[Callable[[str, float], None]]


class Supervisor:
    def __init__(self, llm: Optional[LLM] = None):
        self.llm = llm or get_llm()
        self.mode = "llm" if self.llm.available else "heuristic"
        self.run_id = ""

    # ------------------------------------------------------------------ public
    def fingerprint(self, p: StudentProfile, as_of: str) -> str:
        models = {r: self.llm.model(r) for r in config.ROLES}
        return sha(p.model_dump(), db.catalog_version(), as_of, config.RULES_VERSION, config.PROMPT_VERSION,
                   config.RUBRIC_VERSION, self.llm.name, models, config.MAX_LLM_CANDIDATES)[:20]

    def run(self, p: StudentProfile, as_of: Optional[str] = None, force: bool = False,
            progress: Progress = None) -> MatchRun:
        as_of = as_of or today_iso()
        fp = self.fingerprint(p, as_of)
        if not force:
            prior = db.find_run(p.profile_id, fp)
            if prior:
                return prior
        self.run_id = uuid.uuid4().hex[:12]
        say = progress or (lambda msg, frac: None)
        exclusions: list[Exclusion] = []

        say("Filtering the catalog with hard rules…", 0.05)
        rows, sql_counts = hard_filter.sql_stage(p, as_of)
        self._audit("supervisor", "sql_stage", {"kept": len(rows), "excluded": sql_counts})

        candidates = []
        for row in rows:
            s: Scholarship = row["scholarship"]
            c = self._rules_criteria(s, row["content_hash"])
            checks = hard_filter.check(s, c, p)
            fails = hard_filter.failures(checks)
            if fails:
                exclusions.append(Exclusion(scholarship_key=s.key, name=s.name, stage="hard_filter", reason="; ".join(fails)))
                continue
            pref = hard_filter.preference_exclusion(c, s, p)
            if pref:
                exclusions.append(Exclusion(scholarship_key=s.key, name=s.name, stage="preference", reason=pref))
                continue
            flags = [f"Caution — {r}" for r in row["scam_reasons"]] if row["scam_level"] == "warn" else []
            if s.extra.get("detail_fetched") is False:
                flags.append("Only the listing title was available when this was scraped; open the link for full criteria.")
            pre = rubric.build_result(s, c, p, checks, rubric.heuristic_judgement(s, c, p), "heuristic", flags)
            candidates.append({"s": s, "c": c, "hash": row["content_hash"], "pre": pre, "flags": flags})

        # Deterministic pre-rank caps LLM spend; ties break on key so the cut is stable.
        candidates.sort(key=lambda d: (-d["pre"].match_score, d["s"].key))
        shortlist, overflow = candidates[:config.MAX_LLM_CANDIDATES], candidates[config.MAX_LLM_CANDIDATES:]
        for d in overflow:
            exclusions.append(Exclusion(scholarship_key=d["s"].key, name=d["s"].name, stage="cap",
                                        reason=f"Eligible, but ranked below the top {config.MAX_LLM_CANDIDATES} on the pre-score."))
        self._audit("supervisor", "shortlist", {"eligible": len(candidates), "shortlisted": len(shortlist)})
        say(f"Shortlisted {len(shortlist)} of {len(candidates)} eligible scholarships…", 0.08)

        results: list[MatchResult] = []
        if self.mode == "heuristic":
            say("Scoring with the rule-based rubric…", 0.5)
            results = [d["pre"] for d in shortlist]
        else:
            view = p.redacted_view()
            done = 0

            def work(d):
                return self._evaluate(d, p, view)

            say(f"Agents reviewing scholarships… 0/{len(shortlist)}", 0.1)
            with ThreadPoolExecutor(max_workers=config.LLM_WORKERS) as pool:
                for out in pool.map(work, shortlist):          # map preserves input order
                    done += 1
                    say(f"Agents reviewing scholarships… {done}/{len(shortlist)}", 0.1 + 0.85 * done / max(1, len(shortlist)))
                    if isinstance(out, Exclusion):
                        exclusions.append(out)
                    else:
                        results.append(out)

        results = rubric.sort_results(results, "Best overall match")
        exclusions.sort(key=lambda e: (e.stage, e.scholarship_key))
        run = MatchRun(run_id=self.run_id, profile_id=p.profile_id, as_of=as_of, fingerprint=fp, mode=self.mode,
                       results=results, exclusions=exclusions,
                       stats={"catalog": db.catalog_stats()["total"], "sql_excluded": sql_counts,
                              "after_sql": len(rows), "eligible": len(candidates), "reviewed": len(shortlist),
                              "matched": len(results), "llm": dict(self.llm.usage), "provider": self.llm.name},
                       created_at=now_iso())
        db.save_run(run)
        self._audit("supervisor", "run_complete", run.stats)
        say("Done.", 1.0)
        return run

    # ------------------------------------------------------------------ per-scholarship pipeline
    def _evaluate(self, d: dict, p: StudentProfile, view: dict) -> MatchResult | Exclusion:
        s: Scholarship = d["s"]
        flags = list(d["flags"])
        try:
            c = self._llm_criteria(s, d["hash"], d["c"])
            if c.scam_signals:
                flags.append("Caution — possible scam signals: " + "; ".join(c.scam_signals[:3]))
            checks = hard_filter.check(s, c, p)
            fails = hard_filter.failures(checks)
            if fails:
                return Exclusion(scholarship_key=s.key, name=s.name, stage="hard_filter", reason="; ".join(fails))
            pref = hard_filter.preference_exclusion(c, s, p)
            if pref:
                return Exclusion(scholarship_key=s.key, name=s.name, stage="preference", reason=pref)

            verdict = self._supervised_filter(s, c, view)
            if verdict.verdict == "drop":
                return Exclusion(scholarship_key=s.key, name=s.name, stage="fuzzy_filter",
                                 reason=f"{verdict.reason} (listing says: “{verdict.scholarship_quote}”)")
            if verdict.verdict == "uncertain":
                flags.append(f"Check — {verdict.reason}")

            judgement, score_flags = self._supervised_score(s, c, view)
            result = rubric.build_result(s, c, p, checks, judgement, "llm", flags + score_flags)
            result.filter_verdict = verdict
            return result
        except LLMError as e:
            # Fail safe: never lose an eligible scholarship because an agent call failed.
            self._audit("supervisor", "llm_failure", {"key": s.key, "error": str(e)[:300]})
            pre: MatchResult = d["pre"]
            pre.flags.append("An AI review step failed for this item; showing the rule-based score instead.")
            return pre

    def _rules_criteria(self, s: Scholarship, chash: str) -> Criteria:
        c = db.get_criteria(s.key, "rules", chash, config.RULES_VERSION)
        if c is None:
            c = extract_rules.extract(s)
            db.put_criteria(s.key, "rules", chash, config.RULES_VERSION, c)
        return c

    def _llm_criteria(self, s: Scholarship, chash: str, rules: Criteria) -> Criteria:
        """Extractor agent with a reflexion loop: ungrounded fields are sent back once, then discarded."""
        # RULES_VERSION is part of the key: `to_criteria` carries rule-extracted fields into the LLM criteria,
        # so a rules change must invalidate the cached LLM criteria too.
        version = f"{config.PROMPT_VERSION}:{config.RULES_VERSION}:{self.llm.model('extractor')}"
        cached = db.get_criteria(s.key, "llm", chash, version)
        if cached:
            return cached
        text, feedback, x = s.full_text(), "", None
        for round_no in range(config.MAX_SUPERVISOR_ROUNDS):
            x = workers.run_extractor(self.llm, s, feedback)
            bad = self._ungrounded_fields(x, text)
            if not bad:
                break
            self._audit("extractor", "rejected", {"key": s.key, "round": round_no + 1, "ungrounded": bad})
            feedback = ("These fields had no verbatim supporting quote in the listing: " + ", ".join(bad) +
                        ". Re-read the listing. Either quote the exact supporting text or set the field to null/empty.")
        # Still unsupported values are dropped in favour of the rule value; rule-found restrictions are kept and list
        # fields are the union of rules and grounded LLM values (see workers.to_criteria).
        c = workers.to_criteria(x, rules, text)
        if c.requirements.essay_count is None:
            c.requirements.essay_count = rules.requirements.essay_count
        db.put_criteria(s.key, "llm", chash, version, c)
        return c

    _HARD_FIELDS = workers.HARD_FIELDS

    def _ungrounded_fields(self, x: ExtractedCriteria, text: str) -> list[str]:
        """A hard criterion from the Extractor can only exclude a student if its quote really is in the listing."""
        return workers.ungrounded_fields(x, text)

    def _supervised_filter(self, s: Scholarship, c: Criteria, view: dict) -> FilterVerdict:
        feedback, v = "", None
        for round_no in range(config.MAX_SUPERVISOR_ROUNDS):
            v = workers.run_filter(self.llm, s, c, view, feedback)
            if v.verdict != "drop" or is_grounded(v.scholarship_quote, s.full_text()):
                return v
            self._audit("filter", "rejected", {"key": s.key, "round": round_no + 1, "quote": v.scholarship_quote[:200]})
            feedback = ("Your 'drop' verdict was rejected: scholarship_quote is not a verbatim span of the listing. "
                        "Quote the exact requirement, or change the verdict to 'uncertain'.")
        # fail open: an unsupported 'drop' never hides a scholarship
        return FilterVerdict(verdict="uncertain", scholarship_quote="",
                             reason=f"Screening agent raised a concern it could not substantiate: {v.reason}")

    def _supervised_score(self, s: Scholarship, c: Criteria, view: dict) -> tuple[ScoreJudgement, list[str]]:
        text, profile_text = s.full_text(), norm(dumps(view))
        titles = {e["title"] for e in view.get("essay_themes", [])}
        feedback, j, problems = "", None, []
        for round_no in range(config.MAX_SUPERVISOR_ROUNDS):
            j = workers.run_scorer(self.llm, s, c, view, feedback)
            problems = self._score_problems(j, text, profile_text, titles)
            ver = None
            if not problems and j.fit_evidence:
                ver = workers.run_verifier(self.llm, s, view, j)
                problems = [f"claim {ch.index} ({j.fit_evidence[ch.index].criterion!r}): {ch.issue}"
                            for ch in ver.checks if not ch.supported and 0 <= ch.index < len(j.fit_evidence)]
                if not ver.fit_rating_justified:
                    problems.append(f"fit '{j.fit}' is not justified by the evidence; verifier suggests '{ver.suggested_fit}'.")
            if not problems:
                self._audit("scorer", "accepted", {"key": s.key, "round": round_no + 1, "fit": j.fit})
                return j, []
            self._audit("scorer", "rejected", {"key": s.key, "round": round_no + 1, "problems": problems})
            feedback = "\n".join(f"- {x}" for x in problems)
        # Rounds exhausted: keep only mechanically-grounded evidence and take the more conservative rating.
        kept = [e for e in j.fit_evidence
                if is_grounded(e.scholarship_quote, text) and (not e.met or norm(e.student_evidence) in profile_text)]
        met = sum(e.met for e in kept)
        ceiling = "strong" if met >= 2 else "moderate" if met == 1 else "weak"
        fit = min(j.fit, ceiling, key=FIT_ORDER.index)
        if ver is not None:
            fit = min(fit, ver.suggested_fit, key=FIT_ORDER.index)
        j = j.model_copy(update={"fit": fit, "fit_evidence": kept,
                                 "reusable_essay_title": j.reusable_essay_title if j.reusable_essay_title in titles else ""})
        return j, ["Low confidence — the fit rating could not be fully verified and was set conservatively."]

    @staticmethod
    def _score_problems(j: ScoreJudgement, text: str, profile_text: str, titles: set[str]) -> list[str]:
        probs = []
        for i, e in enumerate(j.fit_evidence):
            if not is_grounded(e.scholarship_quote, text):
                probs.append(f"claim {i}: scholarship_quote is not a verbatim span of the listing.")
            if e.met and (len(e.student_evidence) < 3 or norm(e.student_evidence) not in profile_text):
                probs.append(f"claim {i}: student_evidence {e.student_evidence!r} is not an item in the student profile.")
        if j.fit == "strong" and sum(e.met for e in j.fit_evidence) < 2:
            probs.append("fit 'strong' needs at least two met criteria with evidence.")
        if j.reusable_essay_title and j.reusable_essay_title not in titles:
            probs.append(f"reusable_essay_title {j.reusable_essay_title!r} is not one of the student's essays.")
        if j.essay_reuse in ("full", "partial") and not j.reusable_essay_title:
            probs.append("essay_reuse is full/partial but reusable_essay_title is empty.")
        return probs

    def _audit(self, agent: str, event: str, detail) -> None:
        db.audit(self.run_id, agent, event, detail)
