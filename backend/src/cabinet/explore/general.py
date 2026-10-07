"""The governed general analysis: one measure, by up to two groupings.

``measure_by_group`` answers the everyday institutional-research questions
("What majors have the highest dropout rate?", "Retention by first-generation
status", "Graduation rate for Pell students by college", "How many
international students are in Nursing?") with one reviewed shape:

- a **measure** from ``MEASURES``: each one is a reviewed SQL aggregate over
  one of five reviewed row sets (``UNITS``), with a written definition that
  "How this was answered" shows;
- at most two **groupings** from the allow-list ``GROUPINGS``;
- **filters**, one value per grouping attribute, and a term window;
- an order and a row limit.

Nothing a person types reaches SQL: the measure and grouping ids select
constant SQL fragments, and every filter value is a bound parameter checked
against the catalog's allowed values first.

Privacy, applied here in code (``MINIMUM_CELL_SIZE`` is 10 students):

- A cell over fewer than 10 students is withheld and never ranked.
- **Complementary suppression in every partition a cell belongs to.** A
  cell is defined by attribute values (its groupings and its filters). For
  each of those attributes, the cell's siblings (the same context with
  another value of that attribute) form a partition whose total is
  published elsewhere. In each partition every group under 10 is withheld,
  and while the withheld groups add up to fewer than 10 students the next
  smallest group is withheld too, so no subtraction from a total recovers
  a group under 10. Majors are partitioned within their college (the
  college total is published).
- **Term windows.** A measure read over a term window (not the default) is
  shown for a cell only when the same cell has no term with 1 to 9 students
  anywhere in the data, so two windows cannot be subtracted to isolate a
  small term. A single named term must not be a withheld term of the cell's
  term partition.
- Two-grouping tables carry no totals.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cabinet.counseling import MINIMUM_CELL_SIZE, SUPPRESSED_DISPLAY

if TYPE_CHECKING:  # pragma: no cover
    from cabinet.explore.catalog import Vocab

ANALYSIS_ID = "measure_by_group"


class GeneralError(ValueError):
    """A measure, grouping, or filter combination the analysis does not allow."""


# --- groupings -------------------------------------------------------------------


@dataclass(frozen=True)
class Grouping:
    key: str
    label: str  # "Gender"
    noun: str  # "gender" (in "by gender")
    values: dict[str, str]  # code -> reader label; empty when read from the data
    units: frozenset[str]
    ordinal: bool = False  # shown in natural order unless a ranking is asked


ALL_UNITS = frozenset(
    {"student", "student_term", "cohort", "graduate", "registration", "alumni", "spell"}
)
PERSON_UNITS = ALL_UNITS
TERM_UNITS = frozenset(
    {"student", "student_term", "registration", "graduate", "alumni"}
)
# Graduate outcomes (the "alumni" unit: one row per graduate) are read at
# graduation, so the enrollment-term groupings below do not apply to them.
NOT_FOR_ALUMNI = frozenset({"load", "housing"})

NOT_RECORDED = "not_recorded"

GROUPINGS: dict[str, Grouping] = {
    g.key: g
    for g in (
        Grouping("major", "Major", "major", {}, ALL_UNITS),
        Grouping("college", "College", "college", {}, ALL_UNITS),
        Grouping(
            "class_level",
            "Class level",
            "class level",
            {
                "Freshman": "Freshmen",
                "Sophomore": "Sophomores",
                "Junior": "Juniors",
                "Senior": "Seniors",
            },
            frozenset({"student", "student_term", "registration"}),
            ordinal=True,
        ),
        Grouping("term", "Term", "term", {}, TERM_UNITS, ordinal=True),
        Grouping(
            "entry_cohort", "Entry cohort", "entry cohort", {}, ALL_UNITS, ordinal=True
        ),
        Grouping(
            "residency",
            "Residency",
            "residency",
            {
                "in_state": "In-state students",
                "out_of_state": "Out-of-state students",
                "international": "International students",
            },
            PERSON_UNITS,
        ),
        Grouping(
            "first_generation",
            "First-generation status",
            "first-generation status",
            {
                "first_generation": "First-generation students",
                "continuing_generation": "Continuing-generation students",
            },
            PERSON_UNITS,
        ),
        Grouping(
            "pell",
            "Pell status",
            "Pell status",
            {"pell": "Pell recipients", "no_pell": "Students without Pell"},
            PERSON_UNITS,
        ),
        Grouping(
            "gender",
            "Gender",
            "gender",
            {"female": "Women", "male": "Men"},
            PERSON_UNITS,
        ),
        Grouping(
            "race_ethnicity",
            "Race and ethnicity (IPEDS)",
            "race and ethnicity",
            {
                "white": "White students",
                "hispanic": "Hispanic or Latino students",
                "black": "Black or African American students",
                "asian": "Asian students",
                "two_or_more": "Students of two or more races",
                "american_indian": "American Indian or Alaska Native students",
                "pacific_islander": (
                    "Native Hawaiian or Other Pacific Islander students"
                ),
                "unknown": "Students whose race and ethnicity are unknown",
                "nonresident": "U.S. Nonresident students",
            },
            PERSON_UNITS,
        ),
        Grouping(
            "age_band",
            "Age at entry",
            "age at entry",
            {
                "under_20": "Students under 20 at entry",
                "20_24": "Students 20 to 24 at entry",
                "25_34": "Students 25 to 34 at entry",
                "35_plus": "Students 35 and over at entry",
            },
            PERSON_UNITS,
            ordinal=True,
        ),
        Grouping(
            "admit_type",
            "Admit type",
            "admit type",
            {"first_time": "First-time students", "transfer": "Transfer students"},
            PERSON_UNITS,
        ),
        Grouping(
            "load",
            "Full-time or part-time",
            "full-time or part-time status",
            {
                "full_time": "Full-time students",
                "part_time": "Part-time students",
                NOT_RECORDED: "Load not recorded (summer term)",
            },
            PERSON_UNITS,
        ),
        Grouping(
            "housing",
            "Housing",
            "housing",
            {
                "on_campus": "Students living on campus",
                "off_campus": "Students living off campus",
                NOT_RECORDED: "Housing not recorded (summer term)",
            },
            PERSON_UNITS,
        ),
        Grouping(
            "athlete",
            "Athletes",
            "athlete status",
            {"athlete": "Athletes", "non_athlete": "Non-athletes"},
            PERSON_UNITS,
        ),
        Grouping(
            "honors",
            "Honors program",
            "honors program",
            {"honors": "Honors students", "non_honors": "Students not in honors"},
            PERSON_UNITS,
        ),
        Grouping(
            "modality",
            "Section modality",
            "section modality",
            {
                "in_person": "In-person sections",
                "online": "Online sections",
                "hybrid": "Hybrid sections",
            },
            frozenset({"registration"}),
        ),
        Grouping(
            "hold",
            "Hold placed in the term",
            "hold status",
            {
                "hold": "Students with a hold",
                "no_hold": "Students without a hold",
            },
            frozenset({"student", "student_term"}),
        ),
        Grouping(
            "gpa_band",
            "Final GPA",
            "final GPA band",
            {
                "2_00_2_49": "Graduates with a final GPA of 2.00 to 2.49",
                "2_50_2_99": "Graduates with a final GPA of 2.50 to 2.99",
                "3_00_3_49": "Graduates with a final GPA of 3.00 to 3.49",
                "3_50_4_00": "Graduates with a final GPA of 3.50 to 4.00",
            },
            frozenset({"alumni"}),
            ordinal=True,
        ),
    )
}
GROUPING_KEYS: tuple[str, ...] = tuple(GROUPINGS)
CLASS_ORDER = ("Freshman", "Sophomore", "Junior", "Senior")
AGE_ORDER = ("under_20", "20_24", "25_34", "35_plus")


# --- measures ---------------------------------------------------------------------


@dataclass(frozen=True)
class Measure:
    id: str
    label: str  # "dropout rate"
    unit: str
    kind: str  # pct, gpa, count, average, years
    num: str  # SQL aggregate over base alias b
    den: str
    where: str  # extra condition on base rows for this measure ("1 = 1" if none)
    definition: str
    value_label: str
    num_label: str
    den_label: str
    scope: str  # term (one term, default latest), latest (fixed), window, cohort
    default_order: str = "highest_first"
    short: str = ""  # one plain sentence for the answer, no digits
    # Money measures: "ratio" (num / den), "median" (of each person's num /
    # den), or "sum" (num), rounded to the nearest ``rounding`` dollars.
    stat: str = "ratio"
    rounding: int = 1
    count_label: str = ""  # who the students column counts, when not den_label


REGULAR = "substr(b.term, 5, 2) IN ('10', '20')"

# Graduate outcomes: the first-destination survey (six months after
# graduating), the graduate-school match, medical school applications, and
# alumni gifts. One row per graduate (the "alumni" unit); the term is the
# graduation term.
_SURVEY = (
    "From the first-destination survey six months after graduating, of bachelor's "
    "graduates who answered it (graduates who did not answer are not counted)."
)
OUTCOME_MEASURES: tuple[Measure, ...] = (
    Measure(
        "knowledge_rate",
        "first-destination knowledge rate",
        "alumni",
        "pct",
        "SUM(b.responded)",
        "COUNT(*)",
        "b.surveyed = 1",
        "Bachelor's graduates who answered the first-destination survey six months "
        "after graduating, over every bachelor's graduate whose six-month point has "
        "passed (the classes through Summer 2025).",
        "Knowledge rate (%)",
        "Answered the survey",
        "Graduates surveyed",
        "window",
        short="The knowledge rate is the share of surveyed graduates whose outcome "
        "is known.",
    ),
    Measure(
        "employment_rate",
        "employment rate",
        "alumni",
        "pct",
        "SUM(b.employed)",
        "COUNT(*)",
        "b.responded = 1",
        "Graduates employed full time or part time six months after graduating. "
        + _SURVEY,
        "Employed (%)",
        "Employed",
        "Graduates who answered the survey",
        "window",
        short="Employment here is from the first-destination survey after "
        "graduation, of graduates who answered it.",
    ),
    Measure(
        "median_salary",
        "median starting salary",
        "alumni",
        "money",
        "SUM(b.salary)",
        "COUNT(*)",
        "b.salary IS NOT NULL",
        "Median starting salary of graduates employed full time six months after "
        "graduating who reported a salary, rounded to the nearest $500. "
        + _SURVEY,
        "Median starting salary ($)",
        "Salaries",
        "Graduates reporting a salary",
        "window",
        short="Starting salaries are from the first-destination survey, for graduates "
        "employed full time who reported one, and are rounded.",
        stat="median",
        rounding=500,
    ),
    Measure(
        "grad_school_rate",
        "graduate school rate",
        "alumni",
        "pct",
        "SUM(b.grad1)",
        "COUNT(*)",
        "b.tracked = 1",
        "Bachelor's graduates found enrolled in graduate or professional school "
        "(master's, doctoral, medical, law, other professional) within one year of "
        "graduating, from the National Student Clearinghouse match, over every "
        "bachelor's graduate followed for a full year (the classes through Fall "
        "2024).",
        "Enrolled within a year (%)",
        "Enrolled within a year",
        "Graduates followed for a year",
        "window",
        short="Graduate school here means enrolled in a graduate or professional "
        "program within one year of graduating, from the Clearinghouse match.",
    ),
    Measure(
        "med_acceptance_rate",
        "medical school acceptance rate",
        "alumni",
        "pct",
        "SUM(b.med_accepted)",
        "COUNT(*)",
        "b.med_applied = 1",
        "Graduates accepted to a medical school (MD or DO), over graduates who "
        "applied, in application cycles decided by Spring 2026 (entering classes "
        "through 2026).",
        "Accepted (%)",
        "Accepted",
        "Medical school applicants",
        "window",
        short="The acceptance rate is of graduates who applied to medical school, "
        "not of every graduate.",
    ),
    Measure(
        "giving_rate",
        "alumni giving participation rate",
        "alumni",
        "pct",
        "SUM(b.donor)",
        "COUNT(*)",
        "1 = 1",
        "Graduates (Fall 2020 to Spring 2026) who made at least one gift to the "
        "university after graduating, through Spring 2026, over all those "
        "graduates. Recent graduates have had less time to give.",
        "Gave at least once (%)",
        "Gave at least once",
        "Alumni",
        "window",
        short="Giving participation here is the share of alumni who have made at "
        "least one gift since graduating.",
    ),
    Measure(
        "avg_gift",
        "average gift",
        "alumni",
        "money",
        "SUM(b.given)",
        "SUM(b.gifts)",
        "b.donor = 1",
        "Dollars given over the number of gifts, for alumni who gave, rounded to "
        "the nearest $5.",
        "Average gift ($)",
        "Dollars given",
        "Gifts",
        "window",
        rounding=5,
        count_label="Donors",
    ),
    Measure(
        "total_giving",
        "total alumni giving",
        "alumni",
        "money",
        "SUM(b.given)",
        "COUNT(*)",
        "b.donor = 1",
        "Dollars given by alumni after graduating, through Spring 2026, rounded to "
        "the nearest $100.",
        "Total given ($)",
        "Dollars given",
        "Donors",
        "window",
        stat="sum",
        rounding=100,
    ),
)

MEASURES: dict[str, Measure] = {
    m.id: m
    for m in (
        Measure(
            "headcount",
            "headcount",
            "student",
            "count",
            "COUNT(*)",
            "COUNT(*)",
            "1 = 1",
            "Students enrolled in the term (each counted once), by default the latest "
            "fall or spring term.",
            "Students",
            "Students",
            "Students",
            "term",
        ),
        Measure(
            "avg_gpa",
            "average cumulative GPA",
            "student",
            "gpa",
            "SUM(b.gpa)",
            "COUNT(*)",
            "b.gpa IS NOT NULL",
            "Average cumulative GPA, each student counted once at their latest "
            "enrolled term (the same definition as average GPA by major).",
            "Average cumulative GPA",
            "Sum of GPAs",
            "students with a GPA",
            "latest",
            "lowest_first",
        ),
        Measure(
            "avg_credits_earned",
            "average credits earned",
            "student",
            "average",
            "SUM(b.cum_earned)",
            "COUNT(*)",
            "1 = 1",
            "Average cumulative credit hours earned (transfer credit included), each "
            "student counted once at their latest enrolled term.",
            "Average credits earned",
            "Credits earned",
            "Students",
            "latest",
            "lowest_first",
        ),
        Measure(
            "dropout_rate",
            "dropout rate",
            "student",
            "pct",
            "SUM(b.dropped)",
            "COUNT(*)",
            "b.dropped IS NOT NULL",
            "A student dropped out when they left without a degree, were not enrolled "
            "in either of the next two fall or spring terms or any term since, and "
            "were "
            "not found enrolled at another college (students who transferred out are "
            "not dropouts). Denominator: students enrolled at any time from Fall 2020 "
            "to Spring 2026, counted in the major of their latest term. Students whose "
            "last term was Fall 2025 without a degree are left out (too recent to "
            "tell).",
            "Dropout rate (%)",
            "Dropped out",
            "Students",
            "latest",
            short="Dropping out here means leaving without a degree, missing the "
            "following fall and spring terms, and not enrolling at another college.",
        ),
        Measure(
            "transfer_out_rate",
            "transfer-out rate",
            "student",
            "pct",
            "SUM(b.transferred)",
            "COUNT(*)",
            "b.dropped IS NOT NULL",
            "Students who left without a degree and were later found enrolled at "
            "another college, over the same students as the dropout rate.",
            "Transfer-out rate (%)",
            "Transferred out",
            "Students",
            "latest",
        ),
        Measure(
            "major_change_rate",
            "major-change rate",
            "student",
            "pct",
            "SUM(b.changed)",
            "COUNT(*)",
            "1 = 1",
            "Students who changed their major at least once, counted in the major of "
            "their latest term.",
            "Changed major (%)",
            "Changed major",
            "Students",
            "latest",
        ),
        Measure(
            "major_change_out_rate",
            "major change-out rate",
            "spell",
            "pct",
            "SUM(b.changed_out)",
            "COUNT(*)",
            "1 = 1",
            "Of the students who were in the major at any time from Fall 2020 to "
            "Spring 2026, the share who left it for another major at the "
            "university. A student who was in two majors is counted in each; "
            "students still in the major count as not having changed.",
            "Left the major for another (%)",
            "Changed to another major",
            "Students in the major",
            "latest",
            short="It counts students who were ever in the major and later switched "
            "to a different one.",
        ),
        Measure(
            "major_attrition_rate",
            "attrition rate",
            "spell",
            "pct",
            "SUM(b.left_univ)",
            "COUNT(*)",
            "b.left_univ IS NOT NULL",
            "Of the students who were in the major at any time from Fall 2020 to "
            "Spring 2026, the share who left the university while in it without a "
            "degree (withdrew, stopped out and had not returned by Spring 2026, or "
            "were dismissed), including students who went on to another college. "
            "Students whose last term was Fall 2025 are left out (too recent to "
            "tell).",
            "Left the university (%)",
            "Left the university",
            "Students in the major",
            "latest",
            short="Attrition here means leaving the university from the major, "
            "without a degree.",
        ),
        Measure(
            "fit_flag_rate",
            "early major-fit rate",
            "cohort",
            "pct",
            "SUM(EXISTS (SELECT 1 FROM support_program_terms x "
            "WHERE x.program_id = 'fit_advising' AND x.student_id = b.sid))",
            "COUNT(*)",
            "b.term IN (SELECT term_code FROM academic_periods)",
            "First-time students who entered in a fall term from Fall 2020 to Fall "
            "2025 and, in their first year, earned a D, F or W in two or more "
            "courses their major requires or changed major twice (the early "
            "major-fit rule). Majors are as at entry.",
            "Met the early fit rule (%)",
            "Met the rule",
            "Entering students",
            "cohort",
            short="The early fit rule flags first-year students with repeated D, F "
            "or W grades in courses their major requires, or repeated changes of "
            "major.",
        ),
        Measure(
            "first_year_major_dfw_rate",
            "first-year D, F or withdrawal rate in major courses",
            "registration",
            "pct",
            "SUM(b.dfw)",
            "COUNT(*)",
            "b.first_year = 1 AND b.major_course = 1",
            "Graded registrations of first-time students in their first year (their "
            "first fall and spring terms), in courses their major requires as major "
            "or support courses, ending in D+, D, F, or W.",
            "First-year D, F or W in major courses (%)",
            "D, F, or W",
            "Graded registrations",
            "window",
        ),
        Measure(
            "pell_share",
            "Pell share",
            "student",
            "pct",
            "SUM(b.pell = 'pell')",
            "COUNT(*)",
            "1 = 1",
            "Share of enrolled students who receive a Pell grant, in the term (the "
            "latest fall or spring by default).",
            "Pell recipients (%)",
            "Pell recipients",
            "Students",
            "term",
        ),
        Measure(
            "first_gen_share",
            "first-generation share",
            "student",
            "pct",
            "SUM(b.first_generation = 'first_generation')",
            "COUNT(*)",
            "1 = 1",
            "Share of enrolled students who are first-generation college students, in "
            "the term (the latest fall or spring by default).",
            "First-generation (%)",
            "First-generation",
            "Students",
            "term",
        ),
        Measure(
            "international_share",
            "international share",
            "student",
            "pct",
            "SUM(b.residency = 'international')",
            "COUNT(*)",
            "1 = 1",
            "Share of enrolled students who are international, in the term.",
            "International (%)",
            "International",
            "Students",
            "term",
        ),
        Measure(
            "part_time_share",
            "part-time share",
            "student",
            "pct",
            "SUM(b.load = 'part_time')",
            "COUNT(*)",
            "b.load != 'not_recorded'",
            "Share of enrolled students registered for fewer than 12 credit hours at "
            "census, in the term.",
            "Part-time (%)",
            "Part-time",
            "Students",
            "term",
        ),
        Measure(
            "on_campus_share",
            "on-campus share",
            "student",
            "pct",
            "SUM(b.housing = 'on_campus')",
            "COUNT(*)",
            "b.housing != 'not_recorded'",
            "Share of enrolled students living in university housing, in the term.",
            "Living on campus (%)",
            "On campus",
            "Students",
            "term",
        ),
        Measure(
            "probation_rate",
            "probation rate",
            "student_term",
            "pct",
            "SUM(b.probation)",
            "COUNT(*)",
            "1 = 1",
            "Share of enrolled student terms ending on academic probation or "
            "continued probation (each student counted once per term).",
            "Probation rate (%)",
            "On probation",
            "Student terms",
            "window",
        ),
        Measure(
            "suspension_rate",
            "suspension rate",
            "student_term",
            "pct",
            "SUM(b.suspension)",
            "COUNT(*)",
            "1 = 1",
            "Share of enrolled student terms ending in academic suspension.",
            "Suspension rate (%)",
            "Suspended",
            "Student terms",
            "window",
        ),
        Measure(
            "stop_out_rate",
            "stop-out rate",
            "student_term",
            "pct",
            "SUM(b.stopped)",
            "COUNT(*)",
            "b.stopped IS NOT NULL",
            "Share of fall and spring student terms, without a degree that term, after "
            "which the student was not enrolled in the next fall or spring term (they "
            "may have come back later). Spring 2026 has no next term yet.",
            "Stop-out rate (%)",
            "Not enrolled next term",
            "Student terms",
            "window",
            short="A stop-out here is a student not enrolled in the next fall or "
            "spring "
            "term without having graduated.",
        ),
        Measure(
            "credit_completion_rate",
            "credit completion rate",
            "student_term",
            "pct",
            "SUM(b.earned)",
            "SUM(b.attempted)",
            "b.attempted > 0",
            "Credit hours earned over credit hours attempted (W excluded from "
            "attempted).",
            "Credits earned of attempted (%)",
            "Hours earned",
            "Hours attempted",
            "window",
            "lowest_first",
        ),
        Measure(
            "avg_credits_attempted",
            "average credits attempted per term",
            "student_term",
            "average",
            "SUM(b.attempted)",
            "COUNT(*)",
            "1 = 1",
            "Average credit hours attempted per enrolled student term.",
            "Average credits attempted",
            "Hours attempted",
            "Student terms",
            "window",
            "lowest_first",
        ),
        Measure(
            "advising_rate",
            "advising contact rate",
            "student_term",
            "pct",
            "SUM(b.advised)",
            "COUNT(*)",
            REGULAR,
            "Share of fall and spring student terms with at least one completed "
            "advising appointment.",
            "Advised (%)",
            "With a completed appointment",
            "Student terms",
            "window",
            "lowest_first",
        ),
        Measure(
            "hold_rate",
            "hold rate",
            "student_term",
            "pct",
            "SUM(b.held)",
            "COUNT(*)",
            REGULAR,
            "Share of fall and spring student terms in which a hold was placed on the "
            "student's account.",
            "With a hold placed (%)",
            "With a hold",
            "Student terms",
            "window",
        ),
        Measure(
            "retention_rate",
            "first-year retention rate",
            "cohort",
            "pct",
            "SUM(b.retained)",
            "COUNT(*)",
            "b.term IN (SELECT value FROM json_each(:ret_cohorts))",
            "First-time students who entered in a fall term (Fall 2020 to Fall 2024) "
            "and "
            "were enrolled the next fall. Majors and housing are as at entry.",
            "First-year retention (%)",
            "Returned the next fall",
            "Entering students",
            "cohort",
            "lowest_first",
            short="First-year retention here is the share of fall entrants enrolled "
            "again the next fall.",
        ),
        Measure(
            "grad_rate_4yr",
            "4-year graduation rate",
            "cohort",
            "pct",
            "SUM(b.grad4)",
            "COUNT(*)",
            "b.term IN (SELECT value FROM json_each(:g4_cohorts))",
            "First-time students who entered in a fall term (Fall 2020 to Fall 2022) "
            "and "
            "graduated within four academic years (the Fall 2022 entrants through "
            "Spring 2026, where the data ends). Majors are as at entry.",
            "Graduated within 4 years (%)",
            "Graduated within 4 years",
            "Entering students",
            "cohort",
            "lowest_first",
            short="It counts first-time fall entrants who graduated within the "
            "window shown.",
        ),
        Measure(
            "grad_rate_6yr",
            "6-year graduation rate",
            "cohort",
            "pct",
            "SUM(b.grad6)",
            "COUNT(*)",
            "b.term IN (SELECT value FROM json_each(:g6_cohorts))",
            "First-time students who entered in Fall 2020 and graduated within six "
            "academic years (through Spring 2026, where the data ends). Fall 2020 is "
            "the only entering class the data follows for six years. Majors are as at "
            "entry.",
            "Graduated within 6 years (%)",
            "Graduated within 6 years",
            "Entering students",
            "cohort",
            "lowest_first",
            short="It follows the first entering class in the records, the only one "
            "they cover for the whole window.",
        ),
        Measure(
            "time_to_degree",
            "average time to degree",
            "graduate",
            "years",
            "SUM(b.years)",
            "COUNT(*)",
            "1 = 1",
            "Average years from a graduate's first term at the university to the term "
            "they graduated (a fall start and a spring graduation four years later is "
            "4.0 years; a summer graduation adds a quarter year). Graduates from Fall "
            "2020 to Spring 2026, including students who entered before Fall 2020.",
            "Average years to degree",
            "Total years",
            "Graduates",
            "window",
            "highest_first",
        ),
        Measure(
            "graduates",
            "graduates",
            "graduate",
            "count",
            "COUNT(*)",
            "COUNT(*)",
            "1 = 1",
            "Students who graduated from Fall 2020 to Spring 2026, in the major they "
            "graduated in.",
            "Graduates",
            "Graduates",
            "Graduates",
            "window",
        ),
        Measure(
            "dfw_rate",
            "D, F or withdrawal rate",
            "registration",
            "pct",
            "SUM(b.dfw)",
            "COUNT(*)",
            "1 = 1",
            "Graded registrations (letter-graded courses, A to F or W) ending in "
            "D+, D, "
            "F, or W, over all graded registrations. Major and class level are the "
            "student's in that term.",
            "D, F or withdrawal rate (%)",
            "D, F, or W",
            "Graded registrations",
            "window",
        ),
        Measure(
            "withdrawal_rate",
            "course withdrawal rate",
            "registration",
            "pct",
            "SUM(b.w)",
            "COUNT(*)",
            "1 = 1",
            "Graded registrations ending in W, over all graded registrations.",
            "Withdrawal rate (%)",
            "W grades",
            "Graded registrations",
            "window",
        ),
        *OUTCOME_MEASURES,
    )
}
MEASURE_KEYS: tuple[str, ...] = tuple(MEASURES)


# --- the fields one request uses ----------------------------------------------------
#
# "How this was answered" lists the data each step used: the measure's own
# inputs, its groupings and filters, and the fields that define who is
# counted. The audit event (data.granted) keeps the analysis's full list,
# every field the reviewed SQL may touch, so the log never understates.

_GROUPING_FIELDS: dict[str, tuple[str, ...]] = {
    "major": ("student_term_records.program_code",),
    "college": ("academic_programs.college_code",),
    "class_level": ("student_term_records.class_level",),
    "term": ("student_term_records.term_code",),
    "entry_cohort": ("students.entry_term",),
    "residency": ("students.residency",),
    "first_generation": ("students.first_generation",),
    "pell": ("students.pell_recipient",),
    "gender": ("student_profiles.gender",),
    "race_ethnicity": ("student_profiles.race_ethnicity",),
    "age_band": ("student_profiles.age_band_at_entry",),
    "admit_type": ("students.entry_type",),
    "load": ("student_term_enrollment.academic_load",),
    "housing": ("student_term_enrollment.housing",),
    "athlete": ("student_profiles.athlete",),
    "honors": ("student_profiles.honors",),
    "modality": ("sections.modality",),
    "hold": ("person_holds.term_code",),
    "gpa_band": ("student_term_records.cumulative_gpa (at graduation)",),
}

_ELSEWHERE = "subsequent_enrollment (enrolled at another college)"

_MEASURE_FIELDS: dict[str, tuple[str, ...]] = {
    "headcount": ("student_term_records.term_code",),
    "avg_gpa": ("student_term_records.cumulative_gpa",),
    "avg_credits_earned": ("student_term_records.cumulative_earned_hours",),
    "dropout_rate": ("students.enrollment_status", _ELSEWHERE),
    "transfer_out_rate": ("students.enrollment_status", _ELSEWHERE),
    "major_change_rate": ("student_academic_programs.status",),
    "major_change_out_rate": ("student_academic_programs.status",),
    "major_attrition_rate": (
        "student_academic_programs.status",
        "student_term_records (last term enrolled)",
    ),
    "fit_flag_rate": (
        "final_grades.grade",
        "program_requirements.requirement_type",
        "student_academic_programs.status",
    ),
    "first_year_major_dfw_rate": (
        "final_grades.grade",
        "program_requirements.requirement_type",
        "students.entry_term",
    ),
    "pell_share": ("students.pell_recipient",),
    "first_gen_share": ("students.first_generation",),
    "international_share": ("students.residency",),
    "part_time_share": ("student_term_enrollment.academic_load",),
    "on_campus_share": ("student_term_enrollment.housing",),
    "probation_rate": ("academic_standings.standing",),
    "suspension_rate": ("academic_standings.standing",),
    "stop_out_rate": ("student_term_enrollment (enrolled the next term or not)",),
    "credit_completion_rate": (
        "student_term_records.attempted_hours",
        "student_term_records.earned_hours",
    ),
    "avg_credits_attempted": ("student_term_records.attempted_hours",),
    "advising_rate": ("student_appointments.status",),
    "hold_rate": ("person_holds.term_code",),
    "retention_rate": (
        "students.entry_term",
        "student_term_records (enrolled the next fall or not)",
    ),
    "grad_rate_4yr": ("students.entry_term", "students.exit_term"),
    "grad_rate_6yr": ("students.entry_term", "students.exit_term"),
    "time_to_degree": ("students.entry_term", "student_academic_programs.end_term"),
    "graduates": ("student_academic_programs.status",),
    "dfw_rate": ("final_grades.grade",),
    "withdrawal_rate": ("final_grades.grade",),
    "knowledge_rate": ("first_destination.student_id (answered or not)",),
    "employment_rate": ("first_destination.outcome",),
    "median_salary": ("first_destination.starting_salary",),
    "grad_school_rate": ("graduate_enrollment.enrollment_begin_date",),
    "med_acceptance_rate": ("medical_school_applications.accepted",),
    "giving_rate": ("alumni_gifts.student_id (gave or not)",),
    "avg_gift": ("alumni_gifts.amount",),
    "total_giving": ("alumni_gifts.amount",),
}

# Who is counted, by unit: the term each row belongs to, and the rows a unit
# leaves out (first-time entrants, standard-graded courses).
_UNIT_FIELDS: dict[str, tuple[str, ...]] = {
    "student": ("student_term_records.term_code",),
    "student_term": ("student_term_records.term_code",),
    "cohort": ("students.entry_type",),
    "graduate": ("student_academic_programs.end_term",),
    "registration": ("section_registrations.term_code", "courses.grade_mode"),
    "alumni": ("student_academic_programs.end_term", "academic_periods.end_date"),
    "spell": ("student_academic_programs.program_code",),
}


def fields_used(p: dict[str, Any]) -> tuple[str, ...]:
    """The fields one measure-by-group request uses, in reading order: the
    measure's inputs, its groupings, its filters, then who is counted."""
    measure = MEASURES.get(str(p.get("measure")))
    if measure is None:
        return ()
    groups = [str(g) for g in (p.get("group_by"), p.get("then_by")) if g]
    filters = [k for k in GROUPING_KEYS if k != "term" and p.get(k)]
    out: list[str] = list(_MEASURE_FIELDS.get(measure.id, ()))
    for key in groups + filters:
        out.extend(_GROUPING_FIELDS.get(key, ()))
    unit = _UNIT_FIELDS.get(measure.unit, ())
    if measure.unit == "cohort" and ("admit_type" in groups or "admit_type" in filters):
        unit = ()
    out.extend(unit)
    return tuple(dict.fromkeys(out))


