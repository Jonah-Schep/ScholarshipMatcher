"""Deterministic (regex) eligibility extraction.

Roles:  * free baseline for every scholarship at ingest (no LLM cost)
        * cross-check for the LLM Extractor agent (disagreements are flagged)
        * the whole extractor when running in offline/heuristic mode

Conservative by design: a sentence with "prefer"/"preference" is recorded as a SOFT
criterion, never a hard one, so the hard filter cannot wrongly eliminate a student.
"""
from __future__ import annotations

import re
from typing import Optional

from ..models import Criteria, Requirements, Scholarship
from ..textutil import squash

STATE_NAMES = {"virginia": "VA", "north carolina": "NC", "south carolina": "SC", "georgia": "GA",
               "tennessee": "TN", "maryland": "MD", "west virginia": "WV", "kentucky": "KY",
               "florida": "FL", "pennsylvania": "PA", "new york": "NY", "texas": "TX", "california": "CA",
               "ohio": "OH", "alabama": "AL", "delaware": "DE", "new jersey": "NJ", "district of columbia": "DC"}
# field of study -> (group, keywords, parent). A scholarship for a field accepts students in that field or in
# any of its sub-fields (parent -> child); it does not accept students in a sibling field. So "engineering"
# accepts a mechanical engineering student, but "computer engineering" does not, and "dentistry" never
# accepts a nursing student. `group` is only a coarse label kept for display / grouping.
FIELDS = {
    "engineering": ("engineering", r"engineer(ing)?", None),
    "mechanical engineering": ("engineering", r"mechanical (and \w+ )?engineer", "engineering"),
    "electrical engineering": ("engineering", r"electrical (and computer )?engineer", "engineering"),
    "computer engineering": ("engineering", r"computer engineer", "engineering"),
    "civil engineering": ("engineering", r"civil (and environmental )?engineer", "engineering"),
    "chemical engineering": ("engineering", r"chemical engineer", "engineering"),
    "biomedical engineering": ("engineering", r"biomedical engineer|bioengineer", "engineering"),
    "industrial engineering": ("engineering", r"industrial (and systems )?engineer", "engineering"),
    "aerospace engineering": ("engineering", r"aerospace (and ocean )?engineer", "engineering"),
    "computer science": ("computing", r"computer science|computing|software|information technology|information systems|cybersecurity|data science", None),
    "business": ("business", r"business|management|entrepreneur", None),
    "accounting": ("business", r"accounting|accountancy", "business"),
    "finance": ("business", r"finance|financial planning", "business"),
    "marketing": ("business", r"marketing", "business"),
    "economics": ("business", r"economics", "business"),
    "hospitality": ("business", r"hospitality|tourism", "business"),
    "merchandising": ("design", r"merchandising|fashion|apparel", None),
    "education": ("education", r"education|teaching|teacher", None),
    "health sciences": ("health", r"health sciences?|allied health|pre-?health", None),
    "nursing": ("health", r"nursing|nurse", "health sciences"),
    "advanced practice nursing": ("health", r"nurse practitioner|clinical nurse specialist|nurse anesthe|doctor of nursing practice|\bdnp\b", "nursing"),
    "medicine": ("health", r"pre-?med|(?<!dental )(?<!veterinary )medicine|\bmedical\b", "health sciences"),
    "dentistry": ("health", r"dental|dentist", "health sciences"),
    "pharmacy": ("health", r"pharmac", "health sciences"),
    "physician assistant": ("health", r"physician assistant", "health sciences"),
    "occupational therapy": ("health", r"occupational therap", "health sciences"),
    "physical therapy": ("health", r"physical therap", "health sciences"),
    "recreational therapy": ("health", r"recreational therap", "health sciences"),
    "public health": ("health", r"public health", "health sciences"),
    "nutrition": ("health", r"nutrition|dietetic", "health sciences"),
    "kinesiology": ("health", r"kinesiology|exercise science", "health sciences"),
    "veterinary": ("health", r"veterinar", "health sciences"),
    "agriculture": ("agriculture", r"agricultur|agribusiness|animal science|dairy|horticultur|crop|agronomy|food science", None),
    "natural resources": ("agriculture", r"forestry|natural resources|wildlife|environmental science|conservation|fisher", "agriculture"),
    "architecture": ("design", r"architecture|landscape architecture|interior design|industrial design", None),
    "construction": ("design", r"building construction|construction", "architecture"),
    "biology": ("science", r"biolog|biochem|microbiology|neuroscience", None),
    "chemistry": ("science", r"chemistry", None),
    "physics": ("science", r"physics", None),
    "geosciences": ("science", r"geolog|geoscience|meteorolog", None),
    "mathematics": ("math", r"mathematic|statistics|actuarial", None),
    "psychology": ("social science", r"psycholog", None),
    "political science": ("social science", r"political science|public policy|international (studies|relations)|government", None),
    "sociology": ("social science", r"sociolog|social work|criminal justice|criminolog", None),
    "history": ("humanities", r"history", None),
    "english": ("humanities", r"english|creative writing|literature", None),
    "languages": ("humanities", r"foreign language|spanish|french|german|linguistics", None),
    "philosophy": ("humanities", r"philosophy|religio", None),
    "communication": ("communication", r"communications?|journalism|public relations|media", None),
    "arts": ("arts", r"\bart\b|(?<!liberal )\barts\b|theatre|theater|dance|studio art|graphic design|cinema|film", None),
    "music": ("arts", r"music", "arts"),
    "law": ("law", r"\blaw\b|pre-?law|legal", None),
}
_MAJOR_TRIGGER = re.compile(
    r"major(ing|s)? in|majors?\b|pursuing (a|an)? ?(degree|career|bachelor'?s?|studies) in|degree in|studying|"
    r"enrolled in (the )?(college|school|department|program)|students? (in|of|within) the (college|school|department)|"
    r"(college|school|department) of", re.I)
