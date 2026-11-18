"""Does the supervised multi-agent pipeline beat simpler set-ups? ("10 principles", principle 10.)

    python scripts/eval_agents.py                          # rules-only; plus both LLM modes if a provider is set
    python scripts/eval_agents.py --modes rules,pipeline   # pick modes: rules, single, pipeline
    python scripts/eval_agents.py --profiles maya,sam      # fewer profiles (a live LLM run is slow on VT ARC)
    python scripts/eval_agents.py --work-db data/eval_agents.db   # keep the DB, so a re-run reuses cached answers
    python scripts/eval_agents.py --db data/scholarmatch.db       # a real catalog instead of the regression corpus

Three modes over the same catalog, profiles and date:
  rules     Supervisor with no LLM (the deterministic stages and the keyword rubric)
  single    one LLM call per shortlisted scholarship, answer used as given (agents/baseline.py)
  pipeline  the real Supervisor: Extractor -> Filter -> Scorer <-> Verifier with every check
The deterministic stages are the same in all three, so differences come from the agent stage only.

Ground truth comes from the regression corpus (tests/fixtures/regression_listings.jsonl): an award is
"ineligible" for a profile when the profile is in `ineligible_for` or `expect[profile].result` is "fail", and
"eligible" when that result is "pass" or "unknown". Awards are matched by normalised name.

Per mode it prints: ineligible awards shown (all / top 10), eligible awards lost (excluded at any stage), items
flagged for the student, LLM calls and tokens. Then, from the audit log of the LLM modes:
  agent drops    how many scholarships an agent removed, and whether the label agrees (right / wrong / no label)
  flips          for every place the Supervisor changed an agent's answer (a rejected drop that became
                 "uncertain", a fit lowered after the rounds ran out) whether it moved toward the label or away
                 from it. Fit ratings have no labels, so fit changes are counted, not judged.
Nothing here is a calibrated benchmark: the corpus is small (67 awards, 8 profiles) and partly synthetic.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scholarmatch import config, db  # noqa: E402
from scholarmatch.agents.baseline import SingleAgentSupervisor  # noqa: E402
from scholarmatch.agents.supervisor import Supervisor  # noqa: E402
from scholarmatch.llm import LLM, OfflineLLM, get_llm  # noqa: E402
from scholarmatch.scrapers.runner import ingest  # noqa: E402
from scholarmatch.textutil import norm_name  # noqa: E402
from tests.regression_profiles import PROFILES, load_corpus  # noqa: E402

DEFAULT_AS_OF = "2026-11-12"   # same pinned date as eval_profiles.py
MODES = ("rules", "single", "pipeline")
_FLAGGED = ("Check —", "Low confidence", "An AI review step failed")


def labels(corpus) -> dict[str, dict[str, str]]:
    """{profile_id: {normalised award name: "eligible" | "ineligible"}}"""
    out: dict[str, dict[str, str]] = {pid: {} for pid in PROFILES}
    for s, expect, inel in corpus:
        n = norm_name(s.name)
        for pid in PROFILES:
            res = expect.get(pid, {}).get("result")
            if pid in inel or res == "fail":
                out[pid][n] = "ineligible"
            elif res in ("pass", "unknown"):
                out[pid][n] = "eligible"
    return out


def judge(label: str | None, kept: bool) -> str:
    """Was keeping (or removing) this award right, according to the label?"""
    if label is None:
        return "no label"
    return "right" if (label == "eligible") == kept else "wrong"


def run_mode(mode: str, llm: LLM, profiles: dict, truth: dict, as_of: str, in_catalog: set[str]) -> dict:
    sup_cls = SingleAgentSupervisor if mode == "single" else Supervisor
    before = dict(llm.usage)
    m = Counter()
    per_profile, kept_sets = {}, {}
    for pid, p in profiles.items():
        sup = sup_cls(llm)
        run = sup.run(p, as_of, force=True)
        lab = truth[pid]
        shown = [norm_name(r.name) for r in run.results]
        eligible = set(shown) | {norm_name(e.name) for e in run.exclusions if e.stage == "cap"}
        kept_sets[pid] = eligible
        row = {
            "shown": len(shown),
            "inel_shown": sum(lab.get(n) == "ineligible" for n in shown),
            "inel_top10": sum(lab.get(n) == "ineligible" for n in shown[:10]),
            "elig_lost": sum(1 for n, v in lab.items() if v == "eligible" and n in in_catalog and n not in eligible),
            "flagged": sum(any(f.startswith(_FLAGGED) for f in r.flags) for r in run.results),
        }
        per_profile[pid] = row
        m.update(row)
        if mode != "rules":
            m.update(audit_stats(run, lab))
    usage = {k: llm.usage[k] - before.get(k, 0) for k in llm.usage}
    return {"mode": mode, "totals": dict(m), "profiles": per_profile, "usage": usage, "kept": kept_sets}


def audit_stats(run, lab: dict[str, str]) -> Counter:
    c = Counter()
    name_of = {r.scholarship_key: norm_name(r.name) for r in run.results}
    name_of.update({e.scholarship_key: norm_name(e.name) for e in run.exclusions})
    for e in run.exclusions:
        if e.stage == "fuzzy_filter":
            c[f"agent drops {judge(lab.get(norm_name(e.name)), kept=False)}"] += 1
    events = db.audit_for(run.run_id)
    first_fit: dict[str, str] = {}
    for a in events:
        d = json.loads(a["detail"]) if a["detail"].startswith("{") else {}
        key = d.get("key", "")
        if a["agent"] == "filter" and a["event"] == "rejected":
            c["filter drops rejected"] += 1
        if a["agent"] == "scorer" and "fit" in d and key not in first_fit:
            first_fit[key] = d["fit"]
        if a["agent"] == "scorer" and a["event"] == "rejected":
            c["scorer rounds rejected"] += 1
        if a["agent"] == "verifier" and a["event"] == "unsubstantiated":
            c["verifier objections without support"] += 1
        if a["event"] in ("llm_failure", "item_error"):
            c["items fell back to rules"] += 1
    # A rejected drop that ended "uncertain" keeps the award: toward the label if it is eligible, away if not.
    rejected_keys = {json.loads(a["detail"]).get("key") for a in events
                     if a["agent"] == "filter" and a["event"] == "rejected"}
    for r in run.results:
        if r.scholarship_key in rejected_keys and r.filter_verdict and r.filter_verdict.verdict == "uncertain":
            verdict = judge(lab.get(name_of[r.scholarship_key]), kept=True)
            c[{"right": "flip toward label", "wrong": "flip away from label"}.get(verdict, "flip, no label")] += 1
        if r.judgement and r.scholarship_key in first_fit and first_fit[r.scholarship_key] != r.judgement.fit:
            c["fit changed by checks"] += 1
    return c


def compare_to_rules(res: dict, rules: dict, truth: dict) -> Counter:
    """Awards the deterministic stages kept but the agent stage removed, judged against the label."""
    c = Counter()
    for pid, kept in rules["kept"].items():
        for n in kept - res["kept"].get(pid, kept):
            c[f"removed vs rules: {judge(truth[pid].get(n), kept=False)}"] += 1
    return c


def main(argv=None, llm: LLM | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modes", default=",".join(MODES), help="comma list of rules, single, pipeline")
    ap.add_argument("--profiles", default="", help=f"comma list from {', '.join(PROFILES)} (default all)")
    ap.add_argument("--db", type=Path, help="a real catalog to copy (default: the regression corpus)")
    ap.add_argument("--work-db", type=Path, help="keep the working DB here; an existing one is reused (with its cache)")
    ap.add_argument("--as-of", default=DEFAULT_AS_OF)
    ap.add_argument("--json", type=Path, help="also write the numbers to this file")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if bad := [m for m in modes if m not in MODES]:
        ap.error(f"unknown mode(s): {', '.join(bad)}")
    profiles = {pid: PROFILES[pid] for pid in (args.profiles.split(",") if args.profiles else PROFILES)}
    llm = llm or get_llm()
    if not llm.available and any(m != "rules" for m in modes):
        print("No LLM provider configured: running the rules mode only (set one up in .env for single/pipeline).")
        modes = [m for m in modes if m == "rules"]
    if "rules" not in modes and len(modes) > 0:
        modes = ["rules"] + modes        # the comparison needs the rules run as its reference

    corpus = load_corpus()
    truth = labels(corpus)
    with tempfile.TemporaryDirectory() as tmp:
        work = args.work_db or Path(tmp) / "eval_agents.db"
        fresh = not work.exists()
        if args.db and fresh:
            src, dst = sqlite3.connect(args.db), sqlite3.connect(work)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
        config.DB_PATH = work
        if fresh and not args.db:
            ingest([s for s, _, _ in corpus])
        in_catalog = {norm_name(s.name) for s in db.all_scholarships()}

        print(f"Catalog: {work} ({len(in_catalog)} awards); as of {args.as_of}; profiles {', '.join(profiles)}")
        print(f"Versions: {config.RULES_VERSION}, {config.PROMPT_VERSION}, {config.RUBRIC_VERSION}, "
              f"{config.CHECKS_VERSION}; provider {llm.name}; scorer {llm.model('scorer')}, "
              f"verifier {llm.model('verifier')}")
        results = {}
        for mode in modes:
            print(f"running {mode}…", flush=True)
            results[mode] = run_mode(mode, llm if mode != "rules" else OfflineLLM(), profiles, truth, args.as_of,
                                     in_catalog)

    cols = ("shown", "inel_shown", "inel_top10", "elig_lost", "flagged")
    print(f"\n{'mode':9} " + " ".join(f"{c:>10}" for c in cols) + f" {'calls':>6} {'tokens':>9}")
    for mode, res in results.items():
        t, u = res["totals"], res["usage"]
        print(f"{mode:9} " + " ".join(f"{t.get(c, 0):10d}" for c in cols) +
              f" {u.get('calls', 0):6d} {u.get('input_tokens', 0) + u.get('output_tokens', 0):9d}")
    for mode, res in results.items():
        if mode == "rules":
            continue
        extra = Counter({k: v for k, v in res["totals"].items() if k not in cols})
        extra.update(compare_to_rules(res, results["rules"], truth))
        print(f"\n{mode}:")
        for k in sorted(extra):
            print(f"  {k:40} {extra[k]}")
    if args.json:
        args.json.write_text(json.dumps({m: {k: v for k, v in r.items() if k != "kept"} for m, r in results.items()},
                                        indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