# Every grouping a measure may use: the unit's list, minus the term for
# measures read at one fixed point (each student once, or a cohort).
def allowed_groupings(measure: Measure) -> tuple[str, ...]:
    out = [k for k, g in GROUPINGS.items() if measure.unit in g.units]
    if measure.scope in ("latest", "cohort"):
        out = [k for k in out if k != "term"]
    if measure.id == "hold_rate":
        # Split by hold status, the hold rate is always 0% or 100%.
        out = [k for k in out if k != "hold"]
    if measure.unit == "alumni":
        out = [k for k in out if k not in NOT_FOR_ALUMNI]
    return tuple(out)


# --- the reviewed row sets ---------------------------------------------------------

_PERSON_COLS = """
    st.residency AS residency,
    CASE st.first_generation WHEN 1 THEN 'first_generation'
         ELSE 'continuing_generation' END AS first_generation,
    CASE st.pell_recipient WHEN 1 THEN 'pell' ELSE 'no_pell' END AS pell,
    p.gender AS gender, p.race_ethnicity AS race_ethnicity,
    p.age_band_at_entry AS age_band, st.entry_type AS admit_type,
    CASE p.athlete WHEN 1 THEN 'athlete' ELSE 'non_athlete' END AS athlete,
    CASE p.honors WHEN 1 THEN 'honors' ELSE 'non_honors' END AS honors,
    (CAST(substr(st.entry_term, 1, 4) AS INTEGER) - 1) || '-'
        || substr(st.entry_term, 1, 4) AS entry_cohort"""

