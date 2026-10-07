# Design record

The visual authority for the CampusLens interface. Every colour, size, spacing step and corner
radius in `ui/src` follows this file. Change this file first when the look changes.

## Concept

A calm, chat-style workspace. The president asks one of the approved questions in the centre
of the screen, and everything else is one click away in the sidebar and opens as a page of its
own beside the sidebar, with its own address (`/view/<page>`) and a Back button: Full briefing,
Key figures, Evidence & sources, Staff actions, Decision, AI employees and data access, Audit
log, Financial Aid review, Profile, Settings, and Institution settings (for administrators). The
figures and their evidence open from the numbers in an answer. Any other question about the
demonstration university is an Explore question: the answer is computed from the records, each
number in it links to the table cell it came from, and a "How this was answered" fold shows
every step. The page never shows more than one thing asking to be done. The governance is visible, not decorative: refusals, sources
and approvals use the same marks everywhere.

## Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--paper` | `#FFFFFF` | `#212121` | the main surface |
| `--rail` | `#F7F7F8` | `#171717` | the sidebar |
| `--surface` | `#F7F7F8` | `#2F2F2F` | cards and panels on the page |
| `--surface-hover` | `#ECECEC` | `#393939` | hover on surfaces and rows |
| `--surface-pressed` | `#E3E3E3` | `#474747` | pressed secondary buttons (differs from hover in both themes) |
| `--ink` | `#212121` | `#ECECEC` | text |
| `--ink-soft` | `#595959` | `#B4B4B4` | secondary text (4.5:1 or better on its surface) |
| `--rule` | `#E3E3E3` | `#393939` | hairlines |
| `--rule-strong` | `#CDCDCD` | `#595959` | input borders, table rules |
| `--navy` (accent, the logo blue) | `#1667C7` | `#5EA2EE` | links, focus rings, the main button |
| `--navy-hover` / `--navy-pressed` | `#1257A8` / `#0F4A8F` | `#78B2F1` / `#93C2F4` | the main button's hover and pressed fills |
| `--on-navy` | `#FFFFFF` | `#171717` | text on a filled accent button (5.5:1 and 6.7:1) |
| `--gold` | `#B45309` | `#F59E0B` | the leadership decision and its Approve, nothing else |
| `--gold-hover` / `--gold-pressed` | `#9A4708` / `#823C07` | `#F7AD35` / `#F9BD5C` | Approve's hover and pressed fills |
| `--on-gold` | `#FFFFFF` | `#171717` | text on the Approve button (5.0:1 and 8.4:1) |
| `--gold-wash` | `#FFFBEB` | `#2A2419` | the decision card's ground |
| `--alert` | `#B91C1C` | `#F87171` | refusals, errors, destructive actions |
| `--alert-wash` | `#FEF2F2` | `#2E1F1F` | the ground of refusals and error panels |
| `--scrim` | black 35 % | black 50 % | behind the phone drawer |
| `--brand-deep` / `--brand-mid` / `--brand-teal` | `#0B2A5B` / `#1667C7` / `#1DB89A` | `#3D7FE0` / `#3B9BE8` / `#2FD0B0` | the CampusLens mark's gradient, nothing else |
| `--brand-glint` / `--brand-glint-soft` | `#5EA2EE` / `#9CC6F4` | `#9CC6F4` / `#C6DEFA` | the two highlights inside the mark's lens |

Shadows are tokens too: `--shadow` (cards that float, the composer), `--shadow-float` (menus),
`--shadow-panel` (kept for a floating panel), `--shadow-drawer` (the phone drawer). Every token is
declared once, in the token block at the top of `ui/src/index.css`; no other stylesheet declares
one.

Rules: one accent (`--navy`) for action; gold belongs only to the decision; red only to refusals,
errors and destructive actions. The `--brand-*` colours belong to the mark only. No other hues. No raw hex or rgb outside the token block.
A question CampusLens does not answer from Explore (counseling records, a single student, a
prediction) is not an error: it is a calm `--surface` card with a `--rule` border, headed "Not
something CampusLens answers", with a plain line saying why. Red stays for real errors and
the briefing's data refusals.

