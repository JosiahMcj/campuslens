"""What Explore protects before any planning or model call, and how it
redirects instead of refusing.

The school database holds no counseling or spiritual-care data, and Explore
answers in aggregates only. Three kinds of question are caught here, in
code, before the planner (rule or model) sees them, and each is recorded as
``data.refused`` in the audit log:

- ``counseling``: counseling or spiritual care. No figure is ever given,
  not even a total; the reply is a gentle line and related questions it can
  answer.
- ``individual_student``: one named or numbered student, or a list of
  students. The restricted data is never answered; the API answers the
  nearest totals for students like that from the rule planner only (the
  question is not sent to the model planner), or suggests questions. Names
  are found by ``mask_names``, an allow list: every word that is not a
  catalog name, campus phrase, acronym or common word is treated as one.
- ``prediction``: what one student will do, a risk score, or a flag on
  students. Answered like an individual question.

A forward-looking question about a group ("how many will drop out", "will
enrollment fall") is not refused: ``is_forward_looking`` marks it, and it is
answered with the closest historical totals (``historical_form`` rewrites it
for the rule planner). ``is_off_topic`` catches requests that have nothing to
do with the university's student data ("code me a website"), the only
questions shown as "not something CampusLens answers".

The individual checks are deliberately broad: a caught question still gets
totals, while a leaked individual answer cannot be taken back.
"""

from __future__ import annotations

import functools
import re
import unicodedata

# The reasons recorded in the audit log (``data.refused``).
COUNSELING_REFUSAL = (
    "CampusLens does not answer questions about counseling or spiritual care, "
    "even as totals."
)
INDIVIDUAL_REFUSAL = (
    "CampusLens answers with totals only, never about a single student, by id or "
    "by description."
)
PREDICTION_REFUSAL = (
    "CampusLens does not predict what an individual student will do or score "
    "students. It answers with totals from the records."
)
OFF_TOPIC_REFUSAL = (
    "The request is not about the university's student records, so no analysis was run."
)

# What the person reads: calm, plain lines that lead into an answer or into
# questions CampusLens can answer. The counseling line refuses the topic,
# totals included (the one authorized counseling count appears only in the
# briefing, never as an answer to a typed question; ``counseling_message``
# below).
# The AI employees, as a denial names them ("outside the Enrollment
# Analyst's authorized scope").
EMPLOYEE_NAMES = {
    "chief_of_staff": "the Chief of Staff",
    "enrollment_analyst": "the Enrollment Analyst",
    "student_success_analyst": "the Student Success Analyst",
}


def counseling_message(employee: str | None = None) -> str:
    """The calm, explicit denial for a counseling, chaplain or spiritual-care
    request (notes, records or totals), naming the answering AI employee when
    known. It carries no figure."""
    name = EMPLOYEE_NAMES.get(employee or "")
    scope = f"{name}'s" if name else "CampusLens's"
    return (
        "Access denied. Counseling and chaplain notes are outside "
        f"{scope} authorized scope, and they aren't needed to answer registration "
        "questions. This request has been recorded in the audit log."
    )


COUNSELING_MESSAGE = counseling_message()
INDIVIDUAL_LEAD = (
    "CampusLens can't look up one student, but here are totals for students like that."
)
INDIVIDUAL_MESSAGE = (
    "CampusLens can't look up one student, but it can answer for groups of students."
)
PREDICTION_LEAD = (
    "CampusLens doesn't forecast or name students, so here's what the records "
    "show for the group."
)
FORWARD_LEAD = "CampusLens doesn't forecast, so here's what the records show."
OFF_TOPIC_MESSAGE = (
    "CampusLens answers questions about your students, courses and majors from "
    "the records, so it can't help with this one."
)

# Related questions for a counseling question: none of them reads or names
# counseling data.
COUNSELING_SUGGESTIONS = (
    "What is first-year retention by first-generation status?",
    "How many students have a hold?",
    "Which majors have the lowest advising coverage?",
)

_COUNSELING_RE = re.compile(
    r"\b(?:c+o+u+n+[cs]+e+l+(?:ing|ling|or|lor|ors|lors|ed|ers?)?|chaplains?|chaplaincy|"
    r"spiritual|wellness\s+cent(?:er|re)|health\s+cent(?:er|re)|"
    r"psych(?:iatric|ological)?\s+(?:referrals?|evals?|evaluations?|services|holds?|"
    r"care|visits?)|bible\s+stud(?:y|ies)\s+(?:attendance|group)|attend\w*\s+(?:mass|"
    r"bible\s+stud(?:y|ies)|devotions?)|mass\s+attendance|devotions|"
    r"pastoral|pray|prays|prayed|praying|prayer|prayers|chapel|faith|religio\w*|"
    r"church|worship\s+attendance|ministry\s+contact|mental\s+health|therapy|therapist|"
    r"psychologists?|psychiatr\w*|depress\w*|anxiety|suicid\w*|self[- ]harm)\b",
    re.IGNORECASE,
)
# "Christian Ministry" and "Worship Arts" are majors; ministry and worship
# alone are not counseling words. Course titles that contain one of these
# words ("Theories of Counseling") are masked before the check by the
# caller, which passes the catalog's titles and names.

