# Scholarship Scraper — Brief for Claude Code

## Goal
Scrape public scholarship listings and save them as structured data, scoped to North Carolina, Virginia, and South Carolina plus national awards, from four groups of sources:

- **Part 1: University portals.** Three schools on Blackbaud AcademicWorks. One scraper with a per-site base URL should work for all three.
- **Part 2: Charlotte, NC local scholarships.** College Board BigFuture (filtered to Charlotte and Mecklenburg County) plus counselor-maintained Google Sites lists.
- **Part 3: CareerOneStop Scholarship Finder (national).** U.S. Department of Labor database, accessed through its official API, not scraping.
- **Part 4: State aid programs (VA, NC, SC).** SCHEV (Virginia), NCSEAA (North Carolina), SC Commission on Higher Education, and SC Tuition Grants Commission program pages. This is a small, high-value set. CFNC is reviewed here but **excluded**.

Build each part as a separate module that writes to the same output format (see "Output" at the end).

---

# Part 1: University portals (AcademicWorks)

## Sites to scrape

| School | Base URL |
|---|---|
| Virginia Tech | https://vt.academicworks.com |
| University of South Carolina (Columbia) | https://sc.academicworks.com |
| East Carolina University | https://ecu.academicworks.com |

**Do not scrape:** UNC Wilmington (uncw.scholarships.ngwebsolutions.com). Most of its content sits behind a student login. Do not attempt to log in anywhere.

## robots.txt (checked Nov 3, 2026)
All three AcademicWorks sites allow everything for `User-Agent: *` (only `EasouSpider` is blocked). Re-check robots.txt at the start of each run and stop if it has changed to disallow these paths.

## Page structure (known)
- **Listing pages:** `{base}/?page=N`. They are paginated, and each entry shows the scholarship name, a truncated description, the deadline (a date or "Ended"), and the amount (a dollar figure or "Varies").
- **Detail pages:** `{base}/opportunities/{id}`. These hold the full description and eligibility text.
- USC has hundreds of listing pages, while VT and ECU have far fewer.

Inspect the real HTML before writing selectors. Don't assume class names. If AcademicWorks exposes a cleaner JSON endpoint or export link on public pages, prefer it, but don't call anything that requires authentication.

## Crawl approach
1. For each site, walk `?page=1, 2, 3…` until a page returns no listings (or repeats the previous page).
2. Collect every opportunity ID and link from the listing pages.
3. Fetch each detail page once.
4. Skip anything that redirects to a sign-in page.

## Politeness rules (required)
- At most 1 request every 1.5–2 seconds per site, with no parallel requests to the same host.
- Set a descriptive User-Agent with contact info, e.g. `ScholarshipResearchBot/0.1 (contact: YOUR_EMAIL)`.
- Back off and retry on 429/5xx with exponential delay, and give up after 3 tries.
- Cache raw HTML locally so re-runs and parser fixes don't re-hit the sites.
- Prefer running off-peak (evenings or weekends, Eastern time).

## Fields to extract
| Field | Notes |
|---|---|
| `school` | `vt`, `usc`, or `ecu` |
| `opportunity_id` | From the URL |
| `url` | Full detail-page URL |
| `name` | |
| `amount_raw` | As shown ("$1,500", "Varies", "$3,000 per year for 4 years") |
| `amount_min` / `amount_max` | Parsed numbers where possible, else null |
| `deadline_raw` | As shown |
| `deadline` | ISO date, or null if "Ended" or missing |
| `status` | `open` / `ended` / `unknown` |
| `description` | Full text from the detail page |
| `eligibility` | Any structured eligibility or requirements text, if separated on the page |
| `college_or_department` | If shown (many names say e.g. "College of Business") |
| `scraped_at` | Timestamp |

## Part 1 notes
- Most of these awards are only for enrolled (or admitted) students at that school, and many are matched through each school's general scholarship application rather than applied for individually. Keep that context in mind for how the data is used.
- A permissive robots.txt isn't a data license, so check Blackbaud's AcademicWorks terms of use before any public or commercial use of the data.

---

# Part 2: Charlotte, NC local scholarships