**Theme:** by default the theme follows the computer's own light or dark setting and changes with it. Settings offers Automatic, Light and Dark, and the sidebar's theme switch picks Light or Dark; an explicit choice is remembered per browser.

## Type

One family: Inter Variable, with the system sans as fallback. Tabular numerals wherever digits
line up (figures, tables, the audit log, counts).

Sizes are in rem. The root is 16 px, rising to 18 px at 1200 px and wider for the projector
(and to 18/20 px with Settings' large text), so every step below, every spacing step and the
sidebar scale together. Pixel values in this file are at the 16 px root.

| Step | Size | Use |
|---|---|---|
| `--text-xs` | 0.75rem (12 px) | captions, timestamps, the smallest label; nothing smaller anywhere |
| `--text-sm` | 0.875rem (14 px) | secondary text, table cells, sidebar items, buttons |
| `--text-md` | 1rem (16 px) | body text, inputs |
| `--text-lg` | 1.125rem (18 px) | briefing prose, panel intros |
| `--text-xl` | 1.375rem (22 px) | panel and section headings |
| `--text-2xl` | 1.75rem (28 px) | the page question, figures in cards |
| `--text-3xl` | 2.25rem (36 px) | the landing headline, the large figure |

Weights 400, 500 and 600 only. Line height 1.5 for prose, 1.25 for headings. Briefing prose
measure 68ch.

## Space and shape

- Spacing steps: `--space-1` to `--space-7` = 0.25, 0.5, 0.75, 1, 1.5, 2, 3 rem (4, 8, 12, 16,
  24, 32, 48 px at the 16 px root). Nothing in between.
- `--tap` = 2.75rem (44 px): the height of every field, and the smallest control on a phone.
- Radii: `--radius-control` 8 px (buttons, inputs, selects, chips that are not pills),
  `--radius-surface` 12 px (cards, panels, the composer), 999 px for pills and avatars only.
- Shadows: `--shadow` on floating things only (menus, the composer). Cards on
  the page use a hairline, not a shadow.

## Components

- **Buttons:** exactly four looks, each a shared class (older class names are aliases of the
  same look and keep working):
  - `.btn-primary`: the main button, one per screen or panel. Filled `--navy`, `--on-navy` text.
  - `.btn-secondary`: outlined, `--rule-strong` border, `--ink` text; hover `--surface-hover`,
    pressed `--surface-pressed`.
  - `.btn-danger`: outlined in `--alert`, `--alert` text. Always confirmed inline.
  - `.btn-approve`: filled `--gold`, `--on-gold` text. The leadership decision only.

  The text on a filled button is white in light and dark ink (`#171717`) in dark: dark text on
  the light gold is 3.2:1 and white on the dark blue is 2.7:1, both under the 4.5:1 this file
  requires, so these are the only foregrounds that pass on both fills. All four are at least
  44 px tall, `--radius-control`, `--text-sm` weight 600. Every button has hover, pressed,
  disabled and focus looks; a working button sets `aria-busy="true"` and shows
  `<span class="spinner">` beside its working label ("Saving…").
- **Inputs:** `.field`, 44 px tall, `--text-md`, `--rule-strong` border, `--radius-control`,
  label above, help text below, error text below in `--alert` linked with `aria-describedby`
  and `aria-invalid="true"` (red border). Placeholders start with "e.g.", except the question box, whose placeholder says what can be
  asked ("Ask about students, courses or majors") and fits a 390 px phone.
- **Pages:** a page (`SidePanel`) is the one pattern for everything outside the conversation.
  Back and the title on one line (`--text-2xl`, `--text-xl` on phones), a one-sentence intro in
  `--ink-soft` (`.panel-intro`) saying what the page is for, then content, and at most one
  `.btn-primary`. Reading pages (the briefing, evidence, the decision, profile, settings) keep a
  52rem column; pages of cards or rows (Staff actions, Key figures, Audit log, Financial Aid
  review) use the wider 76rem column (`.side-panel.is-wide`) so a 1440 px screen is used, not a
  narrow strip. Institution settings has the same title size and intro. Escape and Back close a
  page; focus returns. It rises in over 200 ms: closing adds `.is-closing` and unmounts it 200 ms
  later; switching pages cross-fades the body. No motion under reduced motion.
- **Worklist (Staff actions):** one notice when any office has no mailbox (never a paragraph
  per card; each such card only says "Can't send yet"), a segmented strip of three status
  toggles (To do, In progress, Done; `aria-pressed`, the pressed one a filled accent segment,
  wrapping on a narrow screen), then the Office and Status filters with a "Showing N of M"
  line, then one card per action (`.action-card`, `--surface` once done): the office and a
  status pill, the title, the count (`--text-md`) as a finding link to its evidence, what to
  do, then the editor (Status, Due date and Owner on one row where the card is 32rem wide,
  Status and Due date over Owner below that, one field per row under 24rem: container
  queries, so larger text never clips the date; "Save changes" appears only when something
  changed, so a page of cards never shows a row of main buttons), the message to the office
  (Send, Sent with who and when, or a failure: the button then says "Retry sending" with the
  reason directly under it; never the raw error), and Notes and History folds, closed. Two
  columns only where each card is at least 36rem wide. Roles that cannot edit see owner and
  due date as a `.kv` list instead of the form.
- **Steps:** the Decision page shows where a decision stands as a line of steps
  (`.decision-steps`, short labels so four fit one desktop line): done steps carry a filled
  accent check, the next step is outlined in the accent, later ones are quiet, and a step that
  waits on someone else ("Waiting: Financial Aid needs a mailbox") has a dashed mark. The
  decision card keeps its gold; the "Approved by …" line on it is ink with an accent check
  (DecisionPanel.css), so an approval never reads as a warning.
- **Filters:** a filter is a labelled `.field` select or date above its control, on one row that
  wraps; a "Clear filters" secondary button appears only while a filter is set, and a count
  line ("Showing 3 of 98 entries.") says what the filters hide.
- **Figures:** a figure is a link to its evidence. Five figure cards (M1, M2, M3, M4, M8); M9,
  when authorized, appears only in the briefing text and the evidence list.
- **States:** every list and panel has a loading line (`.skeleton-line`), an empty state that
  says what to do next (`.state-empty`), and an error state that says what went wrong and offers
  Retry (`.state-error`, the Retry button inside it). A failed load never looks like "nothing
  here".
- **Shared layouts:** `.kv` is a `dl` of label/value pairs on a `7rem minmax(0,1fr)` grid; long
  values wrap, and under 400 px each label sits above its value. `.stack-table` is a full-width
  table that becomes one card per row under 640 px, each cell reading "label: value" from its
  `data-label` (an empty `data-label` shows no label, for action cells). `.fold` is a
  `details`/`summary` for long content behind "Show …", with a 44 px summary that draws its
  own chevron.
- **Governance marks:** granted (check in a circle), refused (no-entry circle, `--alert`),
  approved (check in a square, `--gold`), sent (arrow out of a tray). Always with a text label.

## Phones

Below 900 px the sidebar becomes a menu button (44 × 44) and a drawer. Every tap target is at
least 44 × 44 px: buttons, fields, rows, chips, segments, summaries; a link inside a sentence
keeps its size and gets an invisible 44 × 44 hit area. No sideways scrolling at 360 px, including with text enlarged to 150 %.
Tables become stacked cards (`.stack-table`), except Explore's computed tables, which scroll
sideways inside their own box with the first column (the row's name) kept in view, so an
opened figure is never shown without its row.

## Words

Plain words for non-technical readers. Students are people who may need support, never scores.
Never on screen: internal ids that mean nothing to a reader, file formats, "API", "JSON",
"payload", finding ids as bare codes, event type codes, raw field names outside a folded
"Technical detail", model or vendor names, raw error text. Source labels stay honest without
jargon: "Written by the Chief of Staff", with whether it was a replay or a live run in a small
"About this answer" detail.