# A student id (S- plus digits, any dash) or a numbered student ("student
# 1234", "student #1234", "the student with id 100001", "id 100001").
_STUDENT_ID_RE = re.compile(
    r"(?<!['’])\bS\s?[-_‐-―]\s?\d{3,}\b|(?<!['’])\bS\s\d{5,}\b|\bS\d{5,}\b"
    r"|\b(?!20\d\d[123]0\b)\d{5,}\b"
    r"|\b(?:student|learner|pupil)\s*(?:(?:with\s+)?(?:the\s+)?(?:id|number|no\.?|#)"
    r"\s*)?#?\s*\d{2,}\b"
    r"|\b(?:id|ids|identifier)\s*(?:number\s*)?#?\s*\d{3,}\b",
    re.IGNORECASE,
)
# Asking for individual students: lists, names, rankings, or "which students".
# Ranking, listing, or naming single people, and contact details.
_INDIVIDUAL_EXTRA_RE = re.compile(
    r"\b(?:give|show|send|print|list|enumerate|name|tell|output|dump|export)\s+"
    r"(?:me\s+|us\s+)?(?:every|each|all(?:\s+the)?|the|any)\s+(?:[\w-]+\s+)?"
    r"(?:students?|learners?|pupils?|people|persons|individuals?)\b(?!['’])"
    r"(?!\s+(?:count|counts|population|headcount|totals?|average|mean|median|"
    r"overall|gpa|by)\b)"
    r"|\b(?:list|name|identify|enumerate)\s+(?:them|those|these|the\s+names)\b"
    r"|\benumerate\s+(?:\w+\s+){0,2}(?:learners|students|people)\b"
    r"|\b(?:every|each)\s+(?:student|learner|pupil|person)\s+(?:who|whose|that|with)\b"
    r"|\bwho(?:'s|s|\s+is|\s+are)\s+(?:failing|flunking|on\s+probation|suspended|"
    r"dropping|at\s+risk|struggling|behind)\b"
    r"|\bwho\s+(?:got|received|earned|failed|passed|withdrew|flunked|scored|cheated)\b"
    r"|\b(?:the\s+)?(?:only|single|sole|lone)\s+(?:[\w-]+\s+){0,3}students?\b"
    r"|\b(?:highest|lowest|best|worst|top|bottom)[- ](?:gpa|grade|performing|scoring|"
    r"ranked|achieving)[- ]students?\b"
    r"|\b(?:which|what|list|name|identify|find)\s+(?:the\s+|all\s+|any\s+)?"
    r"(?:(?:first|second|third|fourth)[- ]year|freshm[ae]n|sophomore|junior|senior|new|"
    r"current|returning|graduating|female|male|black|white|hispanic|asian|honors|"
    r"athlete|nursing|engineering)\s+(?:students?|learners?|people)\b"
    r"|\be-?mails?\b|\bemail\s+addresses\b|\bphone\s+numbers?\b|\bhome\s+address"
    r"|\bcontact\s+(?:info|information|details)\b|\bnames\s+of\b",
    re.IGNORECASE,
)
# A named person asked about by a verb about their record ("Did Jane Doe
# pass MEEN 3310?", "Will jane drop out?", "Is José Núñez on probation?",
# "What GPA did Jean-Luc Picard get?", "Jane Doe's transcript"). Catalog
# names (instructors, majors, courses) are masked first, so "What has Alicia
# Shelby taught?" is not caught. A name is one to three words of letters in
# any script; ordinary words (students, enrollment, the, ...) never are.
_CAP_WORD = r"[^\W\d_a-z][\w'’-]*"  # starts with a capital (any script)
_LOW_WORD = r"[^\W\d_A-Z][\w'’-]*"
_CAP_NAME = rf"{_CAP_WORD}(?:\s+{_CAP_WORD}){{0,2}}"
# Several people in one question: "Jane Doe and John Smith".
_CAP_NAMES = rf"(?P<name>{_CAP_NAME}(?:\s*(?:,|and|or|&)\s*{_CAP_NAME})*)"
_LOW_NAMES = rf"(?P<name>{_LOW_WORD}(?:\s+{_LOW_WORD})?)"
_PERSON_TRIGGER = (
    r"\b(?i:how\s+(?:did|does|do|is|was|will)|what\s+(?:grade|grades|gpa|mark|score)"
    r"\s+(?:did|does|will|has)|what\s+(?:did|does|will|has)|did|does|do|is|was|"
    r"has|had|will|would|can|could|should|might|may|won't|isn't|didn't|doesn't)"
)
_PERSON_VERB = (
    r"(?:pass(?:es|ed)?|fail(?:s|ed)?|get|gets|got|do|does|did|take|takes|took|"
    r"graduate[sd]?|withdr(?:aw|aws|ew|awn)|drop(?:s|ped)?|earn(?:s|ed)?|"
    r"score[sd]?|enroll(?:s|ed)?|register(?:s|ed)?|perform(?:s|ed)?|"
    r"have|has|had|owe[sd]?|change[sd]?|switch(?:es|ed)?|leave|left|quit|"
    r"return(?:s|ed)?|come\s+back|stay|transfer(?:s|red)?|make|made|"
    r"finish(?:es|ed)?|complete[sd]?|(?:going|likely|expected)\s+to|"
    r"on\s+(?:academic\s+)?probation|(?:be\s+)?(?:suspended|dismissed|expelled)|"
    r"in\s+good\s+standing|at\s+risk|still\s+enrolled|enrolled|flunk(?:s|ed)?)"
)
# A lowercase name only before a verb that asks about one person's record.
_STRONG_VERB = (
    r"(?:pass(?:es|ed)?|fail(?:s|ed)?|flunk(?:s|ed)?|drop\s*out|drop(?:s|ped)?\s+out|"
    r"graduate[sd]?|withdr(?:aw|ew)|quit|get\s+(?:an?\s+)?[a-f][+-]?\b|"
    r"(?:going|likely)\s+to|on\s+probation|be\s+suspended|have\s+a\s+hold|"
    r"register(?:ed)?|enrolled|been\s+suspended)\b"
)
_PERSON_RES = (
    re.compile(rf"{_PERSON_TRIGGER}\s+{_CAP_NAMES}\s+(?=(?i:{_PERSON_VERB})\b)"),
    re.compile(
        r"\b(?:did|does|is|was|will|has)\s+" + _LOW_NAMES + rf"\s+(?={_STRONG_VERB})"
    ),
    re.compile(
        r"(?i:\b(?:grades?|gpas?|transcripts?|records?|schedules?|holds?|standing|"
        r"classes|file|profile)\s+(?:of|for))\s+"
        + _CAP_NAMES
        # The whole capitalized run, and not a group ("GPA for Mechanical
        # Engineering majors", "GPA of Pell students").
        + r"(?![\w'’-]|\s+[^\W\d_a-z])(?!\s+(?i:majors?|students?|programs?|"
        r"colleges?|departments?|courses?|classes|sections?|recipients?|athletes?|"
        r"graduates?|transfers?|cohorts?|freshm[ae]n|seniors?|juniors?|sophomores?))"
    ),
    re.compile(
        rf"(?P<name>\b{_CAP_NAME})(?:'s|’s)"
        r"\s+(?i:grades?|gpa|transcript|record|schedule|holds?|standing|major|"
        r"classes|courses|file|profile|chances?|odds)\b"
    ),
)
# Words that are never a person's name in these patterns.
_NOT_NAMES_TEXT = """
    a an the our my your their his her its this that these those any all some each
    every many most more less fewer few much no none other others such same student
    students student's learners pupils people person persons kids anyone anybody
    someone somebody everyone everybody nobody one we they he she it i you me us
    them him who what which whom whose there here enrollment enrolment retention
    headcount gpa gpas grades rate rates average overall total number freshman
    freshmen sophomore sophomores junior juniors senior seniors first second third
    fourth first-gen first-generation transfer transfers international online in-
    person hybrid part-time full-time pell athletes athlete honors women men female
    male major majors class classes course courses college colleges program programs
    department departments faculty instructor instructors professor professors
    campus university school fall spring summer term terms semester semesters year
    years cohort cohorts graduates graduation dropout dropouts not also still ever
    already really actually just even likely going expected able enough to be been
    being do does did doing of in on at for with by from as and or nor but if so
    well highest lowest best worst most least top bottom hardest easiest
    various different several alumni grads
"""
_NOT_NAMES = frozenset(_NOT_NAMES_TEXT.split())
_STOP_INITIALS = frozenset({"What", "Which", "How", "Who", "Why", "When", "Where"})


# --- allow-list masking -------------------------------------------------------
#
# A pattern cannot list every way to ask about a person ("Tell me about Kenji
# Watanabe in Nursing", "I'm advising Hannah Lee; ..."), so every capitalized
# word that is not on an allow list is treated as a name: catalog names
# (majors, colleges, subjects, course codes and titles, instructors, terms),
# campus phrases (hold offices, ...), acronyms, and the common English and
# campus words below. A lowercase word is a name only in a name position
# ("mary jane watson's gpa", "did jane doe pass").

