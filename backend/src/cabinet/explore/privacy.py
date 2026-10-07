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
  nearest totals for students like that from the rule planner only (a typed
  name or id never reaches a model), or suggests questions.
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
    "The request is not about the university's student records, so no analysis "
    "was run."
)

# What the person reads: calm, plain lines that lead into an answer or into
# questions CampusLens can answer. The counseling line refuses the topic,
# totals included (the one authorized counseling count appears only in the
# briefing, never as an answer to a typed question).
COUNSELING_MESSAGE = (
    "CampusLens keeps counseling and spiritual care out of its answers, even as "
    "totals. It can help with related questions."
)
INDIVIDUAL_LEAD = (
    "CampusLens can't look up one student, but here are totals for students "
    "like that."
)
INDIVIDUAL_MESSAGE = (
    "CampusLens can't look up one student, but it can answer for groups of "
    "students."
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
# pass MEEN 3310?"). Catalog names (instructors, majors, courses) are
# masked first, so "What has Alicia Shelby taught?" is not caught.
_NAMED_PERSON_RE = re.compile(
    r"\b(?i:did|does|do|is|was|has|had|will|can|how\s+did|how\s+is|what\s+(?:grade|gpa)"
    r"\s+did|what\s+did)\s+([A-Z][a-z'’-]+)\s+([A-Z][a-z'’-]+)\b"
    r"(?=.*\b(?i:pass|fail|get|got|do|did|take|took|graduat\w*|withdr\w*|drop\w*|earn|"
    r"score|enroll\w*|regist\w*|grade|gpa|perform\w*)\b)"
    r"|\b(?i:grade|gpa|transcript|record)s?\s+(?i:of|for)\s+([A-Z][a-z'’-]+)\s+"
    r"([A-Z][a-z'’-]+)\b",
)
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
    r"|the\s+)?(?:student|learner|pupil)\b",
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
# Requests that have nothing to do with the university's student data.
_OFF_TOPIC_RE = re.compile(
    r"\b(?:code|build|make|create|write|design|generate|draft|compose|develop|program)"
    r"\s+(?:me\s+|us\s+)?(?:an?\s+|the\s+|some\s+|my\s+)?(?:[\w-]+\s+){0,2}?"
    r"(?:websites?|web\s*sites?|web\s*pages?|landing\s+pages?|apps?|"
    r"scripts?|functions?|games?|poems?|songs?|stor(?:y|ies)|essays?|jokes?|"
    r"haikus?|limericks?|raps?|novels?|recipes?|cover\s+letters?|resumes?|"
    r"logos?|slogans?|tweets?)\b"
    r"|\b(?:website|web\s*page|html|css|javascript|python|java|sql\s+query)\b"
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
    r"dfw|cohorts?|transfer\w*|athlet\w*|honors|housing|registration|regist\w*)\b",
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


def _mask(question: str, names: tuple[str, ...]) -> str:
    """The question with catalog names (course titles, majors) blanked, so a
    title like "Theories of Counseling" is not read as a counseling question."""
    masked = question
    for name in sorted(names, key=len, reverse=True):
        if len(name) >= 4:
            masked = re.sub(re.escape(name), " ", masked, flags=re.IGNORECASE)
    return masked


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
    if _COUNSELING_RE.search(masked) or re.search(r"\bCAPS\b", folded):
        return "counseling", COUNSELING_REFUSAL
    if (
        _STUDENT_ID_RE.search(question)
        or _STUDENT_ID_RE.search(folded)
        or _INDIVIDUAL_RE.search(folded)
        or _INDIVIDUAL_EXTRA_RE.search(folded)
        or _NAMED_PERSON_RE.search(masked)
    ):
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
    return _OFF_TOPIC_RE.search(_mask(fold(question), names)) is not None


def mentions_campus_data(question: str) -> bool:
    """The question names something in the university's records (students,
    majors, courses, GPA, enrollment, ...)."""
    return _CAMPUS_WORDS_RE.search(fold(question)) is not None


# --- forward-looking questions, read as history ---------------------------------

_FUTURE = (
    r"(?:will|would|might|may|could|gonna|(?:are|is)\s+(?:likely|going|expected)\s+to|"
    r"(?:likely|going|expected|projected)\s+to)"
)
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
        rf"\b{_FUTURE}\s+(?:\w+\s+)?stop\s*[- ]?out\b",
        " stop-out rate ",
    ),
    (
        rf"\b{_FUTURE}\s+(?:\w+\s+)?(?:drop\s*[- ]?out|drop|leave|quit|not\s+"
        r"(?:come\s+back|return|finish)|be\s+lost)\b(?:\s+(?:of\s+)?(?:school|"
        r"college|the\s+university))?",
        " dropout rate ",
    ),
    (
        rf"\b{_FUTURE}\s+(?:\w+\s+)?(?:graduate|finish|complete\s+(?:a|their)\s+"
        r"degree|get\s+(?:a|their)\s+degree)\b(?:\s+on\s+time)?",
        " graduation rate ",
    ),
    (
        rf"\b{_FUTURE}\s+(?:\w+\s+)?(?:return|come\s+back|stay|persist|be\s+retained)"
        r"\b(?:\s+for\s+(?:a|their)\s+second\s+year)?",
        " retention ",
    ),
    (rf"\b{_FUTURE}\s+(?:\w+\s+)?fail\b", " DFW rate "),
    (rf"\b{_FUTURE}\s+(?:\w+\s+)?withdraw\b", " withdrawal rate "),
    (
        rf"\b{_FUTURE}\s+(?:\w+\s+)?(?:be\s+)?(?:put\s+)?on\s+probation\b",
        " probation rate ",
    ),
    (rf"\b{_FUTURE}\s+(?:\w+\s+)?be\s+suspended\b", " suspension rate "),
    (rf"\b{_FUTURE}\s+(?:\w+\s+)?transfer(?:\s+out)?\b", " transfer-out rate "),
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
    "|".join(f"(?:{pattern})" for pattern, _ in _FORWARD_REWRITES[1:]),
    re.IGNORECASE,
)


