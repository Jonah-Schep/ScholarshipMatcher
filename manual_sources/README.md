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