_TERM_COLS = """
    ap.major_code AS major, ap.college_code AS college, t.class_level AS class_level,
    COALESCE(e.academic_load, 'not_recorded') AS load,
    COALESCE(e.housing, 'not_recorded') AS housing"""

_NEXT_REGULAR = (
    "CASE substr(t.term_code, 5, 2) WHEN '10' THEN substr(t.term_code, 1, 4) || '20' "
    "ELSE (CAST(substr(t.term_code, 1, 4) AS INTEGER) + 1) || '10' END"
)


# Hold status: a hold placed on the student's account in the row's term (the
# same definition as the hold rate). For a measure read once per student
# (dropout, GPA), the term is the student's latest term in the window.
_HOLD_COL = """CASE WHEN EXISTS (SELECT 1 FROM person_holds h
                WHERE h.student_id = t.student_id AND h.term_code = t.term_code)
             THEN 'hold' ELSE 'no_hold' END AS hold"""


def _term_index(col: str) -> str:
    return (
        f"(2 * CAST(substr({col}, 1, 4) AS INTEGER) + CASE substr({col}, 5, 2) "
        "WHEN '10' THEN -1.0 WHEN '20' THEN 0.0 ELSE 0.5 END)"
    )


# Each unit: a CTE named b with one row per counted thing and the columns
# sid, term, every grouping key, and the measure inputs. :term_from and
# :term_to bound the term window; the student unit reads one row per
# student (their latest term in the window) unless grouped by term.
UNIT_SQL: dict[str, str] = {
    "student": f"""
WITH pick AS (
    SELECT t.*, ROW_NUMBER() OVER (
        PARTITION BY t.student_id, CASE WHEN :per_term = 1 THEN t.term_code END
        ORDER BY t.term_code DESC) AS rn
    FROM student_term_records t
    WHERE t.term_code BETWEEN :term_from AND :term_to
      AND (:regular_only = 0 OR substr(t.term_code, 5, 2) IN ('10', '20'))
),
last_any AS (
    SELECT student_id, MAX(term_code) AS last_term FROM student_term_records
    GROUP BY student_id
),
b AS (
    SELECT t.student_id AS sid, t.term_code AS term, {_TERM_COLS}, {_PERSON_COLS},
        t.cumulative_gpa AS gpa, t.cumulative_earned_hours AS cum_earned,
        EXISTS (SELECT 1 FROM student_academic_programs x
                WHERE x.student_id = t.student_id AND x.status = 'changed') AS changed,
        CASE WHEN st.enrollment_status = 'graduated' THEN 0
             WHEN se.student_id IS NOT NULL THEN 0
             WHEN la.last_term < :dropout_cutoff THEN 1
             WHEN la.last_term = :dropout_cutoff THEN NULL
             ELSE 0 END AS dropped,
        CASE WHEN st.enrollment_status != 'graduated' AND se.student_id IS NOT NULL
             THEN 1 ELSE 0 END AS transferred,
        {_HOLD_COL}
    FROM pick t
    JOIN students st ON st.student_id = t.student_id
    JOIN student_profiles p ON p.student_id = t.student_id
    JOIN academic_programs ap ON ap.program_code = t.program_code
    JOIN last_any la ON la.student_id = t.student_id
    LEFT JOIN subsequent_enrollment se ON se.student_id = t.student_id
    LEFT JOIN student_term_enrollment e
        ON e.student_id = t.student_id AND e.term_code = t.term_code
    WHERE t.rn = 1
)""",
    "student_term": f"""
WITH b AS (
    SELECT t.student_id AS sid, t.term_code AS term, {_TERM_COLS}, {_PERSON_COLS},
        a.standing IN ('Academic Probation', 'Continued Probation') AS probation,
        a.standing = 'Academic Suspension' AS suspension,
        t.attempted_hours AS attempted, t.earned_hours AS earned,
        EXISTS (SELECT 1 FROM student_appointments x
                WHERE x.student_id = t.student_id AND x.term_code = t.term_code
                  AND x.status = 'completed') AS advised,
        EXISTS (SELECT 1 FROM person_holds h
                WHERE h.student_id = t.student_id
                  AND h.term_code = t.term_code) AS held,
        EXISTS (SELECT 1 FROM person_holds h
                WHERE h.student_id = t.student_id AND h.term_code = t.term_code
                  AND h.category = 'financial') AS fin_held,
        (SELECT COALESCE(SUM(h.amount), 0) FROM person_holds h
         WHERE h.student_id = t.student_id AND h.term_code = t.term_code
           AND h.category = 'financial') AS fin_amount,
        CASE WHEN substr(t.term_code, 5, 2) = '30' THEN NULL
             WHEN nx.status IS NULL OR nx.status = 'graduated' THEN NULL
             WHEN nx.status = 'enrolled' THEN 0 ELSE 1 END AS stopped,
        {_HOLD_COL}
    FROM student_term_records t
    JOIN academic_standings a
        ON a.student_id = t.student_id AND a.term_code = t.term_code
    JOIN students st ON st.student_id = t.student_id
    JOIN student_profiles p ON p.student_id = t.student_id
    JOIN academic_programs ap ON ap.program_code = t.program_code
    LEFT JOIN student_term_enrollment e
        ON e.student_id = t.student_id AND e.term_code = t.term_code
    LEFT JOIN student_term_enrollment nx
        ON nx.student_id = t.student_id AND nx.term_code = {_NEXT_REGULAR}
    WHERE t.term_code BETWEEN :term_from AND :term_to
)""",
    "cohort": f"""
WITH b AS (
    SELECT st.student_id AS sid, st.entry_term AS term, {_TERM_COLS}, {_PERSON_COLS},
        EXISTS (SELECT 1 FROM student_term_records r
                WHERE r.student_id = st.student_id
                  AND r.term_code = (CAST(substr(st.entry_term, 1, 4) AS INTEGER) + 1)
                      || '10') AS retained,
        (st.enrollment_status = 'graduated' AND gp.academic_year
            <= (CAST(substr(st.entry_term, 1, 4) AS INTEGER) + 2) || '-'
               || (CAST(substr(st.entry_term, 1, 4) AS INTEGER) + 3)) AS grad4,
        (st.enrollment_status = 'graduated' AND gp.academic_year
            <= (CAST(substr(st.entry_term, 1, 4) AS INTEGER) + 4) || '-'
               || (CAST(substr(st.entry_term, 1, 4) AS INTEGER) + 5)) AS grad6
    FROM students st
    JOIN student_term_records t
        ON t.student_id = st.student_id AND t.term_code = st.entry_term
    JOIN student_profiles p ON p.student_id = st.student_id
    JOIN academic_programs ap ON ap.program_code = t.program_code
    LEFT JOIN student_term_enrollment e
        ON e.student_id = st.student_id AND e.term_code = st.entry_term
    LEFT JOIN academic_periods gp ON gp.term_code = st.exit_term
    WHERE substr(st.entry_term, 5, 2) = '10'
      AND (:admit_default = 0 OR st.entry_type = 'first_time')
)""",
    "graduate": f"""
WITH b AS (
    SELECT st.student_id AS sid, sap.end_term AS term, {_TERM_COLS}, {_PERSON_COLS},
        ({_term_index("sap.end_term")} - {_term_index("st.entry_term")} + 1.0) / 2.0
            AS years
    FROM student_academic_programs sap
    JOIN students st ON st.student_id = sap.student_id
    JOIN student_term_records t
        ON t.student_id = sap.student_id AND t.term_code = sap.end_term
    JOIN student_profiles p ON p.student_id = sap.student_id
    JOIN academic_programs ap ON ap.program_code = sap.program_code
    LEFT JOIN student_term_enrollment e
        ON e.student_id = sap.student_id AND e.term_code = sap.end_term
    WHERE sap.status = 'graduated' AND sap.end_term BETWEEN :term_from AND :term_to
)""",
    "spell": f"""
WITH spell AS (
    SELECT sap.student_id, sap.program_code, sap.status, sap.end_term,
        (SELECT MAX(r.term_code) FROM student_term_records r
         WHERE r.student_id = sap.student_id AND r.program_code = sap.program_code
           AND r.term_code >= sap.start_term
           AND (sap.end_term IS NULL OR r.term_code <= sap.end_term)) AS last_term
    FROM student_academic_programs sap
),
last_any AS (
    SELECT student_id, MAX(term_code) AS last_term FROM student_term_records
    GROUP BY student_id
),
b AS (
    SELECT sp.student_id AS sid, sp.last_term AS term, {_TERM_COLS}, {_PERSON_COLS},
        sp.status = 'changed' AS changed_out,
        CASE WHEN sp.status IN ('changed', 'graduated', 'active') THEN 0
             WHEN la.last_term > sp.last_term THEN 0
             WHEN la.last_term = :dropout_cutoff THEN NULL
             ELSE 1 END AS left_univ
    FROM spell sp
    JOIN student_term_records t
        ON t.student_id = sp.student_id AND t.term_code = sp.last_term
    JOIN students st ON st.student_id = sp.student_id
    JOIN student_profiles p ON p.student_id = sp.student_id
    JOIN academic_programs ap ON ap.program_code = sp.program_code
    JOIN last_any la ON la.student_id = sp.student_id
    LEFT JOIN student_term_enrollment e
        ON e.student_id = sp.student_id AND e.term_code = sp.last_term
)""",
    "registration": f"""
WITH b AS (
    SELECT r.student_id AS sid, r.term_code AS term, {_TERM_COLS}, {_PERSON_COLS},
        s.modality AS modality,
        g.grade IN ('D+', 'D', 'F', 'W') AS dfw, g.grade = 'W' AS w,
        (st.entry_type = 'first_time' AND r.term_code IN (st.entry_term,
            CASE substr(st.entry_term, 5, 2)
                WHEN '10' THEN substr(st.entry_term, 1, 4) || '20'
                ELSE (CAST(substr(st.entry_term, 1, 4) AS INTEGER) + 1) || '10'
            END)) AS first_year,
        EXISTS (SELECT 1 FROM program_requirements pr
                WHERE pr.program_code = t.program_code AND pr.course_id = s.course_id
                  AND pr.requirement_type IN ('major', 'support')) AS major_course
    FROM final_grades g
    JOIN section_registrations r ON r.registration_id = g.registration_id
    JOIN sections s ON s.section_id = r.section_id
    JOIN courses c ON c.course_id = s.course_id
    JOIN student_term_records t
        ON t.student_id = r.student_id AND t.term_code = r.term_code
    JOIN students st ON st.student_id = r.student_id
    JOIN student_profiles p ON p.student_id = r.student_id
    JOIN academic_programs ap ON ap.program_code = t.program_code
    LEFT JOIN student_term_enrollment e
        ON e.student_id = r.student_id AND e.term_code = r.term_code
    WHERE c.grade_mode = 'standard'
      AND g.grade IN ('A','A-','B+','B','B-','C+','C','C-','D+','D','F','W')
      AND r.term_code BETWEEN :term_from AND :term_to
)""",
    # One row per graduate. The data end is the last day of the latest term:
    # a graduate is surveyed once six months have passed, and followed for
    # graduate school once a full year has.
    "alumni": f"""
WITH ends AS (SELECT MAX(end_date) AS data_end FROM academic_periods),
gf AS (
    SELECT student_id, COUNT(*) AS n, SUM(amount) AS dollars
    FROM alumni_gifts GROUP BY student_id
),
b AS (
    SELECT st.student_id AS sid, sap.end_term AS term, {_TERM_COLS}, {_PERSON_COLS},
        CASE WHEN t.cumulative_gpa >= 3.5 THEN '3_50_4_00'
             WHEN t.cumulative_gpa >= 3.0 THEN '3_00_3_49'
             WHEN t.cumulative_gpa >= 2.5 THEN '2_50_2_99'
             ELSE '2_00_2_49' END AS gpa_band,
        (ap.award_level = 'Bachelor'
         AND date(gp.end_date, '+183 days') <= ends.data_end) AS surveyed,
        fd.student_id IS NOT NULL AS responded,
        COALESCE(fd.outcome IN ('employed_full_time', 'employed_part_time'), 0)
            AS employed,
        fd.starting_salary AS salary,
        (ap.award_level = 'Bachelor'
         AND date(gp.end_date, '+1 year') <= ends.data_end) AS tracked,
        COALESCE(ge.enrollment_begin_date <= date(gp.end_date, '+1 year'), 0) AS grad1,
        ma.student_id IS NOT NULL AS med_applied,
        COALESCE(ma.accepted, 0) AS med_accepted,
        gf.student_id IS NOT NULL AS donor,
        COALESCE(gf.dollars, 0) AS given, COALESCE(gf.n, 0) AS gifts
    FROM student_academic_programs sap
    CROSS JOIN ends
    JOIN students st ON st.student_id = sap.student_id
    JOIN student_term_records t
        ON t.student_id = sap.student_id AND t.term_code = sap.end_term
    JOIN student_profiles p ON p.student_id = sap.student_id
    JOIN academic_programs ap ON ap.program_code = sap.program_code
    JOIN academic_periods gp ON gp.term_code = sap.end_term
    LEFT JOIN student_term_enrollment e
        ON e.student_id = sap.student_id AND e.term_code = sap.end_term
    LEFT JOIN first_destination fd ON fd.student_id = sap.student_id
    LEFT JOIN graduate_enrollment ge ON ge.student_id = sap.student_id
    LEFT JOIN medical_school_applications ma ON ma.student_id = sap.student_id
    LEFT JOIN gf ON gf.student_id = sap.student_id
    WHERE sap.status = 'graduated' AND sap.end_term BETWEEN :term_from AND :term_to
)""",
}