# Common words a question may capitalize (a sentence start, a title, emphasis).
_COMMON_WORDS_TEXT = """
    a about above across after again against all almost along also although always
    am among an and another any anyone anything are aren't around as ask at average
    away back based be because been before behind being below best better between
    both but by can can't cannot change changed changes check compare compared
    comparison could couldn't count counts data day days did didn't difference do
    does doesn't doing don't down during each either else enough even ever every
    everyone exactly explain fall few fewer find first for from full get give given
    go going good great had has hasn't have haven't having he help her here hers him
    his how however i i'd i'll i'm i've if in include including instead into is
    isn't it it's its just know last least less let let's like likely list lot low
    lower lowest make many may maybe me might more most much must my need new next
    no none nor not now number of off often old on once one only or other others our
    ours out over overall own part past per percent percentage please plus pull
    quick quickly rank ranked rate rates rather really recent recently right same
    say see she should show showing since so some something sorry still such summary
    take tell than thank thanks that that's the their theirs them then there there's
    these they this those though through to today together too top total totals
    trend true under until up us use used very was wasn't way we we're well were
    weren't what what's whats when where whether which while who who's whom whose
    why will with within without won't would wouldn't year years yes yet you your
    yours actually also already anyway approximately around basically currently
    especially hey hi hello ok okay so well yeah sure great cool monday tuesday
    wednesday thursday friday saturday sunday january february march april may june
    july august september october november december fall spring summer winter autumn
    zero two three four five six seven eight nine ten hundred thousand student
    students learner learners pupil pupils people person kids alumni university
    college colleges campus school department departments office offices program
    programs major majors minor minors course courses class classes section sections
    term terms semester semesters cohort cohorts faculty instructor instructors
    professor professors advisor advisors adviser dean provost president registrar
    library bursar admissions housing dining athletics athlete athletes team teams
    club clubs honors gpa gpas grade grades credit credits hour hours enrollment
    enrolled enroll retention retained graduation graduate graduates graduated
    dropout dropouts withdrawal withdrawals probation suspension standing hold holds
    advising financial aid pell scholarship scholarships tuition fees transfer
    transfers freshman freshmen sophomore sophomores junior juniors senior seniors
    undergraduate undergraduates graduate first-generation first-gen international
    domestic in-state out-of-state online hybrid in-person part-time full-time women
    men female male gender race ethnicity hispanic latino latina latinx black white
    asian american native pacific islander alaska hawaiian nonresident multiracial
    veteran veterans accounts life main academic student-life headcount yield
    applicants applications admitted admit accepted acceptance deposits dfw stem
    budget budgets budgeted actual actuals spend spending spent overspend
    overspending overspent underspent underspending expense expenses expenditure
    expenditures revenue revenues income gift gifts grant grants endowment draw
    deficit surplus discount discounting discounted net gross fund funds funded
    fiscal fy cost costs center centers division divisions category categories
    variance payment payments installment installments paid pay paying owe owed
    owes owing balance balances overdue past-due delinquent delinquency receivable
    receivables collection collections collected aging bill bills billed billing
    charge charges late on-time money dollars salaries salary benefits operations
    travel technology facilities auxiliary restricted operating source sources
    ledger books spiritual chapel advancement administration plan plans
    mech mechanical nurses business psychology biology chemistry engineering
"""
# Short and common names of subjects people type ("failed Calc").
_ABBREVIATIONS_TEXT = """
    calc precalc trig chem ochem orgo bio biochem psych econ stats comp sci eng lit
    phys gen ed intro algebra calculus chemistry biology physics nursing math
"""
# More ordinary English that questions use, chat shorthand, titles, and tool
# names that are also course subjects ("Intro to Python").
_GENERAL_WORDS_TEXT = """
    ability able across act add added affect affected after age ago ahead already
    analysis answer appear area areas attend attended attending available bad bar
    become became begin big biggest bigger break bring broken build built busy call
    came care case cases cause caused center chance chances choose chosen clear close
    come coming common compare complete completed concern consider continue
    continued continuing cost costs could cover current currently cut decide decline
    declined declining deep define describe detail details differ different
    difficult doing done drop dropped dropping during early easier easy effect else
    end ended ending entering entire especially estimate even exist expect expected
    explain face fact factor factors fail failed failing failure far fast faster
    feel felt figure final finally fine finish finished follow following found free
    gain gained gap gaps general generally grew grow growing grown growth happen
    happened hard harder hardest have head hear heard high higher highest
    historically history hold hope huge idea impact important improve improved
    increase increased increasing indicate inside instead interest interested issue
    issues keep kept kind kinds known large larger largest late later lead learn
    leave left level levels lie likely line little live lived lives living long
    longer look looked looking lose losing loss lost lots main mainly matter mean
    means measure meet middle mind miss missed missing month months move moved
    near nearly never non-athletes normal note nothing notice ones open order
    outcome outcomes overall owe owed pass passed passing pay people perform
    performance performing period place plan point points poor possible pretty
    probably problem problems put question questions rather reach read ready real
    reason reasons record records reduce reduced remain report reports require
    required result results return returned returning rise rising risk role room run
    running save saw second seem seen send sense serious set several share short
    shrank shrink shrinking shrunk side significant similar simple single situation
    status statuses transcript transcripts schedule schedules roommate advisee
    advisees teaching taught history histories
    size slow small smaller smallest sort spend spent stand start started state
    stated stay step stop stopped story strong struggle struggling study subject
    success successful suggest support switch switched system taking talk taught
    teach teaches teaching tend tends test think thought time times tiny told took
    tough toughest toward track trend trends try trying turn turned type types
    understand unusual usually value various view want wanted watch week weeks went
    whole wide wise work worked working worse worst write writing wrong
    at-risk six-year four-year two-year stop-out transfer-out spring-to-spring
    commuters commuter recipients recipient attrition entering differ coach
    conference football basketball baseball soccer weather website poem
    r u ur pls plz thx teh hw wat whats im dont doesnt cant wont isnt
    dr prof mr mrs ms miss sir madam
    python java javascript excel sql html css matlab spss tableau
"""
# Graduate outcomes: what graduates did next and whether they gave back
# ("Median Starting Salary", "Med School", "Alumni Giving").
_OUTCOME_WORDS_TEXT = """
    earn earned earning earnings potential salary salaries starting median mean pay
    paid income wage wages job jobs employed employment unemployed unemployment
    employer employers hire hired hiring career careers placement placed working
    med medical school schools grad grads professional law doctor doctors physician
    pre-med premed postgraduate master's masters doctoral graduate-school giving give
    gave given donate donated donating donation donations donor donors gift gifts
    participation fund annual philanthropy alumnus alumna alumnae back survey surveys
    knowledge destination first-destination clearinghouse band bands acceptance
    accepted outcome outcomes debt loan loans company companies
"""
# US states and territories, for residency questions ("Texas residents").
_STATES_TEXT = """
    alabama alaska arizona arkansas california colorado connecticut delaware florida
    georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine
    maryland massachusetts michigan minnesota mississippi missouri montana nebraska
    nevada new hampshire jersey mexico york north carolina dakota ohio oklahoma
    oregon pennsylvania rhode island south tennessee texas utah vermont virginia
    washington west wisconsin wyoming puerto rico guam district columbia
    residents resident states state usa u.s. us
"""
# Words that name a group of students, never one person ("Did Hispanic
# students ...", "Texas residents").
_GROUP_WORDS_TEXT = """
    hispanic latino latina latinx black white asian american native pacific
    islander alaska hawaiian nonresident multiracial veteran veterans domestic
    international transfer transfers commuter commuters resident residents adult
    adults returning continuing new incoming entering graduating women men female
    male honors pell athletes athlete main accounts life academic advising
    registrar library admissions housing
"""
# Acronyms kept in capitals; any other all-capitals word may be a name
# ("JOHN SMITH", "KIM").
_ACRONYMS_TEXT = """
    gpa dfw stem cs it us usa fafsa sat act esl hbcu rotc ncaa ir ap ib ged ta ra
    phd mba ba bs ms ma bsn rn ipeds fte ftic sap caps id ids ok faq pdf csv
    md do mcat nsc nace
"""
KNOWN_ACRONYMS = frozenset(_ACRONYMS_TEXT.split())
COMMON_WORDS = frozenset(
    (
        _COMMON_WORDS_TEXT
        + _ABBREVIATIONS_TEXT
        + _GENERAL_WORDS_TEXT
        + _OUTCOME_WORDS_TEXT
        + _STATES_TEXT
    ).split()
)
GROUP_WORDS = frozenset((_GROUP_WORDS_TEXT + _STATES_TEXT).split())
# Counseling words that are also given names: "Will Faith graduate?" is about
# a person, not about faith.
_NAME_LIKE_COUNSELING = frozenset({"faith", "grace", "hope", "mercy", "charity"})
# Emails, handles and quoted single tokens: ids.
_ID_TOKEN_RE = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?<![\w@])@[A-Za-z_]\w*|"
    r"[\"“”'‘’]([A-Za-z_][\w.-]*)[\"“”'‘’](?=\s|[?.!,;:]|$)"
)
# A sentence that starts with a name and a record verb ("Ravi failed MEEN
# 3310", "Hunter and Brooklyn both dropped out").
_RECORD_PAST = (
    r"(?:failed|passed|flunked|withdrew|dropped(?:\s+out)?|graduated|registered|"
    r"enrolled|transferred|quit|left|got|earned|scored|took|switched|changed)\b"
)
# Multi-word campus names the catalog does not list.
CAMPUS_PHRASES = (
    "Student Accounts",
    "Academic Advising",
    "Student Life",
    "Main Campus",
    "Financial Aid",
    "Dean of Students",
    "Office of the Registrar",
    "Institutional Research",
)
_WORD_TOKEN_RE = re.compile(r"[^\W\d_][\w'’-]*")
_POSSESSIVE_TAIL_RE = re.compile(r"(?:'s|’s|'|’)$")
# A lowercase run that ends in a possessive before a record word ("mary jane
# watson's gpa", "jane's transcript").
_LOW_POSSESSIVE_RE = re.compile(
    r"((?:[^\W\d_][\w'’-]*\s+){0,2}[^\W\d_][\w-]*)(?:'s|’s)\s+(?:gpa|grades?|"
    r"transcripts?|records?|schedules?|holds?|standing|major|classes|courses|file|"
    r"profile|roommates?|advisors?|advisees?|chances?|odds|status|credits?|balance|"
    r"son|daughter|friend)\b",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=8)
