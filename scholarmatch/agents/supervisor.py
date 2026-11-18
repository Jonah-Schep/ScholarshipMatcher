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

import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from .. import config, db
from ..ingest import extract_rules
from ..llm import LLM, LLMError, dumps, get_llm
from ..matching import hard_filter, rubric
from ..models import (Criteria, Exclusion, ExtractedCriteria, FilterVerdict, MatchResult, MatchRun, Scholarship,
                      ScoreJudgement, StudentProfile, Verification)
from ..textutil import is_grounded, norm, now_iso, sha, today_iso
from . import workers

FIT_ORDER = ["none", "weak", "moderate", "strong"]

# Held-out check (paper principle 7): a Filter "drop" must quote a sentence with requirement wording. This list is
# deliberately NOT in any prompt, so an agent cannot satisfy it by habit. A drop that fails it is retried, then becomes
# "uncertain" (the scholarship stays), so it can only make the pipeline more cautious. Changing it -> bump CHECKS_VERSION.
_REQUIREMENT_WORDING = re.compile(
    r"\b(must|only|requir\w*|eligib\w*|open to|limited to|restricted to|reserved for|available (?:only )?to|"
    r"awarded to|intended for|members? of|employees? of|child(?:ren)? of|dependents? of|spouses? of|"
    r"applicants? (?:who|with|from)|students? (?:who|with|majoring|enrolled|pursuing|planning|in the)|"
    r"need to|has to|have to)\b", re.I)
_STOP = {"with", "that", "this", "from", "have", "will", "must", "their", "they", "your", "students", "student",
         "applicant", "applicants", "scholarship", "scholarships", "award", "awards", "should", "which", "other"}


def _words(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]{4,}", norm(text)) if w not in _STOP}


def shares_word(criterion: str, quote: str) -> bool:
    """Cheap relevance check: the criterion must share a content word (compared on its first five letters, so
    "volunteer" matches "volunteering") with the listing text quoted for it."""
    return bool(_words(criterion) & _words(quote))


def drop_problem(quote: str, text: str) -> str:
    """Why a Filter "drop" quote is not acceptable, or "" if it is."""
    if not is_grounded(quote, text, config.MIN_EVIDENCE_QUOTE_CHARS):
        return (f"scholarship_quote is not a verbatim span of the listing of at least "
                f"{config.MIN_EVIDENCE_QUOTE_CHARS} characters.")
    if not _REQUIREMENT_WORDING.search(quote):
        return "the quoted text does not state a requirement."
    return ""