## 2A. College Board BigFuture (main source)

**Base:** https://bigfuture.collegeboard.org

**robots.txt (checked Nov 3, 2026):** `/scholarships/` pages are allowed. Disallowed paths include `/search/`, `/admin/`, `/user/*`, `/core/`, `/profiles/`, and `/media/oembed`. **Do not use `/search/`.** Re-check robots.txt at the start of each run.

**Before building:** Check College Board's site terms of use for restrictions on automated access. If they prohibit scraping, stop and report back rather than proceeding.

### Approach
1. Fetch the scholarship sitemap: `https://bigfuture.collegeboard.org/scholarships/sitemap.xml`. It may be a sitemap index that points to child sitemaps, so follow them.
2. Collect all scholarship detail URLs (pattern: `/scholarships/{slug}`, e.g. `/scholarships/charlotte-mecklenburg-schools-scholarship`).
3. Fetch each detail page and **keep only scholarships tied to the Charlotte area**, meaning any of:
   - Location fields list City = Charlotte, or County = Mecklenburg
   - "Current School" is a Charlotte-area high school
   - Name or description mentions Charlotte, Mecklenburg, or CMS / Charlotte-Mecklenburg Schools
4. Log the URLs that matched, so the filter can be audited.

There are likely tens of thousands of URLs in the sitemap. Two ways to keep the crawl small:
- First, check whether a cheap pre-filter works on the slug or page title (e.g. slugs containing `charlotte` or `mecklenburg`). It will miss some, so treat it as a first pass.
- Then, with cached HTML, run the full location-field filter over everything. A full crawl at 1 request every 2 seconds can take a day or more, so run it in the background, resumable, and cache every page.

### Rendering
Inspect a detail page first. If the scholarship data is in the initial HTML (or in an embedded JSON blob like `__NEXT_DATA__` or JSON-LD), parse that directly. Only fall back to a headless browser (Playwright) if the content is rendered client-side and not present in the HTML.

### Fields on BigFuture detail pages (seen so far)
Name, description, open date, close date, status ("Accepting Applications" / "Not Open Yet"), award amount (often "Varies"), essay required (Y/N), need-based (Y/N), merit-based (Y/N), degree level, current grade, location (country/state/city/county), current school, and sponsor. Map these into the shared schema below, and put anything that doesn't fit into `extra` as JSON.

## 2B. Counselor-maintained Google Sites lists

**robots.txt for sites.google.com (checked Nov 3, 2026):** Page paths are allowed. `/feeds` and internal `/*/_/` paths are disallowed (except `/*/_/rsrc/` and `/_/atari/*`). Fetch only the public page URLs.

### Pages to scrape
| Source | URL |
|---|---|
| Queen City 2 College (deadline-sorted list, with a local CMS / Charlotte / Mecklenburg section) | https://sites.google.com/view/queencity2college/scholarships |
| West Charlotte HS | https://sites.google.com/cms.k12.nc.us/wcstudentsupport/scholarships-financial-aid |
| Northwest School of the Arts | https://sites.google.com/cms.k12.nc.us/nwsastudentservices/scholarships |
| Hawthorne Academy | https://sites.google.com/cms.k12.nc.us/hawthornestudentservicesdept/scholarships-2324 |
| North Mecklenburg HS | https://sites.google.com/cms.k12.nc.us/north-meck-student-services/college-corner/scholarships |
| Independence HS | https://sites.google.com/cms.k12.nc.us/cdcindependencehighschool/college-and-career-info/scholarships |

Some of these URLs are from past school years (e.g. `scholarships-2324`). If one 404s or looks stale, log it and skip it. Don't guess replacement URLs.

### Approach
- Each page is one long, hand-edited page, not a database. Expect free-form text, headings, tables, and embedded Google Docs. There's no consistent structure.
- Extract each scholarship as a block: name, sponsor (if stated), amount, deadline, eligibility text, and the outbound link (if any).
- Embedded Google Docs (iframes to `docs.google.com/document/...`) can be fetched as plain text via `/export?format=txt` on the same document ID. Only do this for docs embedded on these pages.
- Parsing will be imperfect. Keep the raw text block for every extracted item and flag low-confidence rows instead of dropping them.
- Many of these lists repeat the same scholarships (and some duplicate BigFuture or Foundation For The Carolinas). Deduplicate on normalized name plus sponsor, but keep a `sources` list showing every page an item appeared on.

