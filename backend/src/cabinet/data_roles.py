"""Which Data page dashboards each role sees.

This is the one table both the API (``cabinet.dashboards``: which charts a
role may request) and the security middleware (``cabinet.security``: which
roles may call the Data routes at all) read. A role with no dashboards is
refused the Data routes (403) and has no Data page.

To give a new role dashboards, add one entry, e.g. for a finance office::

    "finance": ("finances",),

and that role gets the Data page with the Student finances dashboard; the
route table, the catalog the page reads, and the per-chart check all follow
from this dict. Dashboard ids are the keys of ``cabinet.dashboards.DASHBOARDS``.
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

ROLE_DASHBOARDS: dict[str, tuple[str, ...]] = {
    # The president sees every dashboard.
    ROLE_EXECUTIVE: (STUDENTS, FINANCES, CAMPUS),
    # The Financial Aid office sees student finances.
    ROLE_AID: (FINANCES,),
    # Staff and the reviewer see enrollment.
    ROLE_STAFF: (STUDENTS,),
    ROLE_REVIEWER: (STUDENTS,),
    # The administrator manages the system, not the figures.
    ROLE_ADMIN: (),
}


def dashboards_for(role: str) -> tuple[str, ...]:
    return ROLE_DASHBOARDS.get(role, ())


# The roles that may call the Data routes: every role with a dashboard.
DATA_ROLES: tuple[str, ...] = tuple(r for r, d in ROLE_DASHBOARDS.items() if d)