def _allowed_words(names: tuple[str, ...]) -> frozenset[str]:
    """Words of catalog names (lowercase) that may appear capitalized. Person
    names in the catalog (instructors) are protected only as whole names."""
    words: set[str] = set()
    for name in names:
        if " (fictional)" in name or _looks_like_person(name):
            continue
        words.update(w.lower() for w in _WORD_TOKEN_RE.findall(name))
    return frozenset(words)


def _looks_like_person(name: str) -> bool:
    """An instructor's "First Last": two or three capitalized words, none of
    them a common or campus word."""
    parts = name.split()
    return (
        2 <= len(parts) <= 3
        and all(p[:1].isupper() and p.isalpha() for p in parts)
        and not any(p.lower() in COMMON_WORDS for p in parts)
    )


def _protected_spans(text: str, names: tuple[str, ...]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pattern = _names_re((*names, *CAMPUS_PHRASES), True)
    if pattern is not None:
        spans.extend(match.span() for match in pattern.finditer(text))
    # Course codes ("MEEN 3310") and term names ("Fall 2025").
    for match in re.finditer(
        r"\b[A-Z]{2,4}\s?-?\d{4}\b|\b(?:fall|spring|summer)\s+20\d\d\b",
        text,
        flags=re.IGNORECASE,
    ):
        spans.append(match.span())
    return spans


def _is_allowed(token: str, allowed: frozenset[str]) -> bool:
    base = _POSSESSIVE_TAIL_RE.sub("", token)
    low = base.lower()
    if low in COMMON_WORDS or low in allowed or low in _NOT_NAMES:
        return True
    if any(ch.isdigit() for ch in base):  # a code: I-0003, MATH1314
        return True
    if base.isupper() and len(base) >= 2:
        # Capitals: a known acronym (GPA, DFW, STEM) or a catalog code only.
        return low in KNOWN_ACRONYMS or low in allowed
    if any(ch.isdigit() for ch in base):  # a code: I-0003, MATH1314
        return True
    if _CAMPUS_WORDS_RE.fullmatch(low):
        return True
    # A hyphenated campus word ("first-generation", "Spring-to-spring").
    return "-" in low and all(
        part in COMMON_WORDS or part in allowed or not part or part == "to"
        for part in low.split("-")
    )


def mask_names(question: str, names: tuple[str, ...] = ()) -> tuple[str, list[str]]:
    """(the question with every word that may be a person's name replaced by
    "[name]", the words replaced). Catalog names, campus phrases, acronyms
    and common words are kept; a capitalized word starting a sentence is kept
    unless the next word is another unknown capitalized word."""
    text = fold(question)
    allowed = _allowed_words(names)
    spans = _protected_spans(text, names)

    def protected(start: int, end: int) -> bool:
        return any(a <= start and end <= b for a, b in spans)

    tokens = [
        m for m in _WORD_TOKEN_RE.finditer(text) if not protected(m.start(), m.end())
    ]
    # Without the catalog (a caller with no names) only the name patterns
    # below apply: every major and course would read as unknown.
    unknown = {
        m.start()
        for m in tokens
        if names
        if any(ch.isupper() for ch in m.group(0))
        and not _is_allowed(m.group(0), allowed)
    }
    hide: list[tuple[int, int]] = []
    for i, m in enumerate(tokens):
        if m.start() not in unknown:
            continue
        before = text[: m.start()].rstrip()
        starts_sentence = not before or before[-1] in ".?!;:"
        if starts_sentence:
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            followed = (
                nxt is not None
                and nxt.start() in unknown
                and (text[m.end() : nxt.start()].strip() == "")
            )
            possessive = _POSSESSIVE_TAIL_RE.search(m.group(0)) is not None
            verb = re.match(
                r"\s+(?:(?:and|&|,)\s+[^\W\d_][\w'’-]*\s+)?(?:both\s+|all\s+)?"
                + _RECORD_PAST,
                text[m.end() :],
                re.I,
            )
            if not (followed or possessive or verb):
                continue
        hide.append(m.span())
    # Lowercase names in a name position.
    for match in _LOW_POSSESSIVE_RE.finditer(text):
        for part in _WORD_TOKEN_RE.finditer(match.group(1)):
            start = match.start(1) + part.start()
            token = part.group(0)
            if not _is_allowed(token, allowed) and not protected(
                start, start + len(token)
            ):
                hide.append((start, start + len(token)))
    for name in person_names(text, names):
        for word in name.split():
            for hit in re.finditer(rf"(?<![\w'’-]){re.escape(word)}(?![\w-])", text):
                if not protected(*hit.span()):
                    hide.append(hit.span())
    # A lowercase run right before a record verb ("sam failed calc", "has
    # jose garcia registered").
    for match in re.finditer(
        r"((?:[^\W\d_][\w'’-]*\s+){1,3}?)(?=" + _RECORD_PAST + ")", text, re.I
    ):
        run = list(_WORD_TOKEN_RE.finditer(match.group(1)))
        while run:
            part = run.pop()
            start = match.start(1) + part.start()
            token = part.group(0)
            if _is_allowed(token, allowed) or protected(start, start + len(token)):
                break
            if any(ch.isupper() for ch in token) and token.lower() in COMMON_WORDS:
                break
            hide.append((start, start + len(token)))
    ids = [m.span() for m in _ID_TOKEN_RE.finditer(text)]
    if not hide and not ids:
        return text, []
    hidden: list[str] = []
    out = []
    last = 0
    marks = sorted(
        {(a, b, "[name]") for a, b in hide} | {(a, b, "[id]") for a, b in ids}
    )
    for start, end, mark in marks:
        if start < last:
            continue
        out.append(text[last:start])
        token = text[start:end]
        hidden.append(_POSSESSIVE_TAIL_RE.sub("", token))
        tail = "'s" if mark == "[name]" and _POSSESSIVE_TAIL_RE.search(token) else ""
        out.append(mark + tail)
        last = end
    out.append(text[last:])
    masked = re.sub(r"\[name\](?:\s+\[name\])+", "[name]", "".join(out))
    return masked, hidden


def person_names(question: str, names: tuple[str, ...] = ()) -> list[str]:
    """The person names a question asks about (catalog names masked first),
    longest first. Empty for a question about groups."""
    masked = _mask(fold(question), names)
    found: list[str] = []
    for pattern in _PERSON_RES:
        for match in pattern.finditer(masked):
            for one in re.split(r"\s*(?:,|\band\b|\bor\b|&)\s*", match.group("name")):
                _add_name(one.split(), found)
    allowed = _allowed_words(names)
    # A run of catalog or campus words ("Does Engineering have ...", "Does
    # Student Accounts have ...") is not a person.
    del allowed
    found = [
        name
        for name in found
        if not all(_is_group_word(word) for word in name.split())
        and not (name.islower() and all(w in COMMON_WORDS for w in name.split()))
    ]
    return sorted(set(found), key=len, reverse=True)


def _is_group_word(word: str) -> bool:
    """A word that names a group of students, an office or a campus thing,
    never one person ("Hispanic", "Texas", "Student", "Accounts")."""
    low = _POSSESSIVE_TAIL_RE.sub("", word).lower()
    return (
        low in GROUP_WORDS
        or low in _NOT_NAMES
        or _CAMPUS_WORDS_RE.fullmatch(low) is not None
    )


def _add_name(words: list[str], found: list[str]) -> None:
    """Add one candidate name unless it is made of ordinary words."""
    # Drop ordinary words at either end ("Did the Jane ..." keeps Jane,
    # "What's Ricky's GPA" keeps Ricky).
    while words and (
        words[0].lower() in _NOT_NAMES
        or _POSSESSIVE_TAIL_RE.sub("", words[0]) in _STOP_INITIALS
    ):
        words.pop(0)
    while words and words[-1].lower() in _NOT_NAMES:
        words.pop()
    if not words or any(w.lower() in _NOT_NAMES for w in words):
        return
    if any(w in _STOP_INITIALS for w in words):
        return
    found.append(" ".join(words))


def safe_text(
    question: str,
    names: tuple[str, ...] = (),
    extra: frozenset[str] = frozenset(),
) -> str:
    """The question as the model planner and the audit log receive it: every
    word kept only if it is on the allow list (common English and campus
    words, catalog names including instructors, course codes, term names,
    known acronyms, ``extra``), in any case or position; every other word
    becomes "[name]" (runs collapse to one). Emails, handles and quoted
    single words become "[id]", and student ids and long numbers
    "[number withheld]"."""
    text = fold(question)
    text = _ID_TOKEN_RE.sub("[id]", text)
    text = _REDACT_RE.sub("[number withheld]", text)
    allowed = _allowed_words(names) | extra
    spans = _protected_spans(text, names)
    # Placeholders already written ("[name]'s", "[number withheld]").
    spans += [m.span() for m in re.finditer(r"\[[^\]]*\](?:'s|’s)?", text)]
    out: list[str] = []
    last = 0
    for m in _WORD_TOKEN_RE.finditer(text):
        start, end = m.span()
        token = m.group(0)
        inside = any(a <= start and end <= b for a, b in spans)
        if inside or _is_allowed(token, allowed) or token.lower() in extra:
            continue
        out.append(text[last:start])
        out.append("[name]" + ("'s" if _POSSESSIVE_TAIL_RE.search(token) else ""))
        last = end
    out.append(text[last:])
    safe = re.sub(r"\[name\](?:'s)?(?:\s+\[name\](?:'s)?)+", "[name]", "".join(out))
    return " ".join(safe.split())


def strip_names(
    question: str,
    names: tuple[str, ...] = (),
    extra: frozenset[str] = frozenset(),
) -> str:
    """What the audit log stores: the names the detector found masked first
    (``mask_names``: a common word used as a name, "Did May pass"), then the
    allow-list rewrite (``safe_text``), with "[name withheld]"."""
    text = _REDACT_RE.sub("[number withheld]", question)
    masked, _ = mask_names(text, names)
    safe = safe_text(masked, names, extra)
    return safe.replace("[name]", "[name withheld]")


_INDIVIDUAL_RE = re.compile(
    r"\b(?:which|what|who|list|name|names\s+of|show\s+me|identify|find)\s+"
    r"(?:the\s+|all\s+|any\s+|specific\s+|individual\s+|\d+\s+|"
    r"(?:first[- ]generation|pell|transfer|international|in[- ]state|"
    r"out[- ]of[- ]state)\s+)?"
    r"(?:students?|student's|pupils?|people|individuals?)\b"
    r"(?!\s+(?:groups?|population|body|count|headcount|enrollment|majors?))"
    r"|\bwho\s+(?:is|are|was|were)\s+(?:the\s+)?(?:students?|failing|on\s+probation|"
    r"suspended|dropping|at\s+risk)\b"
    r"|\b(?:top|bottom|best|worst)\s+\d*\s*students?\b"
    r"|\brank(?:ing|ed)?\s+(?:the\s+|all\s+)?students?\b"
    r"|\bstudents?\s+(?:with|who\s+have|having)\s+the\s+(?:most|highest|lowest|fewest|"
    r"best|worst)\b"
    r"|\bstudents?\s+named\b|\bnamed\s+students?\b|\bwhose\s+gpa\b"
    r"|\b(?:this|that|a\s+specific|one|an\s+individual|particular)\s+student(?:'s)?\b"
    r"|\bstudent\s+(?:records?|transcripts?|names?|ids?|rows?)\b",
    re.IGNORECASE,
)
# A prediction about one student, a risk score, or flagging students: the
# individual protection applies ("Who will be suspended next term?").
_PREDICTION_RE = re.compile(
    r"\bwho\s+(?:will|might|may|could|would|(?:is|are)\s+(?:likely|going|expected)"
    r"|(?:is|are)\s+(?:most\s+)?likely)\b"
    r"|\brisk\s+(?:scores?|ratings?|levels?\s+(?:of|for)\s+(?:each|every))\b"
    r"|\bscore\s+(?:each|every|the|all)\s+students?\b"
    r"|\bflag(?:ged)?\s+(?:the\s+|any\s+|all\s+)?students?\b"
    r"|\bearly[- ]warning\s+(?:list|flags?|scores?)\b"
    r"|\b(?:predict|forecast)\w*\s+(?:whether|if)\s+(?:an?\s+|one\s+|this\s+|that\s+"
    r"|the\s+)?(?:student|learner|pupil)\b"
    # Listing the students at risk, or most likely to do something.
    r"|\b(?:list|name|identify|show(?:\s+me)?|find|give\s+me|flag|pull|which)\s+"
    r"(?:the\s+|all\s+|any\s+|our\s+)?(?:[\w-]+\s+){0,2}?at[- ]risk\s+"
    r"(?:students?|learners?|people|kids)\b"
    r"|\bat[- ]risk\s+(?:students?|learners?)\s+(?:list|names?|roster)\b"
    r"|\b(?:students?|learners?|people|kids)\s+(?:who\s+are\s+)?(?:most|least)\s+"
    r"likely\s+to\b"
    r"|\bwhich\s+(?:[\w-]+\s+){0,3}?(?:students?|learners?|people|kids)\s+(?:will|"
    r"would|might|may|could|(?:are|is)\s+(?:likely|going|expected))\b",
    re.IGNORECASE,
)
# A forward-looking question about a group: answered from the records.
_FORWARD_RE = re.compile(
    r"\b(?:predict\w*|forecast\w*|projections?|projected|outlook)\b"
    r"|\bwill\b(?!\s+(?:you|it|this|that|campus\s*lens)\b)|\bgonna\b"
    r"|\b(?:likely|going|expected|projected)\s+to\b"
    r"|\bat[- ]risk\b(?!\s+(?:courses?|class(?:es)?|sections?))"
    r"|\bnext\s+(?:year|term|semester|fall|spring|summer|academic\s+year)\b"
    r"|\bin\s+the\s+(?:future|coming\s+(?:years?|terms?|semesters?))\b"
    r"|\bgoing\s+forward\b|\bupcoming\s+(?:term|semester|year)\b",
    re.IGNORECASE,
)
# Requests that have nothing to do with the university's student data: a
# request to make something ("code me a website", "write a poem").
_OFF_TOPIC_RE = re.compile(
    r"\b(?:code|build|make|create|write|design|generate|draft|compose|develop|program)"
    r"\s+(?:me\s+|us\s+)?(?:an?\s+|the\s+|some\s+|my\s+)?(?:[\w-]+\s+){0,2}?"
    r"(?:websites?|web\s*sites?|web\s*pages?|landing\s+pages?|apps?|"
    r"scripts?|functions?|games?|poems?|songs?|stor(?:y|ies)|essays?|jokes?|"
    r"haikus?|limericks?|raps?|novels?|recipes?|cover\s+letters?|resumes?|"
    r"logos?|slogans?|tweets?)\b"
    r"|\btranslate\s+(?:this|that|the|my|it|these|those|following|a|an)\b",
    re.IGNORECASE,
)
# Words that are off-topic only in a request that names nothing in the
# records: "what's the weather" is, "weather impact on enrollment" and "how
# many students study film" are not.
_OFF_TOPIC_WORDS_RE = re.compile(
    r"\b(?:website|web\s*page|html|css|javascript|python|java|sql\s+query)\b"
    r"|\b(?:poem|haiku|limerick|joke|riddle|recipe|lyrics)\b"
    r"|\bweather\b|\btemperature\s+(?:today|tomorrow|outside)\b"
    r"|\btranslate\b|\bstock\s+(?:price|market)\b|\bbitcoin\b|\bcrypto\w*\b"
    r"|\b(?:movie|film|tv\s+show|netflix)\b|\bsports?\s+scores?\b"
    r"|\bcapital\s+of\b|\bmeaning\s+of\s+life\b|\bwho\s+won\s+the\b"
    r"|\b(?:solve|calculate)\s+(?:this\s+)?(?:equation|integral|math\s+problem)\b",
    re.IGNORECASE,
)
# Words that make a question about the university's own records; a model's
# empty plan for such a question is "not answerable yet", not off-topic.
_CAMPUS_WORDS_RE = re.compile(
    r"\b(?:students?|learners?|majors?|minors?|courses?|class(?:es)?|sections?|"
    r"gpas?|grades?|enrol\w*|enrollment|retention|retain\w*|graduat\w*|dropout\w*|"
    r"drop\s*outs?|stop\s*outs?|withdr\w*|probation|suspen\w*|holds?|advis\w*|"
    r"credits?|terms?|semesters?|fall|spring|summer|college|colleges|faculty|"
    r"instructors?|professors?|teach\w*|taught|tuition|aid|pell|first[- ]gen\w*|"
    r"freshm[ae]n|sophomores?|juniors?|seniors?|alumni|campus|universit\w*|"
    r"dfw|cohorts?|transfer\w*|athlet\w*|honors|housing|registration|regist\w*|"
    r"pass\s+rates?|fail\w*\s+rates?|departments?|applicants?|applications?|"
    r"admissions?|admit\w*|people|kids|undergrad\w*|grad\s+students?|yield|"
    r"acceptance\s+rates?|deposits?|scholarships?|financial\s+aid|endowment|"
    r"alumni|staff|library|dining|clubs?|dorms?|residence\s+halls?|parking|"
    r"[A-Z]{2,4}\s?-?\d{4})\b",
    re.IGNORECASE,
)

# What the audit log never stores: S- ids in any dash form, and any run of
# five or more digits that is not a term code (202620).
_REDACT_RE = re.compile(
    r"(?<!['’])\bS\s?[-_‐-―]?\s?\d{2,}\b"
    r"|\b(?:student|learner|pupil|id|ids|identifier|number|no\.?)\s*#?\s*\d{2,}\b"
    r"|#\s*\d{2,}\b"
    r"|\b(?!20\d\d[123]0\b)\d{5,}\b",
    re.IGNORECASE,
)

# Letters that look like Latin ones (Cyrillic, Greek) and digits used as
# letters inside words ("c0unselor"), folded before the refusal checks.
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "ԁ": "d",
        "ɡ": "g",
        "һ": "h",
        "ο": "o",
        "α": "a",
        "ε": "e",
        "ι": "i",
        "κ": "k",
        "ν": "v",
        "τ": "t",
        "ρ": "p",
        "А": "A",
        "Е": "E",
        "О": "O",
        "Р": "P",
        "С": "C",
        "Т": "T",
        "Н": "H",
        "К": "K",
        "М": "M",
        "В": "B",
        "Х": "X",
    }
)
_LEET = str.maketrans(
    {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a"}
)


def fold(question: str) -> str:
    """The question with look-alike letters folded to Latin and digits used as
    letters inside a word ("c0unselor") read as letters. Words that are all
    digits (course numbers, years, term codes) are left alone."""
    text = unicodedata.normalize("NFKC", question).translate(_HOMOGLYPHS)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")

    def word(match: re.Match[str]) -> str:
        token = match.group(0)
        if any(ch.isalpha() for ch in token) and any(ch.isdigit() for ch in token):
            if re.fullmatch(
                r"[A-Za-z]{2,4}\d{4}", token
            ):  # a course code typed "MEEN3310"
                return token
            return token.translate(_LEET)
        return token

    return re.sub(r"[\w@]+", word, text)


@functools.lru_cache(maxsize=8)
def _names_re(names: tuple[str, ...], bounded: bool) -> re.Pattern[str] | None:
    """One pattern for every catalog name of 4 or more characters, longest
    first (compiled once per catalog)."""
    parts = sorted({n for n in names if len(n) >= 4}, key=len, reverse=True)
    if not parts:
        return None
    body = "|".join(re.escape(n) for n in parts)
    if bounded:
        body = rf"(?<![\w-])(?:{body})(?![\w-])"
    return re.compile(body, re.IGNORECASE)


def _mask(question: str, names: tuple[str, ...]) -> str:
    """The question with catalog names (course titles, majors) blanked, so a
    title like "Theories of Counseling" is not read as a counseling question."""
    pattern = _names_re(names, False)
    return question if pattern is None else pattern.sub(" ", question)


def refusal_for(question: str, names: tuple[str, ...] = ()) -> tuple[str, str] | None:
    """(category, audit reason) when the question touches protected data,
    else None.

    Categories: ``counseling``, ``individual_student``, ``prediction`` (about
    one student, or scoring students). ``names`` are catalog names (course
    titles) masked out before the counseling and named-person checks. A
    forward-looking question about a group is not caught here (see
    ``is_forward_looking``).
    """
    folded = fold(question)
    masked = _mask(folded, names)
    # "Will Faith graduate?": a counseling word used as one person's name.
    for name in person_names(question, names):
        if name.lower() in _NAME_LIKE_COUNSELING:
            masked = re.sub(rf"\b{re.escape(name)}\b", " ", masked)
    if _COUNSELING_RE.search(masked) or re.search(r"\bCAPS\b", folded):
        return "counseling", COUNSELING_REFUSAL
    if (
        _STUDENT_ID_RE.search(question)
        or _STUDENT_ID_RE.search(folded)
        or _INDIVIDUAL_RE.search(folded)
        or _INDIVIDUAL_EXTRA_RE.search(folded)
    ):
        return "individual_student", INDIVIDUAL_REFUSAL
    if mask_names(question, names)[1]:
        # One named person: a prediction when the question asks what they
        # will do ("Will Jane drop out?").
        if is_forward_looking(question):
            return "prediction", PREDICTION_REFUSAL
        return "individual_student", INDIVIDUAL_REFUSAL
    if _PREDICTION_RE.search(folded):
        return "prediction", PREDICTION_REFUSAL
    return None


def is_forward_looking(question: str) -> bool:
    """A question about what will happen ("how many will drop out", "will
    enrollment fall next year"), answered from the records."""
    return _FORWARD_RE.search(fold(question)) is not None


def is_off_topic(question: str, names: tuple[str, ...] = ()) -> bool:
    """A request with nothing to do with the university's student data
    ("code me a website", "write a poem", "what's the weather"). ``names``
    are catalog names (course titles, majors) masked first, so "Web
    Development" or "Poetry Writing" stays a question about a course."""
    text = _mask(fold(question), names)
    if _OFF_TOPIC_RE.search(text):
        return True
    return _OFF_TOPIC_WORDS_RE.search(text) is not None and not mentions_campus_data(
        question, names
    )


def mentions_campus_data(question: str, names: tuple[str, ...] = ()) -> bool:
    """The question names something in the university's records (students,
    majors, courses, GPA, enrollment, a course code, or any catalog name:
    a course title, major or college)."""
    folded = fold(question)
    if _CAMPUS_WORDS_RE.search(folded):
        return True
    return _mask(folded, names) != folded


# --- forward-looking questions, read as history ---------------------------------

_FUTURE = (
    r"(?:will|would|might|may|could|gonna|(?:are|is)\s+(?:likely|going|expected)\s+to|"
    r"(?:likely|going|expected|projected)\s+to)"
)
# The words between "will" and the verb, kept ("will Pell students graduate").
_MID = r"(?P<mid>(?:[\w-]+\s+){0,3}?)"
# (future phrase, the historical measure words the rule planner reads).
_FORWARD_REWRITES: tuple[tuple[str, str], ...] = (
    (
        r"\b(?:will|would|is|are)?\s*(?:the\s+)?(?:our\s+)?(?:total\s+)?"
        r"(?:enrol(?:l)?ment|headcount)\s+(?:\w+\s+){0,2}?(?:be\s+)?(?:fall|drop|"
        r"decline|shrink|grow|rise|increase|decrease|change|go\s+(?:up|down)|"
        r"look\s+like)\b",
        " enrollment over time ",
    ),
    (
        rf"\b{_FUTURE}\s+{_MID}stop\s*[- ]?out\b",
        " stop-out rate ",
    ),
    (
        rf"\b{_FUTURE}\s+{_MID}(?:drop\s*[- ]?out|drop|leave|quit|not\s+"
        r"(?:come\s+back|return|finish)|be\s+lost)\b(?:\s+(?:of\s+)?(?:school|"
        r"college|the\s+university))?",
        " dropout rate ",
    ),
    (
        rf"\b{_FUTURE}\s+{_MID}(?:graduate|finish|complete\s+(?:a|their)\s+"
        r"degree|get\s+(?:a|their)\s+degree)\b(?:\s+on\s+time)?",
        " graduation rate ",
    ),
    (
        rf"\b{_FUTURE}\s+{_MID}(?:return|come\s+back|stay|persist|be\s+retained)"
        r"\b(?:\s+for\s+(?:a|their)\s+second\s+year)?",
        " retention ",
    ),
    (rf"\b{_FUTURE}\s+{_MID}fail\b", " DFW rate "),
    (rf"\b{_FUTURE}\s+{_MID}withdraw\b", " withdrawal rate "),
    (
        rf"\b{_FUTURE}\s+{_MID}(?:be\s+)?(?:put\s+)?on\s+probation\b",
        " probation rate ",
    ),
    (
        rf"\b{_FUTURE}\s+{_MID}be\s+suspended\b",
        " suspension rate ",
    ),
    (
        rf"\b{_FUTURE}\s+{_MID}transfer(?:\s+out)?\b",
        " transfer-out rate ",
    ),
    (
        r"\bat[- ]risk\s+of\s+(?:failing|a\s+dfw)\b",
        " DFW rate ",
    ),
    (
        r"\bat[- ]risk(?:\s+of\s+(?:dropping\s+out|leaving|not\s+returning|"
        r"stopping\s+out|withdrawing))?\b(?!\s+(?:courses?|class(?:es)?|sections?))",
        " dropout rate ",
    ),
)
_FUTURE_WORDS_RE = re.compile(
    r"\b(?:predict(?:ed|ion|ions|s)?|forecast(?:ed|s)?|projected|projections?|outlook"
    r"\s+for|(?:in\s+the\s+)?(?:next|coming|upcoming)\s+(?:academic\s+)?(?:year|term|"
    r"semester|fall|spring|summer)s?|in\s+the\s+future|going\s+forward|"
    r"will|gonna|be\s+expected\s+to|likely\s+to|going\s+to|expected\s+to)\b",
    re.IGNORECASE,
)
# The outcome a forward question asks about, so a count question ("how many
# students have holds and will drop") can also be read as a count now.
_OUTCOME_RE = re.compile(
    "|".join(
        "(?:" + pattern.replace("(?P<mid>", "(?:") + ")"
        for pattern, _ in _FORWARD_REWRITES[1:]
    ),
    re.IGNORECASE,
)


def _keep_mid(words: str, match: re.Match[str]) -> str:
    return " " + (match.groupdict().get("mid") or "") + words


def historical_form(question: str) -> str:
    """A forward-looking question as the closest question about the records,
    for the rule planner: "what % will graduate" -> "what % graduation rate",
    "will enrollment fall next year" -> "enrollment over time"."""
    text = fold(question)
    for pattern, words in _FORWARD_REWRITES:
        # Keep the group named between "will" and the verb ("will Pell
        # students graduate" -> "Pell students graduation rate").
        text = re.sub(
            pattern,
            functools.partial(_keep_mid, words),
            text,
            flags=re.IGNORECASE,
        )
    text = _FUTURE_WORDS_RE.sub(" ", text)
    return " ".join(text.split())


def count_form(question: str) -> str | None:
    """A forward count question without its outcome ("how many students have
    holds and will drop" -> "how many students have holds"), or None when the
    question asks no count or names no outcome."""
    text = fold(question)
    if not re.search(r"\bhow\s+many\b|\bnumber\s+of\b", text, re.IGNORECASE):
        return None
    stripped = _OUTCOME_RE.sub(" ", text)
    if stripped == text:
        return None
    stripped = re.sub(r"\b(?:and|or|but)\s*(?=[?.!]*\s*$)", " ", stripped.strip())
    stripped = _FUTURE_WORDS_RE.sub(" ", stripped)
    return " ".join(stripped.split())


# --- individual questions, read as totals for students like that ----------------

_AGGREGATE_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:e-?mails?|email\s+addresses|phone\s+numbers?|home\s+address(?:es)?|"
            r"contact\s+(?:info|information|details)|names?)\s+(?:of|for)\b",
            re.IGNORECASE,
        ),
        "how many",
    ),
    (
        re.compile(
            r"\b(?:which|what|who|list|name|names\s+of|show(?:\s+me)?|identify|find|"
            r"give(?:\s+me)?|tell\s+me\s+about|print|enumerate|rank(?:ing)?|dump|"
            r"export|output)\b(?:\s+(?:the|all|any|every|each|specific|individual|"
            r"me))*(?=\s|$)",
            re.IGNORECASE,
        ),
        "how many",
    ),
    (
        re.compile(
            r"\b(?:the\s+)?(?:only|single|sole|lone|top|bottom|best|worst|"
            r"highest[- ]\w+|lowest[- ]\w+)\s+(?:\d+\s+)?(?=(?:[\w-]+\s+){0,3}"
            r"students?\b)",
            re.IGNORECASE,
        ),
        "",
    ),
    (re.compile(r"\bwho(?:'s|s|\s+is|\s+are)\b", re.IGNORECASE), "how many are"),
    (
        re.compile(r"\b(?:this|that|a\s+specific|one|particular)\s+student\b", re.I),
        "students",
    ),
)


