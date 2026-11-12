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
    "rehabilitation": ("health", r"addiction|rehabilitation", "health sciences"),
    "communication sciences and disorders": ("health", r"communication sciences and disorders|communication disorders|"
                                                       r"speech[- ]language|audiology", "health sciences"),
    "psychology": ("social science", r"psycholog", None),
    "human development": ("social science", r"child development|family relations|family and consumer sciences?|"
                                            r"human development|family science|family studies", None),
    "political science": ("social science", r"political science|public policy|international (studies|relations)|government", None),
    "sociology": ("social science", r"sociolog|social work|criminal justice|criminolog", None),
    "history": ("humanities", r"history", None),
    "english": ("humanities", r"english|creative writing|literature", None),
    "languages": ("humanities", r"foreign language|spanish|french|german|linguistics", None),
    "philosophy": ("humanities", r"philosophy|religio", None),
    "communication": ("communication", r"communications?(?! (?:sciences and disorders|disorders))|journalism|"
                                       r"public relations|media", None),
    "arts": ("arts", r"\bart\b|(?<!liberal )\barts\b|theatre|theater|dance|studio art|graphic design|cinema|film", None),
    "music": ("arts", r"music", "arts"),
    "law": ("law", r"\blaw\b|pre-?law|legal", None),
}
_MAJOR_TRIGGER = re.compile(
    r"major(ing|s)? in|majors?\b|pursuing (a|an)? ?(degree|career|bachelor'?s?|studies) in|degree in|studying|"
    r"enrolled in (the )?(college|school|department|program)|students? (in|of|within) the (college|school|department)|"
    r"(college|school|department) of|\bprogram in\b|\bconcentration in\b|"
    r"\b(?:(?:enrolled|students?) in|(?:accepted|admitted) (?:in)?to) (?:the |a |an )?(?:[\w&'’()/-]+ ){1,6}?"
    r"(?:programs?|majors?|curriculum)\b", re.I)
# Wording that names the major itself; a sentence with a College/School/Department name but none of this wording
# only tells us the unit ("For students in the College of Health and Human Sciences").
_MAJOR_WORDS = re.compile(r"\bmajor(?:s|ing|ed)?\b|\bdegree in\b|\bstudying\b|\bprogram in\b|\bconcentration in\b|"
                          r"\bpursuing (?:a|an)? ?(?:degree|bachelor'?s?|studies) in\b|"
                          r"\benrolled in (?:the |a |an )?(?:[\w&'’()/-]+ ){1,6}?(?:programs?|majors?|curriculum)\b", re.I)
# "College of Liberal Arts and Human Sciences", "School of Dental Medicine", "Department of Addictions and
# Rehabilitation Studies". The unit name is capitalised; it stops at the first lower-case word ("…Sciences who").
_UNIT_RX = re.compile(r"\b((?i:college|school|department|dept\.?|division) of (?:the )?"
                      r"((?-i:[A-Z][\w'’-]*)(?:,? (?:(?:and|&) )?(?:of )?(?-i:[A-Z][\w'’-]*))*))")
_SCIENCES = ["biology", "chemistry", "physics", "geosciences", "mathematics", "psychology", "economics"]
_HUMANITIES = ["history", "english", "languages", "philosophy", "political science", "sociology", "communication"]
# Units whose name does not map to the right fields by keyword: broad colleges get the fields they hold at VT / ECU /
# USC (so "College of Science" does not admit a computer science student, who is in Engineering at all three), and
# "Arts and Sciences" no longer reads as the `arts` field. Checked in order; first match wins.
_UNIT_FIELDS: list[tuple[re.Pattern, list[str]]] = [(re.compile(rx), fields) for rx, fields in (
    (r"^(?:liberal arts and human sciences|clahs)\b", _HUMANITIES + ["arts", "music", "human development", "education"]),
    (r"^health and human (?:sciences|services|performance|ecology)\b",
     ["health sciences", "human development", "nutrition", "kinesiology"]),
    (r"^(?:natural |physical |life )?sciences?\b(?! and)", _SCIENCES),
    (r"^(?:liberal )?arts and sciences\b", _SCIENCES + _HUMANITIES + ["arts"]),
    (r"^engineering(?: and (?:technology|applied science|computing))?\b", ["engineering", "computer science"]),
)]
_FIELD_STOP = set("a an the for to by in with who are is all any from at on as that which our their his her its "
                  "these those must be been have has will being".split())
