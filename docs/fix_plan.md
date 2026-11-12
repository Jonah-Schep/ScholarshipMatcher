# Fix plan for the "Still to fix" list

Written 2026-11-12. Covers items 1–11 of **Still to fix** in `CLAUDE.md`. Evidence for each item is in
`docs/manual_test_2026-11-10.md` and `docs/manual_test_2026-11-10_round2.md`.

The work is split into nine work packages (WP0–WP8). Each one is sized for a single Claude session and
ends in one branch and one pull request.

## Status (update this table in the same PR as the work)

| WP | Fixes item(s) | Depends on | Status | Branch / PR | Notes |
|---|---|---|---|---|---|
| WP0 Regression corpus + eval script | groundwork | none | done (2026-11-12) | `fix/still-to-fix` | 42 strict xfails (WP3 12, WP4 6, WP5 12, WP6 12); see "WP0 notes" |
| WP1 UI and launch config | 9, 11 | none | done (2026-11-12) | `fix/still-to-fix` | `rubric-2` (zero-amount flag); header not checked in the running app; see "WP1 notes" |
| WP2 Certainty when information is missing | 2 | WP0 | done (2026-11-12) | `fix/still-to-fix` | `rubric-3`; `HardCheck.weight` + x0.25 chance factor ready for WP3; see "WP2 notes" |
| WP3 Awards limited to a specific group | 1 | WP0, WP2 | done (2026-11-12) | `fix/still-to-fix` | `rules-3`, `rubric-4`; all 12 WP3 xfails fixed, plus 2 SCNG CAP (WP6) ones; Profile form not checked in the running app; see "WP3 notes" |
| WP4 Locality, school and out-of-state limits | 3, 6 | WP3 | done (2026-11-12) | `fix/still-to-fix` | `rules-4`, `rubric-5`; all 6 WP4 xfails fixed; Locality check semantics changed (see "WP4 notes"); remaining xfails WP5 12, WP6 10 |
| WP5 Graduate programs, departments, declared majors | 4, 7, 8 | WP4 | todo | | |
| WP6 SC CHE programs | 5 | WP5 | todo | | |
| WP7 Extractor agent learns the new fields | LLM parity | WP6 | todo | | |
| WP8 VT re-scrape and manual test round 3 | 10, final check | WP7 | todo | | needs network + the user |

Status values: `todo`, `in progress (date)`, `done (date)`, `blocked (reason)`.

WP3–WP7 all edit `extract_rules.py`, `hard_filter.py` and `models.py`, so they run **one at a time, in
order**. WP1 touches only `app.py`, `textutil.py` and `.claude/launch.json` and can run alongside any other WP.

## How to pick up a work package

1. Read `CLAUDE.md` (invariants) and this file. Take the first `todo` WP whose dependencies are `done`.
2. Branch from `main`: `fix/wpN-short-name`. Set the WP to `in progress` in the table.
3. Write the tests listed under the WP first, see them fail, then implement.
4. Run `python -m pytest -q` (must be green) and `python scripts/eval_profiles.py` (exists after WP0). Paste
   the before/after eval counts into the PR description.
5. In the same PR: set the WP to `done`, fill in the Notes column with anything the next session needs, and
   remove or reword the fixed items in `CLAUDE.md` → "Still to fix" and "Current state".
6. If a design choice in this plan turns out wrong, change the plan in the PR and say why. Do not silently diverge.

## Rules that apply to every work package

- **Invariant 4 stays.** A new rule may return `fail` only when the listing states a requirement *and* the
  profile contradicts it. If the profile is silent, the result is `unknown`.
- **Version bumps.** Any change to `extract_rules.py` output → `RULES_VERSION` to the next number (`rules-3`,
  `rules-4`, …). Any change to rubric numbers → `RUBRIC_VERSION`. Any prompt or agent-schema change →
  `PROMPT_VERSION` (only WP7 should need this).
- **New `Criteria` fields must survive LLM mode.** `workers.to_criteria` builds the LLM criteria from the
  Extractor output and drops anything it does not copy. Until WP7, every new field added to `Criteria` must be
  copied from `rules` inside `to_criteria` (the way `application_method` is today), together with its
  `evidence` entry. Do not add these fields to `Supervisor._HARD_FIELDS` before WP7.
- **New `StudentProfile` fields stay local.** Do not add them to `redacted_view()`. `test_no_pii_reaches_agents`
  must keep passing; extend it to assert the new fields are absent from agent prompts.
- **Every new regex gets a positive and a negative test**, including a "preference given to …" sentence that
  must stay soft.
- **Detail text for the user** follows the existing style: plain language, says what the listing requires and
  what the student should check.

## Things found while planning that every session should know

- **This Windows checkout has only the 77-row sample catalog** (50 VT, 49 of them title-only; 9 SC CHE; 18
  Queen City 2 College). The 2,467-row catalog used in round 2 is not here, and there is no `data/http_cache/`.
  Most of the false positives named in the test notes (ECU, USC, most VT detail text) cannot be reproduced
  from local data. WP0 deals with this.
- **Why title-only rows show "100%".** `rubric.build_result` gives 0.8 certainty when there are no checks, but
  every AcademicWorks row gets an `Enrollment` check that passes, so `n = 1`, `passed = 1` → 100%.
- **The LLM criteria cache ignores `RULES_VERSION`.** `Supervisor._llm_criteria` caches under
  `PROMPT_VERSION:model`. After a rules change, a cached LLM `Criteria` would lack the new rule-carried
  fields. WP0 adds `RULES_VERSION` to that cache version (done).
- **In LLM mode the Filter agent already targets group-limited awards** (its prompt covers membership,
  employer and family affiliation). Those awards dominated the manual tests because the LLM endpoint was down
  and every item fell back to rules. The deterministic fixes below are still required (invariant 1, offline mode).

## Decisions made in this plan (the owner can overrule)

- **D1. Group-limited awards give `unknown`, not `fail`, by default**, but a *restrictive* unknown that counts
  as 0 toward certainty and cuts the estimated chance. They sink to the bottom or below the LLM cap instead
  of ranking in the top ten. They `fail` only when the student has ticked "none of these apply to me" (WP3).
  Reason: invariant 4. A student who really is an employee's child must not lose the award.
