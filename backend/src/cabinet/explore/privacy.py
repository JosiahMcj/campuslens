"""Questions Explore refuses before any planning or model call.

The school database holds no counseling or spiritual-care data, and Explore
answers in aggregates only. A question about counseling or spiritual care,
about one named or numbered student, or asking to predict what an individual
student will do is refused here, in code, before the planner (rule or model)
sees it. The API records the refusal as ``data.refused``.

The checks are deliberately broad: a refused question can be rephrased as an
aggregate question ("What is the DFW rate in College Algebra by
first-generation status?"), while a leaked individual answer cannot be taken
back.
"""

from __future__ import annotations

import re
import unicodedata

# Calm, plain lines: a refusal is CampusLens working as designed, not an
# error. The counseling line refuses the topic, totals included, because the
# questions it catches are mostly aggregate ("How many students visited the
# counseling center?"). It does not say the data is absent: the one
# authorized counseling count (M9) appears only in the briefing, never as an
# answer to a typed question.
COUNSELING_REFUSAL = (
    "CampusLens does not answer questions about counseling or spiritual care, "
    "even as totals."
)
INDIVIDUAL_REFUSAL = (
    "CampusLens answers with totals only, never about a single student, by id or "
    "by description."
)
PREDICTION_REFUSAL = (
    "CampusLens does not predict what an individual student will do. It can show "
    "totals, such as withdrawal or probation rates by major."
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
_PREDICTION_RE = re.compile(
    r"\b(?:predict\w*|forecast\w*|will\s+(?:\w+\s+)?(?:drop|fail|leave|withdraw|stop|quit|"
    r"graduate|transfer|return|be\s+suspended|be\s+dismissed|be\s+on\s+probation)|"
    r"likely\s+to\s+(?:drop\w*|fail|leave|withdraw|stop|quit|graduate|transfer|return)|"
    r"at[- ]risk\s+of\s+\w+|"
    r"going\s+to\s+(?:drop|fail|leave|withdraw|quit)|"
    r"at[- ]risk\s+students?|students?\s+(?:who\s+are\s+)?at[- ]risk|"
    r"risk\s+scores?|early\s+warning|flag\s+students?)\b"
    r"|\bwho\s+will\b",
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
    """(category, message) when the question must be refused, else None.

    Categories: ``counseling``, ``individual_student``, ``prediction``.
    ``names`` are catalog names (course titles) masked out before the
    counseling check only.
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


def redact_question(question: str) -> str:
    """The question as recorded in the audit log: any student-id-shaped token
    or long number replaced, so the log never stores an id a person typed."""
    return _REDACT_RE.sub("[number withheld]", question)