OUTCOME_TABLES = (
    "first_destination",
    "graduate_enrollment",
    "medical_school_applications",
    "alumni_gifts",
)


# --- complementary suppression ------------------------------------------------------


def complementary(sizes: dict[Any, int]) -> set[Any]:
    """The keys withheld in one partition whose total is published: every
    group under the minimum, then, while the withheld groups add up to fewer
    than the minimum, the next smallest group (ties by key), so subtracting
    the visible groups from the total never recovers a group under 10."""
    hidden = {k for k, n in sizes.items() if n < MINIMUM_CELL_SIZE}
    if not hidden:
        return hidden
    visible = sorted((n, str(k), k) for k, n in sizes.items() if k not in hidden)
    while sum(sizes[k] for k in hidden) < MINIMUM_CELL_SIZE and visible:
        hidden.add(visible.pop(0)[2])
    return hidden


# --- running a request ---------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    measure: Measure
    groups: tuple[str, ...]
    filters: dict[str, str]  # grouping key -> value
    term_from: str | None
    term_to: str | None


@dataclass
class Cell:
    key: tuple[str, ...]  # group values in group order
    students: int
    num: float
    den: float
    values: list[float] | None = None  # each person's num / den (median measures)


@dataclass(frozen=True)
class TermWindow:
    """The terms a measure is actually read over."""

    term_from: str
    term_to: str
    default: bool  # the measure's own default window
    regular_only: int  # 1: fall and spring terms only
    applies: bool  # False when the measure ignores named terms


