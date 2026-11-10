# ScholarMatch — Requirements (as built)

This refines the 16 requirements in `final_project_reqs_and_arch_jonahs23.qmd` (left untouched — it is
the graded, no-AI original). **Origin** shows where each came from: `R#` = original requirement,
`changed` = original tightened or corrected, `new` = added during the build. **Where** points at the code.

Goals, in priority order: (1) show scholarships the student can actually win, (2) a short list instead
of dozens to dig through, (3) the same inputs give the same answer, (4) every decision is explainable.

## A. Catalog (scraping and ingest)

| # | Requirement | Origin | Where |
|---|---|---|---|
| A1 | Collect scholarships from the sources in `scraper-brief.md` into one shared schema (source, id, name, url, amount raw/min/max, deadline raw/ISO, open date, status, description, eligibility, sponsor, geo_scope, sources, confidence, raw_text). | R1, R4 | `models.Scholarship`, `scrapers/` |
| A2 | Scraping must be polite and lawful: re-check robots.txt each run, ≤1 request / 2 s per host (10 s where robots asks), descriptive User-Agent with contact, 3 retries with backoff, disk cache, **stop on 403/challenge — never work around it**, never log in. | new (from brief) | `scrapers/http.py` |
| A3 | A source whose terms forbid automation, or that cannot be verified, is not scraped; the reason is printed and a hand-entered CSV path exists instead. | new | `scrapers/pending.py`, `scrapers/manual.py` |
| A4 | Ended / past-deadline scholarships are never shown but are kept in the database. Rows are never deleted. | R11 | `db.upsert_scholarships`, `hard_filter.sql_stage` |
| A5 | Scam and spam listings (fees, sweepstakes, gambling promotion, payment-detail requests) are blocked at ingest by deterministic rules; pre-vetted sources can be flagged but not blocked. | R7, changed | `ingest/scam.py` |
| A6 | Extract hard criteria, soft criteria and application requirements (essays, recommendations, transcript, interview…) for every scholarship. A free rule-based pass runs on everything at ingest; the LLM Extractor runs lazily, only on scholarships that reach a student's shortlist, and is cached by content hash. | R8, changed | `ingest/extract_rules.py`, `agents/workers.py`, `Supervisor._llm_criteria` |
| A7 | "Preference given to…" is a soft criterion, never a hard filter. | new | `extract_rules._PREF`, extractor prompt |
| A8 | Hand-edited sources keep their raw text; stale or uncertain rows are flagged low-confidence, not dropped. Duplicates across local lists merge and keep every source URL. | new (from brief) | `scrapers/gsites.py` |

## B. Student profile

| # | Requirement | Origin | Where |
|---|---|---|---|
| B1 | A form collects residency, level, GPA, schools, majors, Pell/need, activities, interests, prior awards, optional demographics, and uploaded past essays (.txt/.pdf). | R2 | `app.page_profile` |
| B2 | Profiles are stored locally, editable later, and fully deletable by the student. | R6, changed | `db.save_profile`, `db.delete_profile` |
| B3 | **Data minimisation:** name, contact, county/city/high school and protected demographics (gender, race, disability) are used only by local deterministic checks and are never sent to an LLM. Essays are reduced once to PII-stripped themes; only themes reach matching agents. | new | `StudentProfile.redacted_view`, `textutil.redact_pii`, test `test_no_pii_reaches_agents` |
| B4 | Student preferences are honoured: no-essay only, minimum award. | Intro ("no-essay") | `hard_filter.preference_exclusion` |

## C. Matching