# Graduate and professional programs (item 4). Long names match case-insensitively; the short degree letters only in
# capitals, as whole words, and next to program wording ("MD program", "pursuing a JD", "(MSN/DNP) program"), so
# "Baltimore, MD 21201" is not a medical degree. "pre-med", "pre-dental", "pre-PA", "pre-law" never count.
_GRAD_TOKEN = r"(?-i:MD|JD|DVM|DMD|DDS|PharmD|MBA|MSN|DNP)"
GRADUATE_PROGRAMS = re.compile(
    r"\bclinical nurse specialists?\b|\bnurse practitioners?\b|\bnurse anesthe\w*|\bdoctor of nursing practice\b"
    r"|\bphysician assistants?\b|\b(?:school|college) of (?:dental |veterinary |osteopathic )?medicine\b"
    r"|\b(?:medical|dental|veterinary|law|pharmacy) (?:students?|school)\b|\bschool of (?:law|pharmacy)\b"
    r"|\bdoctor of (?:pharmacy|medicine|dental medicine|dental surgery|veterinary medicine|jurisprudence)\b"
    r"|\bjuris doctor\b|\bmaster(?:'s|’s|s)? (?:of|in) (?-i:[A-Z])"
    rf"|\b{_GRAD_TOKEN}\b(?:\s*[/,]\s*(?-i:[A-Z][A-Za-z.]{{1,7}}))*\)?\s+(?:programs?|degrees?|students?|candidates?|"
    r"curriculum|class|track)\b"
    rf"|\b(?:pursuing|earning|seeking|enrolled in|admitted to|accepted (?:in)?to|toward|towards)\s+"
    rf"(?:an?\s+|the\s+|their\s+)?{_GRAD_TOKEN}\b", re.I)
_MIT_VT = re.compile(r"\bMIT\b")          # Master of Information Technology, only on Virginia Tech rows
_PRE_PREFIX = re.compile(r"\bpre-?\s?$", re.I)
_CAREER_BEFORE = re.compile(r"\b(?:become|becoming|careers?|aspir\w*|goals?|plans? to|planning to|intends? to|"
                            r"intending to|interest(?:ed)? in|will (?:enter|attend|apply)|apply(?:ing)? to|"
                            r"prepar\w+ (?:for|to))\b[^.;]*$", re.I)
# The listing also names undergraduates, so a professional-program mention widens `levels` instead of replacing it.
_UNDERGRAD_NAMED = re.compile(r"\bundergrad|\bbachelor|\bBSN\b|\bpre-|\bfreshm[ae]n\b|\bsophomores?\b|\bjuniors?\b|"
                              r"\bseniors?\b|\bhigh school\b|\bcommunity college\b|\bassociate", re.I)
# Generic graduate wording ("graduate students", "master's", "Ph.D."), whole words: "undergraduate students" and
# "graduates of" are not graduate level.
_GRAD_GENERIC = re.compile(r"(?<!under)\bgraduate[- ](?:students?|programs?|degrees?|level|school|studies|study)\b|"
                           r"\bmaster'?s\b|\bdoctoral\b|\bph\.?\s?d\b", re.I)
# AcademicWorks fallback (rows with no level wording at all): "graduate" as a level, not "graduates of" / "graduated
# from" / "will graduate in May".
_GRAD_WORD = re.compile(r"(?<!under)\bgraduate\b(?!\s+(?:from|in|with|this|next|by|before|after|on|at|high)\b)", re.I)
# Declared major / admitted to the program (item 8).
_DECLARED_RX = re.compile(
    r"\bdeclared (?:a |an |their |the )?major\b|\bhave declared\b|\bmajor (?:must be|is) declared\b"
    r"|\b(?:admitted|accepted) (?:in)?to (?:the |a |an )?(?:[\w&'’()/-]+ ){0,6}?"
    r"(?:programs?|majors?|curriculum|college|school|department)\b"
    r"|\benrolled in (?:the |a |an )?(?:[\w&'’()/-]+ ){0,6}?(?:programs?|majors?|curriculum)\b"
    r"|\benrolled in the (?:college|school|department) of\b", re.I)
