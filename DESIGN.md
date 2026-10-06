# Design record

The visual authority for the Cabinet's interface. Every colour, size, spacing step and corner
radius in `ui/src` follows this file. Change this file first when the look changes.

## Concept

A calm, chat-style workspace. The president asks one of the approved questions in the centre
of the screen, and everything else (the full briefing, the figures, the evidence, the staff
actions, the decision, the AI employees, data access, the audit log, the Financial Aid review)
is one click away in the sidebar and opens as a slide-over panel. The page never shows more
than one thing asking to be done. The governance is visible, not decorative: refusals, sources
and approvals use the same marks everywhere.

## Colour

| Token | Light | Dark | Use |
|---|---|---|---|
| `--paper` | `#FFFFFF` | `#212121` | the main surface |
| `--rail` | `#F7F7F8` | `#171717` | the sidebar |
| `--surface` | `#F7F7F8` | `#2F2F2F` | cards and panels on the page |
| `--surface-hover` | `#ECECEC` | `#393939` | hover on surfaces and rows |
| `--ink` | `#212121` | `#ECECEC` | text |
| `--ink-soft` | `#595959` | `#B4B4B4` | secondary text (4.5:1 or better on its surface) |
| `--rule` | `#E3E3E3` | `#393939` | hairlines |
| `--rule-strong` | `#CDCDCD` | `#595959` | input borders, table rules |
| `--navy` (accent) | `#126E6B` | `#41A79D` | links, focus rings, the main button |
| `--gold` | `#B45309` | `#F59E0B` | the leadership decision and its Approve, nothing else |
| `--gold-wash` | `#FFFBEB` | `#2A2419` | the decision card's ground |
| `--alert` | `#B91C1C` | `#F87171` | refusals, errors, destructive actions |

Rules: one accent (`--navy`) for action; gold belongs only to the decision; red only to refusals,
errors and destructive actions. No other hues. No raw hex in components: tokens only.

**Theme:** light is the default for everyone (the demo is projected in a lit room). Dark is
available from the theme toggle and remembered per browser. The operating system's setting does
not switch the theme on its own.

## Type

One family: Inter Variable, with the system sans as fallback. Tabular numerals wherever digits
line up (figures, tables, the audit log, counts).

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

- Spacing steps: 4, 8, 12, 16, 24, 32, 48 px (`--space-1` to `--space-7`). Nothing in between.
- Radii: `--radius-control` 8 px (buttons, inputs, selects, chips that are not pills),
  `--radius-surface` 12 px (cards, panels, the composer), 999 px for pills and avatars only.
- Shadows: `--shadow` on floating things only (slide-over panels, menus, the composer). Cards on
  the page use a hairline, not a shadow.

## Components

- **Buttons:** one main button per screen (filled `--navy`, white text). Secondary buttons are
  outlined (`--rule-strong` border, `--ink` text). Destructive buttons are outlined in
  `--alert` and always confirm inline. The Approve button is filled `--gold` with dark text.
  Every button has hover, pressed, disabled and focus looks; a working button shows a spinner and
  its working label ("Saving…").
- **Inputs:** 44 px tall, `--radius-control`, label above, help text below, error text below in
  `--alert` linked with `aria-describedby`. Placeholders start with "e.g.".
- **Panels:** the slide-over panel is the one pattern for everything outside the conversation.
  Title, one-sentence intro, then content. Escape and the close button close it; focus returns.
  Open and close with a 200 ms slide; none under reduced motion.
- **Figures:** a figure is a link to its evidence. Five figure cards (M1, M2, M3, M4, M8); M9,
  when authorized, appears only in the briefing text and the evidence list.
- **States:** every list and panel has a loading line, an empty state that says what to do next,
  and an error state that says what went wrong and offers Retry. A failed load never looks like
  "nothing here".
- **Governance marks:** granted (check in a circle), refused (no-entry circle, `--alert`),
  approved (check in a square, `--gold`), sent (arrow out of a tray). Always with a text label.

## Phones

Below 768 px the sidebar becomes a menu button (44 × 44) and a drawer. Every tap target is at
least 44 × 44 px. No sideways scrolling at 360 px, including with text enlarged to 150 %.
Tables become stacked rows.

## Words

Plain words for non-technical readers. Students are people who may need support, never scores.
Never on screen: internal ids that mean nothing to a reader, file formats, "API", "JSON",
"payload", model or vendor names, raw error text. Source labels stay honest: "(recorded live
run)", "(live model)", "Test stub, not a live model".
