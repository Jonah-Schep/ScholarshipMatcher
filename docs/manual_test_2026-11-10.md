# Manual test — made-up student profiles (2026-11-10)

Run in Chrome against `streamlit run app.py` with the scraped catalog (77 rows: 50 VT AcademicWorks,
18 Queen City 2 College, 9 SC CHE). Eligibility judged by reading each listing on vt.academicworks.com / che.sc.gov.

**Caveat:** the AI agents never ran. Every call to the VT ARC endpoint returned
`PermissionDeniedError: API access is restricted to the VT Campus VPN` (30 `llm_failure` audit events per run),
so all results are the rule-based fallback. Re-run with the VPN on (tick "Re-run from scratch").

## Root causes
1. **VT scrape has titles only.** 49 of 50 VT rows have `detail_fetched: false` (no description, no eligibility).
   The hard filter therefore sees only "VT student" and passes everything. Re-scrape VT without `--max-details`.
2. **Unknown criteria score as certain.** A title-only row gets "Eligibility certainty 100% (1 of 1 stated
   requirements confirmed)" and 78/100. Missing text should lower certainty, not raise it.
3. **No rules for age, institution type, or membership-only programs** (SC CHE rows).
4. **Rule extraction misreads "enhancement" language as a major restriction** (false exclusions, see profile 3).

## Profile 1 — NC HS senior, Charlotte, GPA 3.1, applying to ECU, nursing
0 results. Correct: VT rows need VT, Queen City 2 College rows are a 2022 list (ended), SC CHE needs SC residency.
The catalog has nothing live for NC students outside VT.

## Profile 2 — VA (Loudoun) VT junior, Computer Science, GPA 2.4, no financial need, male
30 recommended, **28 likely ineligible**:

| Scholarship | Why not |
|---|---|
| College of Science Dean's Roundtable (#1, 82) | College of Science only; CS is in Engineering |
| Alie Reichling Memorial | Psychology majors in AC4P |
| Archie R. and Dorothy A. Moss | Montgomery Co./Radford HS grads, financial need |
| Buzz's Bunch | Registered with Services for Students with Disabilities |
| Caitlin Millar Hammaren | International Studies majors |
| CLAHS Faculty Campaign | Graduate students in CLAHS |
| Cobb Office Products | Minimum 3.0 GPA |
| Donald F. Morris | Rising seniors in Multimedia Journalism |
| Dunham Charitable | Engineering + demonstrated financial need (he has none) |
| English Family-Teacher Education | Graduate teacher-education students |
| Faculty-Staff Dependent and Spouse | Dependents of VT employees; entering freshmen/transfers |
| Hallie L. Hughes | CLAHS students |
| Harry Bramhall Gilbert | Chesapeake Public Schools grads, seniors |
| Herman C. and Carrie Watson | Northampton/Accomack county students |
| Hoban, Lee and Dance | College of Veterinary Medicine |
| Holdren Family | 3.0 GPA, need, Business/CLAHS/Biology, specific counties |
| Honors College Scholarship | Incoming first-year Honors students |
| IMPACT Scholarships | Incoming VT Carilion medical students |
| Kimberly Ware | Graduating seniors who finished CEIP co-op |
| Lena L. Giannotti | Rising seniors in Human/Child Development, 3.5 in-major GPA |
| Margaret Groseclose Skelton | Smyth County, CLAHS |
| Martin Luther and Clara B. Vaughan | Documented descendants of the Vaughans |
| Melinda Marie Somers | School of Communication majors |
| Merryman School Bus | Incoming students; relative in VA pupil transportation |
| MIT Scholars | Master of Information Technology students |
| Mo Donaldson Memorial | School of Communication majors |
| Virginia Tech Tribal Match | Recipients of a VA tribal scholarship |
| Timothy Owens Shepherd | Graduating seniors who finished CEIP co-op |

Possible but weak: Austin Michelle Cloyd Social Justice (open to all; needs social-justice activism),
Nagy & Jennifer Maalouf ("exceptional academic achievement" vs. 2.4 GPA).

## Profile 3 — SC (Richland) HS senior, GPA 2.8, not a U.S. citizen, going to USC, Biology
5 recommended, **all 5 likely ineligible**:

| Scholarship | Why not |
|---|---|
| Free Tuition for Residents Over 60 (#2, 75) | Must be 60 or older |
| SC WINS | Technical colleges only; she is going to a 4-year university |
| SC National Guard CAP | Guard members only (only flagged "verify military") |
| SC HOPE (Freshmen) | Needs 3.0 GPA and citizen/eligible non-citizen (rules are in the linked PDF, not the scraped text) |
| SC Need-based Grant | State aid; needs FAFSA and citizen/eligible non-citizen status |

Correct exclusion: Lottery Tuition Assistance (citizenship).
False exclusions (would hide awards from eligible students): LIFE ("for accounting, education") and Palmetto
Fellows ("for mathematics"). Both come from the STEM/education/accounting *enhancement* text, not a major requirement.

## Profile 4 — VT graduate student, Master of Information Technology, GPA 3.7, financial need
Nearly the same 30 as profile 2; 49 of 50 VT rows pass. The one award made for him, **MIT Scholars**, ranks #26 at the
same 78 as everything else. The top two are senior-only undergraduate awards (Dean's Roundtable; C.T. Tate Distinguished
Co-op Senior Award). Plausible: MIT Scholars, Austin Michelle Cloyd. Everything else is undergraduate-only, another
college, or another specific group.
