// @vitest-environment jsdom

// The application shell against a mocked API: friendly error screens, the
// 429 retry, a failed ask that leaves no orphan row, load errors that never
// look like "nothing here", unknown routes, and page titles.

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { resetBriefingOnce } from './api'
import { clearSession } from './auth'
import { BUSY_MESSAGE, NETWORK_MESSAGE } from './errors'

const SESSION = {
  user: {
    id: 2,
    email: 'president@demo.test',
    role: 'executive',
    institution_id: 1,
    institution: { slug: 'bootstrap', name: 'Demonstration University' },
  },
  csrf_token: 'token',
}

const FINDINGS = {
  meta: {
    as_of: null,
    fixture: 'demo',
    terms: {},
    fictional: true,
    dataset: { id: 1, name: 'Demonstration (fictional)', sha256: 'x' },
  },
}

const QUESTION = 'What should I know about spring registration?'

const OWNER =
  'Which major has the lowest GPA? In that major, what is historically the hardest class, and which instructor has historically taught it?'
const EXAMPLES = [
  OWNER,
  'Which majors have the highest average GPA?',
  'What is the average GPA by college?',
  'Which term had the largest gap between online and in-person withdrawal rates?',
]

/** GET /briefing when a briefing was already produced (minimal shape). */
const BRIEFING = {
  question_id: 'spring-registration',
  question: QUESTION,
  question_event_id: 1,
  sections: {},
}

/** A short real answer (POST /explore, recorded): one step, one sentence. */
const EXPLORE_ANSWER = {
  refused: false,
  answer: [
    {
      text: 'Mechanical Engineering has the lowest average cumulative GPA, 2.62 across 250 students.',
      claims: [
        { table: 0, row: 0, column: 'avg_gpa' },
        { table: 0, row: 0, column: 'students' },
      ],
    },
  ],
  steps: [
    {
      analysis_id: 'gpa_by_major',
      title: 'Average GPA by major',
      params_plain: ['Ranked: lowest first', 'Only majors with at least 20 students'],
      fields_read: ['student_term_records.cumulative_gpa'],
      aggregate_only: true,
      table: {
        columns: [
          { key: 'major', label: 'Major code' },
          { key: 'major_name', label: 'Major' },
          { key: 'students', label: 'Students' },
          { key: 'avg_gpa', label: 'Average GPA' },
        ],
        rows: [['MEEN', 'Mechanical Engineering', 250, 2.623]],
      },
      notes: [],
    },
  ],
  source: 'Calculated directly from the records',
  planner: 'rule',
  notes: [],
  fallbacks: [],
}

const REFUSAL = {
  refused: true,
  message:
    'Individual counseling and spiritual-care records are never disclosed.',
  answer: [],
  steps: [],
  source: null,
}

function sessionAs(role: string) {
  return { ...SESSION, user: { ...SESSION.user, id: 9, email: `${role}@demo.test`, role } }
}

/** Type a question into the composer and send it. */
async function askQuestion(text: string) {
  const input = (await screen.findByLabelText('Ask CampusLens a question')) as HTMLInputElement
  fireEvent.change(input, { target: { value: text } })
  fireEvent.submit(input.closest('form') as HTMLFormElement)
}

type Handler = (url: string, init?: RequestInit) => Promise<Response> | Response

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

/** GET /staff: the Chief of Staff and one department employee. */
const STAFF = {
  yours: ['chief_of_staff'],
  employees: [
    {
      role: 'chief_of_staff',
      title: 'Chief of Staff',
      job: "Hands each question to the right department's analyst, then writes the summary and its limits from their checked totals.",
      office: "President's Office",
      may_read: ['Majors, colleges, class levels and terms'],
      never_reads: ['Student names', 'Counseling and chaplain notes'],
      outside_scope: [],
      findings: ['M1'],
      no_data: false,
      yours: true,
      requests_today: 1,
    },
    {
      role: 'registrar_analyst',
      title: 'Registrar Analyst',
      job: 'Reports registration, academic standing, credit hours and course sections.',
      office: 'Office of the Registrar',
      may_read: ['Academic standing (probation and suspension)'],
      never_reads: ['Student names'],
      outside_scope: ['Pell status'],
      findings: ['M1'],
      no_data: false,
      yours: false,
      requests_today: 0,
    },
  ],
}

