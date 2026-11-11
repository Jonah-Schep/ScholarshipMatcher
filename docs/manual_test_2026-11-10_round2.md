# Manual test, round 2: made-up student profiles (2026-11-10)

Run in Chrome against `streamlit run app.py` (port 8517) on a copy of the full scraped catalog
(2,467 rows: 1,235 ECU, 1,000 USC, 187 VT, 36 Google Sites, 9 SC CHE). Profiles were entered through the
Profile form. I judged eligibility by reading each listing's stored description.

**Caveat: the AI agents never finished a review.** The OpenAI-compatible endpoint (VT ARC) returned
`502 backend 'GLM-5.3' unavailable` for most calls. Every result in every run has `mode = "heuristic"`.
The page still says **"AI-reviewed"** in the run header, because the run's mode is `llm` even when 0 items
were AI-reviewed. Each card does show "An AI review step failed".

## Summary

| Profile | Matches | Clearly ineligible | Ineligible in top 10 |
|---|---|---|---|
| 1. Maya: NC (Pitt Co.) HS senior → ECU, Nursing, GPA 3.2, need, first-gen | 26 | 11 | 4 |
| 2. Derek: SC USC sophomore, Mechanical Eng., GPA 2.6, no need | 6 | 4 (incl. #1) | 4 |
| 3. Aisha: VA (Henrico) HS senior → VT, Business/Finance, GPA 3.9, need | 24 | 14 | 8 |

## Root causes (most impact first)

1. **Major matching runs at the group level.** `hard_filter.check` passes "Field of study" when
   `field_groups(c.majors) & field_groups(p.majors)` overlap. Nursing, dental medicine, PA, OT, vet and
   "health sciences" are all one `health` group, so a nursing student passes dental and PA awards. Computer
   Science / Computer Engineering and Mechanical Engineering share a group, so the CS-only award ranks #1 for an ME student.
   A group overlap should give `unknown` at most, not `pass`.
2. **Affiliation-only awards are never extracted.** "children of employees of X", "direct descendants of",
   "children of tobacco growers", "receiving a Virginia tribal scholarship", "dependents of VT faculty/staff",
   "active USATT member", "transferring after an associate degree" produce no criteria, so these awards pass with
   "Eligibility certainty 100%" and score 78–80. This is most of the VT list.
3. **"graduating senior" is read as a high-school senior.** `extract_rules.py:140` maps `graduating senior` to
   `levels=["hs_senior"]`, so a college co-op award for graduating seniors (Kimberly Ware) **passes** "Education level" for a high-school student.
4. **Locality and specific-school limits are missed.** "students from Northampton or Accomack counties", "graduated
   from the Chesapeake Public School system", "Every senior at West Charlotte High School enrolling at Central
   Piedmont Community College" give no locality, high school or destination check.
5. **Department and program wording without a "major" keyword is missed.** "students majoring in Fashion
   Merchandising", "enrolled in the Department of Occupational Therapy", "declared major in Recreational Therapy",
   "Child Development and Family Relations student" give `majors=[]` from the rules, so there is no field check.
6. **Graduate programs are not recognised as graduate-level.** Clinical Nurse Specialist (MSN/DNP), Physician
   Assistant Studies and Dental Medicine are graduate or professional programs but get no level restriction.
7. **SC CHE problems from round 1 are still open:** Free Tuition for Residents Over 60 (age), SC WINS (technical
   colleges only), SC National Guard CAP (Guard members; only `Military=unknown`).
8. **"Out-of-state students" is not a residency rule.** O. C. Thompson (#2 for Aisha) is for out-of-state students; she is a VA resident.

Smaller UI issues seen:
- Check label truncated to **"U"**: `label.split(".")[0]` on "U.S. citizen or permanent resident." (`hard_filter.py:152`).
- Andrew and Margaret Little Real Estate fund shows **Award $0.00**; it is program support, not a student scholarship.
- Run header says "AI-reviewed" when no item was AI-reviewed (see caveat).

## Profile 1: Maya, NC HS senior → ECU, Nursing (run `fc70e4fc8772`)

Ineligible or almost certainly ineligible:

| # | Score | Scholarship | Why not | Rule result |
|---|---|---|---|---|
| 5 | 86 | Growing the Future of Clinical Nurse Specialists | CNS is a graduate nursing program | Field = pass (nursing) |
| 8 | 86 | Merchandising Advisory Board and Faculty Fund | Fashion Merchandising majors only | no field check |
| 9 | 86 | Robert S. and Ann P. Griffin Endowed | School of Dental Medicine students | Field = pass (health group) |
| 10 | 84 | Anthony Fox (West Charlotte) | West Charlotte HS seniors going to CPCC | GPA only |
| 18 | 83 | Burevitch, Occupational Therapy | OT department students | no field check |
| 19 | 83 | Burevitch, Physician Assistant Studies | Graduate PA program | Field = pass (health group) |
| 20 | 82 | Caswell Center, Recreational Therapy | Recreational Therapy majors | no field check |
| 21 | 82 | Addictions and Rehabilitation Studies Inclusion | That department's students | no field check |
| 22 | 82 | JB & Vera Congleton | Child Development and Family Relations | no field check |
| 24 | 82 | Myrtle Westmoreland | Family and Consumer Science majors | no field check |
| 25 | 82 | Pecheles Automotive | College of Health and Human Sciences (Nursing is a separate college at ECU) | likely ineligible |

Plausible: Hunnings, Patient Circle, Boudreaux, Rabunsky, Academic Scholarship Pool, Lillian Jenkins, Garris (Pitt Co. HS),
Griffin Memorial (first-gen), Veneziano, Service League (Pitt Co.), Garner, Taylor Foundation, Louise Metts Lloyd.
American Legion #39 and Justin Bryce Clark need "enrolled in nursing" / "declared major in the College of Nursing".
Incoming pre-nursing students may not qualify yet, so these should carry a Verify flag.

## Profile 2: Derek, USC sophomore, Mechanical Engineering (run `a7e528140947`)

| # | Score | Scholarship | Why not |
|---|---|---|---|
| **1** | **82** | Larry and Pat Wilson | Computer Eng / CIS / CS majors only. Field = pass via the shared engineering group |
| 3 | 79 | SC National Guard CAP | Guard members only |
| 4 | 75 | Free Tuition for Residents Over 60 | Must be 60+ |
| 5 | 75 | SC WINS | Technical colleges only; he attends USC |

Eligible: Senator John G. Blackmon ME Scholarship (#2). Possible: LGBTQ+ Alumni Council (needs inclusivity work).
Note: 1,023 rows were dropped as "deadline passed" (mostly USC), leaving only 7 candidates.

## Profile 3: Aisha, VA HS senior → Virginia Tech, Business (run `a299f…`)

| # | Score | Scholarship | Why not |
|---|---|---|---|
| 2 | 80 | O. C. Thompson | Out-of-state students (or Thompson descendants) |
| 3 | 80 | Universal Leaf Tobacco Company | Children of Southside VA tobacco growers |
| 5 | 78 | Herman C. and Carrie Watson | Northampton / Accomack counties |
| 6 | 78 | McGuireWoods LLP | Children of the firm's staff |
| 7 | 78 | VT Tribal Match | Must hold a VA tribal scholarship |
| 8 | 78 | Faculty-Staff Dependent and Spouse | Dependents of VT employees |
| 9 | 78 | Smithfield Foundation | Dependents of Smithfield Foods employees |
| 10 | 78 | VCCS Transfer Achievement | Transfers with an associate degree |
| 12 | 74 | Mossberg Table Tennis (Freshman) | Active USATT member, must join the team |
| 13 | 74 | Harry Bramhall Gilbert | Chesapeake Public Schools grads, college seniors |
| 14 | 73 | Kimberly Ware Outstanding Achievement | Graduating seniors after a CEIP co-op (rule says Education level = pass) |
| 15 | 72 | Martin Luther and Clara B. Vaughan | Vaughan descendants |
| 16 | 71 | Graham Stedman Neal Diabetes Fund | Students with diabetes or an autoimmune disease |
| 18 | 64 | Richard E. Browning '71 | Energy-field internship in Russia, Central Asia or India |

Plausible: Cobb Office Products (#1, 3.0 GPA), Nagy & Jennifer Maalouf, Adam Ward '11 (need-based), Honors College (if invited),
Pamplin Leadership (needs her high school's nomination). Weak or conditional: Real Estate fund (#4, not a student award),
GEO study abroad, Knox study abroad (Honors), Hokies on the Hill (program acceptance), Austin Michelle Cloyd.

## Fixed after this test (`RULES_VERSION` rules-2)

- **Root cause 1.** Fields now have a parent (`extract_rules.FIELDS`). A scholarship for a field accepts that field
  and its sub-fields, never a sibling. Nursing vs dentistry / PA / OT, and Mechanical vs Computer Engineering,
  now **fail**. A student who gave only the broad field ("Nursing" vs a nurse-practitioner award) gets **unknown**.
  When a listing names a sub-field, the broad field from the college name is dropped. Fashion merchandising,
  recreational therapy and the health professions got their own fields.
- **Root cause 3.** On university portals "graduating senior" now means a college senior (`undergrad`, class
  year senior). An award open only to upper-class years now **fails** for a high-school senior. On
  high-school counselor pages it still means a high-school senior.
- The "U" label is now "Citizenship"; check names are explicit.

Rule-based re-run on the same catalog: Derek loses Larry and Pat Wilson. Maya loses the dental, PA, OT,
recreational-therapy, merchandising and CNS awards. Aisha loses Kimberly Ware. Still open: root causes 2, 4, 6–8.
