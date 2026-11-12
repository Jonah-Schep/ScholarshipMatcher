# Hand-entered scholarships

One CSV per source; the file name becomes the source id (`schev.csv` → `schev`). Load with:

```bash
python -m scholarmatch.scrapers.runner --sources manual
```

Columns (header row required; only `name` is mandatory; extra columns are kept in `extra`):

| Column | Example |
|---|---|
| `name` | Virginia Tuition Assistance Grant |
| `url` | page you copied it from |
| `sponsor` | SCHEV |
| `geo_scope` | `va`, `nc`, `sc`, `national`, `multi_state` |
| `amount_raw` | Up to $5,000 |
| `deadline_raw` | 3/1/2027 (leave blank if rolling) |
| `description` | what the award is |
| `eligibility` | copy the eligibility text **verbatim** — the rule extractor and agents quote from it |
| `aid_type` | `scholarship`, `grant`, `waiver`, `forgivable_loan`, `loan` (loans are filtered out) |

Intended for: SCHEV tables, NCSEAA programs, SC Tuition Grants, and small local organisations
(see `scholarmatch/scrapers/pending.py` for why these are not scraped). `_template.csv` shows the header.

`*.csv` files directly in this folder are scholarship sources. The `supplements/` folder below is not
(the loader only reads this folder, not its subfolders).

## Supplements: rules that are only in linked PDFs (`supplements/`)

Some state programs state their real eligibility rules only in linked PDFs (SC HOPE, LIFE, Palmetto Fellows and
the SC Need-based Grant). The scraper does not read PDFs (decision D2 in `docs/fix_plan.md`), so these rules are
typed by hand into `supplements/<source>.csv` (`sc_che.csv` → rows of the `sc_che` source). At ingest
(`scrapers/runner.ingest`, also used by `scripts/load_sample.py`) each filled-in row is appended to that
program's `eligibility` text, the PDF is added to its `sources`, and the row's `confidence` drops to `medium`.
The normal rule extraction then reads the text, and agents can quote it.

| Column | Meaning |
|---|---|
| `source_id` | the program's `source_id` (e.g. `life-scholarship`; see `data/export/sc_che.jsonl`) |
| `requirement` | the requirement, typed **as written** in the PDF, one requirement per row, ending with a full stop |
| `source_url` | the PDF you typed it from (must start with `http://` or `https://`; rows without one are ignored) |

Rows whose `requirement` starts with `TODO` (the shipped placeholders), and rows whose `source_id` starts with
`#`, are ignored. To fill one in, replace the `TODO(user): …` text with the PDF's wording and keep the URL.

**Check before you save.** Compare every typed value with the PDF (the PDF wins). Then:

1. Re-ingest: `python scripts/load_sample.py` (sample) or `python -m scholarmatch.scrapers.runner --sources sc_che`.
2. Run `python -m pytest -q`. The regression test applies this file to the SC CHE corpus rows. The four
   cases marked `WP6-supplement` in `tests/test_regression.py` expect `Citizenship = fail` for round 1 profile 3
   (not a U.S. citizen). Once the citizenship rows are filled in they pass, and pytest reports them as XPASS
   (strict): delete those entries from `XFAIL`.
3. Run `python scripts/eval_profiles.py --corpus`: `elig_lost` must stay 0 (the `sam` control student, SC
   resident, citizen, GPA 3.7, must keep LIFE, Palmetto Fellows and HOPE).

**How the extractor reads the text** (so the typed wording has the intended effect):

- Citizenship: "U.S. citizen", "United States citizen", "permanent resident" or "citizenship" makes a Citizenship
  check (a student who answered "No" fails it).
- GPA: a number next to "GPA" / "grade point average" ("a GPA of at least 3.0", "minimum 3.0 GPA") becomes a hard
  minimum, and students below it are **hidden**. If the PDF lets students qualify in more than one way (for example
  GPA *or* SAT *or* class rank, "two of the following three"), do **not** type a GPA sentence: the rules cannot
  read alternatives and would hide students who qualify another way. Type the citizenship row only.
- A sentence with "prefer", "priority" or "encouraged" is treated as a preference, not a rule.