/** The API: every route answers like a healthy server unless overridden. */
function mockApi(overrides: Record<string, Handler> = {}) {
  const calls: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push(url)
    const path = url.replace(/^\/api/, '').split('?')[0]
    if (overrides[path] !== undefined) return overrides[path](url, init)
    switch (path) {
      case '/auth/me':
        return json(SESSION)
      case '/findings':
        return json(FINDINGS)
      case '/questions':
        return json([{ id: 'spring-registration', text: QUESTION }])
      case '/decisions':
        return json({ question_id: null, decisions: [] })
      case '/events':
        return json({ events: [] })
      case '/briefing':
        return json({ detail: 'none' }, 404)
      case '/explore/catalog':
        return json({ institution: 'Demonstration University (fictional)', examples: EXAMPLES })
      case '/explore':
        return json(EXPLORE_ANSWER)
      case '/staff':
        return json(STAFF)
      default:
        return json({ detail: 'not found' }, 404)
    }
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

beforeEach(() => {
  clearSession()
  window.sessionStorage.clear()
  resetBriefingOnce()
  window.history.replaceState(null, '', '/')
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('the session check', () => {
  it('shows a plain sentence and Try again when CampusLens cannot be reached', async () => {
    mockApi({
      '/auth/me': () => {
        throw new TypeError('Failed to fetch')
      },
    })
    render(<App />)
    expect(await screen.findByText(NETWORK_MESSAGE)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/make api|8910|Failed to fetch|HTTP/)
  })

  it('says CampusLens is busy on 429 and checks again after Retry-After', async () => {
    let answers = 0
    const calls = mockApi({
      '/auth/me': () => {
        answers += 1
        return answers === 1
          ? json({ detail: 'rate limit exceeded' }, 429, { 'Retry-After': '1' })
          : json(SESSION)
      },
    })
    render(<App />)
    expect(await screen.findByText(BUSY_MESSAGE)).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/429|HTTP/)
    await waitFor(() => expect(calls.filter((c) => c.endsWith('/auth/me')).length).toBe(2), {
      timeout: 2500,
    })
    expect(await screen.findByText('What would you like to know?')).toBeTruthy()
  })
})

describe('the conversation', () => {
  it('drops a failed ask, says why in plain words, and offers Ask again', async () => {
    let asks = 0
    mockApi({
      '/ask': () => {
        asks += 1
        throw new TypeError('Failed to fetch')
      },
    })
    render(<App />)
    const input = (await screen.findByLabelText(
      'Ask CampusLens a question',
    )) as HTMLInputElement
    fireEvent.change(input, { target: { value: QUESTION } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)
    expect(await screen.findByText(NETWORK_MESSAGE)).toBeTruthy()
    // No orphan exchange is left in the thread.
    expect(document.querySelectorAll('.exchange').length).toBe(0)
    expect(document.body.textContent).not.toContain('Failed to fetch')
    fireEvent.click(screen.getByRole('button', { name: 'Ask again' }))
    await waitFor(() => expect(asks).toBe(2))
  })

  it('has no "Or open" chip row and no "Test a refusal" in the navigation', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('What would you like to know?')
    expect(document.body.textContent).not.toContain('Or open')
    expect(document.body.textContent).not.toContain('Test a refusal')
    // One fictional-data mark, in the top bar.
    expect(screen.getAllByText('Fictional data').length).toBe(1)
  })

  it('shows a failed audit log load as an error with Retry, never Loading forever', async () => {
    let fail = true
    mockApi({
      '/events': () => (fail ? json({ detail: 'boom' }, 500) : json({ events: [] })),
    })
    render(<App />)
    await screen.findByText('What would you like to know?')
    fireEvent.click(screen.getByRole('button', { name: 'Audit log' }))
    expect(await screen.findByText(/Couldn't load the audit log/)).toBeTruthy()
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByText(/Couldn't load the audit log/)).toBeNull())
  })

  it('shows a failed decision load as Retry instead of the decision card', async () => {
    mockApi({
      '/decisions': () => json({ detail: 'boom' }, 503),
      '/briefing': () => json(BRIEFING),
    })
    render(<App />)
    await screen.findByText('Student success briefing')
    fireEvent.click(screen.getByRole('button', { name: 'Decision' }))
    const dialog = await screen.findByRole('region', { name: 'Decision' })
    await waitFor(() => expect(dialog.textContent).toContain("Couldn't load the decision"))
    expect(within(dialog).getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})

describe('wiring of the panels', () => {
  it('shows a quiet Retry line when the approved questions fail to load', async () => {
    let fail = true
    mockApi({
      '/questions': () =>
        fail ? json({ detail: 'boom' }, 500) : json([{ id: 'spring-registration', text: QUESTION }]),
    })
    render(<App />)
    expect(await screen.findByText(/The approved questions didn’t load/)).toBeTruthy()
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(QUESTION)).toBeTruthy()
    expect(screen.queryByText(/The approved questions didn’t load/)).toBeNull()
  })

  it('lists the decision in the Full briefing and opens the Decision panel from it', async () => {
    mockApi({
      '/decisions': () =>
        json({
          question_id: 'spring-registration',
          decisions: [
            {
              id: 'D-spring-registration-1',
              title: 'Authorize the eligibility review',
              text: 'Authorize a focused review.',
              follow_up: { office: 'Financial Aid', description: 'Report back.' },
              approved: true,
              approved_by: 'president@demo.test',
              approved_at: '2026-10-05T12:00:00+00:00',
            },
          ],
        }),
      '/decisions/D-spring-registration-1/dispatch': () =>
        json({
          decision_id: 'D-spring-registration-1',
          task_id: 'TASK-D-spring-registration-1',
          office: 'Financial Aid',
          office_contact: null,
          approved: true,
          approved_by: 'president@demo.test',
          approved_at: '2026-10-05T12:00:00+00:00',
          dispatch: null,
        }),
      '/briefing': () => json(BRIEFING),
    })
    render(<App />)
    await screen.findByText('Student success briefing')
    fireEvent.click(screen.getByRole('button', { name: 'Full briefing' }))
    const open = await screen.findByRole('button', { name: 'Open the decision' })
    expect(screen.getByText('Authorize the eligibility review')).toBeTruthy()
    fireEvent.click(open)
    await screen.findByRole('region', { name: 'Decision' })
    expect(await screen.findByText(/Approved by you at/)).toBeTruthy()
  })
})

describe('the Full briefing when the decision fails to load', () => {
  it('says so with Retry instead of Loading forever', async () => {
    mockApi({
      '/decisions': () => json({ detail: 'boom' }, 503),
      '/briefing': () => json(BRIEFING),
    })
    render(<App />)
    await screen.findByText('Student success briefing')
    fireEvent.click(screen.getByRole('button', { name: 'Full briefing' }))
    const dialog = await screen.findByRole('region', { name: 'Full briefing' })
    await waitFor(() => expect(dialog.textContent).toContain("Couldn't load the decision"))
    expect(dialog.textContent).not.toContain('Loading the decision')
    expect(within(dialog).getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})

describe('routes and titles', () => {
  it('sends an unknown address to the conversation and names the screen', async () => {
    window.history.replaceState(null, '', '/nowhere')
    mockApi()
    render(<App />)
    await screen.findByText('What would you like to know?')
    expect(window.location.pathname).toBe('/')
    expect(document.title).toBe('Ask · CampusLens')
  })

  it('titles the sign-in screen and keeps the address at /login', async () => {
    mockApi({ '/auth/me': () => json({ detail: 'signed out' }, 401) })
    render(<App />)
    await waitFor(() => expect(document.title).toBe('Sign in · CampusLens'))
    expect(window.location.pathname).toBe('/login')
  })

  it('names the open panel in the title and closes it on Escape', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('What would you like to know?')
    fireEvent.click(screen.getByRole('button', { name: 'Staff actions' }))
    await waitFor(() => expect(document.title).toBe('Staff actions · CampusLens'))
    const dialog = screen.getByRole('region', { name: 'Staff actions' })
    act(() => {
      fireEvent.keyDown(dialog, { key: 'Escape' })
    })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Staff actions' })).toBeNull())
    expect(document.title).toBe('Ask · CampusLens')
  })
})

describe('Explore', () => {
  it('shows the home screen with the approved cards and a Try row from the catalog', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('What would you like to know?')
    expect(
      screen.getByText(
        "Ask an approved briefing question, or ask anything about Demonstration University's students, courses and majors. Every number is computed from the records.",
      ),
    ).toBeTruthy()
    expect(await screen.findByText(QUESTION)).toBeTruthy()
    expect(await screen.findByText(OWNER)).toBeTruthy()
    expect(screen.getByText('Try asking')).toBeTruthy()
    expect(screen.getByText('Every number is computed from the records and checked.')).toBeTruthy()
  })

  it('answers a free question from Explore, links its numbers, and lists it as Explore', async () => {
    const calls = mockApi()
    render(<App />)
    await screen.findByText('What would you like to know?')
    await askQuestion('Which major has the lowest GPA?')
    expect(await screen.findByRole('button', { name: '2.62' })).toBeTruthy()
    expect(calls.some((c) => c.endsWith('/api/explore'))).toBe(true)
    expect(calls.some((c) => c.endsWith('/api/ask'))).toBe(false)
    expect(screen.getByText('How this was answered')).toBeTruthy()
    expect(screen.getByText('Calculated directly from the records')).toBeTruthy()
    // The sidebar lists the question, with no "Explore" tag on it.
    const row = screen.getByTitle('Which major has the lowest GPA?')
    expect(within(row).queryByText('Explore')).toBeNull()
  })

  it('shows each AI employee’s progress inline while a briefing runs', async () => {
    let asked = false
    const ts = '2026-10-05T12:00:00+00:00'
    const event = (id: number, type: string, payload: Record<string, unknown>) => ({
      id,
      ts,
      type,
      actor: 'chief_of_staff',
      payload,
    })
    mockApi({
      // The run stays in flight: the reply shows progress from the polled log.
      '/ask': () => {
        asked = true
        return new Promise<Response>(() => {})
      },
      '/events': () =>
        json({
          events: asked
            ? [
                event(5, 'question.asked', { question: QUESTION }),
                event(6, 'task.assigned', { task_id: 'task-enrollment_analyst-5', role: 'enrollment_analyst', fields: [] }),
                event(7, 'task.assigned', { task_id: 'task-student_success_analyst-5', role: 'student_success_analyst', fields: [] }),
                event(8, 'finding.produced', { task_id: 'task-enrollment_analyst-5' }),
              ]
            : [],
        }),
    })
    render(<App />)
    await screen.findByText('What would you like to know?')
    await askQuestion(QUESTION)
    expect(await screen.findByText('Enrollment Analyst: done', {}, { timeout: 6000 })).toBeTruthy()
    expect(screen.getByText('Student Success Analyst: working…')).toBeTruthy()
  }, 10_000)

  it('runs an approved question as a briefing, never through Explore', async () => {
    const calls = mockApi()
    render(<App />)
    await screen.findByText('What would you like to know?')
    await askQuestion(QUESTION.toLowerCase())
    await waitFor(() => expect(calls.some((c) => c.endsWith('/api/ask'))).toBe(true))
    expect(calls.some((c) => c.endsWith('/api/explore'))).toBe(false)
  })

  it('reads a refusal as a calm note, with three questions to try instead', async () => {
    mockApi({ '/explore': () => json(REFUSAL) })
    render(<App />)
    await screen.findByText('What would you like to know?')
    await askQuestion('Which students are in counseling?')
    const heading = await screen.findByText('Not something CampusLens answers')
    const card = heading.closest('.explore-declined') as HTMLElement
    expect(card.getAttribute('role')).toBe('status')
    expect(card.textContent).toContain(
      'Individual counseling and spiritual-care records are never disclosed.',
    )
    expect(screen.getByText('You can ask one of these instead')).toBeTruthy()
    expect(screen.getByText(OWNER)).toBeTruthy()
    const row = screen.getByTitle('Which students are in counseling?')
    expect(within(row).getByText('Refused')).toBeTruthy()
    // A refusal is not kept for after a reload (reopening it would ask again).
    expect(window.sessionStorage.getItem('cabinet.explore.questions.2') ?? '[]').toBe('[]')
  })

  it('keeps a failed Explore question in the thread as interrupted, with Ask again', async () => {
    let fail = true
    mockApi({
      '/explore': () => {
        if (fail) throw new TypeError('Failed to fetch')
        return json(EXPLORE_ANSWER)
      },
    })
    render(<App />)
    await screen.findByText('What would you like to know?')
    await askQuestion('Which major has the lowest GPA?')
    const interrupted = await screen.findByText(/This answer was interrupted\./)
    expect(interrupted.textContent).toContain(NETWORK_MESSAGE)
    // The question stays where it was asked.
    expect(document.querySelectorAll('.exchange').length).toBe(1)
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Ask again' }))
    expect(await screen.findByRole('button', { name: '2.62' })).toBeTruthy()
    // The answer takes the interrupted one's place.
    expect(document.querySelectorAll('.exchange').length).toBe(1)
    expect(screen.queryByText(/This answer was interrupted/)).toBeNull()
  })

  it('lists questions from before a reload and asks again when one is opened', async () => {
    window.sessionStorage.setItem(
      'cabinet.explore.questions.2',
      JSON.stringify(['Which offices hold the most active holds?']),
    )
    const calls = mockApi()
    render(<App />)
    await screen.findByText('What would you like to know?')
    const row = screen.getByTitle('Which offices hold the most active holds?')
    // The answers were not kept; the empty screen says so in one line.
    expect(
      screen.getByText(/Answers from before you reloaded were not kept/),
    ).toBeTruthy()
    fireEvent.click(row)
    expect(await screen.findByRole('button', { name: '2.62' })).toBeTruthy()
    expect(calls.filter((c) => c.endsWith('/api/explore')).length).toBe(1)
    // Listed once, not twice.
    expect(screen.getAllByTitle('Which offices hold the most active holds?')).toHaveLength(1)
  })

  it('lets staff ask Explore questions without the briefing cards', async () => {
    const calls = mockApi({ '/auth/me': () => json(sessionAs('staff')) })
    render(<App />)
    await screen.findByText('What would you like to know?')
    expect(await screen.findByText(OWNER)).toBeTruthy()
    expect(screen.queryByText('Approved question')).toBeNull()
    expect(calls.some((c) => c.endsWith('/api/questions'))).toBe(false)
    // Even the approved question's words go to Explore for staff (no /ask).
    await askQuestion(QUESTION)
    await waitFor(() => expect(calls.some((c) => c.endsWith('/api/explore'))).toBe(true))
    expect(calls.some((c) => c.endsWith('/api/ask'))).toBe(false)
  })

  it('gives the Financial Aid role no question field and never calls Explore', async () => {
    const calls = mockApi({
      '/auth/me': () => json(sessionAs('aid')),
      '/aid-queue': () => json({ rows: [], counts: {} }),
    })
    render(<App />)
    await screen.findByText('Student success briefing')
    await waitFor(() => expect(calls.some((c) => c.endsWith('/api/briefing'))).toBe(true))
    expect(document.getElementById('question-input')).toBeNull()
    expect(calls.some((c) => c.includes('/api/explore'))).toBe(false)
  })
})

describe('the sidebar clean-up', () => {
  it('has one Data access row (the page keeps its full title) and no Key figures or Evidence rows', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('What would you like to know?')
    const nav = screen.getByRole('complementary', { name: 'CampusLens navigation' })
    const rows = within(nav)
    expect(rows.getByRole('button', { name: 'Data access' })).toBeTruthy()
    expect(rows.queryByRole('button', { name: 'Key figures' })).toBeNull()
    expect(rows.queryByRole('button', { name: 'Evidence & sources' })).toBeNull()
    expect(rows.queryByRole('button', { name: 'AI employees and data access' })).toBeNull()
    expect(rows.queryByRole('button', { name: 'AI employees' })).toBeNull()
    fireEvent.click(rows.getByRole('button', { name: 'Data access' }))
    expect(await screen.findByRole('region', { name: 'AI employees and data access' })).toBeTruthy()
  })

  it('says the questions could not be loaded, with Retry, instead of the empty line', async () => {
    let fail = true
    mockApi({ '/briefing': () => (fail ? json({ detail: 'boom' }, 500) : json({ detail: 'none' }, 404)) })
    render(<App />)
    const nav = await screen.findByRole('complementary', { name: 'CampusLens navigation' })
    expect(await within(nav).findByText("We couldn't load your questions.")).toBeTruthy()
    expect(within(nav).queryByText('Questions you ask appear here.')).toBeNull()
    fail = false
    fireEvent.click(within(nav).getByRole('button', { name: 'Retry' }))
    expect(await within(nav).findByText('Questions you ask appear here.')).toBeTruthy()
  })
})

describe('the briefing before any question', () => {
  it('offers "Ask it now" in the Full briefing panel, which asks the spring question', async () => {
    let asked: unknown = null
    mockApi({
      '/ask': (_url, init) => {
        asked = JSON.parse(String(init?.body ?? '{}'))
        return json({ detail: 'busy' }, 429)
      },
    })
    render(<App />)
    await screen.findByText('What would you like to know?')
    fireEvent.click(screen.getByRole('button', { name: 'Full briefing' }))
    const dialog = await screen.findByRole('region', { name: 'Full briefing' })
    const button = await within(dialog).findByRole('button', { name: 'Ask it now' })
    expect(button.className).toContain('btn-primary')
    fireEvent.click(button)
    await waitFor(() => expect(asked).toMatchObject({ question: QUESTION }))
  })

  it('keeps the sentence without a button for a role that cannot ask briefings', async () => {
    mockApi({ '/auth/me': () => json(sessionAs('staff')) })
    render(<App />)
    await screen.findByText('What would you like to know?')
    fireEvent.click(screen.getByRole('button', { name: 'Full briefing' }))
    const dialog = await screen.findByRole('region', { name: 'Full briefing' })
    await waitFor(() =>
      expect(dialog.textContent).toContain('once an executive asks the spring registration question'),
    )
    expect(within(dialog).queryByRole('button', { name: 'Ask it now' })).toBeNull()
  })

  it('shows the Decision as an empty state, with nothing to approve', async () => {
    mockApi({
      '/decisions': () =>
        json({
          question_id: null,
          decisions: [
            {
              id: 'D-1',
              title: 'Emergency-aid eligibility review',
              text: 'Decide whether to authorize a review.',
              follow_up: { office: 'Financial Aid', description: 'Report back.' },
              approved: false,
            },
          ],
        }),
      '/decisions/D-1/dispatch': () =>
        json({ decision_id: 'D-1', task_id: 'T', office: 'Financial Aid', office_contact: null, approved: false, dispatch: null }),
    })
    render(<App />)
    await screen.findByText('What would you like to know?')
    for (const name of ['Decision', 'Full briefing']) {
      fireEvent.click(screen.getByRole('button', { name }))
      const dialog = await screen.findByRole('region', { name })
      await waitFor(() =>
        expect(dialog.textContent).toContain(
          'Ask the spring registration question first. The briefing and the decision appear here.',
        ),
      )
      expect(within(dialog).queryByRole('button', { name: 'Approve' })).toBeNull()
    }
  })

  it('starts on a new question even when a decision waits; the restored briefing opens from history and says "the latest briefing"', async () => {
    mockApi({
      '/briefing': () => json(BRIEFING),
      '/decisions': () =>
        json({
          question_id: 'spring-registration',
          decisions: [
            {
              id: 'D-1',
              title: 'Emergency-aid eligibility review',
              text: 'Decide whether to authorize a review.',
              follow_up: { office: 'Financial Aid', description: 'Report back.' },
              approved: false,
            },
          ],
        }),
      '/decisions/D-1/dispatch': () =>
        json({ decision_id: 'D-1', task_id: 'T', office: 'Financial Aid', office_contact: null, approved: false, dispatch: null }),
    })
    render(<App />)
    // Signing in lands on the empty question screen, not the restored answer.
    await screen.findByText('What would you like to know?')
    await waitFor(() => expect(document.querySelector('.recent-row')).not.toBeNull())
    expect(document.body.textContent).not.toContain('Showing the latest briefing')
    // The restored briefing is one click away in the history.
    const row = Array.from(document.querySelectorAll<HTMLButtonElement>('.recent-row')).find(
      (button) => button.textContent?.includes('Latest briefing'),
    )
    expect(row).toBeTruthy()
    fireEvent.click(row!)
    expect(await screen.findByText(/Showing the latest briefing/)).toBeTruthy()
    expect(document.body.textContent).not.toContain('Showing your last briefing')
    // The work line opens the one AI employees panel, with each job named.
    fireEvent.click(screen.getByRole('button', { name: /Showing the latest briefing/ }))
    const dialog = await screen.findByRole('region', { name: 'AI employees and data access' })
    expect(await within(dialog).findByText(/writes the summary and its limits/)).toBeTruthy()
  })
})

describe('history: Back on a page and the evidence', () => {
  it('goes back to the conversation from a page it opened (the browser entry, not a new one)', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('What would you like to know?')
    const before = window.history.length
    fireEvent.click(screen.getByRole('button', { name: 'Staff actions' }))
    await screen.findByRole('region', { name: 'Staff actions' })
    expect(window.location.pathname).toBe('/view/staff-actions')
    expect(window.history.length).toBe(before + 1)
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    await waitFor(() => expect(window.location.pathname).toBe('/'))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Staff actions' })).toBeNull())
    // Back went back: Forward would reopen the page, and nothing new was pushed.
    expect(window.history.length).toBe(before + 1)
  })

  it('replaces the entry when a page was opened straight from its address', async () => {
    window.history.replaceState(null, '', '/view/staff-actions')
    mockApi()
    render(<App />)
    await screen.findByRole('region', { name: 'Staff actions' })
    const before = window.history.length
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    await waitFor(() => expect(window.location.pathname).toBe('/'))
    expect(window.history.length).toBe(before)
  })

  it('closes the evidence on the browser Back, keeping the page under it', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('What would you like to know?')
    fireEvent.click(screen.getByRole('button', { name: 'Staff actions' }))
    await screen.findByRole('region', { name: 'Staff actions' })
    // The browser goes back from an evidence entry to the page's own entry.
    act(() => {
      window.history.pushState({ campuslens: true }, '', '/view/staff-actions?evidence=M1')
      window.history.pushState({ campuslens: true }, '', '/view/staff-actions')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(screen.getByRole('region', { name: 'Staff actions' })).toBeTruthy()
    expect(window.location.search).toBe('')
  })
})

describe('the last briefing failing to load', () => {
  it('says so with Retry on the home screen, never "nothing yet"', async () => {
    let fail = true
    mockApi({
      '/briefing': () => (fail ? json({ detail: 'boom' }, 500) : json({ detail: 'none' }, 404)),
    })
    render(<App />)
    const line = await screen.findByText(/We couldn't load the last briefing\./)
    const panel = line.closest('[role="alert"]') as HTMLElement
    fail = false
    fireEvent.click(within(panel).getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByText(/We couldn't load the last briefing/)).toBeNull())
  })

  it('says so on the Full briefing instead of offering "Ask it now"', async () => {
    mockApi({ '/briefing': () => json({ detail: 'boom' }, 500) })
    render(<App />)
    await screen.findByText('What would you like to know?')
    fireEvent.click(screen.getByRole('button', { name: 'Full briefing' }))
    const dialog = await screen.findByRole('region', { name: 'Full briefing' })
    await waitFor(() => expect(dialog.textContent).toContain("We couldn't load the last briefing."))
    expect(within(dialog).queryByRole('button', { name: 'Ask it now' })).toBeNull()
    expect(within(dialog).getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})

describe('the decision when its message state fails to load', () => {
  it('still shows the decision, with the error on the message step only', async () => {
    mockApi({
      '/briefing': () => json(BRIEFING),
      '/decisions': () =>
        json({
          question_id: 'spring-registration',
          decisions: [
            {
              id: 'D-1',
              title: 'Emergency-aid eligibility review',
              text: 'Decide whether to authorize a review.',
              follow_up: { office: 'Financial Aid', description: 'Report back.' },
              approved: true,
              approved_by: 'president@demo.test',
            },
          ],
        }),
      '/decisions/D-1/dispatch': () => json({ detail: 'boom' }, 503),
    })
    render(<App />)
    await screen.findByText('Student success briefing')
    fireEvent.click(screen.getByRole('button', { name: 'Decision' }))
    const dialog = await screen.findByRole('region', { name: 'Decision' })
    await waitFor(() => expect(dialog.textContent).toContain('Emergency-aid eligibility review'))
    expect(dialog.textContent).toContain("We couldn't check the message to the office.")
    expect(within(dialog).getByRole('button', { name: 'Check the message again' })).toBeTruthy()
  })
})

describe('a page this role cannot open, typed as an address', () => {
  it('rewrites /institution for staff to the conversation and says why in one sentence', async () => {
    window.history.replaceState(null, '', '/institution')
    mockApi({ '/auth/me': () => json(sessionAs('staff')) })
    render(<App />)
    expect(
      await screen.findByText('Only an administrator can open Institution settings.'),
    ).toBeTruthy()
    expect(window.location.pathname).toBe('/')
    expect(document.body.textContent).not.toContain('the office mailboxes, the counseling permission')
  })
})

describe('Institution settings for an administrator', () => {
  it('has the shared page header (Back and the title) and no "Administration" bar title', async () => {
    window.history.replaceState(null, '', '/institution')
    mockApi({ '/auth/me': () => json(sessionAs('admin')) })
    render(<App />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Institution settings' })).toBeTruthy()
    expect(document.body.textContent).not.toContain('Administration')
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    await waitFor(() => expect(window.location.pathname).toBe('/'))
  })
})