- **D2. HOPE / LIFE rules come from a hand-entered supplement**, not from PDF scraping (WP6). Three programs
  do not justify a PDF pipeline, and `scraper-brief.md` already treats state tables as manual work.
- **D3. Age is asked as an optional number on the profile** and stays local.

---

## WP0 — Regression corpus and evaluation script

**Goal:** give later sessions failing tests to work against, and one command that measures false positives.

1. `tests/fixtures/regression_listings.jsonl`: one `Scholarship` JSON per line, for every award named in
   the two manual-test notes. Each line has an extra key `expect` (see below).
   - SC CHE rows: copy the real rows from `data/export/sc_che.jsonl` (present locally).
   - Everything else: if a full catalog is available (ask the user for the 2,467-row `scholarmatch.db`, or run
     WP8 step 1 first), export the real rows. Otherwise write the listing text from the wording quoted in the
     test notes (for example "children of employees of McGuireWoods LLP", "students from Northampton or
     Accomack counties", "graduated from the Chesapeake Public School system") and set `"synthetic": true`
     in `extra`. Replace synthetic rows with real ones when the catalog is available.
   - `scripts/export_regression.py --db PATH --names-file tests/fixtures/regression_names.txt` does the export,
     so it can be re-run.
2. `tests/regression_profiles.py`: the seven test profiles as `StudentProfile` objects (round 1: profiles 2, 3,
   4; round 2: Maya, Derek, Aisha; plus round 1 profile 1). Made-up people, so they can be committed.
3. `expect` format: `{"maya": {"result": "fail" | "unknown" | "pass", "check": "Field of study"}, …}`. Only
   list the profiles the notes cover for that award. `result` is the worst check result the hard filter
   should give. Include the eligible awards too (Blackmon for Derek, MIT Scholars for profile 4, LIFE and
   Palmetto Fellows for an SC student) as `pass` or `unknown` so that over-exclusion is caught.
4. `tests/test_regression.py`: parametrised over (listing, profile). Runs `extract_rules.extract` +
   `hard_filter.check` only. Each case that fails today gets `@pytest.mark.xfail(strict=True, reason="WPn")`.
   Later WPs remove the marks they fix; `strict=True` makes a forgotten mark fail the suite.
5. `scripts/eval_profiles.py [--db PATH]`: runs `Supervisor(OfflineLLM)` for each profile on the given catalog
   and prints, per profile: matches, how many are in the "known ineligible" list from the notes, how many
   of those are in the top ten. Rule-based only, no network.
6. `Supervisor._llm_criteria`: cache version becomes `PROMPT_VERSION:RULES_VERSION:model`.

**Done when:** suite green with xfails; eval script prints a baseline table; the baseline is pasted into this
file under WP0 notes.

### WP0 notes (2026-11-12)

**Files.** `tests/fixtures/regression_listings.jsonl` (67 rows), `tests/fixtures/regression_names.txt`,
`tests/regression_profiles.py` (`PROFILES`, `load_corpus`), `tests/test_regression.py`, `scripts/eval_profiles.py`,
`scripts/export_regression.py`. `Supervisor._llm_criteria` now caches under `PROMPT_VERSION:RULES_VERSION:model`.

**Corpus.** 9 real SC CHE rows (copied unchanged from `data/export/sc_che.jsonl`); 58 synthetic rows
(`extra.synthetic = true`) whose text is written from the wording in the two test notes. The 33 VT awards that
are in the local sample keep their real `source_id`, name and URL; only the text is synthetic (the local rows are
title-only). ECU, USC, the Charlotte counselor page and 7 VT awards from round 2 have `syn-…` ids. Synthetic rows
have `extra.detail_fetched = true` (they stand in for a detail page) and deadline 2030-01-22.
Each line has two sibling keys that `load_corpus` strips before `Scholarship` validation:
- `expect`: `{profile: {"result": ..., "check": ...}}`. The test asserts that the **worst** result over all checks
  equals `result`, and, when `check` is given, that the check with that name gives `result`. The check names
  for future rules are the ones this plan uses: `Specific group` (WP3, and WP6 National Guard), `Locality` (WP4
  counties, school systems, high schools), `Residency` (WP4 out-of-state; the plan did not name this check, so
  WP4 may rename it, and must then update the corpus), `Field of study` / `Education level` (WP5), `Age`, `Type of
  college`, `Citizenship` (WP6).
- `ineligible_for`: profiles the notes call ineligible. Only `eval_profiles.py` uses it. It also lists cases no WP
  can decide in the hard filter (Richard E. Browning '71: an internship requirement), which have no `expect`.

**Profiles.** The seven from the notes (`r1p1`…`r1p4`, `maya`, `derek`, `aisha`) plus one control, **`sam`** (SC
HS senior, citizen, GPA 3.7 → USC, Biology). Deviation: round 1 profile 3 is not a U.S. citizen with a 2.8 GPA,
so once WP6 adds the HOPE/LIFE rules she is not eligible for LIFE/Palmetto Fellows either; `sam` is the student who
must keep them. Facts the notes do not give stay unset (as the Profile form leaves them), so `military_affiliation`
is `None` for everyone. Consequence: **SC National Guard CAP expects `unknown` (Specific group), not `fail`**, for
`r1p3` and `derek` (invariant 4, D1); WP6's `fail` path only applies when the profile says "no".

**xfail marks.** One dict, `XFAIL`, at the top of `tests/test_regression.py`, grouped by WP and keyed by
(scholarship name, profile id). Each entry becomes `xfail(strict=True, reason="WPn")`. When a WP makes a case
pass, the strict mark turns into a failure: delete the entry. An entry that no longer matches a corpus case fails
collection. Counts: WP3 12, WP4 6, WP5 12, WP6 12 (42). WP2 has none (it changes certainty, not check results).

**For WP6 in particular.** The HOPE / Need-based Grant / LIFE / Palmetto `Citizenship` expectations need the
hand-entered supplement text in the row. The test reads the corpus rows directly (no ingest), so WP6 must put the
merged text into those four corpus rows (or make the merge a function the test can call).

**Bugs noticed while building the corpus (not fixed, WP0 only):**
- `extract_rules.extract`, AcademicWorks fallback: `"graduate" in low_hard` also matches "graduates of" /
  "graduated from", so the row gets `levels = ["undergrad", "graduate"]` (Garris → `unknown` for Maya; the
  corpus expects `unknown` there, which is acceptable on a university portal). WP5 should use a word boundary.
- "rising seniors" is stored as class year `senior`, so a **junior** (who *is* a rising senior) fails Class
  year (Donald F. Morris for `r1p2`; the expectation still holds because the field fails too). WP5.
- The Dean's Roundtable is currently excluded for `r1p2` by Class year only; the Field-of-study reason (College
  of Science vs CS) is the WP5 xfail.

**How to run.**
- `python -m pytest -q tests/test_regression.py` (also part of the full suite).
- `python scripts/eval_profiles.py` (local catalog), `--db PATH` (another catalog), `--corpus` (the corpus loaded
  into a temp DB; the best measure without the full catalog), `--show` (names in each top ten), `--as-of` (default
  2026-11-12, fixed so runs are comparable). The catalog is always copied to a temp DB first.
- `python scripts/export_regression.py --db PATH [--dry-run]` replaces corpus lines with real rows of the same
  normalised name and keeps `expect` / `ineligible_for`. It skips title-only rows, so on this machine it only
  rewrites the 9 SC CHE rows (byte-identical). With the full catalog, ECU/USC names written from the notes
  ("American Legion Post #39 Scholarship", "Anthony Fox Scholarship", …) may differ from the real titles: fix the
  name in the corpus, `regression_names.txt` and `XFAIL` together.

**Baseline (rules-2, rubric-1, rule-based, as of 2026-11-12).** `eligible` = passed SQL + hard filter;
`matches` = shown (top 30); `known_inel` = matches the notes call ineligible; `elig_lost` = awards the corpus
expects `pass`/`unknown` that were excluded.

Local catalog (77 rows; VT rows are title-only, so most VT false positives cannot be seen by the rules):
```
profile  eligible matches known_inel inel_top10 elig_lost
r1p1            0       0          0          0         0
r1p2           47      30         28          9         0
r1p3            5       5          5          5         0
r1p4           49      30          2          2         0
maya            0       0          0          0         0
derek           3       3          3          3         0
aisha          49      30          6          0         0
sam             6       6          0          0         2
total         159     104         44         19         2
```

Regression corpus (`--corpus`, 67 rows):
```
profile  eligible matches known_inel inel_top10 elig_lost
r1p1            9       9          0          0         0
r1p2           15      15          6          4         0
r1p3            5       5          5          5         0
r1p4           23      23          0          0         0
maya           11      11          6          6         0
derek           4       4          3          3         0
aisha          22      22         12          5         0
sam             6       6          0          0         2
total          95      95         32         23         2
```
`sam` loses LIFE and Palmetto Fellows to the "enhancement" major text (WP6).

## WP1 — UI and launch config (items 9, 11)

Files: `app.py`, `scholarmatch/textutil.py` (`parse_amounts`), `scholarmatch/agents/supervisor.py` (progress
calls only), `.claude/launch.json`.

- **Run header** (`app.py:205`): count `sum(r.mode == "llm" for r in run.results)`. Show "AI-reviewed (N of M)"
  when N > 0, "rule-based (AI review unavailable)" when the run's mode is `llm` but N = 0, "rule-based" otherwise.
- **Progress label:** in `Supervisor.run`, call `say("Agents reviewing scholarships… 0/N", 0.1)` before the
  thread pool starts, and call `say` for the shortlist step.
- **"Award $0.00":** a parsed amount of 0 becomes `None` (amount unknown), and `MatchResult.amount_display`
  shows "Varies" when the raw amount parses to zero. Add a flag "No award amount is listed; this may be
  program support rather than a student scholarship." Treat it as a display fix; do not exclude the row.
- **Launch config:** add a second configuration `scholarmatch-mac` using `.venv/bin/python`; keep the existing
  one for Windows. Update the Commands section of `CLAUDE.md`.

Tests: unit test for zero amounts; a pipeline test that a `FakeLLM` run where every scorer call fails reports 0
AI-reviewed items (test the helper that builds the header text, not Streamlit). Check the header in the
running app (`scholarmatch` preview config).

### WP1 notes (2026-11-12)

- **Run header.** New pure module `scholarmatch/run_summary.py` (`ai_reviewed_count`, `review_mode_label`), used by
  `app.page_matches`; it imports no Streamlit, so tests call it directly. Tests in `test_pipeline.py`: a
  `ScorerDownLLM` (every Scorer call raises `LLMError`) gives a run with `mode == "llm"`, 0 items with `mode ==
  "llm"` and the label "rule-based (AI review unavailable)"; a working `FakeLLM` run gives "AI-reviewed (N of N)";
  offline gives "rule-based". **Not checked in the running app** (the WP1 session could not drive a browser); check
  it with the `scholarmatch` preview config during WP8.
- **Progress.** `Supervisor.run` now reports "Shortlisted N of M eligible scholarships…" (0.08), then "Agents
  reviewing scholarships… 0/N" (0.1) before the thread pool starts, or "Scoring with the rule-based rubric…" (0.5)
  offline. Fractions stay monotonic (tested).
- **"$0.00" awards.** Deviation from the plan text: `parse_amounts` already returned `(None, None)` for "$0.00"
  (figures under $50 are dropped), so ingest is unchanged and stores nothing new. The "$0.00" came from
  `MatchResult.amount_display = s.amount_raw`. Now `textutil.is_zero_amount(raw)` detects all-zero dollar text;
  `rubric.amount_display(s)` shows "Varies" for it; `rubric.amount_value` returns `None` for a zero midpoint (guard
  only: no ingested row has a stored 0); `build_result` adds `rubric.ZERO_AMOUNT_FLAG`. The Saved page and the
  offline chat answer also show "Varies" for an old "$0.00" snapshot. No rubric number changes for existing rows,
  but the flag and display text change the stored result, so `RUBRIC_VERSION` went to `rubric-2` (invariant 8:
  same-day stored runs would otherwise keep "$0.00"). **WP2 therefore bumps to `rubric-3`.**
- **Launch config.** `scholarmatch` unchanged; `scholarmatch-mac` runs `.venv/bin/python` (not tested on a Mac;
  there is no `.venv` on the Windows checkout).

## WP2 — Certainty when information is missing (item 2)

Files: `matching/rubric.py`, `config.py` (`RUBRIC_VERSION` → `rubric-3`; WP1 already used `rubric-2`), tests.

- Add `info_level(s, c) -> "none" | "thin" | "ok"` in `rubric.py`:
  - `none`: `s.extra.get("detail_fetched") is False`, or the listing text other than the name is under 40 characters.
  - `thin`: no hard criterion was extracted (all of GPA, residency, localities, levels, majors, need,
    demographics and, later, the WP3–WP6 fields are empty).
  - `ok`: otherwise.
- The `Enrollment` check is excluded from the certainty ratio (it is a property of the source, not of the listing).
- Certainty: `none` → capped at 0.30; `thin` → capped at 0.60; `ok` → the existing ratio.
- Add a per-check weight: `HardCheck.weight: Literal["normal", "restrictive"] = "normal"`. A normal unknown
  counts 0.5 as today; a restrictive unknown counts 0. WP3 uses it.
- The "why" line says what is missing ("The listing gives no eligibility details; open the link to check").
- The estimated chance already scales with certainty; confirm with a test that a title-only row now ranks below a
  row with two confirmed requirements.

Tests: title-only row is not 100%; `thin` row is ≤ 60%; a row with real passes is unchanged; the sort
order of the existing pipeline tests still holds (adjust expected numbers only where the rubric change explains it).

### WP2 notes (2026-11-12)

- **Code.** All in `matching/rubric.py`: `HARD_CRITERIA_FIELDS` (one module-level tuple; it already lists the WP3–WP6
  fields `restricted_groups`, `destination_schools`, `non_resident_required`, `declared_major_required`, `min_age`,
  `institution_types`, read with `getattr(c, f, None)` so they are skipped until they exist; **WP3–WP6 do not need to
  touch it** unless they add a field with another name), `listing_body`, `has_hard_criteria`, `info_level`,
  `eligibility_certainty` (returns certainty, level and the "why" text), constants `CERTAINTY_CAP` (none 0.30, thin
  0.60), `NO_CHECKS_CERTAINTY` (0.8, unchanged), `SOURCE_CHECKS = {"Enrollment"}`, `RESTRICTIVE_CHANCE_FACTOR` (0.25).
  `models.HardCheck.weight: Literal["normal", "restrictive"] = "normal"`. `config.RUBRIC_VERSION = "rubric-3"`.
- **Rules as built.** `info_level`: `none` when `extra.detail_fetched is False` or the listing text other than the
  name (`full_text()` minus the leading name) is under 40 characters; `thin` when no `HARD_CRITERIA_FIELDS` value is
  set (`class_years` and `us_citizen_or_resident_required` count as hard criteria too; the plan's list did not name
  them); `ok` otherwise. Certainty = (passes + 0.5 × normal unknowns + 0 × restrictive unknowns) / checks, with
  `Enrollment` left out of both counts; 0.8 when no counted check remains; then capped by level. A `thin` row has no
  counted checks by construction (every check except Enrollment comes from a hard field), so it always shows 60%.
- **WP3 only has to set `weight="restrictive"`** on the "Specific group" unknown: the 0-count, the x0.25 chance factor
  (applied after the normal chance formula, also to automatic state programs) and a "why" sentence ("N of these limit
  the award to a specific group…") are in place and unit-tested with hand-built `HardCheck`s.
- **Side effect on `ok` rows with Enrollment.** Leaving Enrollment out of the ratio lowers certainty where a school row
  also has an unknown (Enrollment pass + Field unknown: 75% → 50%), and leaves all-pass rows at 100%. No existing test
  asserted a certainty or score number, so **no expected value in the existing tests changed**.
- **Tests.** `test_units.py`: title-only row 30% (was 100%) with the "no eligibility details" why; short text → `none`;
  thin row 60% with and without Enrollment; a row with real passes keeps 100% / 75%; restrictive unknown → 50% vs
  75%, chance x0.25; `info_level` with a stand-in object that has `restricted_groups`; title-only row ranks below a
  row with two confirmed requirements on match and chance. `test_pipeline.py`: an offline run puts an ingested
  title-only VT row below `test:svc` and `vt1`, at 30%.
- **Eval (rules-2, rule-based, as of 2026-11-12).** Local catalog, rubric-2 → rubric-3:
  ```
  profile  eligible matches known_inel inel_top10 elig_lost      (before: known_inel / inel_top10)
  r1p1            0       0          0          0         0
  r1p2           47      30         27          8         0      (28 / 9)
  r1p3            5       5          5          5         0
  r1p4           49      30          2          2         0
  maya            0       0          0          0         0
  derek           3       3          3          3         0
  aisha          49      30          6          0         0
  sam             6       6          0          0         2
  total         159     104         43         18         2      (44 / 19)
  ```
  Regression corpus (`--corpus`): **unchanged** (total 95 / 95 / 32 / 23 / 2), as expected: every corpus row has
  detail text and hard criteria, so it is `ok`, and WP2 changes no check result.
- **Why the local numbers barely move.** 49 of the 50 local VT rows are title-only, so they all drop to 30% *together*
  and still tie among themselves (alphabetical tie-break). They now rank below every row that has real text (that is
  the item-2 fix), but for a VT student nearly all eligible rows are VT title-only rows, so the top ten is still made
  of them. Only re-scraping VT detail pages (WP8) lets the rules tell them apart. One known-ineligible award (Dunham
  Charitable Scholarship Fund) fell out of r1p2's top ten and one fell below the 30-item cap.

## WP3 — Awards limited to a specific group (item 1)

Files: `models.py`, `ingest/extract_rules.py`, `matching/hard_filter.py`, `matching/rubric.py` (`narrowing`,
`info_level`), `agents/workers.py` (`to_criteria` carry-over), `app.py` (`page_profile`), tests. `RULES_VERSION` bump.

- `Criteria.restricted_groups: list[str]`: short phrases copied from the listing, with the sentence in
  `evidence["restricted_groups"]`.
- Patterns (hard sentences only; a "preference" sentence stays soft):
  - family of an employer's staff: `(child(ren)?|sons? (and|or) daughters?|dependents?|spouses?) of [^.]{0,60}(employees?|staff|faculty|associates|members|growers|farmers)`
  - descendants: `(direct |lineal |documented )?descendants? of`
  - membership: `(active |current )?members? of (the )?[A-Z]`, `member in good standing`, known clubs (USATT, 4-H, FFA, Scouts)
  - holders of another award or programme: `recipients? of (a|the) [^.]{0,60}scholarship`, `receiving a [^.]{0,40}scholarship`, `completed (a|the) [^.]{0,40}(co-?op|internship) program`, `registered with (the )?Services for Students with Disabilities`
  - transfers: `transfer(ring)? (students? )?from (a |the )?(community college|VCCS|[A-Z])`, `(earned|completed) an associate('s)? degree`
  - medical condition: `(students?|applicants?) (with|diagnosed with|living with) (diabetes|[^.]{0,30}(disease|disorder|condition))`
  - Keep the 4-H test case in `conftest.py` in mind: it must still be kept for a student with no affiliations
    listed, now with a Verify flag.
- Profile: `StudentProfile.affiliations: list[str]` (free text: parents' employers, clubs, family ties,
  transfer status) and `no_special_affiliations: bool = False` ("None of these apply to me"). Both local only.
  Add them to the Profile page under a short explanation.
- `hard_filter.check` → check name "Specific group":
  - a normalised token overlap between a restricted-group phrase and an affiliation → `pass`;
  - `no_special_affiliations` is ticked and there is no overlap → `fail`;
  - otherwise → `unknown`, `weight="restrictive"`, detail "Only for {phrase}. Add this under Affiliations if it
    applies to you."
- `rubric.narrowing` adds "limited to a specific group"; a restrictive unknown multiplies the estimated chance by 0.25.

Tests: one positive and one negative per pattern family; the regression cases for McGuireWoods, Smithfield,
Universal Leaf, Vaughan, Tribal Match, Faculty-Staff Dependent, VCCS Transfer, Mossberg (USATT), Buzz's Bunch,
Graham Stedman Neal; `test_unknown_never_excludes` still passes; PII test extended.

### WP3 notes (2026-11-12)

- **Code.** `models.Criteria.restricted_groups`; `models.StudentProfile.affiliations` / `no_special_affiliations` (not in
  `redacted_view()`). `extract_rules.GROUP_PATTERNS` (six families, as in the plan) + `restricted_groups(hard, name)`,
  called from `extract`; evidence in `evidence["restricted_groups"]`. `hard_filter.group_check` → check "Specific
  group". `rubric.narrowing` adds "limited to a specific group". `workers.to_criteria` copies `restricted_groups` and
  its evidence from `rules` (not in `_HARD_FIELDS`, no prompt change). `app.page_profile`: an "Affiliations" text area
  (one per line) and a "None of these apply to me" checkbox inside the optional-details expander, with a short
  explanation. `RULES_VERSION = "rules-3"`; `RUBRIC_VERSION = "rubric-4"` (the narrowing entry changes Competition
  points and the chance for group-limited rows).
- **Patterns as built (deviations from the plan text).** All run only on hard (non-preference) sentences.
  - Proper names are required case-sensitively inside the `re.I` patterns with `(?-i:[A-Z])` (membership, transfer
    "from X"), so "members of the community" / "transfer students from any college" do not match. Membership also
    has a lookahead that skips "the community / graduating class / class / public / student body / team / household /
    family". Known clubs (USATT, 4-H, FFA, Boy/Girl Scouts) need a member/membership/club word next to them, so a
    title like "FFA Scholarship" alone is not a group limit.
  - "recipients of (a|the) X scholarship" needs at least one word before "scholarship", is skipped at the start of a
    sentence ("Recipients of the … will …") and when X is this award's own name (renewal rules). "receiving a
    scholarship" with no name in between does not match.
  - A sentence saying the group is "not eligible" / "ineligible" / "excluded" is skipped.
  - The phrase runs from the match to the end of its clause (cut at `, ; :`, " who ", " by ", " and must/be/have/…");
    capped at 90 characters; a leading article is dropped. `sentences()` splits "Clara B. Vaughan" after the
    initial, so `restricted_groups` re-joins a sentence that ends in a single capital initial with the next one (only
    for this field; other extraction is unchanged).
- **Check.** Overlap = shared words after removing a stop list of generic group words (child, employee, member,
  descendant, scholarship, transfer, college, community, company, …) and US state names, so "Virginia Tech employee"
  does not match "tobacco growers in Southside Virginia". Pass → "Only for a specific group: “…”. This matches
  something you listed under Affiliations." (the detail never repeats the student's own text, because check details
  reach the chat agent). `no_special_affiliations` with no overlap → fail. Otherwise restrictive unknown with "Add
  this under Affiliations if it applies to you." An overlap wins over the tick box.
- **Tests.** `test_units.py`: positives for every family (13 sentences), negatives (13 sentences, including two
  "preference/priority" sentences, "members of the community", "member of the graduating class", "Recipients of this
  scholarship will…", "students with a disability", "transfer students"), the preference sentence lands in
  `soft_criteria`, grounded evidence, `group_check` pass/fail/unknown and the state-name/generic-word guard, ranking
  below an open award, `to_criteria` carry-over. `test_pipeline.py`: the conftest 4-H award is kept with a "Verify —
  Specific group" flag (restrictive) for a student with no affiliations, passes for a 4-H member and is excluded with
  `no_special_affiliations`; `test_no_pii_reaches_agents` now sets affiliations, runs the chat on the 4-H award (whose
  "Specific group" check passes) and asserts the affiliation text and both field names never reach a prompt.
- **xfails.** All 12 WP3 entries removed. **Also removed the two WP6 entries for SC National Guard CAP (`r1p3`,
  `derek`)**: the generic membership pattern reads "members of the South Carolina Army and Air National Guard", so the
  expected restrictive `Specific group` unknown is there now. WP6 still has to add the fail path for
  `military_affiliation is False`. Remaining: WP4 6, WP5 12, WP6 10 (28). No `expect` value was changed.
- **Eval (as of 2026-11-12, rule-based).** Regression corpus (`--corpus`), rules-2/rubric-3 → rules-3/rubric-4:
  ```
  profile  eligible matches known_inel inel_top10 elig_lost      (before: inel_top10)
  r1p1            9       9          0          0         0
  r1p2           15      15          6          5         0      (4)
  r1p3            5       5          5          5         0
  r1p4           23      23          0          0         0
  maya           11      11          6          6         0
  derek           4       4          3          3         0
  aisha          22      22         12          3         0      (5)
  sam             6       6          0          0         2
  total          95      95         32         22         2      (23)
  ```
  Local catalog: **unchanged** (159 / 104 / 43 / 18 / 2): the local VT rows are title-only, so no group text exists.
  `elig_lost` did not grow. `eligible` and `known_inel` cannot drop under D1 (a silent profile gets unknown, not fail);
  the effect is in the ranking: every group-limited row now has 0–50% certainty and about a quarter of the chance, so
  in Aisha's list McGuireWoods, Smithfield, Universal Leaf, VCCS, Mossberg, Vaughan, Tribal Match and Graham Stedman
  Neal fell from the top ten to ranks 13–21 of 22. **Why r1p2 went from 4 to 5:** r1p2 has only 15 eligible rows, 9 of
  them group-limited, so at least four group rows must fill the top ten. They are now ranks 6–14, but which ones land
  in ranks 6–10 is a tie broken alphabetically (Buzz's Bunch and Vaughan are labelled ineligible for r1p2; McGuireWoods
  etc. are not, because the notes did not test them on r1p2). Buzz's Bunch ranks first among them because it also has
  a normal Disability unknown, which counts 0.5 and lifts its certainty ratio to 25% (a WP2 ratio quirk, not changed
  here). Faculty-Staff Dependent stays in Aisha's top ten (rank 10, 50%: its Education level check passes).
- **Not checked in the running app**: the Profile form fields (the session could not drive a browser; `app.py` parses).
  Check them during WP8.

## WP4 — Locality, school and out-of-state limits (items 3, 6)

Files: same core files as WP3. `RULES_VERSION` bump.

- **County lists:** capture "A or B counties", "A, B and C Counties", "counties of A and B", "A County" →
  each as "X County" in `Criteria.localities`. Also "City of X" and "X Public School(s) (system)" →
  the locality name (matches `p.city` or `p.county`).
- **High schools:** "senior(s) at X High School", "graduate(s)/graduated from X High School", "attend(s) X High
  School" → `localities` entry with the full school name (the existing check already compares `p.high_school`).
  The current Charlotte-only pattern is folded into the general one.
- **Destination college:** `Criteria.destination_schools: list[str]` from "enrolling at / attending / planning
  to attend / accepted to X (Community College|College|University)". Check name "College attending": compare
  with `p.current_school` and `p.intended_schools` (normalised text). No schools on the profile → `unknown`;
  schools listed and none match → `fail`. Skip this when the row already has `s.school` (Enrollment covers it).
- **Out-of-state:** `Criteria.non_resident_required: bool` from "out-of-state students", "non-residents of
  Virginia", "non-Virginia residents". The school's state comes from `config.SCHOOLS[s.school][1]`.
  `p.state` equals it → `fail`; differs → `pass`; no school state known → `unknown`. An "or" alternative in the
  same sentence ("out-of-state students or descendants of …") downgrades the fail to a restrictive unknown.
- Locality check: split on commas and "or" before comparing; require a whole-word match so "Smyth" does not
  match "Smythe".

Tests: Watson (Northampton / Accomack), Gilbert (Chesapeake Public Schools), Anthony Fox (West Charlotte HS →
CPCC), Margaret Groseclose Skelton (Smyth County), Moss (Montgomery Co. / Radford HS), O. C. Thompson;
a student from the named county passes; "preference given to residents of Roanoke County" stays soft
(existing `women` fixture).

### WP4 notes (2026-11-12)

- **Code.** `models.Criteria.destination_schools: list[str]` and `non_resident_required: Optional[bool]`.
  `extract_rules`: `localities(hard)`, `destination_schools(hard)`, `non_resident_required(hard)`,
  `non_resident_has_alternative(sentence)`, `non_resident_state(sentence)`; the old "any `X County` in any hard sentence"
  loop and the Charlotte-only high-school pattern are gone. `hard_filter`: `locality_check`, `college_check` ("College
  attending"), `non_resident_check` (check name **"Residency"**, as in the WP0 corpus). `rubric.pool_tier` puts a row with
  `destination_schools` (and no locality) in the `school` tier; `rubric.narrowing` adds "out-of-state students only".
  `workers.to_criteria` copies `restricted_groups`, `destination_schools`, `non_resident_required` and their evidence
  from `rules` (`_RULE_ONLY_FIELDS`); no prompt change, not in `_HARD_FIELDS`. `RULES_VERSION = "rules-4"`,
  `RUBRIC_VERSION = "rubric-5"` (pool tier / narrowing change Competition points and the chance for those rows).
- **Locality extraction as built.** Hard sentences only. Place shapes (case-sensitive proper names): county lists ("A or
  B counties", "A, B and C Counties", "counties of A and B", slashes like "Concord/Cabarrus"), "X County", "City of X",
  "X (City) Public School(s) (system)" → stored as "X Public Schools", "Charlotte-Mecklenburg Schools" / "CMS
  seniors", "X High School". A place counts only when restrictive wording points at it: a plural trigger right before
  it ("students/applicants/graduates/seniors from|of|at|in", "residents of", "high schools in", "counties of"), or a
  singular/verb trigger ("resident of", "a graduate of", "senior at", "graduated from", "attend", "enrolled in", "live
  in") that starts the sentence (bullet style) or follows a word about the applicant (students, applicants, must,
  eligible, every, …), or a following noun ("Smyth County residents", "a Mecklenburg County public … school"), or "or"/
  "and" after an accepted place ("… Counties in NC or York County, SC"). Articles may sit in between ("graduates of a
  Pitt County high school"). Skipped: "were/was residents of" (biography), negated sentences ("… are not eligible"),
  "born in", sponsor addresses, preference sentences. **Deviation:** an award *name* alone ("Charlotte County
  Scholarship") no longer yields a locality (no restrictive wording); before WP4 it did.
- **Locality check (semantics changed; read before WP5).** Each entry is parsed into a kind and split on commas, "/",
  "or", "and"; names match as whole words (Smyth ≠ Smythe). County entry: pass if the name is the student's county (or
  city, for independent cities); fail if the student gave a county and it differs; **unknown if only a city / high
  school was given** (before: fail). City / school-system entry: compared with county and city; fail if either was given
  and neither matches; unknown if only a high school was given. High-school entry: compared with `p.high_school`; with
  no high school given, unknown when the school's name contains the student's city or county ("West Charlotte" for a
  Charlotte student), fail otherwise (a Pitt County student is not a West Charlotte senior; the corpus expects this for
  Maya). Combined: any pass → pass, else any unknown → unknown, else fail. No location at all → unknown. Details never
  repeat the student's county / city / high school (they reach the chat agent).
- **College attending.** Only for rows without `s.school`. Triggers "enrolling/enrolled at", "attend(ing)", "planning
  to attend/enroll at", "will attend", "accepted to/at/into", "admitted to", followed by a capitalised name ending in
  Community College / Technical College / College / University / Institute (at least one word before it, so "admitted to
  the College of Nursing" does not match) or "University of X"; "X or the University of Y" gives both. Questions ("Why
  … attending CPCC?") are skipped. Match: whole-phrase containment either way against the student's schools (codes
  expanded to `config.SCHOOLS` names) or the initials ("CPCC"). No schools → unknown; none match → fail.
- **Out-of-state.** "out-of-state students/applicants/residents/undergraduates/freshmen", "non-resident(s) (of
  <state>)", "non-<State> residents"; not "nonresident tuition/fees/aliens", not "residents and non-residents", not
  negated, not in a preference sentence; "transferring from an out-of-state institution" does not match. The existing
  state-residency rule no longer reads "non-residents of Virginia" as VA residency. School state from
  `config.SCHOOLS[s.school][1]`; for a non-school row, the state the sentence names; otherwise unknown. Same state → fail,
  or a **restrictive** unknown when " or " joins the out-of-state phrase to another way in ("out-of-state students or
  descendants of O. C. Thompson"); different state → pass.
- **Tests.** `test_units.py`: 13 locality positives, 9 negatives (two preference sentences, biography, "were residents
  of", sponsor address, negated, award name), grounded evidence, the Locality check matrix (named county passes,
  Smythe ≠ Smyth, city-only → unknown, high-school rules, CMS, details do not echo the student's location), destination
  positives/negatives and the check, out-of-state positives/negatives and the check (fail / pass / restrictive unknown
  / no state → unknown), pool tier, narrowing and the `to_criteria` carry-over. The conftest `women` fixture ("Preference
  given to residents of Roanoke County") still has no locality (`test_rules_preference_is_soft_not_hard`).
- **xfails.** All 6 WP4 entries removed; no `expect` value changed. Remaining: WP5 12, WP6 10 (22).
- **Not handled (left for later):** a locality sentence with an "or" alternative ("Resident of Mecklenburg … Counties
  or have accessed Time Out Youth programming", a local Queen City row) still fails a student from elsewhere; only the
  out-of-state rule has the "or" downgrade. In LLM mode `localities` still come from the Extractor alone (WP7 should
  union them with the rule values).
- **Eval (as of 2026-11-12, rule-based).** Regression corpus (`--corpus`), rules-3/rubric-4 → rules-4/rubric-5:
  ```
  profile  eligible matches known_inel inel_top10 elig_lost      (before: eligible / known_inel / inel_top10)
  r1p1            8       8          0          0         0      (9 / 0 / 0)
  r1p2           14      14          5          4         0      (15 / 6 / 5)
  r1p3            5       5          5          5         0
  r1p4           23      23          0          0         0
  maya           10      10          5          5         0      (11 / 6 / 6)
  derek           4       4          3          3         0
  aisha          21      21         11          2         0      (22 / 12 / 3)
  sam             6       6          0          0         2
  total          91      91         29         19         2      (95 / 32 / 22)
  ```
  Watson is now excluded for r1p2 and Aisha, Anthony Fox for Maya and r1p1 (College attending: she applies to ECU, not
  CPCC; the notes do not list it for r1p1, so it is not counted in `known_inel`). Watson was in Aisha's top ten before;
  her top ten now holds only Richard E. Browning '71 (internship, no hard-filter rule) and Faculty-Staff Dependent. O. C.
  Thompson stays in her list with a restrictive Residency unknown, below the top ten (as before). `elig_lost` did not
  grow (2, both WP6). Local catalog: **unchanged** (159 / 104 / 43 / 18 / 2). The Queen City rows now carry their
  county lists, but all 18 are `status = ended`, so no profile sees them.

## WP5 — Graduate programs, departments and declared majors (items 4, 7, 8)

Files: `ingest/extract_rules.py` (mostly `FIELDS`, level block, `_MAJOR_TRIGGER`), `hard_filter.py`, `models.py`. `RULES_VERSION` bump.

- **Graduate and professional programs (4):** add `GRADUATE_PROGRAMS` regex: clinical nurse specialist, nurse
  practitioner, nurse anesthesia, MSN, DNP, physician assistant (studies), school/college of dental
  medicine, DMD/DDS, college of veterinary medicine, DVM, school of medicine / medical students, MD, PharmD,
  law school / JD, "Master of …", MBA, MIT (Master of Information Technology, only on VT rows). When it
  matches a hard sentence or the listing name and the listing does not also name undergraduates, set
  `levels = ["graduate"]` with the sentence as evidence. "pre-med", "pre-dental", "pre-vet", "pre-PA" must not
  trigger it.
- **Departments and colleges (7):**
  - New fields so the specific unit wins over the broad one: `human development` (child development, family
    relations, family and consumer science(s), human development and family science), `rehabilitation`
    (addictions and rehabilitation, rehabilitation counseling/studies), `communication sciences and disorders`,
    `social work` split from sociology if the test data needs it. Parents as appropriate.
  - `_MAJOR_TRIGGER` additions: "declared major (in)", "department of", "program in", "<Field> student(s)/majors".
  - `Criteria.majors_from_unit: bool`: true when the only source of `majors` is a college/department
    name (`s.college_or_department`, or a "College/School of X" sentence with no "major" wording). In
    `hard_filter.check`, a `pass` from `field_match` is downgraded to `unknown` ("For students in the College
    of X; check that your major is in that college"). A `fail` stays a fail.
  - Pecheles case: "College of Health and Human Sciences" vs a nursing major must come out `unknown` at best.
- **Declared major / enrolled in the program (8):** `Criteria.declared_major_required: bool` from "declared
  major in", "admitted to the (College|School) of X", "enrolled in (the )?(nursing|X) (program|major|curriculum)",
  "accepted into the … program". For `p.level == "hs_senior"`, a "Field of study" `pass` becomes `unknown`
  ("Requires a declared major in X; incoming students may not qualify yet").

Tests: CNS, Griffin (Dental Medicine), Burevitch PA, IMPACT, MIT Scholars (pass for profile 4, fail for an
undergraduate), CLAHS Faculty Campaign; Pecheles, Congleton, Westmoreland, Addictions & Rehab, College of
Science Dean's Roundtable vs a CS student; American Legion #39 and Justin Bryce Clark → unknown for Maya,
pass for an enrolled nursing undergraduate. Keep `test_field_match_uses_specific_fields_not_groups` and
`test_rule_majors_keep_the_specific_field` green.

## WP6 — SC CHE programs (item 5)

Files: core rule files, `scrapers/state_aid.py` or `manual_sources/` (HOPE/LIFE supplement), `app.py` (age field). `RULES_VERSION` bump.

- **Age:** `Criteria.min_age: Optional[int]` from "(age )?N (years of age )?or older", "over N", "at least N years
  old"; also from the listing name ("Residents Over 60"). `StudentProfile.age: Optional[int]`, local only.
  Check "Age": age given → pass/fail; age missing and `p.level == "hs_senior"` and `min_age >= 25` → `fail`
  (state the assumption in the detail); otherwise restrictive `unknown`.
- **Institution type:** `Criteria.institution_types: list[Literal["two_year", "technical", "four_year"]]` from
  "technical college(s)", "two-year (public|institution)", "community college". Check "Type of college": the
  schools in `config.SCHOOLS` are four-year; if every school on the profile is one of those → `fail`; if the
  profile names another school or none → `unknown`.
- **National Guard:** "member(s) of the (South Carolina )?(Army|Air )?National Guard" → `restricted_groups`
  entry (WP3 machinery) in addition to the military flag. `p.military_affiliation is False` → `fail`.
- **Enhancement text:** sentences containing "enhancement" or "additional $… for … majors" are removed from
  the major-restriction pass. LIFE and Palmetto Fellows must have `majors == []`.
- **HOPE / LIFE / Palmetto Fellows / Need-based Grant rules (D2):** add `manual_sources/sc_che_rules.csv`
  (or extend the existing manual format) with the GPA, citizenship and FAFSA requirements typed from the
  official PDFs, each with the PDF URL as its source. Ingest merges this text into the SC CHE row's
  `eligibility` so the normal extraction and grounding work. Mark `confidence="medium"`. Ask the user to
  check the typed values against the PDFs before merging.

Tests: all nine SC CHE rows against round 1 profile 3 and Derek; an SC resident with GPA 3.2 and citizenship
keeps LIFE and HOPE; a 62-year-old SC resident passes Free Tuition; a technical-college student keeps SC WINS.

## WP7 — Extractor agent learns the new fields

Files: `models.py` (`ExtractedCriteria`), `agents/workers.py` (prompt, `to_criteria`), `agents/supervisor.py`
(`_HARD_FIELDS`), `tests/conftest.py` (`FakeLLM`), `config.py` (`PROMPT_VERSION` → `p2`).

- Add to `ExtractedCriteria`: `restricted_groups`, `destination_schools`, `non_resident_required`,
  `declared_major_required`, `min_age`, `institution_types`. Describe each in `EXTRACTOR_SYSTEM`; move
  "member of 4-H / child of an employee" out of the `soft_criteria` instruction into `restricted_groups`.
- `to_criteria`: use the LLM value when it is grounded; otherwise keep the rule value. For list fields take
  the union. Add the new fields to `_HARD_FIELDS` so the grounding gate applies.
- `FakeLLM` returns the new fields; add one scripted case with an ungrounded `restricted_groups` quote and
  assert it falls back to the rule value.
- Keep the "treat as data" and quote rules in the prompt unchanged.

Tests: existing pipeline tests green; new test for the ungrounded fallback; `test_no_pii_reaches_agents` green.

## WP8 — VT re-scrape and manual test round 3 (item 10)

Needs network access, `SCRAPER_CONTACT_EMAIL` and a working LLM endpoint, so do it with the user present.

1. Count title-only rows: `SELECT count(*) FROM scholarships WHERE json_extract(data,'$.extra.detail_fetched') = 0`
   grouped by source. On this machine it is 49 of 50 VT rows. Re-scrape with
   `python -m scholarmatch.scrapers.runner --sources vt` (no `--max-details`), then ECU and USC as needed.
2. Re-export the regression corpus from the real rows (`scripts/export_regression.py`) and replace synthetic lines.
3. Run `scripts/eval_profiles.py` and record the numbers next to the WP0 baseline.
4. Repeat the manual test with the seven profiles in the app, once rule-based and once with the LLM on
   (Anthropic is still untested). Check the **Data & audit** page for `rejected` / `llm_failure` events.
   Write `docs/manual_test_<date>_round3.md`.
5. Update `CLAUDE.md`: current state, catalog counts, and whatever is still open.

**Target for round 3:** no clearly ineligible award in any profile's top ten in rule-based mode, and no
award the notes mark as eligible is excluded.