def term_window(
    m: Measure,
    per_term: bool,
    term_from: str | None,
    term_to: str | None,
    v: Vocab,
) -> TermWindow:
    """The window ``run`` reads for a measure and the requested terms. A
    term measure without a term grouping reads ONE term: the end of a
    requested range (or its start when only that is named), by default the
    latest fall or spring. Fixed-scope and cohort measures ignore terms."""
    terms = list(v.terms)
    regular = [t for t in terms if v.term_season[t] != "Summer"]
    if m.scope == "term":
        chosen = term_to or term_from
        if per_term:
            start, end = term_from or regular[0], term_to or regular[-1]
            return TermWindow(start, end, True, 1, True)
        if chosen:
            return TermWindow(chosen, chosen, chosen == regular[-1], 0, True)
        return TermWindow(regular[-1], regular[-1], True, 1, True)
    if m.scope in ("latest", "cohort"):
        return TermWindow(terms[0], terms[-1], True, 0, False)
    start, end = term_from or terms[0], term_to or terms[-1]
    if start > end:
        start, end = end, start
    return TermWindow(start, end, (start, end) == (terms[0], terms[-1]), 0, True)


class _Runner:
    def __init__(self, con: sqlite3.Connection, req: Request, v: Vocab) -> None:
        self.con = con
        self.req = req
        self.v = v
        m = req.measure
        terms = list(v.terms)
        regular = [t for t in terms if v.term_season[t] != "Summer"]
        self.notes: list[str] = []
        self.default_scope = True
        per_term = "term" in req.groups
        window = term_window(m, per_term, req.term_from, req.term_to, v)
        self.term_from, self.term_to = window.term_from, window.term_to
        self.default_scope = window.default
        self.regular_only = window.regular_only
        if m.scope in ("latest", "cohort") and (req.term_from or req.term_to):
            self.notes.append(
                f"The {m.label} is read over all the records, so the named terms "
                "were not applied."
            )
        self.per_term = 1 if per_term else 0
        # Partial sums per window (see ``_base``); a major is always read
        # with its college, since majors are partitioned within colleges.
        self._bases: dict[
            tuple[str, str], tuple[tuple[str, ...], list[tuple[Any, ...]]]
        ] = {}
        involved = set(req.groups) | set(req.filters)
        self.base_attrs = involved | ({"college"} if "major" in involved else set())
        # Cohort measures read first-time students only unless admit type
        # is asked about.
        self.admit_default = (
            0 if ("admit_type" in req.groups or "admit_type" in req.filters) else 1
        )
        fall_codes = [t for t in terms if v.term_season[t] == "Fall"]
        last_year = v.term_year[terms[-1]]
        years = list(v.academic_years)

        def within(code: str, n: int) -> bool:
            start = years.index(v.term_year[code])
            return start + n - 1 <= years.index(last_year)

        self.params: dict[str, Any] = {
            "term_from": self.term_from,
            "term_to": self.term_to,
            "per_term": self.per_term,
            "regular_only": self.regular_only,
            "dropout_cutoff": regular[-2],
            "admit_default": self.admit_default,
            "ret_cohorts": json.dumps(
                [t for t in fall_codes if f"{int(t[:4]) + 1}10" in v.terms]
            ),
            "g4_cohorts": json.dumps([t for t in fall_codes if within(t, 4)]),
            "g6_cohorts": json.dumps([t for t in fall_codes if within(t, 6)]),
        }

    def _base(
        self, window: tuple[str, str], attrs: tuple[str, ...]
    ) -> list[tuple[Any, ...]]:
        """Per-student partial sums over ``attrs`` for one term window:
        rows ``(sid, *attr values, num, den)``. Every measure's numerator and
        denominator is a SUM or COUNT, so any coarser cell is the sum of these
        rows (and its student count the distinct ids among them). One query
        per window serves every partition a request checks, instead of one
        scan of the row set per partition."""
        cached = self._bases.get(window)
        if cached is not None and set(attrs) <= set(cached[0]):
            return cached[1]
        if cached is not None:
            attrs = tuple(sorted(set(attrs) | set(cached[0])))
        m = self.req.measure
        params = dict(self.params)
        params["term_from"], params["term_to"] = window
        cols = "".join(f", {_column(k)}" for k in attrs)
        sql = (
            UNIT_SQL[m.unit]
            + f"\nSELECT b.sid{cols}, {m.num}, {m.den} FROM b WHERE {m.where} "
            f"GROUP BY b.sid{cols}"
        )
        rows = [
            (row[0], *(str(x) for x in row[1:-2]), row[-2], row[-1])
            for row in self.con.execute(sql, params)
        ]
        self._bases[window] = (attrs, rows)
        return rows

    def cells(
        self,
        keys: list[str],
        filters: dict[str, str],
        *,
        term_from: str | None = None,
        term_to: str | None = None,
    ) -> dict[tuple[str, ...], Cell]:
        """Aggregate cells over ``keys`` with ``filters`` (all bound)."""
        for key in [*keys, *filters]:
            _column(key)  # an unknown grouping is an error, as before
        window = (
            term_from if term_from is not None else self.params["term_from"],
            term_to if term_to is not None else self.params["term_to"],
        )
        wanted = set(keys) | set(filters) | self.base_attrs
        rows = self._base(window, tuple(sorted(wanted)))
        attrs = self._bases[window][0]
        pos = {a: i + 1 for i, a in enumerate(attrs)}
        key_pos = [pos[k] for k in keys]
        filter_pos = [(pos[k], v) for k, v in filters.items()]
        sids: dict[tuple[str, ...], set[Any]] = {}
        nums: dict[tuple[str, ...], list[float]] = {}
        dens: dict[tuple[str, ...], list[float]] = {}
        # A median is not a sum: keep each person's value (one base row per
        # person, since a graduate graduates once).
        per_person = self.req.measure.stat == "median"
        values: dict[tuple[str, ...], list[float]] = {}
        for row in rows:
            if any(row[i] != v for i, v in filter_pos):
                continue
            cell_key = tuple(row[i] for i in key_pos)
            sids.setdefault(cell_key, set()).add(row[0])
            nums.setdefault(cell_key, []).append(float(row[-2] or 0))
            dens.setdefault(cell_key, []).append(float(row[-1] or 0))
            if per_person and row[-1]:
                values.setdefault(cell_key, []).append(float(row[-2]) / float(row[-1]))
        return {
            cell_key: Cell(
                cell_key,
                len(sids[cell_key]),
                math.fsum(nums[cell_key]),
                math.fsum(dens[cell_key]),
                values.get(cell_key) if per_person else None,
            )
            for cell_key in sorted(sids)
        }