_DECLARED_NOT = re.compile(r"\bnot (?:yet )?(?:declared|admitted|accepted|enrolled)\b|\bundeclared\b|\bintended major\b|"
                           r"\b(?:intend(?:s|ing)?|plan(?:s|ning)?) to (?:declare|major|enroll|apply|pursue|study)\b|"
                           r"\bor (?:intend|plan)\w*|\bpre-", re.I)
_RISING = re.compile(r"\brising (sophomore|junior|senior)s?\b", re.I)
_YEAR_BELOW = {"sophomore": "freshman", "junior": "sophomore", "senior": "junior"}
_PREF = re.compile(r"\bprefer|\bpriority\b|\bconsideration (will be|is) given|\bmay be given\b|\bdesirable\b|\bencouraged\b", re.I)
_SOFT = re.compile(r"leadership|community service|volunteer|extracurricular|involve(d|ment)|commitment to|"
                   r"interest in|passion|demonstrat\w+ (a |an )?(interest|commitment|leadership|character)|"
                   r"participat\w+|character|work ethic|career goal|aspir", re.I)
# --- awards limited to a specific group (WP3). Only hard sentences are searched, so "preference given to children
# of employees" stays soft. `(?-i:[A-Z])` requires a capitalised proper name even though the patterns run with re.I,
# which keeps "members of the community" / "transfer students from any college" out.
_GROUP_FAMILY = r"(?:child(?:ren)?|sons? (?:and|or) daughters?|dependents?|spouses?)"
GROUP_PATTERNS: dict[str, str] = {
    "family": rf"\b{_GROUP_FAMILY}(?: (?:and|or) {_GROUP_FAMILY})? of [^.;]{{0,60}}?"
              r"\b(?:employees?|staff|faculty|associates|members|growers|farmers)\b",
    "descendants": r"\b(?:direct |lineal |documented )?descendants? of\b",
    "membership": r"\b(?:an? |active |current )?members? of (?:an? |the )?"
                  r"(?!(?:community|graduating|class|public|student body|team|household|family)\b)(?-i:[A-Z0-9])"
                  r"|\bmembers? in good standing\b"
                  r"|\b(?:active |current )?(?:USATT|4-H|FFA)\b[^.;]{0,40}\b(?:members?|membership|club)\b"
                  r"|\b(?:members?|membership)\b[^.;]{0,30}\b(?:USATT|4-H|FFA|Boy Scouts|Girl Scouts)\b",
    "award holders": r"\brecipients? of (?:a|an|the) (?!scholarship\b)[^.;]{1,60}?\bscholarship\b"
                     r"|\breceiving an? (?!scholarship\b)[^.;]{1,40}?\bscholarship\b"
                     r"|\bcompleted (?:a|an|the) [^.;]{0,60}?\b(?:co-?op|internship)\b(?: program\b)?"
                     r"|\bregistered with (?:the )?Services for Students with Disabilities\b",
    "transfer": r"\btransfer(?:ring)? (?:students? )?from (?:a |the )?(?:community college|VCCS|(?-i:[A-Z]))"
                r"|\b(?:earned|completed) an associate(?:'s|’s)? degree\b",
    "medical condition": r"\b(?:students?|applicants?) (?:with|diagnosed with|living with) "
                         r"(?:diabetes|[^.;]{0,30}?\b(?:disease|disorder|condition)s?\b)",
}
_GROUP_RX = {k: re.compile(v, re.I) for k, v in GROUP_PATTERNS.items()}
_GROUP_NEGATION = re.compile(r"\b(?:not eligible|ineligible|may not apply|cannot apply|are excluded|is excluded)\b", re.I)
# The group phrase runs from the match to the end of the clause; later clauses ("and must join the team") are cut.
_GROUP_CLAUSE_END = re.compile(r"[,;:]| who | by | and (?:must|be|have|are|is|will|demonstrate)\b| in order to | to be\b",
                               re.I)
_MAX_GROUP_PHRASE = 90
_ENDS_WITH_INITIAL = re.compile(r"(?:^|\s)(?-i:[A-Z])\.$")