def aggregate_form(question: str, names: tuple[str, ...] = ()) -> tuple[str, bool]:
    """(the question as a question about totals, whether it named one student
    by id or name). Ids and names are removed, never passed on: "Did Jane Doe
    pass MEEN 3310?" -> "Did students pass MEEN 3310?"."""
    # Ids first, on the text as typed ("S12345" must not fold to letters).
    named = bool(_STUDENT_ID_RE.search(question))
    text = _STUDENT_ID_RE.sub(" students ", question)
    text = fold(text)
    if _STUDENT_ID_RE.search(text):
        named = True
        text = _STUDENT_ID_RE.sub(" students ", text)
    masked, hidden = mask_names(text, names)
    if hidden:
        named = True
        text = re.sub(r"\[name\](?:'s|’s)?", " ", masked)
        # "Jane and John pass" -> "pass"; "student named Ravi" -> "students".
        text = re.sub(
            r"(?<=\s)(?:and|or|&|,)(?=\s+(?:and|or|&|,)?\s*(?:pass|fail|get|got|have|"
            r"has|do|did|been|on|in)\b)",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\b(?:student|learner)\s+named\b", "students", text, flags=re.I)
        text = " ".join(text.split())
    if named:
        text = re.sub(
            rf"{_PERSON_TRIGGER}\s+(?={_PERSON_VERB}\b|\?|$)",
            lambda m: m.group(0).rstrip() + " students ",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b(of|for)(?=\s*(?:\?|$)|\s+(?:in|this|last)\b)",
            r"\1 students ",
            text,
            flags=re.IGNORECASE,
        )
    # "Did students pass MEEN 3310?": the course's D, F or withdrawal rate.
    text = re.sub(
        r"\b(?:how\s+)?(?:did|does|do|has|have)\s+(?:the\s+)?students\s+(?:(?:pass|fail|do|"
        r"perform)\b(?:\s+(?:in|at|on))?|(?:get|got)\s+(?:in|at|on)\b)",
        "the DFW rate in",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bstudents\s*(?:'s|’s|')", "students", text)
    for pattern, words in _AGGREGATE_REWRITES:
        text = pattern.sub(words, text)
    return " ".join(text.split()), named


def redact_question(question: str) -> str:
    """The question as recorded in the audit log: any student-id-shaped token
    or long number replaced, so the log never stores an id a person typed."""
    return _REDACT_RE.sub("[number withheld]", question)
