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

COUNSELING_REFUSAL = (
    "The Cabinet does not answer questions about counseling or spiritual care. That "
    "information is not in this data, and it is never disclosed."
)
INDIVIDUAL_REFUSAL = (
    "The Cabinet answers in aggregates only. It never answers about an individual "
    "student, by id or by description."
)
PREDICTION_REFUSAL = (
    "The Cabinet does not predict what an individual student will do. It can show "
    "aggregate rates, such as withdrawal or probation rates by major."
)

_COUNSELING_RE = re.compile(
    r"\b(?:counsel(?:ing|ling|or|lor|ors|lors|ed)?|chaplains?|chaplaincy|spiritual|"
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
    r"\bS\s?[-_‐-―]\s?\d{3,}\b|\bS\d{5,}\b"
    r"|\b(?:student|learner|pupil)\s*(?:(?:with\s+)?(?:the\s+)?(?:id|number|no\.?|#)"
    r"\s*)?#?\s*\d{2,}\b"
    r"|\b(?:id|ids|identifier)\s*(?:number\s*)?#?\s*\d{3,}\b",
    re.IGNORECASE,
)
# Asking for individual students: lists, names, rankings, or "which students".
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
    r"likely\s+to\s+(?:drop|fail|leave|withdraw|stop|quit|graduate|transfer|return)|"
    r"going\s+to\s+(?:drop|fail|leave|withdraw|quit)|"
    r"at[- ]risk\s+students?|students?\s+(?:who\s+are\s+)?at[- ]risk|"
    r"risk\s+scores?|early\s+warning|flag\s+students?)\b"
    r"|\bwho\s+will\b",
    re.IGNORECASE,
)
# What the audit log never stores: S- ids in any dash form, and any run of
# five or more digits that is not a term code (202620).
_REDACT_RE = re.compile(
    r"\bS\s?[-_‐-―]?\s?\d{3,}\b|\b(?!20\d\d[123]0\b)\d{5,}\b", re.IGNORECASE
)


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
    if _COUNSELING_RE.search(_mask(question, names)):
        return "counseling", COUNSELING_REFUSAL
    if _STUDENT_ID_RE.search(question) or _INDIVIDUAL_RE.search(question):
        return "individual_student", INDIVIDUAL_REFUSAL
    if _PREDICTION_RE.search(question):
        return "prediction", PREDICTION_REFUSAL
    return None


def redact_question(question: str) -> str:
    """The question as recorded in the audit log: any student-id-shaped token
    or long number replaced, so the log never stores an id a person typed."""
    return _REDACT_RE.sub("[number withheld]", question)