def _group_phrase(sent: str, start: int, end: int) -> str:
    tail = sent[start:]
    cut = _GROUP_CLAUSE_END.search(tail, end - start)
    phrase = tail[:cut.start()] if cut else tail
    if len(phrase) > _MAX_GROUP_PHRASE:
        phrase = phrase[:_MAX_GROUP_PHRASE].rsplit(" ", 1)[0]
    phrase = re.sub(r"^(?:an?|the) ", "", phrase.strip(" .!?\"'"), flags=re.I)
    return phrase


def _join_initials(sents: list[str]) -> list[str]:
    """`sentences` splits "Clara B. Vaughan" after the initial; put such names back together."""
    out: list[str] = []
    for sent in sents:
        if out and _ENDS_WITH_INITIAL.search(out[-1]):
            out[-1] = f"{out[-1]} {sent}"
        else:
            out.append(sent)
    return out


def restricted_groups(hard: list[str], name: str = "") -> tuple[list[str], str]:
    """Group phrases found in hard (non-preference) sentences, plus the first evidence sentence.

    A sentence that says the group is NOT eligible is skipped, and so is "recipients of the <this award's own name>"
    (a renewal rule, not a group limit)."""
    own = {w for w in re.findall(r"[a-z]{4,}", name.lower()) if w != "scholarship"}
    groups, evidence = [], ""
    for sent in _join_initials(hard):
        if _GROUP_NEGATION.search(sent):
            continue
        for kind, rx in _GROUP_RX.items():
            for m in rx.finditer(sent):
                if kind == "award holders" and m.group(0).lower().startswith("recipient"):
                    if m.start() == 0 or (own and own <= set(re.findall(r"[a-z]{4,}", m.group(0).lower()))):
                        continue        # "Recipients of the X Scholarship will/must …" describes this award
                phrase = _group_phrase(sent, m.start(), m.end())
                if phrase and not any(phrase.lower() in g.lower() or g.lower() in phrase.lower() for g in groups):
                    groups.append(phrase)
                    evidence = evidence or sent
    return groups, evidence


# --- locality, destination college, out-of-state (WP4). Only hard sentences are searched, and a place name only
# becomes a requirement when restrictive wording points at it ("students from", "residents of", "graduates of",
# "senior at", "must attend", "high schools in", "<X County> residents"). So a county in a preference sentence, in
# the sponsor's address or in the donor's biography ("born in Smyth County") is not a requirement.
_PLACE_WORD = r"(?!(?:The|This|Each|Any|All|Every|For|From|Of|In|At|And|Or|To|A|An|Students?|Residents?|Graduates?|" \
              r"Applicants?|Seniors?|High|School|Schools|Public|County|Counties|City|Must|Be|Have)\b)[A-Z][\w'’]*"
_PLACE = rf"{_PLACE_WORD}(?:(?: of)? {_PLACE_WORD}|/{_PLACE_WORD}){{0,2}}"
_PLACE_RX = {
    # "Northampton or Accomack counties", "Mecklenburg, Union, Gaston Counties", "A, B and C Counties"
    "county_list": re.compile(rf"({_PLACE}(?:(?:, (?:and |or |& )?| (?:and|or|&) ){_PLACE})*) [Cc]ounties\b"),
    # "the counties of Northampton and Accomack"
    "counties_of": re.compile(rf"\b[Cc]ounties of ({_PLACE}(?:(?:, (?:and |or |& )?| (?:and|or|&) ){_PLACE})*)"),
    "county": re.compile(rf"({_PLACE}) County\b"),
    "city": re.compile(rf"\b[Cc]ity of ({_PLACE})"),
    "system": re.compile(rf"({_PLACE}) (?:City )?Public Schools?\b(?: [Ss]ystem)?|\b(Charlotte[- ]Mecklenburg) Schools?\b"),
    "high_school": re.compile(rf"({_PLACE_WORD}(?:[ -]{_PLACE_WORD}){{0,3}}) High School\b"),
}
# Restrictive wording before the place. Plural nouns ("students from") are always restrictive. Singular nouns and
# verbs ("a graduate of", "graduated from", "attend") are restrictive only at the start of the sentence (a bullet:
# "Resident of X County") or after a word about the applicant ("Applicants must have graduated from"), so a
# biography ("…and a graduate of Marion High School, she…") is not read as a rule.
_LOC_PLURAL = re.compile(
    r"\b(?:residents of|(?:students|applicants|candidates|graduates|seniors|juniors) (?:from|of|at|in|attending)|"
    r"(?:high )?schools? (?:in|of)|count(?:y|ies) of)\s+$", re.I)