def _hidden(
    r: _Runner,
    keys: list[str],
    filters: dict[str, str],
    main: dict[tuple[str, ...], Cell],
    v: Vocab,
) -> set[tuple[str, ...]]:
    """The cells of ``main`` that must be withheld (see the module notes)."""
    m = r.req.measure
    hidden = {k for k, c in main.items() if c.students < MINIMUM_CELL_SIZE}
    # Every attribute that defines a cell: its siblings within the rest of
    # the cell's definition form a partition with a published total.
    for attr in keys + sorted(filters):
        ctx_filters = {k: val for k, val in filters.items() if k != attr}
        others = [k for k in keys if k != attr]
        nest_college = (
            attr == "major" and "college" not in others and "college" not in ctx_filters
        )
        part_keys = others + (["college"] if nest_college else []) + [attr]
        partitions: dict[tuple[str, ...], dict[str, int]] = {}
        for key, cell in r.cells(part_keys, ctx_filters).items():
            partitions.setdefault(key[:-1], {})[key[-1]] = cell.students
        withheld = {pk: complementary(sizes) for pk, sizes in partitions.items()}
        for key in main:
            gv = dict(zip(keys, key, strict=True))
            value = gv.get(attr, filters.get(attr))
            pk_vals = [gv[k] for k in others]
            if nest_college:
                pk_vals.append(v.major_college.get(str(value), ""))
            if value in withheld.get(tuple(pk_vals), set()):
                hidden.add(key)
    # A cell read over a term window that is not the default (or over one
    # named term) must not isolate a small term of the same cell.
    if not r.default_scope and m.scope in ("window", "term"):
        terms = list(v.terms)
        by_cell: dict[tuple[str, ...], dict[str, int]] = {}
        per_term = r.cells(
            keys + ["term"], filters, term_from=terms[0], term_to=terms[-1]
        )
        for key, cell in per_term.items():
            by_cell.setdefault(key[:-1], {})[key[-1]] = cell.students
        single = r.term_from == r.term_to
        for key in main:
            sizes = by_cell.get(key, {})
            if single:
                if r.term_from in complementary(sizes):
                    hidden.add(key)
            elif any(n < MINIMUM_CELL_SIZE for n in sizes.values()):
                hidden.add(key)
    return hidden


