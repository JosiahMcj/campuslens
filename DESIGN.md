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
| `--rule-strong` | `#CDCDCD` | `#595959` | secondary button outlines, pills, table rules |
| `--field-border` | `#8F8F8F` | `#7A7A7A` | the border of every input, select and text area (3.3:1 on paper light, 3.8:1 dark; at least 3:1 on every surface) |
| `--navy` (accent, the logo blue) | `#1667C7` | `#5EA2EE` | links, focus rings, the main button |
| `--navy-hover` / `--navy-pressed` | `#1257A8` / `#0F4A8F` | `#78B2F1` / `#93C2F4` | the main button's hover and pressed fills |
| `--on-navy` | `#FFFFFF` | `#171717` | text on a filled accent button (5.5:1 and 6.7:1) |
| `--gold` | `#B45309` | `#F59E0B` | the leadership decision and its Approve, nothing else |
| `--gold-hover` / `--gold-pressed` | `#9A4708` / `#823C07` | `#F7AD35` / `#F9BD5C` | Approve's hover and pressed fills |
| `--on-gold` | `#FFFFFF` | `#171717` | text on the Approve button (5.0:1 and 8.4:1) |
| `--gold-wash` | `#FFFBEB` | `#2A2419` | the decision card's ground |
| `--alert` | `#B91C1C` | `#F87171` | refusals, errors, destructive actions |
| `--alert-wash` | `#FEF2F2` | `#2E1F1F` | the ground of refusals and error panels |
| `--navy-wash` | `#E8F0FB` | `#1B2738` | a quiet accent ground: the Done status pill, a table cell opened from a figure; never a button |
| `--scrim` | black 35 % | black 50 % | behind the phone drawer |
| `--brand-deep` / `--brand-mid` / `--brand-teal` | `#0B2A5B` / `#1667C7` / `#1DB89A` | `#3D7FE0` / `#3B9BE8` / `#2FD0B0` | the CampusLens mark's gradient and the sign-in screen's backdrop, nothing else |
| `--brand-glint` / `--brand-glint-soft` | `#5EA2EE` / `#9CC6F4` | `#9CC6F4` / `#C6DEFA` | the two highlights inside the mark's lens |
| `--series-1` … `--series-7` | `#2A78D6` `#EB6834` `#1BAF7A` `#EDA100` `#E87BA4` `#008300` `#4A3AA7` | `#3987E5` `#D95926` `#199E70` `#C98500` `#D55181` `#008300` `#9085E9` | the Data page's chart groups only, in this fixed order (a group keeps its slot whatever else is shown); validated for colour-blind separation on `--surface` in both themes. The "All students" reference is a dashed `--ink` line, never a series colour |

Shadows are tokens too: `--shadow` (cards that float, the composer), `--shadow-float` (menus),
`--shadow-panel` (kept for a floating panel), `--shadow-drawer` (the phone drawer). Every token is
declared once, in the token block at the top of `ui/src/index.css`; no other stylesheet declares
one.

Rules: one accent (`--navy`) for action; gold belongs only to the decision; red only to refusals,
errors and destructive actions. The `--brand-*` colours belong to the mark and the sign-in screen's backdrop only. The `--series-*` colours belong to the Data page's charts only. No other hues. No raw hex or rgb outside the token block.
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
  - `.btn-danger`: outlined in `--alert`, `--alert` text. Only on the inline confirmation's
    own button: the row's Disable or Delete that opens the confirmation is `.btn-secondary`,
    and signing out is not destructive (`.btn-secondary`).
  - `.btn-approve`: filled `--gold`, `--on-gold` text. The leadership decision only.

  The text on a filled button is white in light and dark ink (`#171717`) in dark: dark text on
  the light gold is 3.2:1 and white on the dark blue is 2.7:1, both under the 4.5:1 this file
  requires, so these are the only foregrounds that pass on both fills. All four are at least
  44 px tall, `--radius-control`, `--text-sm` weight 600. Every button has hover, pressed,
  disabled and focus looks; a working button sets `aria-busy="true"` and shows
  `<span class="spinner">` beside its working label ("Saving…").
- **Sign-in screen:** one centred column: the name, the one-line description, then the sign-in
  card. Behind it, a backdrop of three soft blurred shapes in `--brand-deep`, `--brand-mid` and
  `--brand-teal` at low opacity, the only place those colours appear outside the mark. The card
  is frosted (a translucent `--paper` or `--surface` with a backdrop blur, a hairline and
  `--shadow`) so the backdrop shows through; where the browser cannot blur, it is solid
  `--surface`. Its fields carry a mail and a lock icon inside the left edge, which turn `--navy`
  on focus. Each part settles in once, in order (500 ms, 80 ms apart), and the backdrop drifts
  slowly; no motion under reduced motion.
- **Inputs:** `.field`, 44 px tall, `--text-md`, `--field-border` border, `--radius-control`,
  label above, help text below, error text below in `--alert` linked with `aria-describedby`
  and `aria-invalid="true"` (red border). Placeholders start with "e.g.", except the question box, whose placeholder says what can be
  asked ("Ask about students, courses or majors") and fits a 390 px phone.