_PREF = re.compile(r"\bprefer|\bpriority\b|\bconsideration (will be|is) given|\bmay be given\b|\bdesirable\b|\bencouraged\b", re.I)
_SOFT = re.compile(r"leadership|community service|volunteer|extracurricular|involve(d|ment)|commitment to|"
                   r"interest in|passion|demonstrat\w+ (a |an )?(interest|commitment|leadership|character)|"
                   r"participat\w+|character|work ethic|career goal|aspir", re.I)
_NUMWORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "a": 1, "an": 1}
_ESSAY_Q = re.compile(r"essay|describe|explain|why |how (will|would|has|have|do|did)|tell us|discuss|statement|share ", re.I)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text or "")
    return [squash(p) for p in parts if squash(p)]


def field_keys(label: str) -> set[str]:
    """Fields named in one label or sentence. A broad field is dropped when one of its sub-fields is also
    named ("Computer Engineering" -> computer engineering, not engineering)."""
    low = label.lower().strip()
    if low in FIELDS:                      # already a field key (rule-extracted criteria store key names)
        return {low}
    return _most_specific({name for name, (_g, rx, _p) in FIELDS.items() if re.search(rx, low)})


def _most_specific(keys: set[str]) -> set[str]:
    return keys - {FIELDS[k][2] for k in keys if FIELDS[k][2]}


def field_groups(labels: list[str]) -> set[str]:
    """Map free-text majors to coarse taxonomy groups (display only; eligibility uses `field_match`)."""
    return {FIELDS[k][0] for lab in labels for k in field_keys(lab)}


def field_match(wanted: list[str], majors: list[str]) -> str:
    """Scholarship field restriction vs the student's majors -> "pass" / "unknown" / "fail".

    pass:    same field, or the scholarship names the broader field ("engineering" vs mechanical engineering)
    unknown: either side can't be mapped, or the student only gave the broader field ("nursing" vs a
             nurse-practitioner award) - Unknown is not fail
    fail:    every named field is a different one (nursing vs dentistry, mechanical vs computer engineering)
    """
    want = set().union(*(field_keys(w) for w in wanted)) if wanted else set()
    have = set().union(*(field_keys(m) for m in majors)) if majors else set()
    if not want or not have:
        return "unknown"
    parent = lambda k: FIELDS[k][2]
    if any(w == h or w == parent(h) for w in want for h in have):
        return "pass"
    if any(h == parent(w) for w in want for h in have):
        return "unknown"
    return "fail"


def _num(tok: str) -> Optional[int]:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else _NUMWORD.get(tok)


