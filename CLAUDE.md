# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
Keep it current when behaviour, commands or invariants change.

## What this is
ScholarMatch: a scholarship matcher for VA/NC/SC students. Python 3.12+, two UIs (Streamlit `app.py`; a React + TypeScript web
app in `frontend/`) over one FastAPI HTTP API, SQLite, a supervised multi-agent LLM pipeline. Course final project (ADSAI).
Specs: `docs/requirements.md` (numbered A1–D7), `docs/architecture.md`. Scraping rules: `scraper-brief.md`.

## Commands
- Install: `pip install -r requirements.txt`
- Tests: `python -m pytest -q` (no network or key needed; must stay green)
  - One test: `python -m pytest -q tests/test_pipeline.py::test_no_pii_reaches_agents` (or `-k <substring>`)
  - `test_units.py` = parsers, rules, rubric; `test_pipeline.py` = Supervisor end-to-end with `FakeLLM`;
    `test_regression.py` = manual-test awards × profiles through `extract_rules` + `hard_filter` (strict xfails per WP);
    `test_api.py` = every API route through FastAPI's `TestClient` (`create_app(llm=..., as_of=...)`)
- False-positive eval (rule-based, offline): `python scripts/eval_profiles.py` (local catalog), `--db PATH`,
  `--corpus` (regression corpus only), `--show`, `--as-of DATE` (default pinned to 2026-11-12 so numbers compare
  across sessions; deadlines are judged against it). Works on a temp copy; never writes to the catalog.
- Agents vs baselines (design review, principle 10): `python scripts/eval_agents.py` runs `rules`, `single` (one
  unchecked LLM call per item, `agents/baseline.py`, eval-only) and `pipeline` on the regression corpus and reports
  ineligible-shown, eligible-lost, flagged, calls/tokens, agent drops judged against the labels, and flips (where a
  Supervisor check changed an answer, toward or away from the label). `--modes`, `--profiles`, `--work-db PATH`
  (reuse the cache), `--db`, `--json`. Uses the provider in `.env`: a full live run on VT ARC takes hours; set
  `SM_LLM_PROVIDER=offline` for the rules-only numbers.
- Refresh the regression corpus from a real catalog: `python scripts/export_regression.py --db PATH [--dry-run]`
- App: `streamlit run app.py`, port 8517. Launch configs in `.claude/launch.json`: `scholarmatch` (Windows,
  `python` on PATH) and `scholarmatch-mac` (`.venv/bin/python`, since `python` is not on PATH on the Mac).
- API: `python -m scholarmatch.api` → `http://127.0.0.1:8000` (`/docs`, `/openapi.json`); launch config
  `scholarmatch-api`. `SM_API_HOST`, `SM_API_PORT`, `SM_API_CORS_ORIGINS` (default the Vite dev server, :5173).
- Web app: `cd frontend && npm install && npm run dev` → http://localhost:5173 (proxies `/api` to :8000; launch config
  `scholarmatch-web`). `npm run build` type-checks and builds. After changing an API route or schema:
  `python scripts/export_openapi.py`, then `npm run gen:api` (regenerates `src/api/schema.d.ts`), then `npm run typecheck`.
  TypeScript stays on 5.x: `openapi-typescript` needs the JS compiler API that TypeScript 7 dropped.
- Sample catalog, offline: `python scripts/load_sample.py`
- Scrape: `python -m scholarmatch.scrapers.runner --sources vt,ecu,sc_che,gsites,manual` (needs `SCRAPER_CONTACT_EMAIL`)
  - Sources: `vt`, `usc`, `ecu` (`scrapers/academicworks.py`), `sc_che` (`scrapers/state_aid.py`), `gsites`,
    `manual`. USC is large: use
    `--max-pages N` and re-run (pages cache under `data/http_cache/`; `--no-cache` to refetch).
  - AcademicWorks only: `--max-details N` caps detail pages per school (rows beyond the cap stay title-only);
    `--include-ended` also fetches detail pages of ended awards.
  - Output: `data/scholarmatch.db`, `data/export/<source>.jsonl`, `data/export/combined.csv`.