## Part 2 notes
- These sources are only a starting point. Many Charlotte awards run through Foundation For The Carolinas (fftcscholarships.communityforce.com), which is **not** included here because it's an ASP.NET WebForms site that would need Playwright and postback handling. It can be added later as its own module.
- Small one-off org pages (Assistance League of Charlotte, Charlotte Links, Zetas of Charlotte, Charlotte Knights) aren't worth automating. Enter those by hand if needed.

---

# Part 3: CareerOneStop Scholarship Finder (national)

## What it is
The U.S. Department of Labor's Scholarship Finder covers 8,000+ scholarships, fellowships, grants, and other awards. Providers must apply and be vetted to be listed, and none of the awards charge an application fee. It's the cleanest national source in this brief.

## Access: use the API, don't scrape
- CareerOneStop offers its data through official Web API services: https://www.careeronestop.org/Developers/WebAPI/web-api.aspx
- Data from the CareerOneStop APIs is **open data under USDOL's Open Data Policy**, so this source has no licensing concerns.
- Access requires free registration ("Request data access" on that page). Registration gives a **user ID and API token**. The human owner has to register. **Don't register on their behalf.** Read both values from environment variables (e.g. `COS_USER_ID`, `COS_API_TOKEN`), and never hard-code or log them.
- CareerOneStop notes that API details change from time to time. **Read the current technical documentation before writing code.** Confirm that a scholarship endpoint exists and check its exact path, parameters, paging, and rate limits. Do not guess endpoint URLs. If no scholarship endpoint is listed, stop and report back rather than scraping the Scholarship Finder website.

## Approach
1. Read the API docs and find the scholarship search endpoint and its parameters.
2. Pull the full database. If the endpoint requires a keyword or filter, page through with the broadest query the API allows, or iterate over its filter values (e.g. state, level of study, affiliation) and deduplicate.
3. If there's a separate detail endpoint, fetch details for each award. Otherwise use what the search response returns.
4. Respect any documented rate limits. If none are documented, use the same politeness defaults as the scrapers.
5. **Scope to NC, VA, and SC by filtering locally, not in the query.** Pull the full database, then add a `geo_scope` field to every award:
   - `national`: no state or residence restriction (open to students in any state)
   - `nc`: restricted to North Carolina residents or NC colleges
   - `va`: restricted to Virginia residents or VA colleges
   - `sc`: restricted to South Carolina residents or SC colleges
   - `multi_state`: restricted to two or more of NC, VA, and SC (e.g. "residents of the Carolinas"). List the states in `extra.states`.
   - `other_state`: restricted only to states outside NC, VA, and SC
   Base this on both residence location and college location if the API returns them separately. An award open to NC/VA/SC plus other states counts as `multi_state` or the single matching state, not `other_state`. Keep `other_state` rows in the raw file but exclude them from the default combined output. The default view is `national` + `nc` + `va` + `sc` + `multi_state`.
   - Why not use the API's state filter: a strict state filter may drop national awards, and those are most of the database. If the API's state filter is reliable and clearly includes national awards, it's fine to use it as a cross-check.
   - Flag Charlotte / Mecklenburg mentions in name or eligibility so these rows can be joined with Part 2.
   - Log counts per `geo_scope` in the run summary.

## Fields
Map whatever the API returns into the shared schema. Expect roughly: name, sponsoring organization, award amount, deadline, level of study, eligibility (state, affiliation, field of study, etc.), and a link to apply. Put API-specific fields that don't fit into `extra`. Use `source = careeronestop` and the API's own award ID as `source_id`.

---

# Part 4: State aid programs (VA, NC, SC)

These are a few dozen state programs, not thousands of listings. They change mostly once a year (award amounts, deadlines, statutes). Build this as a small fetch-and-parse step that produces a draft file a human reviews, not a crawler. Fetch only the URLs listed below, and don't follow links outward.