def _clip(text: str, n: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def low_confidence_flag(disputes: list[tuple[str, str, bool]], proposed: str, suggested: Optional[str]) -> str:
    """The student-facing flag when the Scorer and its checks did not agree within the round limit. It carries the
    checker's actual objections (up to two) instead of a generic warning, so the dissent is visible (principle 9)."""
    msg = "Low confidence — the fit rating could not be fully verified and was set conservatively."
    against = [(c, issue) for c, issue, met in disputes if met]
    for_you = [(c, issue) for c, issue, met in disputes if not met]
    for label, group in (("A checker disputed", against), ("A checker thinks you may meet", for_you)):
        if group:
            parts = [f"“{_clip(c, 60)}” ({_clip(issue).rstrip('.')})" for c, issue in group[:2]]
            more = f" and {len(group) - 2} more" if len(group) > 2 else ""
            msg += f" {label} " + "; ".join(parts) + more + "."
    if not disputes and suggested and FIT_ORDER.index(suggested) < FIT_ORDER.index(proposed):
        msg += f" A checker rated the fit “{suggested}”, not “{proposed}”."
    return msg


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
                   config.RUBRIC_VERSION, config.CHECKS_VERSION, config.MIN_EVIDENCE_QUOTE_CHARS, self.llm.name,
                   models, config.MAX_LLM_CANDIDATES)[:20]

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
                # Count items as they finish (WP8): with map() the bar sat at 0/N while the first item was slow.
                # Order does not matter here: results and exclusions are sorted below.
                for fut in as_completed([pool.submit(work, d) for d in shortlist]):
                    out = fut.result()
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
        except Exception as e:     # noqa: BLE001 - one item must never end the whole run
            # Fail safe: never lose an eligible scholarship because an agent call failed. Anything other than an
            # LLMError is a bug (or, in development, Streamlit re-importing a module mid-run, WP8); it gets its own
            # audit event so it is not mistaken for a provider outage.
            event = "llm_failure" if isinstance(e, LLMError) else "item_error"
            self._audit("supervisor", event, {"key": s.key, "error": f"{type(e).__name__}: {e}"[:300]})
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
        text, feedback, v = s.full_text(), "", None
        for round_no in range(config.MAX_SUPERVISOR_ROUNDS):
            v = workers.run_filter(self.llm, s, c, view, feedback)
            if v.verdict != "drop":
                return v
            problem = drop_problem(v.scholarship_quote, text)
            if not problem:
                return v
            self._audit("filter", "rejected", {"key": s.key, "round": round_no + 1, "problem": problem,
                                               "quote": v.scholarship_quote[:200]})
            feedback = (f"Your 'drop' verdict was rejected: {problem} "
                        "Quote the exact sentence that states the requirement, or change the verdict to 'uncertain'.")
        # fail open: an unsupported 'drop' never hides a scholarship
        return FilterVerdict(verdict="uncertain", scholarship_quote="",
                             reason=f"Screening agent raised a concern it could not substantiate: {v.reason}")

    def _supervised_score(self, s: Scholarship, c: Criteria, view: dict) -> tuple[ScoreJudgement, list[str]]:
        text, profile_text = s.full_text(), norm(dumps(view))
        titles = {e["title"] for e in view.get("essay_themes", [])}
        feedback, j, problems, objections, ver = "", None, [], [], None
        for round_no in range(config.MAX_SUPERVISOR_ROUNDS):
            j = workers.run_scorer(self.llm, s, c, view, feedback)
            problems = self._score_problems(j, text, profile_text, titles)
            ver, objections = None, []
            if not problems and j.fit_evidence:
                ver = workers.run_verifier(self.llm, s, view, j)
                objections = self._objections(s.key, ver, j, text, profile_text)
                problems = [f"claim {i} ({j.fit_evidence[i].criterion!r}): {issue}" + (f' The text shows: "{q}".' if q else "")
                            for i, issue, q in objections]
                if not ver.fit_rating_justified:
                    problems.append(f"fit '{j.fit}' is not justified by the evidence; verifier suggests '{ver.suggested_fit}'.")
            if not problems:
                self._audit("scorer", "accepted", {"key": s.key, "round": round_no + 1, "fit": j.fit})
                return j, []
            self._audit("scorer", "rejected", {"key": s.key, "round": round_no + 1, "fit": j.fit, "problems": problems})
            feedback = "\n".join(f"- {x}" for x in problems)
        # Rounds exhausted: keep only evidence that is mechanically grounded AND that the Verifier did not dispute in
        # its last check, and take the most conservative rating.
        disputed = {i for i, _, _ in objections}
        criteria = [e.criterion for e in j.fit_evidence]
        claim_met = [e.met for e in j.fit_evidence]            # the Scorer's own call, before any evidence is dropped
        kept = [e for i, e in enumerate(j.fit_evidence)
                if i not in disputed and is_grounded(e.scholarship_quote, text, config.MIN_EVIDENCE_QUOTE_CHARS)
                and (not e.met or norm(e.student_evidence) in profile_text)]
        met = sum(e.met for e in kept)
        ceiling = "strong" if met >= 2 else "moderate" if met == 1 else "weak"
        proposed = j.fit
        fit = min(j.fit, ceiling, key=FIT_ORDER.index)
        if ver is not None:
            fit = min(fit, ver.suggested_fit, key=FIT_ORDER.index)
        j = j.model_copy(update={"fit": fit, "fit_evidence": kept,
                                 "reusable_essay_title": j.reusable_essay_title if j.reusable_essay_title in titles else ""})
        self._audit("scorer", "downgraded", {"key": s.key, "from": proposed, "to": fit, "disputed": sorted(disputed)})
        disputes = [(criteria[i], issue, claim_met[i]) for i, issue, _ in objections]
        return j, [low_confidence_flag(disputes, proposed, ver.suggested_fit if ver is not None else None)]

    def _objections(self, key: str, ver: Verification, j: ScoreJudgement, text: str,
                    profile_text: str) -> list[tuple[int, str, str]]:
        """The Verifier's rejections as (claim index, issue, counter_quote). A rejection that cites text which is in
        neither the listing nor the profile is itself unsupported: it is logged and does not count."""
        out = []
        for ch in ver.checks:
            if ch.supported or not 0 <= ch.index < len(j.fit_evidence):
                continue
            q = ch.counter_quote.strip()
            if q and not (is_grounded(q, text) or is_grounded(q, profile_text)):
                self._audit("verifier", "unsubstantiated", {"key": key, "claim": ch.index, "counter_quote": q[:200]})
                continue
            out.append((ch.index, ch.issue.strip() or "not supported by the listing or profile", q))
        return out

    @staticmethod
    def _score_problems(j: ScoreJudgement, text: str, profile_text: str, titles: set[str]) -> list[str]:
        probs = []
        for i, e in enumerate(j.fit_evidence):
            if not is_grounded(e.scholarship_quote, text, config.MIN_EVIDENCE_QUOTE_CHARS):
                probs.append(f"claim {i}: scholarship_quote is not a verbatim span of the listing of at least "
                             f"{config.MIN_EVIDENCE_QUOTE_CHARS} characters.")
            elif not shares_word(e.criterion, e.scholarship_quote):
                probs.append(f"claim {i}: criterion {e.criterion!r} does not appear in its scholarship_quote; "
                             "name the criterion in the listing's own words and quote where the listing asks for it.")
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