def _column(key: str) -> str:
    if key not in GROUPINGS:
        raise GeneralError(f"unknown grouping {key!r}")
    return f"b.{key}"


def _money(cell: Cell, m: Measure) -> int:
    """A money value in whole dollars, rounded half up to ``m.rounding``."""
    if m.stat == "median":
        value = statistics.median(cell.values) if cell.values else 0.0
    elif m.stat == "sum":
        value = cell.num
    else:
        value = cell.num / cell.den if cell.den else 0.0
    step = m.rounding or 1
    return int(math.floor(value / step + 0.5) * step)


def _value_of(cell: Cell, m: Measure) -> float | int:
    if m.kind == "count":
        return int(cell.num)
    if m.kind == "money":
        return _money(cell, m)
    if not cell.den:
        return 0.0
    ratio = cell.num / cell.den
    if m.kind == "pct":
        return round(100.0 * ratio, 1)
    if m.kind == "gpa":
        return round(ratio, 3)
    if m.kind == "years":
        return round(ratio, 2)
    return round(ratio, 1)


def check_request(
    measure: str, groups: list[str], filters: dict[str, Any]
) -> tuple[Measure, tuple[str, ...]]:
    """The measure and groupings, or GeneralError when the allow-lists refuse
    the combination (used by the plan validator and by the analysis)."""
    m = MEASURES.get(measure)
    if m is None:
        raise GeneralError(f"unknown measure {measure!r}")
    allowed = allowed_groupings(m)
    clean: list[str] = []
    for g in groups:
        if g not in allowed:
            raise GeneralError(f"the {m.label} cannot be grouped by {g}")
        if g in clean:
            raise GeneralError("a grouping is named twice")
        clean.append(g)
    if len(clean) > 2:
        raise GeneralError("at most two groupings")

    for key in filters:
        if key not in allowed and key != "term":
            raise GeneralError(f"the {m.label} cannot be filtered by {key}")
        if key in clean:
            raise GeneralError(f"{key} is both a grouping and a filter")
    return m, tuple(clean)


def _labels(key: str, value: str, v: Vocab) -> str:
    if key == "major":
        return v.majors.get(value, value)
    if key == "college":
        return v.colleges.get(value, value)
    if key == "term":
        return v.terms.get(value, value)
    if key == "entry_cohort":
        return f"Entered in {value}"
    return GROUPINGS[key].values.get(value, value)


def _sort_key(key: str, value: str) -> Any:
    if key == "class_level":
        return CLASS_ORDER.index(value) if value in CLASS_ORDER else 99
    if key == "age_band":
        return AGE_ORDER.index(value) if value in AGE_ORDER else 99
    return value


