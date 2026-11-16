# Manual test, round 3 (WP8, 2026-11-14)

Run in Chrome against `streamlit run app.py` (port 8517) on the Windows checkout, after a fresh VT scrape.
Eligibility was judged by reading each listing's stored text. Branch `fix/wp8-round3`.

## Catalog after the re-scrape

`python -m scholarmatch.scrapers.runner --sources vt` (no `--max-details`): 187 VT rows, 93 open, 3 unknown status,
91 ended. **Every open or unknown VT row now has its detail page** (`detail_fetched = true`). The 91 title-only rows are
all ended awards: the scraper skips their detail pages unless `--include-ended`, and they are filtered at query time
anyway. So item 10 of "Still to fix" is closed. Catalog on this machine: 214 rows (VT 187, Queen City 2 College 18,
SC CHE 9). All 18 Queen City rows are ended (a 2021–22 list), and ECU / USC rows are only in the Mac catalog, so this
machine has **nothing live for an NC student**.

## Test students

Priya was entered through the Profile form in Chrome (this also checked the Age and Affiliations fields, which had not
been checked in the running app). The other four were saved with `db.save_profile` to save time and then run in the app.

| Profile id | Student | What it exercises |
|---|---|---|
| `r3_va_hs_cs` | Priya: Montgomery Co. (Blacksburg HS) senior → VT, Computer Science, GPA 3.7, need, age 17 | HS senior against VT's college-student awards |
| `r3_eastern_shore_ag` | Marcus: Northampton Co. VT junior, Animal Science, GPA 3.1, Pell, first-gen, 4-H | Eastern Shore locality awards, groups |
| `r3_charlotte_hs` | Tasha: Mecklenburg Co., West Charlotte HS senior, Business | NC coverage, empty result page |
| `r3_sc_over60` | Carl: SC, USC freshman, age 64, "None of these apply" ticked | Age check, group fails, SC CHE programs |
| `r3_vt_vetmed` | Lena: VT graduate student, Veterinary Medicine, staff-parent affiliation | Graduate levels, affiliation pass |

## What the round found and what changed

### Rule extraction (`rules-7`)
1. **Supplemental questions were read as eligibility rules.** Smithfield Foundation's application asks about an
   internship "for rising Seniors?", which became `class_years = [junior, senior]`, so a high-school senior was
   **excluded** for the wrong reason while the real limit (dependents of Smithfield employees) was missed. Rule
   extraction now reads `full_text(questions=False)`; the questions still count for essays and uploads.
2. **Group wording that was missed** (these awards passed with no "Specific group" check):
   "dependent children or dependent grandchildren of a current … employee" (Smithfield); "students who have a parent,
   grandparent, or legal guardian employed by or retired from …" (Merryman school-bus award); "transferring to Virginia
   Tech after completion of an associate degree" (VT–VCCS Transfer); "recognized as a student with a disability by
   Services for Students with Disabilities" (Buzz's Bunch); "students whose lives have been impacted by type 1
   diabetes" (Graham Stedman Neal).
3. **False limits removed:** "receipt of veterans' benefits … may affect the amount" set a Military requirement on the
   VCCS Transfer award; "If you are an individual with a disability and desire an accommodation, welcome!" set a
   Disability requirement on the Cloyd award.
4. **A preference clause hid a real rule.** "…at least a 3.0 GPA, … with preference to children of employees of Cobb"
   was a soft sentence as a whole, so the 3.0 minimum was lost. The clause before "with preference …" is now kept
   as a hard sentence; "nursing students are preferred" stays wholly soft.
5. **Levels:** "incoming undergraduate students" now means new students (Merryman: a VT junior fails, a senior gets
   in); "Graduate during the academic year" is no longer read as graduate school, and a named college year
   ("Senior Co-op student", C.T. Tate) means undergraduate; "VTCSOM students" (VT Carilion School of Medicine,
   IMPACT) is graduate level on VT rows.