_LOC_SINGULAR = re.compile(
    r"\b(?:resident of|(?:student|applicant|graduate|senior) (?:from|of|at|in|attending)|"
    r"resid(?:e|es|ing) in|li(?:ve|ves|ving) in|domiciled in|graduat(?:ed|ing|e) from|attend(?:s|ing)?|"
    r"enrolled (?:at|in))\s+$", re.I)
_LOC_SUBJECT = re.compile(
    r"\b(?:students?|applicants?|candidates?|recipients?|nominees?|seniors?|must|eligible|open to|available to|"
    r"awarded to|limited to|restricted to|every|each|any|all)\b|^\W*(?:for|be|have|current|graduating)\b", re.I)
_LOC_START = re.compile(r"^\W*(?:(?:must )?be )?(?:an? )?$", re.I)
_LOC_PAST = re.compile(r"\b(?:was|were|had been|has been)\s+(?:an? )?$", re.I)
_LOC_AFTER = re.compile(r"^\s+(?:residents?|students?|seniors?|graduates?|high schools?|public|schools?|area)\b", re.I)
_LOC_FILLER = re.compile(r"(?:\b(?:an?|the|any|public|private)\s+)*$", re.I)     # "graduates of a Pitt County …"
_LOC_CHAIN = re.compile(r"^(?:\s+in\s+[A-Z]{2}\b|,\s*[A-Z]{2}\b)?,?\s+(?:or|and)\s+(?:an?\s+|the\s+)?$")


def _restrictive_before(sent: str, start: int) -> bool:
    """Is the place starting at `start` introduced by restrictive wording (a few articles may sit in between)?"""
    head = sent[:start]
    head = head[:_LOC_FILLER.search(head).start()]
    m = _LOC_PLURAL.search(head) or _LOC_SINGULAR.search(head)
    if not m or _LOC_PAST.search(head[:m.start()]):
        return False                              # no trigger, or "The Smiths were residents of Smyth County"
    if m.re is _LOC_PLURAL:
        return True
    return bool(_LOC_START.match(head[:m.start()]) or _LOC_SUBJECT.search(head[:m.start()]))


def _place_names(group: str) -> list[str]:
    return [p.strip() for p in re.split(r",\s*(?:and |or |& )?|\s+(?:and|or|&)\s+", group) if p.strip()]


def localities(hard: list[str]) -> tuple[list[str], str]:
    """Counties ("X County"), cities ("City of X"), school systems ("X Public Schools") and high schools named as a
    requirement in hard sentences, plus the first evidence sentence."""
    out, evidence = [], ""

    def add(loc: str, sent: str):
        nonlocal evidence
        loc = squash(loc)
        if loc and loc.lower() not in {x.lower() for x in out}:
            out.append(loc)
            evidence = evidence or sent

    for sent in hard:
        if _GROUP_NEGATION.search(sent):
            continue
        taken: list[tuple[int, int]] = []

        def ok(m: re.Match) -> bool:
            if any(a <= m.start() < b for a, b in taken):
                return False
            if _restrictive_before(sent, m.start()) or _LOC_AFTER.match(sent[m.end():]):
                return True
            # "… Counties in NC or York County, SC": a place joined by "or"/"and" to one already accepted
            return any(b <= m.start() and _LOC_CHAIN.match(sent[b:m.start()]) for _a, b in taken)

        def accept(m: re.Match, *locs: str):
            for loc in locs:
                add(loc, sent)
            taken.append((m.start(), m.end()))

        for kind in ("county_list", "counties_of"):
            for m in _PLACE_RX[kind].finditer(sent):
                if ok(m):
                    accept(m, *(f"{name} County" for name in _place_names(m.group(1))))
        for m in _PLACE_RX["county"].finditer(sent):
            if ok(m):
                accept(m, f"{m.group(1)} County")
        for m in _PLACE_RX["city"].finditer(sent):
            if ok(m):
                accept(m, f"City of {m.group(1)}")
        for m in _PLACE_RX["system"].finditer(sent):
            if ok(m):
                accept(m, f"{m.group(1)} Public Schools" if m.group(1) else "Charlotte-Mecklenburg Schools")
        for m in _PLACE_RX["high_school"].finditer(sent):
            if ok(m):
                accept(m, f"{m.group(1)} High School")
        if re.search(r"\bCMS (?:student|senior|graduate)s?\b", sent):
            add("Charlotte-Mecklenburg Schools", sent)
    return out, evidence