def run(
    con: sqlite3.Connection, p: dict[str, Any], v: Vocab
) -> tuple[list[dict[str, Any]], list[str], list[tuple[str, str, str]]]:
    """(rows, notes, columns as (key, label, kind)) for one request."""
    groups = [g for g in (p.get("group_by"), p.get("then_by")) if g]
    filters = {k: str(p[k]) for k in GROUPING_KEYS if k not in ("term",) and p.get(k)}
    m, groups_t = check_request(str(p.get("measure")), groups, filters)
    if m.unit == "alumni" and not _has_outcomes(con):
        raise GeneralError(
            "graduate outcomes are not in this school database yet; regenerate it "
            "with make school-data"
        )
    req = Request(m, groups_t, filters, p.get("term_from"), p.get("term_to"))
    r = _Runner(con, req, v)
    keys = list(groups_t)
    main = r.cells(keys, filters)
    hidden = _hidden(r, keys, filters, main, v)

    columns: list[tuple[str, str, str]] = []
    for g in keys:
        if g in ("major", "college", "term"):
            columns.append((g, f"{GROUPINGS[g].label} code", "text"))
            columns.append((f"{g}_name", GROUPINGS[g].label, "text"))
        else:
            label_key = (
                "group" if not any(c[0] == "group" for c in columns) else "group_2"
            )
            columns.append((label_key, GROUPINGS[g].label, "text"))
    value_kind = {"years": "average"}.get(m.kind, m.kind)
    scope_name = (
        v.terms.get(r.term_to, r.term_to)
        if m.scope == "term" and "term" not in keys
        else None
    )
    if scope_name is not None:
        columns.append(("scope", "Term", "text"))
    window = {
        "grad_rate_4yr": "Within 4 academic years",
        "grad_rate_6yr": "Within 6 academic years",
    }.get(m.id)
    if window is not None:
        columns.append(("window", "Window", "text"))
    if m.kind == "count":
        columns.append(("value", m.value_label, "count"))
    elif m.kind == "money":
        # Only the people counted and the rounded value: an exact sum beside
        # a rounded figure would undo the rounding.
        columns.append(("students", m.count_label or m.den_label, "count"))
        columns.append(("value", m.value_label, "money"))
    else:
        per_student = _per_student(m)
        columns.append(
            ("students", m.den_label if per_student else "Students", "count")
        )
        if not per_student:
            columns.append(("denominator", m.den_label, _den_kind(m)))
        if m.kind not in ("gpa", "years"):
            columns.append(("numerator", m.num_label, _num_kind(m)))
        columns.append(("value", m.value_label, value_kind))

    def row_for(key: tuple[str, ...], cell: Cell) -> dict[str, Any]:
        row: dict[str, Any] = {} if scope_name is None else {"scope": scope_name}
        if window is not None:
            row["window"] = window
        label_slot = 0
        for g, value in zip(keys, key, strict=True):
            if g in ("major", "college", "term"):
                row[g] = value
                row[f"{g}_name"] = _labels(g, value, v)
            else:
                row["group" if label_slot == 0 else "group_2"] = _labels(g, value, v)
                label_slot += 1
        if m.kind == "count":
            row["value"] = int(cell.num)
        else:
            row["students"] = cell.students
            row["denominator"] = _num_out(cell.den, m, den=True)
            row["numerator"] = _num_out(cell.num, m, den=False)
            row["value"] = _value_of(cell, m)
        row["_sort"] = (
            row["value"]
            if m.kind == "money"
            else (cell.num / cell.den)
            if (cell.den and m.kind != "count")
            else cell.num
        )
        row["_key"] = tuple(_sort_key(g, x) for g, x in zip(keys, key, strict=True))
        return row

    visible = [row_for(k, c) for k, c in main.items() if k not in hidden]
    withheld = len([k for k in main if k in hidden])
    order = p.get("order") or ""
    natural = order == "natural" or (
        not order and keys and all(GROUPINGS[g].ordinal for g in keys)
    )
    if natural:
        visible.sort(key=lambda r_: r_["_key"])
    else:
        descending = (order or m.default_order) == "highest_first"
        visible.sort(
            key=lambda r_: (-r_["_sort"] if descending else r_["_sort"], r_["_key"])
        )
    top = p.get("top")
    ranked_of = len(visible)
    if top and not natural:
        visible = visible[: int(top)]
    cut_note: str | None = None
    if len(visible) < ranked_of and keys:
        plural = {"major": "majors", "college": "colleges", "term": "terms"}
        noun = plural.get(keys[0], "groups") if len(keys) == 1 else "groups"
        cut_note = (
            f"The first {len(visible)} of {ranked_of:,} {noun} in this ranking "
            "are shown."
        )

    # The whole (one grouping): the parent of every row above, checked the
    # same way as a cell of its own.
    if len(keys) == 1:
        total = r.cells([], filters)
        if () in total and () not in _hidden(r, [], filters, total, v):
            whole_row = row_for_total(total[()], m, keys)
            if scope_name is not None:
                whole_row["scope"] = scope_name
            if window is not None:
                whole_row["window"] = window
            visible.append(whole_row)

    notes = list(r.notes)
    notes.insert(0, m.definition)
    if withheld and not keys:
        notes.append(
            f"The figure is withheld: it describes {SUPPRESSED_DISPLAY} students, or "
            "could be worked out from published totals for a group that small."
        )
    elif withheld:
        notes.append(
            f"{withheld} {'group is' if withheld == 1 else 'groups are'} withheld "
            f"(groups of {SUPPRESSED_DISPLAY} students, with more where needed so a "
            "withheld figure cannot be worked out from a total) and not ranked."
        )
    if cut_note is not None:
        notes.append(cut_note)
    if "hold" in keys or "hold" in filters:
        notes.append(
            "A student counts as having a hold when a hold was placed on their "
            "account in the term counted"
            + (
                " (their latest term, for a figure read once per student)."
                if m.unit == "student" and m.scope == "latest"
                else "."
            )
        )
    if m.unit == "cohort" and r.admit_default:
        notes.append(
            "Entering students are first-time students unless admit type is asked."
        )
    if m.unit == "alumni":
        notes.extend(_outcome_notes(con, m))
    keep = {c[0] for c in columns}
    rows = [
        {k: val for k, val in row.items() if k in keep or k.startswith("_")}
        for row in visible
    ]
    for row in rows:
        row.pop("_key", None)
    return rows, notes, columns


def row_for_total(cell: Cell, m: Measure, keys: list[str]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    g = keys[0]
    everyone = "All graduates" if m.unit == "alumni" else "All students"
    if g in ("major", "college", "term"):
        row[g] = "All"
        row[f"{g}_name"] = everyone
    else:
        row["group"] = everyone
    if m.kind == "count":
        row["value"] = int(cell.num)
    else:
        row["students"] = cell.students
        row["denominator"] = _num_out(cell.den, m, den=True)
        row["numerator"] = _num_out(cell.num, m, den=False)
        row["value"] = _value_of(cell, m)
    row["_sort"] = None
    row["_total"] = True
    return row


def _per_student(m: Measure) -> bool:
    """The denominator is the students themselves (one row per student)."""
    return m.unit in ("student", "cohort", "graduate", "alumni") and m.den == "COUNT(*)"


def _den_kind(m: Measure) -> str:
    return "hours" if m.id == "credit_completion_rate" else "count"


def _num_kind(m: Measure) -> str:
    if m.kind == "gpa":
        return "average"
    if m.kind in ("average", "years") or m.id == "credit_completion_rate":
        return "hours" if m.id != "time_to_degree" else "average"
    return "count"


def _num_out(value: float, m: Measure, *, den: bool) -> float | int:
    if (
        den
        or m.kind in ("pct", "count")
        or m.id
        in (
            "avg_credits_earned",
            "avg_credits_attempted",
        )
    ):
        return int(round(value))
    return round(value, 2)


def _has_outcomes(con: sqlite3.Connection) -> bool:
    found = {
        str(r[0])
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
            "(SELECT value FROM json_each(?))",
            (json.dumps(OUTCOME_TABLES),),
        )
    }
    return found == set(OUTCOME_TABLES)


def _outcome_notes(con: sqlite3.Connection, m: Measure) -> list[str]:
    """Institution-wide context for an outcome measure: the survey's
    knowledge rate (so a rate of respondents is read honestly), and how
    salaries and small groups are shown. Every count here is over all
    graduates in the records, never a group."""
    notes: list[str] = []
    if m.id in ("knowledge_rate", "employment_rate", "median_salary"):
        row = con.execute(
            """SELECT MIN(sap.end_term), MAX(sap.end_term), COUNT(*),
                      SUM(fd.student_id IS NOT NULL)
               FROM student_academic_programs sap
               JOIN academic_programs ap ON ap.program_code = sap.program_code
               JOIN academic_periods gp ON gp.term_code = sap.end_term
               LEFT JOIN first_destination fd ON fd.student_id = sap.student_id
               WHERE sap.status = 'graduated' AND ap.award_level = 'Bachelor'
                 AND date(gp.end_date, '+183 days')
                     <= (SELECT MAX(end_date) FROM academic_periods)"""
        ).fetchone()
        if row and row[2]:
            names = dict(
                con.execute("SELECT term_code, name FROM academic_periods").fetchall()
            )
            first, last, eligible, answered = row
            notes.append(
                f"First-destination survey: {answered:,} of {eligible:,} bachelor's "
                f"graduates surveyed ({names.get(first, first)} to "
                f"{names.get(last, last)} graduates) answered, a knowledge rate of "
                f"{round(100.0 * answered / eligible, 1)}%. Graduates who did not "
                "answer are not in the rates or salaries."
            )
    if m.kind == "money":
        notes.append(
            f"Dollar figures are rounded to the nearest ${m.rounding:,}; a figure "
            f"for a group of {SUPPRESSED_DISPLAY} people is withheld."
        )
    return notes