### Ranking (`rubric-6`)
6. **Group-limited awards still filled the top ten.** A restrictive "Specific group" unknown cost only part of the
   25 certainty points, so "children of Smithfield employees only" (≈12.5 points) outranked open awards whose listing
   is merely thin (15 points). A restrictive unknown now caps certainty at 30%, the same as a title-only listing.
   Aisha's rule-based top ten went from 3 known-ineligible awards to 0 on the real catalog (1 after the corpus
   refresh added Faculty-Staff Dependent as a known case; it carries a "Specific group" Verify).

### App behaviour
7. **Rate limit.** VT ARC allows at most 10 calls in flight per user and answers `429 concurrent session limit`
   above that; each 429 turned a scholarship into the rule-based fallback. One run uses 6 workers, so two runs at once
   (two users, or two tabs) exceeded it. All provider calls now share one process-wide limit
   (`SM_LLM_MAX_INFLIGHT`, default 6).
8. **A traceback replaced the whole matches page** when an error escaped one scholarship's review. The trigger was a
   development artefact (Streamlit re-imported `scholarmatch.llm` while a run was live, so `LLMError` was a different
   class), but one item must never end a run: `_evaluate` now turns any per-item exception into the rule-based
   fallback (`item_error` audit event).
9. **Progress bar stuck at 0/N.** Results were collected in input order, so a slow first item hid all the finished
   ones. Items are now counted as they finish.
10. **Empty LLM run said "AI review unavailable".** With nothing passing the rule checks (Tasha), the header now says
    "AI review on (nothing passed the rule checks to review)".