## 4A. SCHEV (State Council of Higher Education for Virginia)

| Page | URL | What's there |
|---|---|---|
| List of state aid programs | https://www.schev.edu/financial-aid/financial-aid/undergraduate-and-graduate-financial-aid-programs | **Main source.** HTML tables (Undergraduate, Specific Undergraduate, Graduate, Specific Graduate) with columns: Program, Application Contact, Participating Colleges, Residency Requirement, Eligibility, Award Amount |
| Aid regulations table | https://www.schev.edu/institutions/policies-guidelines/financial-aid-domicile-officers/financial-aid-regulations | Statute (Code of Virginia §), Appropriation Act item, VAC regulation, and administering office per program. It includes a few programs not on the main list (e.g. Enslaved Ancestors College Access Scholarship, License Plate Scholarships, New Economy Workforce Grant, Industry-Based Credentials). |
| Primary state aid programs | https://www.schev.edu/financial-aid/financial-aid/federal-state-financial-aid | Overview only. Fetch it for context, but it doesn't add rows. |
| Financial aid overview | https://www.schev.edu/financial-aid/financial-aid | Overview only, same as above. |

**robots.txt:** Not yet verified. When I tried to fetch `https://www.schev.edu/robots.txt`, the site's bot detection blocked the request (the program pages themselves loaded fine). Before running, check robots.txt from your environment. **If the site blocks requests or shows a challenge page, stop. Don't try to get around it.** It's only two tables, so copying them by hand is a fine fallback.

**Parsing:**
- Parse each table row into one record. Keep the table name it came from (`undergraduate`, `undergraduate_specific`, `graduate`, `graduate_specific`) in `extra.schev_table`.
- Several programs appear in both the undergraduate and graduate tables (e.g. Brown v. Board, Senior Citizen Tuition Waiver, Virginia Teacher Scholarship/Loan). Merge them into one record and record all the levels they apply to.
- Join the regulations table by program name (fuzzy match, logging any unmatched names) to add `extra.statute`, `extra.appropriation_item`, and `extra.vac_regulation`. Programs found only in the regulations table become their own rows with `confidence = medium`.
- Award amounts are mostly descriptive ("Varies Based on Need, Up to Tuition/Fees", "Up to $10,000"). Fill in `amount_min` / `amount_max` only when an explicit dollar figure appears.
- Map "Residency Requirement" to `geo_scope`: "Virginia Residents Only" → `va`, "Not Restricted" → `national`, and anything else → `va` with the raw text kept.
- Mark entitlements and waivers that aren't really scholarships (e.g. Academic Common Market, Senior Citizen Tuition Waiver, loan repayment programs) with `extra.aid_type` = `waiver`, `loan_repayment`, `forgivable_loan`, `grant`, or `scholarship`, so they can be filtered out.

## 4B. NCSEAA (North Carolina State Education Assistance Authority)

| Page | URL | What's there |
|---|---|---|
| Programs | https://www.ncseaa.edu/programs-2/ | **Main source.** Lists higher-ed scholarships (Next NC Scholarship, NC Need-Based Scholarship for private colleges, NCSSM and UNCSA UNC Tuition Grant, Golden LEAF Scholars, UNC Campus Scholarships), forgivable loans (FELS, NC Teaching Fellows, Principal Fellows), and three programs NCSEAA is fiscal agent for (NC Reach, The Washington Center Scholarship, Patriot Star Foundation Scholarship). It also lists K-12 programs (Opportunity Scholarship, ESA+), which should be **excluded** because they're for private K-12 tuition, not college. |
| State grants and scholarships rules | https://www.ncseaa.edu/psr/higher-education-state-grants-scholarships/ | Statutes and rules for each grant and scholarship. Use it to fill `eligibility` and `extra.statute`. |
| Program statutes and rules index | https://www.ncseaa.edu/psr/ | Index linking to the category pages. Also fetch `https://www.ncseaa.edu/psr/higher-ed-forgivable-loans` for forgivable-loan rules. |
| Forgivable loans (FELS) | https://www.ncseaa.edu/loan/state-funded-loans/forgivable-loans-fels/ | FELS details (eligible programs, service requirement, amounts) |
| Sitemap | https://www.ncseaa.edu/sitemap_index.xml | Use it only to confirm the URLs above still exist, not to crawl the whole site. |