# Destination college: "enrolling at / attending / planning to attend / accepted to X Community College".
_DEST_RX = re.compile(
    r"\b(?i:enroll(?:ing|ed|s)? at|attend(?:ing|s)?|plan(?:s|ning)? to (?:attend|enroll at)|will attend|"
    r"intend(?:s|ing)? to (?:attend|enroll at)|accepted (?:to|at|into)|admitted to)\s+(?:the\s+)?")
_COLLEGE = (rf"((?:{_PLACE_WORD}[ -]){{1,5}}(?:Community College|Technical College|College|University|Institute)\b"
            r"|University of [A-Z][\w'’]*(?: [A-Z][\w'’]*){0,3})")
_DEST_RX = re.compile(_DEST_RX.pattern + _COLLEGE)
_DEST_MORE = re.compile(r"\s*(?:,\s*(?:or\s+|and\s+)?|\s(?:or|and)\s+)(?:the\s+)?" + _COLLEGE)   # "X or the University of Y"


def destination_schools(hard: list[str]) -> tuple[list[str], str]:
    """Colleges named after "enrolling at / attending / planning to attend / accepted to"."""
    out, evidence = [], ""
    for sent in hard:
        if sent.rstrip().endswith("?") or _GROUP_NEGATION.search(sent):
            continue                          # a question to the applicant is not a rule
        for m in _DEST_RX.finditer(sent):
            names, end = [m.group(1)], m.end()
            while more := _DEST_MORE.match(sent, end):
                names.append(more.group(1))
                end = more.end()
            for name in map(squash, names):
                if name.lower() not in {x.lower() for x in out}:
                    out.append(name)
                    evidence = evidence or sent
    return out, evidence


# Out-of-state: "out-of-state students", "non-residents of Virginia", "non-Virginia residents".
_STATE_ALT = "|".join(sorted((n.title() for n in STATE_NAMES), key=len, reverse=True))
_NONRES_RX = re.compile(
    r"\bout[- ]of[- ]state (?:students?|applicants?|residents?|undergraduates?|freshm[ae]n)\b"
    rf"|\bnon-?residents?(?: of (?:the (?:state|commonwealth) of )?(?:{_STATE_ALT}))?\b(?! (?:tuition|fees?|rates?|aliens?)\b)"
    rf"|\bnon-(?:{_STATE_ALT}) residents?\b", re.I)
_NONRES_NOT = re.compile(r"\b(?:not eligible|ineligible|may not apply|cannot apply|are excluded|is excluded|"
                         r"not open to)\b|\bresidents? (?:and|or) non-?residents?|\bnon-?residents? (?:and|or) residents?"
                         r"|\bin-state (?:and|or) out-of-state|\bout-of-state (?:and|or) in-state", re.I)


def non_resident_required(hard: list[str]) -> tuple[bool, str]:
    for sent in hard:
        if _NONRES_RX.search(sent) and not _NONRES_NOT.search(sent):
            return True, sent
    return False, ""


def non_resident_has_alternative(sentence: str) -> bool:
    """"out-of-state students or descendants of X": the out-of-state limit is one of several ways to qualify."""
    m = _NONRES_RX.search(sentence or "")
    if not m:
        return False
    return bool(re.match(r"\s*,?\s*or\b", sentence[m.end():]) or re.search(r"\bor\s+$", sentence[:m.start()]))


def non_resident_state(sentence: str) -> Optional[str]:
    """The state a "non-residents of Virginia" / "non-Virginia residents" sentence names, if any."""
    low = (sentence or "").lower()
    for name, code in sorted(STATE_NAMES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"non-?residents? of (?:the (?:state|commonwealth) of )?{name}\b|non-{name} resident", low):
            return code
    return None


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