- No linter, formatter or build step is configured.
- `data/` and `.env` are gitignored: a fresh clone has no catalog until `load_sample.py` or a scrape is run.
- Config comes from `.env` (python-dotenv): `ANTHROPIC_API_KEY`, `SM_MODEL_<ROLE>`, `SM_LLM_PROVIDER=openai_compat`
  + `SM_OPENAI_*`, `SM_DB_PATH`, `SM_DATA_DIR`, `SM_MAX_LLM_CANDIDATES` (30), `SM_MAX_RESULTS` (15),
  `SM_LLM_WORKERS` (6, parallel scholarships per run), `SM_LLM_MAX_INFLIGHT` (6, provider calls at once across all
  runs). See `scholarmatch/config.py`.
  Provider choice (`config.llm_provider`): explicit `SM_LLM_PROVIDER` wins, then an Anthropic key, then
  `SM_OPENAI_BASE_URL`, else offline. So with both keys in `.env`, Anthropic is used unless the provider is set.
  The README mentions a `.env.example`, but no such file exists.

## Pipeline (big picture)
Ingest: scraper → `ingest/supplements.py` (hand-typed PDF rules from `manual_sources/supplements/<source>.csv`
merged into `eligibility`) → `ingest/scam.py` (scam screen) + `ingest/extract_rules.py` (regex criteria) →
`db.upsert_scholarships` (keyed by content hash). All in `scrapers/runner.ingest`.
Match: `Supervisor.run(profile)` in `agents/supervisor.py`:
1. `fingerprint` (profile, catalog version, date, all version constants, provider, per-role models, candidate cap) → if a stored `MatchRun` matches, it is
   returned unchanged (`force=True` skips this). Agent responses are also cached in the DB by prompt, so a
   forced re-run with unchanged prompts makes no new LLM calls.
