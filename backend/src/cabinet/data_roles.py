"""Which Data page dashboards each role sees.

This is the one table both the API (``cabinet.dashboards``: which charts a
role may request) and the security middleware (``cabinet.security``: which
roles may call the Data routes at all) read. A role with no dashboards is
refused the Data routes (403) and has no Data page.

To give a new role dashboards, add one entry, e.g. for a finance office::

    "registrar": ("students",),

(and, to let it narrow or compare by Pell status, add it to
RESTRICTED_ATTRIBUTES). That role then gets the Data page with the Student
finances dashboard; the route table, the catalog the page reads, and the
per-chart check all follow from these dicts. The UI's sidebar row reads
DATA_PAGE_ROLES in ui/src/dataPage.ts, which mirrors this table. Dashboard
ids are the keys of ``cabinet.dashboards.DASHBOARDS``.
"""

from __future__ import annotations

from cabinet.auth import (
    ROLE_ADMIN,
    ROLE_AID,
    ROLE_EXECUTIVE,
    ROLE_REVIEWER,
    ROLE_STAFF,
)

STUDENTS = "students"
FINANCES = "finances"
CAMPUS = "campus"
# The university's own budget (no student data): the president and the
# finance office only (cabinet.explore.finance.BUDGET_ROLES also lets the
# administrator read it in Explore and on the Finance overview).
BUDGET = "budget"

ROLE_DASHBOARDS: dict[str, tuple[str, ...]] = {
    # The president sees every dashboard.
    ROLE_EXECUTIVE: (STUDENTS, FINANCES, BUDGET, CAMPUS),
    # The Financial Aid office sees student finances.
    ROLE_AID: (FINANCES,),
    # Staff and the reviewer see enrollment.
    ROLE_STAFF: (STUDENTS,),
    ROLE_REVIEWER: (STUDENTS,),
    # The administrator manages the system, not the figures.
    ROLE_ADMIN: (),
    # The department roles (added on the role-logins branch; inert until
    # those roles exist): finance sees student finances, the registrar and
    # student life see students, IT sees none.
    "finance": (FINANCES, BUDGET),
    "registrar": (STUDENTS,),
    "studentlife": (STUDENTS,),
    "it": (),
}

# Attributes that say something about a student's finances: only these
# roles may narrow or split a chart by them (any chart, not only the
# finance dashboard). Every other attribute is open to every Data role.
RESTRICTED_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "pell": (ROLE_EXECUTIVE, ROLE_AID, "finance"),
}


def attribute_allowed(role: str, key: str) -> bool:
    roles = RESTRICTED_ATTRIBUTES.get(key)
    return roles is None or role in roles


def dashboards_for(role: str) -> tuple[str, ...]:
    return ROLE_DASHBOARDS.get(role, ())


# The roles that may call the Data routes: every role with a dashboard.
DATA_ROLES: tuple[str, ...] = tuple(r for r, d in ROLE_DASHBOARDS.items() if d)