def historical_form(question: str) -> str:
    """A forward-looking question as the closest question about the records,
    for the rule planner: "what % will graduate" -> "what % graduation rate",
    "will enrollment fall next year" -> "enrollment over time"."""
    text = fold(question)
    for pattern, words in _FORWARD_REWRITES:
        text = re.sub(pattern, words, text, flags=re.IGNORECASE)
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
    folded = fold(question)
    named = False
    text = folded
    for match in list(_NAMED_PERSON_RE.finditer(_mask(folded, names))):
        for group in (1, 2, 3, 4):
            word = match.group(group)
            if word:
                named = True
                text = re.sub(rf"\b{re.escape(word)}\b\s*", "", text, count=1)
        text = text.replace("  ", " ")
    if named:
        text = re.sub(
            r"\b(did|does|do|is|was|has|had|can|of|for)\s+(?=(?:pass|fail|get|got|"
            r"do|did|take|took|graduat|withdr|drop|earn|score|enroll|regist|"
            r"perform|\?|$))",
            r"\1 students ",
            text,
            flags=re.IGNORECASE,
        )
    # "Did students pass MEEN 3310?": the course's D, F or withdrawal rate.
    text = re.sub(
        r"\b(?:how\s+)?(?:did|does|do|has|have)\s+students\s+(?:pass|fail|do|get|got|"
        r"perform)\b(?:\s+(?:in|at|on))?",
        "the DFW rate in",
        text,
        flags=re.IGNORECASE,
    )
    ids = _STUDENT_ID_RE.findall(text)
    if ids:
        named = True
        text = _STUDENT_ID_RE.sub(" students ", text)
    text = re.sub(r"\bstudents\s*(?:'s|’s|')", "students", text)
    for pattern, words in _AGGREGATE_REWRITES:
        text = pattern.sub(words, text)
    return " ".join(text.split()), named


def redact_question(question: str) -> str:
    """The question as recorded in the audit log: any student-id-shaped token
    or long number replaced, so the log never stores an id a person typed."""
    return _REDACT_RE.sub("[number withheld]", question)