2. SQL filter (scam, deadline, state/school, loans, GPA) → `hard_filter.check` pass/fail/unknown → pre-rank → cap at `SM_MAX_LLM_CANDIDATES`.
3. For each shortlisted scholarship (in parallel across scholarships): Extractor (criteria + quotes, grounded) → rule re-check →
   Filter (keep/drop/uncertain; a drop must quote the listing) → Scorer (fit category + evidence) → Verifier
   (tries to disprove each claim, never sees the Scorer's reasoning, runs on a different model by default). Each loop
   is at most `MAX_SUPERVISOR_ROUNDS` rounds.
   LLM criteria are cached per row under `PROMPT_VERSION:RULES_VERSION:model` (they carry rule-extracted fields).
4. `rubric.py` turns categories into points, chance, effort and expected value → saves the `MatchRun` and audit events to the DB.
Front ends: `app.py` (Streamlit) and `scholarmatch/api/` (FastAPI) call the same backend functions; shared input and
display logic lives in `profile_io.py` (essay reading, list parsing, `add_essays`) and `run_summary.py`
(`review_mode_label`, `for_display`, `needs_verification`, `exclusion_counts`), never in a front end. The API runs
`Supervisor.run` as an in-memory background job (`api/jobs.JobRunner`) and streams its progress callback as SSE.
Chat (`agents/chat.py`) answers follow-ups on a single scholarship from the listing, the stored rubric and a compacted history.
Full sequence diagram: `docs/architecture.md`.

## Do not touch
- `final_project_reqs_and_arch_jonahs23.qmd` / `.pdf`: the graded original, which must be the student's own
  work with no AI assistance. Never edit it. Requirement or design changes go in `docs/`.
- `scraper-brief.md`: the user's source list and rules. Read it; do not rewrite it.

## Invariants (tests enforce most of these — do not weaken them)
1. **Deterministic first.** Eligibility is decided in `matching/hard_filter.py`. No LLM call may happen
   before the SQL stage and rule checks.
2. **Agents never output numbers.** Scores, chance, effort, expected value come only from
   `matching/rubric.py`. Changing weights or tiers → bump `config.RUBRIC_VERSION`.
3. **Grounding gate.** Every agent quote is checked with `textutil.is_grounded` against
   `Scholarship.full_text()`; student evidence must appear in the redacted profile. Do not bypass.
   An Extractor value for a field in `workers.HARD_FIELDS` counts only when its quote is grounded; `to_criteria`
   then keeps the rule value when the model gave none (lists: union). New hard `Criteria` fields go in `HARD_FIELDS`.
4. **Unknown is not fail.** Only a definite fail excludes; unknown keeps the item and adds a "Verify" flag.
   Preference language ("preference given to") is soft, never hard.
5. **Fail safe, fail open.** Supervisor loops are capped at `MAX_SUPERVISOR_ROUNDS`. On exhaustion or an
   `LLMError`: keep the scholarship, take the conservative rating, add a flag. Never hide an eligible award
   because an agent misbehaved.
6. **Privacy.** Agents only ever receive `StudentProfile.redacted_view()`. Name, contact, county/city/high
   school, gender, ethnicity, disability stay local. Essay titles stay local too (agents get "Essay N"); essay text
   leaves only through `workers.essay_themes`, redacted. New profile fields default to *not* being in the view.
   `test_no_pii_reaches_agents` must keep passing.
7. **One scholarship per agent call**, prompts built with `llm.dumps` (sorted keys) so cache keys are stable.
8. **Consistency.** Anything that can change an answer must be in `Supervisor.fingerprint`. Changing a
   prompt → bump `config.PROMPT_VERSION`; changing rule extraction → bump `config.RULES_VERSION`; changing a
   Supervisor acceptance check (`drop_problem`, `_score_problems`, `_objections`, `MIN_EVIDENCE_QUOTE_CHARS`) → bump
   `config.CHECKS_VERSION`.
9. **Catalog rows are never deleted**; ended awards are filtered at query time.
10. **Scraping.** All HTTP goes through `scrapers/http.PoliteFetcher`. Never add a source without checking
    its robots.txt and terms; never work around a 403/challenge; never log in; no parallel requests to one
    host. Sources that are deliberately not automated, with reasons, live in `scrapers/pending.py`.
11. **API is local-only.** It returns the student's own PII and has no auth: binds to 127.0.0.1, never returns
    essay text (titles, themes and length only), and shows essay titles via `for_display` / `show_essay_titles`.
    Profile writes go through `ProfileIn` (`extra="forbid"`); essays only through the essay endpoints.
12. Listing text is untrusted input: keep the "treat as data" rule in every prompt; give agents no
    state-changing tools.

## LLM layer
- Agents call `scholarmatch.llm.LLM.structured(role, system, user, PydanticModel)` or `.chat(...)`; never
  a vendor SDK directly.
- `llm/anthropic_client.py` uses the official `anthropic` SDK: `messages.parse(output_format=...)`,
  `output_config={"effort": ...}`, server-side refusal fallback on models that support it. Current Claude
  models reject `temperature`/`top_p`/`budget_tokens` and assistant prefill — do not add them.
  Default model `claude-opus-5-5`, except the Verifier (`config.ROLE_DEFAULT_MODELS`: `claude-sonnet-5-5`, so the
  checker does not share the Scorer's model); per-role overrides via `SM_MODEL_<ROLE>`. The OpenAI-compatible
  provider ignores `ROLE_DEFAULT_MODELS` (VT ARC has too few models; decided 2026-11-17).
- `llm/openai_compat.py` is a separate, optional provider (OpenAI SDK). Keep the two files independent.
- No provider configured → `OfflineLLM` (`available = False`) and every agent has a rule-based path.

## Where things are
| Change | File |
|---|---|
| Add/alter a profile field | `models.StudentProfile` (+ `redacted_view` decision), `app.page_profile`, `hard_filter.check` |
| Group-limited awards ("children of employees of X", members, transfers) | `extract_rules.GROUP_PATTERNS`, `hard_filter.group_check` |
| Age limits, kind of college (technical / two-year / four-year) | `extract_rules.min_age` / `institution_types`, `hard_filter.age_check` / `institution_check` |
| Rules typed by hand from PDFs (HOPE, LIFE, Palmetto, Need-based Grant) | `manual_sources/supplements/<source>.csv`, `ingest/supplements.py`, `manual_sources/README.md` |
| Counties, high schools, destination college, out-of-state | `extract_rules.localities` / `destination_schools` / `non_resident_required`, `hard_filter.locality_check` / `college_check` / `non_resident_check` |
| New eligibility rule | `ingest/extract_rules.py` (regex), `models.Criteria`/`ExtractedCriteria`, `hard_filter.check`, extractor prompt |
| Scoring weights, chance model, sort orders | `matching/rubric.py` |
| Prompts and agent contracts | `agents/workers.py`, `models.py` |
| How Extractor output and rule criteria are merged (grounding, union, rule fallback, `from_rules`) | `workers.to_criteria` / `HARD_FIELDS` |
| Supervisor checks / retry policy | `agents/supervisor.py` |
| API route or response shape | `scholarmatch/api/app.py`, `api/schemas.py`, `tests/test_api.py`; logic both front ends need goes in `profile_io.py` / `run_summary.py`; then regenerate the web app's types |
| Web app page or component | `frontend/src/pages/`, `frontend/src/components/`; routes in `frontend/src/main.tsx` |
| New scraper | new module in `scrapers/`, register in `scrapers/runner.py`, add a trimmed real fixture + parser test |

## Testing notes
- `tests/conftest.py`: `tmp_db` (autouse) points `config.DB_PATH` at a temp file; `FakeLLM` scripts agent
  outputs, including hallucinated evidence and ungrounded drops. Its catalog is synthetic.
- The LLM response cache lives in the DB. Two `FakeLLM`s in one test share it — use separate tests when a
  scenario needs different scripted answers for the same prompt.
- `tests/fixtures/*.html` are trimmed real pages (fetched 2026-11-10). Regenerate when a site's markup changes.
- Regression corpus: `tests/fixtures/regression_listings.jsonl` (9 real SC CHE + 38 real VT rows,
  refreshed 2026-11-14, + 20 rows with `extra.synthetic = true` for ECU / USC / Charlotte awards not in the Windows
  catalog, text written from the test notes) and `tests/regression_profiles.py` (the seven
  made-up test students plus the control `sam`). Each line has sibling keys `expect` / `ineligible_for`, stripped by
  `load_corpus`. Known-wrong cases are listed in `XFAIL` in `tests/test_regression.py` by WP; a WP that fixes one
  must delete its entry (strict). Details: `docs/fix_plan.md` → "WP0 notes".

## Current state (update as work lands)
Fix plan WP0–WP8 are done (WP6 apart from the supplement values). History, dated live-run findings and catalog
snapshots are in `docs/fix_plan.md` ("WPn notes" and "After the plan"); read it before changing a rule below.
- Tests: 252 collected = 248 pass + 4 strict xfail ("WP6-supplement"; they wait on the user's PDF values).
- Versions: `rules-7`, `p5`, `rubric-6`, `checks-2`.
- Catalog: sizes differ by machine and change with each scrape; check `GET /api/catalog/stats` or the Data & audit page.
- Behaviour that is easy to miss when reading one file:
  - **Unknown amounts:** "$0.00" means unknown (`amount_value=None`, "Varies", plus a flag). It is not excluded.
  - **Certainty** (`rubric.info_level`): `none` (title only, or under 40 chars) caps eligibility certainty at 30%;
    `thin` (no field in `rubric.HARD_CRITERIA_FIELDS`) caps it at 60%. A `HardCheck.weight="restrictive"` unknown
    counts 0, caps certainty at 30% and multiplies the chance by 0.25, so group-limited awards rank below open ones.
    New hard `Criteria` fields go in both `workers.HARD_FIELDS` and `rubric.HARD_CRITERIA_FIELDS`.
  - **Rule input:** `extract` reads `Scholarship.full_text(questions=False)`; supplemental questions are never
    eligibility rules (they still count for essays/uploads). In "…, with preference to X" the clause before the marker
    stays hard (`_PREF_SPLIT`).
  - **Restrictive checks** fail only on a definite mismatch, otherwise give a restrictive unknown: specific group
    (`restricted_groups` vs `affiliations` / `no_special_affiliations`, `military_affiliation`), locality (only behind
    restrictive wording, never from preference sentences), college attending (`destination_schools`), residency
    (`non_resident_required`), age (`min_age` vs the optional local `age`), type of college (`institution_types`).
    A sentence offering an "or" alternative gives an unknown. Check details never repeat the student's affiliations,
    county, city or high school.
  - **Levels and majors:** graduate/professional program wording sets `levels` to graduate
    (`extract_rules.GRADUATE_PROGRAMS`). A college or department name alone sets `majors_from_unit`, so only a direct
    field hit passes. `declared_major_required` turns a high-school senior's pass into an unknown. "Enhancement"
    sentences are ignored for majors.
  - **Supervisor checks** (`checks-2`): Scorer evidence and Filter drop quotes need ≥ `MIN_EVIDENCE_QUOTE_CHARS` (20)
    characters; each evidence criterion must share a content word with its quote (`supervisor.shares_word`); a drop
    quote must contain requirement wording (`supervisor._REQUIREMENT_WORDING`), a held-out check deliberately kept out
    of every prompt. These can only retry, turn a drop into "uncertain" or lower a fit; none can hide a scholarship.
  - **Verifier:** asked to disprove each claim, not told another agent wrote them. A rejection needs a `counter_quote`
    from the listing or profile, else it is ignored (`verifier` / `unsubstantiated`). When rounds run out, disputed
    evidence is dropped and the "Low confidence" flag names up to two disputed criteria.
  - **Essays:** agents see only "Essay 1", "Essay 2" (`models.essay_label`); the app restores real titles for display
    (`StudentProfile.show_essay_titles`). `workers.essay_themes` removes the full name, county, city and high school
    as whole phrases (`textutil.redact_pii(..., places=...)`); other names in an essay are still sent.
  - **Run header:** `run_summary.review_mode_label(run)` gives "AI-reviewed (N of M)", "rule-based", "rule-based
    (AI review unavailable)", or "AI review on (nothing passed the rule checks to review)".
  - **LLM concurrency:** every provider call goes through one process-wide semaphore (`llm._inflight()`,
    `SM_LLM_MAX_INFLIGHT`). `Supervisor._evaluate` turns *any* per-item exception into the rule-based fallback
    (`llm_failure` for an `LLMError`, else `item_error`), so one item can never end a run.
  - **Forms:** `ingest/scam.is_form` tags survey / release forms (`aid_type = "form"`); the SQL stage drops them.
- Supplements (decision D2): `manual_sources/supplements/sc_che.csv` holds only `TODO(user)` placeholders, which are
  ignored. `manual.load_all` does not read that subfolder.
- Live check 2026-11-18 (VT ARC, `SM_MAX_LLM_CANDIDATES=5`, catalog copy): Streamlit in a browser and the API each ran
  essay upload, a match run (16-17 calls, about 5 min), save, chat and audit. One Verifier invalid-JSON `llm_failure`
  fell back as designed. A rubric reason quoted "Essay 1"; `for_display` now maps every agent-worded field.
- Web app (`frontend/`, 2026-11-18): every page checked in a browser against a catalog copy (AI off, plus a slowed
  stand-in model for live progress), incl. a phone-width pass. A run is followed over SSE, picked up again after a page
  reload (job id in sessionStorage) and checked every 15 s by a watchdog, because Vite's dev proxy keeps the stream
  open after the API dies. Pages remount per profile (`<Outlet key={pid}>`). No automated front-end tests yet;
  `npm run build` is the check. The profile form sends only the fields it knows (`EMPTY` in `ProfilePage.tsx`): a new
  `ProfileIn` field also needs adding there and to the form. Live run through the web app (VT ARC, 5-item cap, catalog
  copy, 2026-11-18): essay themes, a 24-call run (about 11 min, no failures), save, chat and audit all worked, and no
  "Essay N" label was shown.
- Web app look (2026-11-18): "Commons · Ink" theme (dark navy + gold, one dark theme; tokens at the top of
  `frontend/src/styles.css`; Fraunces + Inter self-hosted via `@fontsource-variable`, no font CDN). Top nav with a
  profile switcher; new profiles are created on the start page. Matches loads the whole run (`limit=500`) and filters
  client-side (strong fit / due in 30 days / 2 h or less); cards show chance, effort, essays and due date, with the
  rubric, evidence and checks behind "Why this match". Mockups: Claude Design canvas "ScholarMatch Redesign Concepts".
- Offline essay themes (`workers.essay_themes` without a provider) now come from the redacted text, placeholders dropped; before
  2026-11-18 they could contain the student's name or county. Profiles saved offline before then may still carry such
  themes (none in the Windows catalog).
- Live LLM: VT ARC (`openai_compat`) works but is slow (30-item run 10–15 min; Extractor 20–60 s per answer) and
  returns 429 above 10 calls in flight per user. Timeouts are not retried; 429 / 5xx back off 5/10/20 s
  (`openai_compat.call_with_backoff`, `SM_OPENAI_TIMEOUT` 300 s). After any live run, check the **Data & audit** page
  (checked after a live run, 2026-11-18) for `rejected` / `llm_failure` / `item_error` events.
- Not built: BigFuture (terms unconfirmed), CareerOneStop (needs the user's API credentials), embedded Google Docs on
  counselor pages, PDF rule extraction (replaced by the hand-typed supplement).

## Still to fix
The fix plan (`docs/fix_plan.md`) is finished; the open items below come from its "WP8 notes". Notes from the manual
tests: `docs/manual_test_2026-11-1*.md`. On the real catalog the rule-based top tens still hold 8 known-ineligible awards,
all "Verify" items D1 keeps visible (group limits, SC CHE programs for profiles with under ten matches).
1. **LLM merge of `levels`:** the Extractor adding `undergrad` re-admits current students the rules excluded
   ("incoming undergraduate", Merryman). The WP7 union cannot remove a wrong widening; needs a policy decision.
2. **Small rule gaps:** VTCSOM (MD) awards pass for vet-med students; an affiliation passes a group check only on a
   distinctive shared word (a staff-parent affiliation does not pass "dependents of … staff or faculty").
3. **Anthropic provider** never run live, so the Verifier's different default model is untested too.
4. **Agents vs baselines not yet measured live:** `scripts/eval_agents.py` exists and is tested with `FakeLLM`; the
   `single` and `pipeline` numbers need a live provider run (rules-only on the corpus, 2026-11-17: 64 shown,
   21 known-ineligible shown, 17 in top tens, 0 eligible lost).
5. **Waiting on the user:** the HOPE, LIFE, Palmetto Fellows and Need-based Grant citizenship (and maybe GPA) rules
   exist only in the PDFs. The user types them into `manual_sources/supplements/sc_che.csv` (read
   `manual_sources/README.md` first). Then re-ingest and delete the 4 "WP6-supplement" `XFAIL` entries that turn
   into XPASS.

## Conventions
- Windows dev box (PowerShell primary; Git Bash available). Use forward-slash paths in code via `pathlib`.
- Keep modules small and typed; pydantic models are the contracts between stages.
- User-facing text: plain language, no promises about winning; the chance is always called an estimate.