# --------------------------------------------------------------------------- majors, units, levels (WP5)
def unit_fields(name: str) -> set[str]:
    """Fields a college / school / department name stands for ("Health and Human Sciences" -> health sciences,
    human development, nutrition, kinesiology; "Dental Medicine" -> dentistry)."""
    low = squash(name).lower()
    for rx, fields in _UNIT_FIELDS:
        if rx.search(low):
            return set(fields)
    return field_keys(low)


def unit_name(text: str) -> str:
    """The first "College/School/Department of X" in a text, or ""."""
    m = _UNIT_RX.search(text or "")
    return re.sub(r"(?:,? (?:and|&|of))+$", "", m.group(1)) if m else ""


def _fields_before_students(text: str) -> set[str]:
    """"a Child Development and Family Relations student", "nursing students", "Psychology majors"."""
    out: set[str] = set()
    for m in re.finditer(r"\b(?:students?|majors?)\b", text, re.I):
        words = re.findall(r"[\w&'’-]+", text[:m.start()])[-6:]
        phrase: list[str] = []
        for w in reversed(words):
            if w.lower() in _FIELD_STOP:
                break
            phrase.insert(0, w)
        if phrase:
            out |= field_keys(" ".join(phrase))
    return out


def _sentence_majors(sent: str) -> tuple[set[str], set[str], bool]:
    """(fields named as the major, fields from a unit name, unit sentence has "major" wording)."""
    units = list(_UNIT_RX.finditer(sent))
    rest = sent
    for u in reversed(units):
        rest = rest[:u.start()] + " " + rest[u.end():]
    named = field_keys(rest) if _MAJOR_TRIGGER.search(rest) else set()
    named |= _fields_before_students(rest)
    from_unit = set().union(*(unit_fields(u.group(2)) for u in units)) if units else set()
    if units and not named and not from_unit and _MAJOR_TRIGGER.search(sent):
        named = field_keys(sent)          # a lower-case unit name the unit pattern does not read
    return named, from_unit, bool(_MAJOR_WORDS.search(sent))


def graduate_program(hard: list[str], school: Optional[str]) -> str:
    """The first hard sentence naming a graduate or professional program, or ""."""
    for sent in hard:
        for m in GRADUATE_PROGRAMS.finditer(sent):
            head = sent[:m.start()]
            if _PRE_PREFIX.search(head) or _CAREER_BEFORE.search(head[-60:]):
                continue                     # "pre-med students", "plan to become a nurse practitioner"
            return sent
        if school == "vt" and _MIT_VT.search(sent):
            return sent
    return ""


def declared_major(hard: list[str]) -> str:
    """The sentence requiring a declared major / admission to the program, or ""."""
    for sent in hard:
        if _DECLARED_NOT.search(sent) or _GROUP_NEGATION.search(sent):
            continue
        for m in _DECLARED_RX.finditer(sent):
            span = sent[m.start():m.end() + 60]
            if "major" in m.group(0).lower() or field_keys(span) or (unit_name(span) and unit_fields(unit_name(span))):
                return sent
    return ""


def rising_years(sentence: str) -> set[str]:
    """Class years a sentence names only as "rising X" ("rising seniors" -> {"senior"})."""
    low = (sentence or "").lower()
    rising = {m.group(1) for m in _RISING.finditer(low)}
    return {yr for yr in rising if not re.search(rf"(?<!rising )\b{yr}s?\b", low)}


