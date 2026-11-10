# ScholarMatch

Matches high-school seniors and college students in VA / NC / SC to scholarships they can realistically
win, and explains every recommendation. Deterministic rules decide eligibility; a supervised multi-agent
pipeline (Extractor → Filter → Scorer → Verifier) judges fit; code computes every number.

Design docs: [docs/requirements.md](docs/requirements.md) · [docs/architecture.md](docs/architecture.md)

## Quick start

```bash
pip install -r requirements.txt
```

```bash
python scripts/load_sample.py
```

```bash
streamlit run app.py
```

`load_sample.py` loads a small real snapshot (77 rows) with no network access so you can try the app
immediately. With no LLM key the app runs in **rule-based mode**; add a key to turn the agents on.

## Configuration (`.env`, copy from `.env.example`)

| Setting | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Enables the agents with Claude (default model `claude-opus-5-5` for every role). |
| `SM_MODEL_EXTRACTOR` / `_FILTER` / `_SCORER` / `_VERIFIER` / `_CHAT` | Per-role model override, e.g. a cheaper model for extraction and screening. |
| `SM_LLM_PROVIDER=openai_compat` + `SM_OPENAI_BASE_URL`, `SM_OPENAI_API_KEY`, `SM_OPENAI_MODEL` | Use any OpenAI-compatible endpoint instead (e.g. a university-hosted gateway). |
| `SCRAPER_CONTACT_EMAIL` | Required before scraping; goes in the User-Agent. |
| `SM_MAX_LLM_CANDIDATES` (30), `SM_MAX_RESULTS` (15) | Cost cap per run and list length. |

Rough cost per match run with agents on: up to 30 scholarships × about 4 calls each on the first run;
repeat runs are served from the cache.

## Building the catalog

```bash
python -m scholarmatch.scrapers.runner --sources vt,ecu,sc_che,gsites,manual
```

```bash
python -m scholarmatch.scrapers.runner --sources usc --max-pages 20
```

The runner re-checks robots.txt, waits 2 s between requests per host, caches every page under
`data/http_cache/`, and prints a summary. USC has hundreds of listing pages: run it in pieces, off-peak;
re-runs reuse the cache. Output: `data/scholarmatch.db`, `data/export/<source>.jsonl`, `data/export/combined.csv`.

### Source status

| Source (brief part) | Status |
|---|---|
| AcademicWorks — VT, USC, ECU (1) | **Implemented.** Selectors verified on VT; same platform for the others. |
| SC Commission on Higher Education (4D) | **Implemented.** PDFs are linked in `extra.documents`, not downloaded. |
| Google Sites counselor lists (2B) | **Implemented, heuristic.** The Queen City 2 College page is a 2022 list: rows are kept but marked ended. Embedded Google Docs are not fetched yet. |
| Hand-entered CSVs (`manual_sources/`) | **Implemented.** See `manual_sources/README.md`. |
| SCHEV (4A) | **Manual.** Site returns 403 to automated clients; brief says stop and copy the tables by hand. |
| NCSEAA (4B), SC Tuition Grants (4E) | **Manual.** NCSEAA blocks AI crawlers and the brief suggests transcribing; SC Tuition Grants is one record. |
| College Board BigFuture (2A) | **Not built.** The brief requires confirming College Board's terms first; they could not be retrieved here. |
| CareerOneStop API (3) | **Not built.** Needs your `COS_USER_ID` / `COS_API_TOKEN` and the current API docs (unreachable from the build environment); the brief forbids guessing endpoints. The geo_scope rule is implemented and tested. |
| CFNC (4C) | **Excluded** — terms prohibit automated collection. |

Also note the brief's caveat: a permissive robots.txt is not a data licence. Check Blackbaud's AcademicWorks
terms before any public or commercial use.

## Tests

```bash
python -m pytest -q
```

Tests use a temporary database, real page fixtures for the parsers, synthetic scholarships for the
pipeline, and a scripted fake model for the agents (no network, no key).

## Layout

```
app.py                     Streamlit UI
scholarmatch/
  config.py  models.py  db.py  textutil.py
  llm/                     provider interface + response cache; anthropic, openai_compat, offline
  scrapers/                http (politeness), academicworks, state_aid, gsites, manual, pending, runner
  ingest/                  scam screen, rule-based criteria extraction
  matching/                hard_filter (SQL + rule checks), rubric (all scoring math)
  agents/                  workers (prompts + calls), supervisor (orchestration), chat
scripts/load_sample.py     offline sample catalog
manual_sources/            hand-entered CSVs
tests/                     pytest suite + fixtures
docs/                      requirements and architecture as built
```

## Limits to know about
- "Est. chance" is a transparent heuristic (pool size, number of awards, fit), not a statistic.
- The live LLM paths have not been run against a real endpoint yet; do one end-to-end run with your key and
  read the Supervisor log on the **Data & audit** page before relying on it.
- Profiles are local handles with no login. Do not host this publicly as-is.