11. **Regression corpus:** 47 synthetic VT rows replaced with the real listings (`export_regression.py`). Four
    expectations for Aisha were written against the paraphrase and were wrong for the real text (O. C. Thompson:
    out-of-state is required on every route, so fail; Cobb: field-of-study Verify; Cloyd and Adam Ward: "current
    undergraduate students" gives a high-school senior an Education-level Verify).

## Eval (rule-based, `scripts/eval_profiles.py`, as of 2026-11-12)

| | eligible | matches | known ineligible | ineligible in top 10 | eligible lost |
|---|---|---|---|---|---|
| Local catalog, WP7 (old 214-row catalog) | 154 | 103 | 38 | 14 | 0 |
| Local catalog after re-scrape, before fixes | 91 | 90 | 16 | 10 | 2 |
| Local catalog after fixes (`rules-7`, `rubric-6`) | 93 | 89 | 16 | 8 | 0 |
| Regression corpus after fixes (47 real VT rows) | 64 | 64 | 19 | 15 | 0 |

The 8 remaining top-ten cases on the local catalog are all awards the student has not ruled out: restrictive
"Specific group" unknowns (Vaughan descendants, Tribal Match, Faculty-Staff Dependent) that rank below every open
award but still reach the top ten because those profiles have fewer than ten better matches, and SC CHE programs for
profiles with only 3–5 matches in total (SCNG CAP with Military unknown, Free Tuition Over 60 with no age, HOPE and
Need-based Grant waiting on the PDF supplement). Decision D1 keeps such awards visible with a Verify flag unless the
student ticks "None of these apply". The corpus number is high for the same reason: Aisha's corpus set is 17 rows,
10 of them deliberately ineligible group awards.

## Rule-based results per new student (after fixes)

- **Priya / Aisha-like seniors:** open VT awards first; group-limited awards (Smithfield, Universal Leaf, Merryman,
  Faculty-Staff Dependent, VCCS Transfer) carry a "Specific group" Verify and rank below them.
- **Marcus:** Herman C. and Carrie Watson (Northampton / Accomack) passes Locality, as it should. VCCS Transfer shows
  a "Specific group" Verify (he is not a transfer student; correct as D1 says).
- **Tasha:** 0 matches, with the exclusion table (deadline passed 109, other school 96, other state 8, loan 1).
  Correct for this catalog; see "Catalog" above.
- **Carl:** Free Tuition for Residents Over 60 passes Age; SCNG CAP fails (not military); group awards fail (box
  ticked). Palmetto Fellows and the Need-based Grant pass for a 64-year-old USC freshman: their real rules (high-school
  senior criteria, citizenship) are only in the PDFs, i.e. the open "WP6-supplement" item.
- **Lena:** graduate-only awards pass; Faculty-Staff Dependent correctly fails Education level (incoming freshmen).
  Her "Daughter of a Virginia Tech staff member" affiliation does not pass that award's group check by itself, because
  the matcher needs a distinctive shared word and the listing's phrase stops at "employed"; it stays a Verify.
  IMPACT (MD students) passes for a vet-med student: nothing in the rules tells medicine from veterinary medicine.

## AI review (VT ARC, `openai_compat`)

ARC answered this time (it returned 502 / VPN errors in rounds 1–2), so this is the first live run of the agents and of
the WP7 Extractor merge.

| Run (Priya) | Code | Wall time | AI-reviewed | 429 rate limit | Timeouts |
|---|---|---|---|---|---|
| `2cf4745f525d` | before the fixes; overlapped other runs | lost (page dropped it) | – | 8+ | – |
| direct script | before the fixes; overlapped | stopped after 25 min at 18/30 | – | 11 | 1 |
| `8be5522922f7` | in-flight cap, old retries | 15 min | 13 of 25 | 8 | 4 |
| `7e57f7b97940` | final (cap + no timeout retries + backoff) | 12.5 min | **20 of 23** | **0** | 3 (> 300 s) |

What the agents did well:
- The Extractor found limits the rules missed, each with a verified quote: Montgomery County / Radford for the Moss
  award (Priya lives in Montgomery County and was told to confirm her high school), Smithfield / McGuireWoods /
  tobacco-grower / Tribal Program groups, the Faculty-Staff Dependent group.
- The Verifier rejected a Scorer claim built on Smithfield's internship *question* ("not an eligibility criterion")
  and an empty student-evidence claim; 5 of 24 Scorer answers were sent back and redone.
- The Filter's "Check" notes are useful and specific (Honors College invitation, GEO study-abroad plans, Buzz's Bunch
  "one full semester connected to SSD"). Group-limited awards end at the bottom with ~0% and plain advice ("Skip this
  scholarship unless a parent or grandparent is a current or retired Smithfield Foods employee").

What is still wrong or slow:
- **Speed.** Extractor answers are 1,400–2,500 tokens and take 20–60 s; a 30-item run takes 10–15 minutes even with
  42 of 70 answers cached. That is too slow for a page someone watches. Options: a smaller `SM_MAX_LLM_CANDIDATES`,
  a faster model for the Extractor (`SM_MODEL_EXTRACTOR`), or running the Extractor at ingest time instead of per run.
- **Levels union (WP7 policy).** For Merryman the Extractor added `undergrad`; the union with the rule value
  (`hs_senior`, "incoming undergraduate") re-admits current VT students. A list union cannot remove a wrong widening.
- **Grounding rejections:** `destination_schools` and `levels` were the fields the Extractor filled without a verbatim
  quote (Buzz's Bunch); the rule value was kept, as designed.

Screenshot of the final run: Chrome, profile `r3_va_hs_cs`, header "AI-reviewed (20 of 23)".

## Not fixed in this round
1. Levels union in the Extractor merge (above): needs a decision on whether the LLM may widen `levels`.
2. IMPACT (VTCSOM, MD students) passes for a veterinary-medicine graduate student.
3. Affiliation matching needs a distinctive shared word, so "Daughter of a Virginia Tech staff member" does not pass
   "spouses or dependents of salaried, full-time staff or faculty employed …".
4. Group phrases are cut at 90 characters or at "by", so some Verify notes read oddly ("… employed by or retired from a
   Virginia", "USATT ) member …"). Cosmetic.
5. AI run time on ARC (above).
6. HOPE / LIFE / Palmetto Fellows / Need-based Grant rules still wait on the PDF values (Palmetto passes for a
   64-year-old freshman until then).
7. Anthropic provider not run live. The Data & audit page was not opened in this round.
8. No live NC rows on the Windows catalog; an NC student gets an empty list here.