def locality_has_alternative(sentence: str, locs: list[str]) -> bool:
    """"Resident of Mecklenburg County or have accessed … programming": after the place, " or " opens another way to
    qualify that is not a place, so living elsewhere does not rule the student out."""
    sentence = sentence or ""
    end = 0
    for loc in locs:
        first = re.escape(loc.split()[0])
        for m in re.finditer(rf"\b{first}\b", sentence):
            end = max(end, m.end())
    if not end:
        return False
    return bool(re.search(r"\bor (?:(?:who|whose|that) )?(?:have|has|had|are|is|be|been|participat\w*|accessed|access|"
                          r"an? member|members?|descendants?|child(?:ren)?|employees?|dependents?|spouses?|served|"
                          r"currently)\b", sentence[end:], re.I))


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
        m, sent = first(rf"(?<!non-)(?<!non)(?:{rx})")     # "non-residents of Virginia" is not VA residency
        if m and not (name == "virginia" and "west virginia" in m.group(0).lower()):
            if code not in c.residency_states:
                c.residency_states.append(code)
                ev.setdefault("residency_states", sent)
    if s.source in ("sc_che", "sc_tuition_grants", "schev", "ncseaa") and s.geo_scope in ("sc", "va", "nc"):
        if not c.residency_states:
            c.residency_states = [s.geo_scope.upper()]
            ev["residency_states"] = f"State program administered for {s.geo_scope.upper()} residents"
    c.localities, loc_ev = localities(hard)
    if c.localities:
        ev["localities"] = loc_ev
    if not s.school:                      # on a school's own portal, Enrollment already covers the college
        c.destination_schools, dest_ev = destination_schools(hard)
        if c.destination_schools:
            ev["destination_schools"] = dest_ev
    nonres, nonres_ev = non_resident_required(hard)
    if nonres:
        c.non_resident_required, ev["non_resident_required"] = True, nonres_ev

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
    if _GRAD_GENERIC.search(low_hard):
        c.levels.append("graduate")
    # Graduate / professional programs: graduate only, unless the listing also names undergraduates.
    grad_sent = graduate_program(hard, s.school)
    if grad_sent:
        ev["levels"] = grad_sent
        if not _UNDERGRAD_NAMED.search(" ".join(hard)):
            c.levels = ["graduate"]
        else:                                    # "BSN and MSN students", "pre-med or medical students"
            if not {"hs_senior", "undergrad"} & set(c.levels):
                c.levels.append("undergrad")
            if "graduate" not in c.levels:
                c.levels.append("graduate")
    # "rising seniors" are juniors now: the year below counts (and the named year itself, which the hard filter
    # turns into an unknown, see hard_filter.check).
    rising = {m.group(1) for m in _RISING.finditer(low_hard)}
    for yr in ("freshman", "sophomore", "junior", "senior"):
        named = re.search(rf"(?<!rising )\b(current )?{yr}s?\b", low_hard) and not (
            yr == "senior" and "high school senior" in low_hard and "undergrad" not in c.levels)
        if named or yr in rising or any(_YEAR_BELOW[r] == yr for r in rising):
            c.class_years.append(yr)
    if c.class_years:
        ev["class_years"] = next((x for x in hard if re.search(r"\b(?:freshm[ae]n|sophomores?|juniors?|seniors?)\b",
                                                                x, re.I)), "")
    if s.source.startswith("academicworks_") and not c.levels:
        c.levels = ["undergrad", "graduate"] if _GRAD_WORD.search(low_hard) else []

    # --- majors (only in restrictive sentences). A field named as the major wins; a College/School/Department name
    # next to it is context ("College of Engineering" + "majoring in Computer Engineering"). When the unit is the only
    # source, `majors_from_unit` tells the hard filter not to treat a match as certain.
    named_f, unit_f, unit_ev, named_ev, unit_major_words = set(), set(), "", "", False
    for sent in hard:
        named, from_unit, major_words = _sentence_majors(sent)
        if named:
            named_f |= named
            named_ev = named_ev or sent
        elif from_unit:
            unit_f |= from_unit
            unit_ev = unit_ev or sent
            unit_major_words = unit_major_words or major_words
    order = list(FIELDS).index
    if named_f:
        c.majors, ev["majors"] = sorted(_most_specific(named_f), key=order), named_ev
    elif unit_f:
        c.majors, ev["majors"] = sorted(unit_f, key=order), unit_ev     # a unit's list is kept whole
        if not unit_major_words:
            c.majors_from_unit, ev["majors_from_unit"] = True, unit_ev
    elif s.college_or_department:
        dept = s.college_or_department
        found = [m.group(2) for m in _UNIT_RX.finditer(dept)]
        fields = set().union(*(unit_fields(u) for u in found)) if found else field_keys(dept)
        if fields:
            c.majors, ev["majors"] = sorted(fields, key=order), dept
            c.majors_from_unit, ev["majors_from_unit"] = True, dept
    declared = declared_major(hard)
    if declared and c.majors:
        c.declared_major_required, ev["declared_major_required"] = True, declared

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
    c.restricted_groups, group_ev = restricted_groups(hard, s.name)
    if c.restricted_groups:
        ev["restricted_groups"] = group_ev

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