**robots.txt (checked Nov 3, 2026):** https://www.ncseaa.edu/robots.txt
- It **explicitly blocks AI and LLM crawlers** (ClaudeBot, anthropic-ai, GPTBot, CCBot, PerplexityBot, etc.). This scraper isn't one of those bots, but **don't use any AI-crawler User-Agent string.** Use the descriptive custom User-Agent from the politeness rules.
- For all other bots, the rules block WordPress internals, `/wp-json/`, feeds, tag/category/author/archive paths, search, dated archive paths (2009–2023), `/events/`, and **any URL with a query string (`/*?`)**. The pages listed above are all allowed.
- It sets **`Crawl-delay: 10`**. Wait at least 10 seconds between requests to ncseaa.edu, and honor it even though a Yoast-generated block at the bottom of the file repeats `User-agent: *` with an empty Disallow. Follow the stricter rules.
- Note that the site owner clearly doesn't want AI systems harvesting it. Keep this to the handful of pages listed, and consider simply transcribing them instead.

**Parsing:**
- One record per program. Many have no dollar amount on the program page, so pull amounts from the rules pages where stated, or leave them null.
- `geo_scope = nc` for all of them (they require NC residency or attendance at an NC school; check each rules page).
- Set `extra.aid_type` (`scholarship`, `grant`, `forgivable_loan`, `loan`, `fiscal_agent`) so loans (NC Assist) and forgivable loans can be filtered out.
- For UNC Campus Scholarships, eligibility is set by each UNC campus. Record it once with a note, not one row per campus.

## 4C. CFNC (College Foundation of North Carolina): **do not scrape**

