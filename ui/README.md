# ui

React + TypeScript UI for CampusLens. We document the commands
and run instructions in the top-level `README.md` (`make setup`, `make ui`),
and the dev server runs on `http://127.0.0.1:5200` while proxying `/api/*` to
the API on 8910 (`vite.config.ts`). Setting `CABINET_API_TARGET` points the
proxy at an API on another port, which we use when two checkouts run side by
side on one host.

We keep the UI free of arithmetic on metrics, so it renders the `display`
strings and row-ID lists from `GET /api/findings` exactly. We interpolate every
number in the placeholder briefing text from those `display` strings, and we
link each one to its finding's evidence drawer.

## Sign-in, sessions, and roles

Every route on the API requires a logged-in user, so the app opens on `/login`
with email, password, and a Sign in button. After sign-in the app loads
`GET /api/auth/me`, keeps the session's CSRF token in memory and never in
storage, and sends it as `X-CSRF-Token` on every state-changing call
(`ui/src/auth.ts`). A 401 from any API call returns the app to `/login` with
"Your session ended. Sign in again." After five failures in fifteen minutes
from one IP or one IP and email pair, the API answers 429 with a `Retry-After`
header, and the sign-in panel announces the wait. A bare email that keeps
failing pays a progressive delay of 1, 2, 4, and 8 seconds, capped at 30,
instead of a lock. Sign out lives in the rail's masthead, next to the
signed-in email and role.

The role from `GET /api/auth/me` shapes the page. The API enforces the same
table, and we render any refusal it still returns inline, never as a crash.

- **executive** sees Ask and Approve, and reads the audit log.
- **staff** sees the briefing without Ask or Approve, and the decision panel
  shows "Only an executive can approve this" in place of the button.
- **reviewer** sees everything read-only, including the audit log.
- **admin** sees all of the above, plus the Institution area (`/institution`).
  That area lists the institution's users with their roles and status, and it
  adds a user with a one-time password shown once. Disable, enable, and role
  changes sit behind inline confirmations, never modals. The same area lists
  the institution's datasets with row counts and active and fictional flags.
  Dataset upload takes JSON only, up to 20 MB, with the API's validation
  errors listed line by line and the counseling flag shown as information.
  Activate and soft delete sit behind inline confirmations too. A newly
  activated dataset starts with no briefing and no approvals, because
  approvals stay pinned to the dataset they were computed from. We keep backups
  out of the UI because they remain an operator task.

The masthead names the institution and the active dataset the briefing is
computed from. While the active dataset is the fictional demonstration set,
the lede keeps the word "fictional" and a note under the figures says so.

## The question chooser

The Ask box offers one button per approved question from `GET /questions`, the
registry, and the field accepts only those approved questions. We submit
anything else anyway so the API logs the refusal as a `data.refused` audit
event.

## Installable

`public/manifest.webmanifest` carries the name, short name, navy theme color,
standalone mode, and `start_url` `/`, with PNG icons at 192 and 512 rendered
once from `public/icons/icon.svg`. Alongside it, `public/sw.js` registers an
offline cache with the browser that holds only the application shell and never
an API response. Offline therefore means the shell opens, and the API is
required for every number on it.

## Served by the API

`make build` writes `ui/dist`, and the API serves that directory itself, with
`CABINET_UI_DIST` to point it elsewhere. One process then serves both the page
and the API, and an API path always wins over the static mount. The `/ready`
check fails while the built UI is missing, so a deploy without the build never
passes readiness.

## Contrast (measured, WCAG ratio)

ink on paper 14.73 · ink-soft on paper 6.90 · ink-soft on rail 6.53 ·
navy on paper 10.62 · navy on rail 10.06 · paper on navy 10.62 ·
alert on paper 7.84 · paper on alert 7.84 · gold on paper 4.53 ·
ink on gold-wash 14.87 · ink-soft on gold-wash 6.96 · ink on rail 13.95.
We measured every pair, and each sits at or above 4.5 to 1.

## UI states

We can induce three states on demand with combinable query switches, and two
more switches help demos and screenshots.

- `?slow=1` shows the **loading** state. It adds a 3 s client-side delay to
  every API call so the loading panel is visible, and without the switch the
  loading state is the real pending fetch.
- `?fail=findings` shows the **error with retry** state. It forces the
  findings fetch to fail, rendering the error panel with a Retry button. The
  same state occurs naturally when the API is stopped, and Retry issues a real
  fetch and recovers.
- `?model=down` shows the **model unavailable** banner in the *Current measure
  and historical comparison* section. The metrics and the evidence drawer
  still render from the findings object. The same state appears on its own
  when an Ask produced a briefing whose analyst section is unavailable, meaning
  the provider answered 503 or `{available:false}`. Its "Check again" button
  re-runs the question (`POST /api/ask`), the one place the analysts and the
  Chief of Staff ever run. The page itself never calls
  `GET /api/briefing/<role>` on load.

- `?evidence=M2` opens the evidence drawer on the given finding once the
  findings load (any of `M1` through `M7`). Opening or closing the drawer
  keeps the URL in sync, so drawer states are shareable links.
- `?demo=refusal` shows the **denied data request** state. It fires the audit
  log's "Show a denied data request" button once on load, which posts the
  Enrollment Analyst's request for `holds.amount` to
  `/api/governance/request`. The gate's refusal sentence appears above the
  log, and the new `data.refused` event is highlighted, exactly as when the
  button is clicked by hand.

The audit log lives at the bottom of the page, and `#audit-log` deep-links to
it and scrolls it into view once the events have loaded.