def extract(s: Scholarship) -> Criteria:
    text = s.full_text()
    c = Criteria(requirements=Requirements())
    ev = c.evidence
    hard, soft = [], []
    for sent in sentences(text):
        (soft if _PREF.search(sent) else hard).append(sent)

    def first(rx: str, flags=re.I):
        for sent in hard:
            m = re.search(rx, sent, flags)
            if m:
                return m, sent
        return None, ""

    # --- GPA
    for rx in (r"(?:minimum|at least|min\.?)[^.]{0,30}?(?:GPA|grade point average)[^.\d]{0,15}(\d\.\d{1,2})",
               r"(?:GPA|grade point average)\s*(?:of|:|is|must be|>=|≥)?\s*(?:at least\s*)?(\d\.\d{1,2})",
               r"(\d\.\d{1,2})\+?\s*(?:or (?:higher|above|better)\s*)?(?:minimum\s*)?(?:cumulative |overall |unweighted |weighted )?(?:GPA|grade point average)"):
        m, sent = first(rx)
        if m and 1.5 <= float(m.group(1)) <= 4.0:
            c.min_gpa, ev["min_gpa"] = float(m.group(1)), sent
            break

    # --- residency / locality
    low_hard = " ".join(hard).lower()
    for name, code in STATE_NAMES.items():
        rx = (rf"resident[s]? of (?:the (?:state|commonwealth) of )?{name}\b|\b{name} resident|"
              rf"(?:reside|live|living|domiciled) in {name}\b|\b{name} (?:high school|domicile|native)|native of {name}\b")
        m, sent = first(rx)
        if m and not (name == "virginia" and "west virginia" in m.group(0).lower()):
            if code not in c.residency_states:
                c.residency_states.append(code)
                ev.setdefault("residency_states", sent)
    if s.source in ("sc_che", "sc_tuition_grants", "schev", "ncseaa") and s.geo_scope in ("sc", "va", "nc"):
        if not c.residency_states:
            c.residency_states = [s.geo_scope.upper()]
            ev["residency_states"] = f"State program administered for {s.geo_scope.upper()} residents"
    for m in re.finditer(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?) County\b", " ".join(hard)):
        loc = m.group(0)
        if loc not in c.localities and m.group(1) not in ("The", "This", "Each", "Any"):
            c.localities.append(loc)
    m, sent = first(r"\b(Charlotte[- ]Mecklenburg Schools?|CMS (?:student|senior|graduate)s?|graduat\w+ (?:of|from) [A-Z][\w. ]+ High School)")
    if m:
        c.localities.append(squash(m.group(1)))
    if c.localities:
        ev["localities"] = next((x for x in hard if c.localities[0] in x), "")

    # --- level / class year
    # On a university's own portal "graduating senior" means a college senior; on a high-school counselor
    # page it means a high-school senior.
    college_portal = s.source.startswith("academicworks_")
    college_grad_senior = college_portal and re.search(r"graduating seniors?\b(?! (are|is) not eligible)", low_hard) and "high school" not in low_hard
    hs_rx = r"high school (senior|student|graduat)|incoming (freshm[ae]n|first[- ]year)|entering freshm[ae]n"
    if not college_portal:
        hs_rx += r"|graduating senior"
    if re.search(hs_rx, low_hard):
        c.levels.append("hs_senior")
    if college_grad_senior or re.search(r"undergraduate|sophomore|junior\b|rising (sophomore|junior|senior)|currently enrolled|current(ly)? (college )?student|full[- ]time student", low_hard):
        c.levels.append("undergrad")
    if re.search(r"graduate student|master'?s|doctoral|ph\.?d|graduate (program|degree|level)|law student|medical student|dvm", low_hard):
        c.levels.append("graduate")
    for yr in ("freshman", "sophomore", "junior", "senior"):
        if re.search(rf"\b(rising |current )?{yr}s?\b", low_hard) and not (yr == "senior" and "high school senior" in low_hard and "undergrad" not in c.levels):
            c.class_years.append(yr)
    if s.source.startswith("academicworks_") and not c.levels:
        c.levels = ["undergrad", "graduate"] if "graduate" in low_hard else []

    # --- majors (only in restrictive sentences)
    for sent in hard + ([s.name] if _MAJOR_TRIGGER.search(s.name) else []):
        if not _MAJOR_TRIGGER.search(sent):
            continue
        for name in sorted(field_keys(sent), key=list(FIELDS).index):
            if name not in c.majors:
                c.majors.append(name)
                ev.setdefault("majors", sent)
    # "College of Engineering" next to "majoring in Computer Engineering" is context, not a wider restriction.
    keep = _most_specific(set(c.majors))
    c.majors = [m for m in c.majors if m in keep]
    if s.college_or_department and not c.majors:
        for name in sorted(field_keys(s.college_or_department), key=list(FIELDS).index):
            c.majors.append(name)
            ev.setdefault("majors", s.college_or_department)

    # --- need / demographics / status
    def flag(field: str, rx: str, value=True):
        m, sent = first(rx)
        if m:
            setattr(c, field, value)
            ev[field] = sent

    flag("need_based", r"financial need|need[- ]based|demonstrated need|unmet need|economically disadvantaged|low[- ]income")
    flag("pell_required", r"pell[- ]eligible|pell grant (recipient|eligible)|eligible for (a |the )?(federal )?pell|must (receive|qualify for) (a |the )?pell")
    flag("gender", r"\b(must be|be a|for|open to|awarded to|supports?|available to)\b[^.]{0,25}\b(female|wom[ae]n)\b|\bfemale (student|applicant|graduate)", "female")
    if c.gender is None:
        flag("gender", r"\b(must be|be a|open to|awarded to|available to)\b[^.]{0,25}\b(male|m[ae]n)\b(?! and)|\bmale (student|applicant|graduate)", "male")
    eth = {"Black / African American": r"african[- ]american|\bblack (student|male|female|applicant|wom[ae]n|m[ae]n)",
           "Hispanic / Latino": r"hispanic|latin[oax]", "Native American": r"native american|american indian|alaska native",
           "Asian / Pacific Islander": r"asian[- ]american|pacific islander",
           "Underrepresented minority": r"underrepresented (minorit|group|student|population)|minority student|students? of color"}
    for label, rx in eth.items():
        m, sent = first(rx)
        if m:
            c.ethnicities.append(label)
            ev.setdefault("ethnicities", sent)
    flag("disability_required", r"(student|applicant|individual)s? with (a )?(documented |physical |learning )?disabilit|must have a (documented )?disabilit")
    flag("first_generation_required", r"first[- ]generation")
    flag("military_affiliation_required", r"\bveterans?\b|military (service|dependent|famil)|active[- ]duty|corps of cadets|\brotc\b|national guard|(child|children|dependent|spouse)s? of (a )?(veteran|service ?member)")
    flag("us_citizen_or_resident_required", r"u\.?s\.? citizen|united states citizen|permanent resident|citizenship")

    # --- number of awards
    m, sent = first(r"\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:\$[\d,]+\s+)?(?:scholarships?|awards?|recipients?|students?|winners?)\s+(?:will be|are|is|shall be)\s+(?:awarded|selected|chosen|given)")
    if not m:
        m, sent = first(r"(?:awarded|given|presented) to\s+(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|a|an)\s+(?:\w+\s+){0,3}?(?:students?|seniors?|recipients?|applicants?|graduates?)")
    if m and _num(m.group(1)):
        c.num_awards, ev["num_awards"] = _num(m.group(1)), sent

    # --- application requirements
    r = c.requirements
    questions = s.extra.get("supplemental_questions") or []
    low_all = text.lower()
    if questions:
        r.essay_count = sum(1 for q in questions if _ESSAY_Q.search(q) and len(q) > 40)
        for q in questions:
            if re.search(r"upload|attach|resume|transcript|letter", q, re.I):
                r.other.append(q[:120])
    elif re.search(r"\bno essay\b|essay (is )?not required", low_all):
        r.essay_count = 0
    else:
        m = re.search(r"\b(\d|one|two|three)\s+(?:short |brief |personal )?essays?\b", low_all)
        if m and _num(m.group(1)):
            r.essay_count = _num(m.group(1))
        elif re.search(r"\bessay\b|personal statement|written statement|\d+[- ]word", low_all):
            r.essay_count = 1
    m = re.search(r"\b(\d|one|two|three)\s+(?:letters? of recommendation|recommendation letters?|references?)", low_all)
    if m and _num(m.group(1)):
        r.recommendation_letters = _num(m.group(1))
    elif re.search(r"letters? of (recommendation|reference|support)|recommendation letters?", low_all):
        r.recommendation_letters = 1
    r.transcript = True if re.search(r"\btranscript", low_all) else None
    r.interview = True if re.search(r"\binterview", low_all) else None
    r.fafsa = True if re.search(r"\bfafsa\b|css profile", low_all) else None
    r.portfolio_or_audition = True if re.search(r"\bportfolio\b|\baudition", low_all) else None

    if s.extra.get("application_method"):
        c.application_method = s.extra["application_method"]
    elif s.source.startswith("academicworks_"):
        c.application_method = "direct" if questions else "general_application"
    else:
        c.application_method = "direct"

    # --- soft criteria: preference sentences + character/interest sentences
    for sent in soft + [h for h in hard if _SOFT.search(h)]:
        if 20 < len(sent) < 400 and sent not in c.soft_criteria:
            c.soft_criteria.append(sent)
    c.soft_criteria = c.soft_criteria[:8]
    return c