- **Pages:** a page (`SidePanel`) is the one pattern for everything outside the conversation.
  Back and the title on one line (`--text-2xl`, `--text-xl` on phones), a one-sentence intro in
  `--ink-soft` (`.panel-intro`) saying what the page is for, then content, and at most one
  `.btn-primary`. Reading pages (the briefing, evidence, the decision, profile, settings) keep a
  52rem column; pages of cards or rows (Staff actions, Key figures, Audit log, Financial Aid
  review) use the wider 76rem column (`.side-panel.is-wide`) so a 1440 px screen is used, not a
  narrow strip. Institution settings has the same title size and intro, and shows one section
  at a time (Users, Offices, Counseling, Data, Connections), chosen from its section links; the
  open link is marked (`aria-current`) and the address keeps it (`/institution#inst-offices`). Escape and Back close a
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
  (`.decision-steps`, short labels; four equal columns at 1200 px and wider, never three and one): done steps carry a filled
  accent check, the next step is outlined in the accent, later ones are quiet, and a step that
  waits on someone else ("Waiting: Financial Aid needs a mailbox") has a dashed mark. The
  decision card keeps its gold; the "Approved by …" line on it is ink with an accent check
  (DecisionPanel.css), so an approval never reads as a warning.
- **Filters:** a filter is a labelled `.field` select or date above its control, on one row that
  wraps, every field the same width (at least 12rem); the page's own buttons (Test a refusal,
  Refresh) sit together at the right of the count line; a "Clear filters" secondary button appears only while a filter is set, and a count
  line ("Showing 3 of 98 entries.") says what the filters hide.
- **Figures:** a figure is a link to its evidence. Five figure cards (M1, M2, M3, M4, M8); M9,
  when authorized, appears only in the briefing text and the evidence list. On a narrow or
  enlarged screen the cards fit as many whole ones per row as there is room for, and a label
  never breaks inside a word.
- **Numbers:** one style everywhere (`ui/src/displayFormat.ts`): thousands separators (1,872),
  a percent sign with no space (−4.8%), dates as "Nov 20, 2025". The server's figures and
  written explanations are not rewritten (they are validated and replayed byte for byte); the
  style is applied when they are drawn.
- **Connections:** each outside connection is a card with its state as a pill (the granted or
  refused mark and the words, e.g. "Not set up"); the settings behind it are folded under
  "Settings on the server".
- **States:** every list and panel has a loading line (`.skeleton-line`), an empty state that
  says what to do next (`.state-empty`), and an error state that says what went wrong and offers
  Retry (`.state-error`, the Retry button inside it). A failed load never looks like "nothing
  here".
- **Shared layouts:** `.kv` is a `dl` of label/value pairs on a `7rem minmax(0,1fr)` grid; long
  values wrap, and under 400 px each label sits above its value. `.stack-table` is a full-width
  table that becomes one card per row under 640 px, each cell reading "label: value" from its
  `data-label` (an empty `data-label` shows no label, for action cells). `.fold` is a
  `details`/`summary` for long content behind "Show …", with a 44 px summary in `--text-sm`
  (every fold the same size) that draws its own chevron.
- **Governance marks:** granted (check in a circle), refused (no-entry circle, `--alert`),
  approved (check in a square, `--gold`), sent (arrow out of a tray). Always with a text label.

- **First result screen:** an answer opens with `FirstResult` (`ui/src/components/FirstResult.tsx`),
  above the long summary: the registration finding in one sentence (the number is a
  `FindingLink`), the comparison period in `--ink-soft`, the four student-support counts, and the next step
  with who decides, its status, the responsible office and any proposed deadline, on `--surface` with a
  `--rule` border (not gold: gold stays on the decision itself). A `.data-tag` repeats
  "Fictional data" beside the heading when the dataset is fictional. It closes with two
  `.btn-lg` actions meant to be read from across a room: "View evidence" (`.btn-secondary`,
  with a 2 px `--navy` border and `--navy` text at this size) and "Review next steps"
  (`.btn-primary`). `.btn-lg` changes size only: `--text-lg`, 56 px tall. No number or date
  here is computed in the UI.
- **Delivery wording:** "Sent" is only said when email delivery is configured. Otherwise the
  message is "Recorded, not emailed" and the screen says it was saved on this server.

- **Data page:** `DataPage.tsx` with `DataChart.tsx` (hand-written SVG). The dashboards a role
  may open (Students, Student finances, Campus life and academics) as segmented toggles, then the
  filter bar: From and To academic years, Students (one group, chosen from one select grouped
  by attribute) and Compare by on one row, a line saying a chart shows one group or compares
  groups, never both (choosing one clears the other), and the chosen group as a removable chip
  with "Show all students". Cards (`--surface`, `--rule` hairline) fit as many 26rem columns as there is room for,
  one on a phone. Each card: title, the measure and its x axis in `--ink-soft`, the latest value
  for a single series, the chart, a legend whose entries are buttons that narrow every chart to
  that group, notes, and a "Show the figures" fold with the table. Lines are 2 px with dots,
  bars, counts, money and rates start at zero; an average (GPA) fits its data with a minimum span and a break mark on the foot of its value axis. A
  withheld point is never drawn: the line breaks and a dashed `--ink-soft` guide marks the place;
  its tooltip and table cell say "Withheld: fewer than 10 students, or it could reveal a group that
  small". The chart takes focus; the arrow keys, Home and End move through the terms and the
  tooltip names the measure, the group and the term. Choices are remembered per account in this
  browser.
- **Find a student:** the one page that shows a person's name (`StudentLookup.tsx`). A `.data-tag`
  and one sentence say the directory is fictional and that searches are logged. One labelled
  search field with a `.btn-primary` Search; it searches on Search, never per keystroke.
  Each result is a card on `--surface` with the name, the id in `--font-mono`, and a grid of
  label/value facts. Degree progress is a native `<progress>` with its percentage in text;
  GPA change is text with a sign, never colour alone.

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