- Pages reviewed: scholarship search (https://www.cfnc.org/pay-for-college/scholarship-search/), grants and scholarships overview, Next NC Scholarship page, robots.txt, and Terms of Use.
- **robots.txt** allows everything (`User-agent: * / Allow: /`). **But the Terms of Use (effective April 19, 2021) prohibit it:**
  - Users may not obtain information through means not intentionally provided by the site, explicitly including data mining, robots, or similar data gathering and extraction tools.
  - Users may not reproduce, duplicate, copy, sell, trade, or otherwise distribute content from the site without a separate agreement.
- The scholarship search also loads its results with JavaScript (the static HTML shows "No Scholarships found"), so scraping it would mean browser automation, which the terms specifically forbid.
- **Action:** Exclude CFNC from automated collection. The Next NC Scholarship is already covered via NCSEAA (4B). If the CFNC scholarship database is wanted, the owner should contact CFNC (866-866-2362) to ask about a data-sharing agreement. Don't build a workaround.
- The CFNC search page promotes the **NC Community Foundation** (https://www.nccommunityfoundation.org/scholarships), which offers 130+ scholarships through one application. That's a separate site that hasn't been reviewed. Don't scrape it until its robots.txt and terms have been checked.

## 4D. SC Commission on Higher Education (CHE)

| Page | URL | What's there |
|---|---|---|
| Scholarships and grants for SC residents | https://che.sc.gov/students-families-and-military/scholarships-and-grants-sc-residents | **Main source.** Every state program on one server-rendered page (Drupal 10) |
| Additional scholarship searches | https://che.sc.gov/additional-scholarship-grant-searches | Links only to national search sites (CareerOneStop, Fastweb, Going Merry, etc.). **Don't scrape it or follow its links.** It's listed only so it isn't mistaken for a database. |

**Programs expected on the main page** (use these to check the parse is complete; flag any missing or new ones):

| Program | Award (as described by owner, verify against the page) |
|---|---|
| Palmetto Fellows Scholarship | Up to $6,700 freshman year, $7,500 after |
| LIFE Scholarship | Verify on page |
| SC HOPE Scholarship | Freshman year only |
| SC Need-based Grant | Up to $3,500 full-time |
| Lottery Tuition Assistance | $80 per credit hour at two-year schools |
| SC National Guard College Assistance Program | Up to $12,000 a year |
| SC WINS | For technical colleges |
| Free tuition for residents over 60 | Waiver (`aid_type = waiver`) |
| Nursing faculty tuition reimbursement | Reimbursement (`aid_type = reimbursement`) |

**robots.txt** (reported by owner as the standard Drupal default, which blocks only admin, login, and search paths, with no crawl delay and no bot blocking): https://che.sc.gov/robots.txt. Re-check it at the start of each run. The footer carries only the SC.gov privacy policy, and no terms prohibiting automated access have been found.

**Parsing:**
- One record per program from the main page: name, description, award amount, eligibility, and a link to the program's own section or page.
- Each program links to rules, FAQs, and eligible-institution lists, mostly PDFs under `/sites/che/files/Documents/`. **Record these links in `extra.documents`** (title + URL). Download and extract text only from the rules and FAQ PDFs, to fill `eligibility` and amounts the page doesn't state. Skip the eligible-institution lists unless they're needed later.
- `geo_scope = sc`. These programs require SC residency.
- Set `extra.aid_type` (`scholarship`, `grant`, `waiver`, `reimbursement`, `tuition_assistance`).
- Note the stacking rules where the page states them (e.g. Palmetto Fellows, LIFE, and HOPE are generally mutually exclusive) in `extra.notes`.

## 4E. SC Higher Education Tuition Grants Commission

| Page | URL | What's there |
|---|---|---|
| Site home | https://sctuitiongrants.org | A single program: need-based grants for SC residents attending private SC colleges (roughly the counterpart to Virginia's Tuition Assistance Grant and North Carolina's Need-Based Scholarship). Students apply through the FAFSA. |

**robots.txt** (reported by owner; WordPress, blocks only `/wp-admin/`, and lists sitemaps): https://sctuitiongrants.org/robots.txt. Re-check it at the start of each run.

**Approach:**
- Use the sitemap only to find the pages describing eligibility, award amounts, participating colleges, and how to apply. Fetch those few pages, not the whole site.
- Produce **one record** for the Tuition Grant, with eligibility rules, the current maximum award and deadline (if stated), and the list of participating colleges in `extra.participating_colleges`.
- `geo_scope = sc`, `aid_type = grant`.

---

# Output (all parts)

## Shared schema
Use the Part 1 field list, with these changes so all sources fit one table:

| Field | Notes |
|---|---|
| `source` | `academicworks_vt`, `academicworks_usc`, `academicworks_ecu`, `bigfuture`, `gsite_{short_name}`, `careeronestop`, `schev`, `ncseaa`, `sc_che`, or `sc_tuition_grants` |
| `source_id` | Opportunity ID (AcademicWorks), slug (BigFuture), API award ID (CareerOneStop), or a hash of name + sponsor (Google Sites) |
| `school` | For Part 1 only, otherwise null |
| `region` | `charlotte` for Part 2 rows |
| `geo_scope` | `national`, `nc`, `va`, `sc`, `multi_state`, or `other_state`, used by every source (Part 1 rows take their school's state; Part 2 rows are `nc`) |
| `sponsor` | If known |
| `sources` | List of every URL the item was found on (after dedup) |
| `confidence` | `high` for structured sources, `low` / `medium` for parsed free text |
| `raw_text` | The original text block (Google Sites rows especially) |
| `extra` | JSON for source-specific fields (e.g. BigFuture's essay / need / merit flags) |

All other Part 1 fields (`name`, `url`, `amount_raw`, `amount_min`, `amount_max`, `deadline_raw`, `deadline`, `status`, `description`, `eligibility`, `scraped_at`) apply to every source.

## Files
- One JSON Lines file per source plus a combined CSV.
- Deduplicate within each source on (`source`, `source_id`), and across Part 2 sources on normalized name + sponsor.
- Print a summary at the end showing counts per source, how many are open vs. ended, how many low-confidence rows there are, and any parse failures or skipped URLs.

## Politeness (all parts)
The Part 1 politeness rules apply to every host. Note that sites.google.com is a single host for all the Google Sites pages, so rate-limit them together.