| # | Requirement | Origin | Where |
|---|---|---|---|
| C1 | Filter deterministically (SQL, then rule checks) before any LLM sees a scholarship. | R15, R5 | `matching/hard_filter.py` |
| C2 | Three-valued eligibility: only a definite **fail** removes a scholarship. **Unknown** (missing profile data or unstated rule) keeps it and raises a "Verify" flag. | new | `hard_filter.check` |
| C3 | A multi-agent pipeline reviews the shortlist: Extractor → Filter → Scorer → Verifier, gated by a Supervisor. One scholarship per agent call. | Architecture | `agents/` |
| C4 | Agents output categories and verbatim evidence only. All numbers (score, estimated chance, effort hours, expected value) are computed by code from a fixed rubric. | R9, R10, changed | `matching/rubric.py` |
| C5 | Every recommendation carries a 5-line rubric (eligibility certainty 25, profile fit 35, competition 20, effort 10, award value 10) with a plain-language reason per line. | R16 | `rubric.build_result` |
| C6 | "Ease" and "chance" reflect number of awards, pool scope (department / school / local / state / multi-state / national), narrowing criteria and application requirements. The chance is labelled an estimate, never a statistic. | R9, changed | `rubric.POOLS`, UI captions |
| C7 | Grounding gate: any quote an agent cites must appear verbatim in the listing; any student evidence must appear in the profile. Unsupported claims are rejected by the Supervisor. | R10 | `textutil.is_grounded`, `Supervisor._score_problems` |
| C8 | Bounded supervision: at most 2 rounds per worker, then fail safe — keep the scholarship, take the more conservative rating, flag low confidence. An unsupported "drop" never hides a scholarship. | new | `Supervisor._supervised_*` |
| C9 | **Consistency:** a run is fingerprinted by (profile, catalog version, date, rule/prompt/rubric versions, models). Same fingerprint → the stored run is returned. Otherwise every agent call is served from a response cache when its inputs are unchanged. Ordering ties break on name then key. | R10 ("consistent"), changed | `Supervisor.fingerprint`, `llm.LLM.structured` |
| C10 | Cost cap: at most `SM_MAX_LLM_CANDIDATES` (30) scholarships per run reach the agents, chosen by a deterministic pre-score; the rest are listed as "ranked below the cut". | new | `Supervisor.run` |
| C11 | If an agent call fails, that scholarship falls back to its rule-based score with a flag. With no LLM configured the whole product runs in rule-based mode. | new | `Supervisor._evaluate`, `llm.OfflineLLM` |
| C12 | Listing text is untrusted: prompts tell agents to treat it as data and ignore embedded instructions. | new | `workers._DATA_RULE` |

## D. Student experience

| # | Requirement | Origin | Where |
|---|---|---|---|
| D1 | Show a short ranked list (default top 15) with name, source, award, open/close dates, match score, estimated chance, effort and expected value. | R4, goal 2 | `app.page_matches` |
| D2 | Student-controlled ordering: best match, highest/lowest chance, easiest/hardest, largest/smallest award, highest/lowest expected value, soonest deadline. | R14 | `rubric.SORTS` |
| D3 | Explicit notification when nothing matches, with the counts of why and what to try. | R13 | `app.page_matches` |
| D4 | "Why not the others?" — every exclusion has a stage and a reason. | R10 (auditable) | `MatchRun.exclusions`, `app.show_exclusions` |
| D5 | Save scholarships, track status and notes, retrieve later; saved items show if they have since closed. | R3 | `app.page_saved` |
| D6 | Follow-up chat about a selected scholarship, grounded in the listing + stored rubric, persisted, with compaction of long conversations. It must not invent facts or write the student's essays. | R12 | `agents/chat.py` |
| D7 | An audit page shows catalog stats and the Supervisor's accept/reject log for the latest run. | R10 | `app.page_data`, `db.audit` |

## Out of scope
Filling out or submitting applications; accounts/authentication (profiles are local handles); states beyond
VA/NC/SC; calibrated win probabilities (no applicant-count data exists); sources listed as not automated in
`scrapers/pending.py`.

## What changed from the original list, and why
- **R9/R10 (LLM scores and probability)** → the LLM no longer produces numbers. Asking a model for a score
  or a probability is where run-to-run drift comes from; categorical judgement + code-side math is stable
  and auditable (C4–C6).
- **R8 (extract criteria)** → two tiers, lazy LLM (A6). Thousands of school-portal awards never reach any
  student's shortlist; paying to extract them all is waste.
- **R7 (scam)** → rules, not an LLM (A5): cheap, consistent, and nothing risky is ever sent to a model.
- **Supervisor** → code + an independent Verifier agent rather than an LLM manager (see architecture.md).
- **Added** privacy minimisation (B3), unknown ≠ fail (C2), fail-safe behaviour (C8, C11), cost cap (C10),
  prompt-injection handling (C12), lawful-scraping rules (A2, A3).
