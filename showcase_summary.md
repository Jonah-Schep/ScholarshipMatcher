# ScholarMatch: Scholarships You Can Actually Win

Tagline: Rules decide who qualifies, supervised AI agents judge fit, code does the math.

Jonah Schepers, ADS 5984 Agentic AI in Practice, Virginia Tech

## 1. The problem

The user is a high-school or college student in Virginia, North Carolina or South Carolina who needs money for college. Today they dig through thousands of scattered listings, some scams. ScholarMatch takes one profile (state, school, year, GPA, major, activities, need, past essays) and returns a short ranked list of scholarships they qualify for, each with an estimated chance, effort to apply, and a quoted reason for every claim.

## 2. Agent personas

1. Supervisor (code, not an LLM). Owns the run and checks every hand-off. In: student profile and catalog. Out: a stored, ranked match run with an audit log.
2. Extractor. Pulls eligibility rules from one listing. In: listing text only, never the profile. Out: criteria (GPA, residency, level, major, groups, age and more), each with a verbatim quote.
3. Filter. Catches free-text requirements the rules missed. In: listing, criteria, redacted profile. Out: keep, drop or uncertain; a drop must quote the listing.
4. Scorer. Judges fit. In: listing and redacted profile. Out: a fit category (strong, moderate, weak, none), evidence pairs (listing quote plus profile fact) and essay-reuse advice. Never scores.
5. Verifier. Independent fact-checker on its own model. Tries to disprove each claim. In: the claims and the sources, not the Scorer's reasoning. Out: per claim, supported or not, with a contradicting quote; rating justified or not.
6. Chat agent. Answers follow-ups on one scholarship. In: listing, stored rubric, compacted history. Out: a grounded answer. Tools: two read-only lookups.
7. Essay summarizer (helper call). In: an uploaded essay with the student's name, town and school removed. Out: short themes. Agents see only the themes, labelled "Essay 1".

## 3. Knowledge stores and tools

1. SQLite catalog: scraped listings; ended awards kept but hidden. Used by the Supervisor and Chat agent.
2. SQLite run store and audit log: runs, saved scholarships, chats, every agent event.
3. LLM response cache (same database): repeat prompts make no new calls. Used by the four workers.
4. Polite web scrapers: VT, USC and ECU portals, SC Commission on Higher Education, counselor sites, hand-entered CSVs.
5. Rule engine and rubric (code): regex criteria, scam screen, pass/fail/unknown checks, scoring formulas.
6. LLM provider: Anthropic Claude or an OpenAI-compatible endpoint; with none, the app runs rule-based.
7. Front ends: React web app or Streamlit, over a local HTTP API; progress streams live.
8. No vector store, graph database or MCP server is used.

## 4. How the agents interact

Pattern: orchestrator-workers with a deterministic code supervisor, run as a per-scholarship pipeline.

1. The student talks only to the web app or Streamlit app, which passes the profile to the Supervisor.
2. The Supervisor sends each shortlisted scholarship, six at a time in parallel, through Extractor, Filter, Scorer and Verifier.
3. Workers never talk to each other; every output returns to the Supervisor for checking.
4. The Chat agent works later, on stored results only.

## 5. The agentic loop

1. Trigger: the student saves a profile and presses "Find my scholarships".
2. Fingerprint check: identical inputs return the stored result.
3. Deterministic filter: SQL removes scams, past deadlines, wrong states, loans and GPA misses; rule checks mark pass, fail or unknown; the top 30 go on.
4. Extract: an unquoted rule is sent back once, then replaced by the rule-based value; rule checks re-run.
5. Filter: a drop without a quote is retried, then becomes "uncertain" and the scholarship stays.
6. Score and verify (reflection loop): the Scorer rates fit, the Supervisor checks quotes and evidence, the Verifier tries to disprove each claim. Disagreement sends feedback back.
7. Stop: at most 2 rounds per loop. Out of rounds: keep the rule value, turn a drop into "uncertain", or lower the fit, and add a flag naming the objection. An agent error falls back to the rule-based score.
8. Rubric: code turns categories into points, estimated chance, effort hours and expected value.
9. Human in the loop: the student re-sorts, checks each "Verify" flag, saves items and asks the Chat agent.

## 6. Guardrails

1. Deterministic first: no LLM call before SQL and rule checks.
2. Grounding gate: every quote must appear verbatim in the listing; student evidence must appear in the profile. One check is kept out of every prompt.
3. Unknown is not fail: only a definite, quoted mismatch excludes.
4. Agents never output scores or odds; code computes them.
5. Privacy: agents see only a redacted profile; name, contact, location details, demographics and essay file names stay local (test-enforced).
6. Listing text is untrusted data; no agent has a tool that changes anything.
7. Fail safe: capped retries, rule-based fallback per item, every rejection logged.

## 7. Why multi-agent

Each step needs different inputs and failure rules: extraction must not see the student, a drop needs proof, and the fact-checker must not see the Scorer's reasoning. Small calls on one scholarship avoid context rot and let a code supervisor check every hand-off; one big prompt could not be checked or reproduced.

## 8. Example run

1. Request: a Virginia senior, GPA 3.6, planning engineering at Virginia Tech, with financial need and a past essay about robotics club.
2. Rules cut the catalog to 30 candidates, removing graduate-only, out-of-state and expired awards.
3. The agents drop a members-only award (quoting the listing) and rate an engineering award a strong fit, citing the listing and the robotics essay.
4. The student gets up to 15 ranked scholarships with amount, deadline, estimated chance, effort, essay tips and "Verify" flags.
