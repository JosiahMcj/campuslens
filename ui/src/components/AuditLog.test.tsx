// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AuditEvent } from '../api'
import { AuditLog, type DeniedRequestState } from './AuditLog'

function event(id: number, type: string, actor: string, payload: Record<string, unknown>): AuditEvent {
  return { id, ts: '2026-10-05T12:00:00+00:00', type, actor, payload }
}

// Shapes as the API records them (see the recorded demo run).
const EVENTS: AuditEvent[] = [
  event(1, 'question.asked', 'executive', {
    question: 'What should I know about spring registration?',
    question_id: 'spring-registration',
  }),
  event(2, 'task.assigned', 'chief_of_staff', {
    task_id: 'task-enrollment_analyst-1',
    role: 'enrollment_analyst',
    fields: ['profile.continuing', 'enrollment.registration_status'],
    findings: ['M1', 'M2'],
  }),
  event(3, 'data.granted', 'enrollment_analyst', {
    task_id: 'task-enrollment_analyst-1',
    granted_fields: ['profile.continuing', 'enrollment.registration_status'],
  }),
  event(4, 'decision.approved', 'executive', { decision_id: 'D-spring-registration-1' }),
  event(5, 'task.sent', 'staff@example.edu', {
    dispatch_id: 1,
    task_id: 'TASK-D-spring-registration-1',
    to_office: 'Financial Aid',
    provider: 'outbox',
    provider_ref: 'bootstrap/1.eml',
  }),
  event(6, 'data.refused', 'enrollment_analyst', {
    task_id: 'governance-demo',
    requested_fields: ['holds.amount'],
    refused_fields: ['holds.amount'],
    reason:
      "Role 'enrollment_analyst' is not permitted to access holds.amount; the request was refused before any model call.",
  }),
]

const IDLE: DeniedRequestState = { kind: 'idle' }

function mount(props: Partial<Parameters<typeof AuditLog>[0]> = {}) {
  return render(
    <AuditLog
      events={EVENTS}
      readOnly={false}
      onRefresh={() => {}}
      deniedRequest={IDLE}
      onShowDeniedRequest={() => {}}
      {...props}
    />,
  )
}

afterEach(() => cleanup())

describe('AuditLog', () => {
  it('writes each entry as a plain sentence, with no codes, ids or raw records', () => {
    mount()
    const text = document.body.textContent ?? ''
    expect(text).toContain('The executive asked “What should I know about spring registration?”.')
    expect(text).toContain('The Chief of Staff gave the Enrollment Analyst its part of the question (2 fields).')
    expect(text).toContain('The Enrollment Analyst was given access to 2 fields.')
    expect(text).toContain('The executive approved the leadership decision.')
    expect(text).toContain('staff@example.edu sent the message to Financial Aid.')
    expect(text).toContain(
      'The Enrollment Analyst asked to see the “Hold amount” field and was refused before any model was called.',
    )
    for (const raw of [
      'decision.approved',
      'data.refused',
      'task-enrollment',
      'TASK-',
      'D-spring',
      'holds.amount',
      'enrollment_analyst',
      'outbox',
      '{',
      'payload',
    ]) {
      expect(text).not.toContain(raw)
    }
  })

  it('folds plain details per entry, with field labels', () => {
    mount()
    const details = [...document.querySelectorAll('details')].map((d) => d.textContent ?? '')
    expect(details.some((d) => d.includes('Fields refused') && d.includes('Hold amount'))).toBe(true)
    expect(details.some((d) => d.includes('Registration status'))).toBe(true)
  })

  it('filters by kind of entry, by who, and by date', () => {
    mount()
    const show = screen.getByLabelText('Show') as HTMLSelectElement
    expect([...show.options].map((o) => o.textContent)).toEqual([
      'Everything',
      'Questions and answers',
      'Data access',
      'Decisions and messages',
      'Staff actions',
      'Data and people',
      'Refusals',
    ])
    fireEvent.change(show, { target: { value: 'refusals' } })
    expect(document.querySelectorAll('.event')).toHaveLength(1)
    fireEvent.change(show, { target: { value: 'decisions' } })
    expect(document.querySelectorAll('.event')).toHaveLength(2)
    expect(screen.getByText('Showing 2 of 6 entries.')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    const who = screen.getByLabelText('Who') as HTMLSelectElement
    // People and AI employees by name, never a raw role code.
    const names = [...who.options].map((o) => o.textContent)
    expect(names).toContain('The Enrollment Analyst')
    expect(names).toContain('staff@example.edu')
    expect(names.join(' ')).not.toContain('enrollment_analyst')
    fireEvent.change(who, { target: { value: 'enrollment_analyst' } })
    expect(document.querySelectorAll('.event')).toHaveLength(2)

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-10-06' } })
    expect(document.querySelectorAll('.event')).toHaveLength(0)
    expect(screen.getByText(/No entries match these filters/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-10-01' } })
    expect(document.querySelectorAll('.event')).toHaveLength(6)
  })

  it('says every staff action entry in plain words, never a note or a code', () => {
    mount({
      events: [
        event(1, 'action.updated', 'staff@example.edu', {
          action_id: 3,
          office: 'Bursar',
          finding_id: 'M5',
          fields: ['owner', 'status'],
          status_from: 'todo',
          status_to: 'in_progress',
        }),
        event(2, 'action.noted', 'president@example.edu', { action_id: 3, office: 'Bursar' }),
        event(3, 'action.sent', 'staff@example.edu', {
          action_id: 3,
          office: 'Bursar',
          dispatch_id: 4,
          provider: 'outbox',
          provider_ref: 'bootstrap/4.eml',
        }),
        event(4, 'action.send_failed', 'staff@example.edu', {
          action_id: 3,
          office: 'Library',
          dispatch_id: 5,
          error: 'the mail server did not answer',
        }),
        event(5, 'explore.answered', 'chief_of_staff', {
          task_id: 'explore-9',
          steps: ['majors_gpa', 'course_dfw'],
          answered: true,
        }),
      ],
    })
    const text = document.body.textContent ?? ''
    expect(text).toContain(
      'staff@example.edu moved the Bursar action from to do to in progress and changed who has the Bursar action.',
    )
    expect(text).toContain('president@example.edu added a note to the Bursar action.')
    expect(text).toContain("staff@example.edu sent the Bursar action to that office's mailbox.")
    expect(text).toContain('tried to send the Library action to its office, and it did not go through.')
    expect(text).toContain('The Chief of Staff answered a question from the records, in 2 steps.')
    for (const raw of ['action.updated', 'action.sent', 'M5', 'outbox', 'majors_gpa', 'did not answer', 'recorded an entry']) {
      expect(text).not.toContain(raw)
    }
  })

  it('offers one "Test a refusal" button that runs the refusal test', () => {
    const onShow = vi.fn()
    mount({ onShowDeniedRequest: onShow })
    fireEvent.click(screen.getByRole('button', { name: 'Test a refusal' }))
    expect(onShow).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: /Show a denied/ })).toBeNull()
  })

  it('keeps the refusal test from the reviewer', () => {
    mount({ readOnly: true })
    expect(screen.queryByRole('button', { name: 'Test a refusal' })).toBeNull()
  })

  it('shows the refusal in words and highlights its entry', () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    mount({ deniedRequest: { kind: 'shown', reason: 'raw', eventId: 6 } })
    expect(screen.getByRole('status').textContent).toContain(
      'The Enrollment Analyst asked to see the “Hold amount” field and was refused before any model was called.',
    )
    expect(document.getElementById('event-6')?.className).toContain('highlighted')
    expect(scroll).toHaveBeenCalled()
  })

  it('scrolls to and highlights an entry passed in', () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    mount({ highlightEventId: 4 })
    expect(document.getElementById('event-4')?.className).toContain('highlighted')
    expect(scroll).toHaveBeenCalled()
  })

  it('shows a failed load with Retry, never "Loading" forever or the empty text', () => {
    const onRefresh = vi.fn()
    mount({ events: null, loadError: 'Check your connection and try again.', onRefresh })
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain("Couldn't load the audit log.")
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRefresh).toHaveBeenCalled()
    expect(document.body.textContent).not.toContain('Loading the audit log')
  })

  it('says what to do when the log is empty', () => {
    mount({ events: [] })
    expect(document.body.textContent).toContain('Nothing is recorded yet. Ask an approved question')
  })
})

describe('AuditLog — order, viewer and details', () => {
  it('lists the newest entry first and says so', () => {
    const { container } = mount()
    const ids = [...container.querySelectorAll('.event')].map((item) => item.id)
    expect(ids).toEqual(['event-6', 'event-5', 'event-4', 'event-3', 'event-2', 'event-1'])
    expect(container.textContent).toContain('Newest entries are first.')
  })

  it('reads the viewer as "You" and anyone else by address', () => {
    mount({ viewerEmail: 'staff@example.edu' })
    expect(screen.getByText(/You sent the message to Financial Aid\./)).toBeTruthy()
    cleanup()
    mount({ viewerEmail: 'someone@example.edu' })
    expect(screen.getByText(/staff@example\.edu sent the message to Financial Aid\./)).toBeTruthy()
  })

  it('shows a short time on the row, the full time only inside Details, and Details only where there is detail', () => {
    const { container } = mount()
    const asked = container.querySelector('#event-1')!
    expect(asked.querySelector('.event-ts')?.textContent).toMatch(/^Oct 5, \d{1,2}:00 (AM|PM)$/)
    // The question is the sentence itself: no Details fold to repeat it.
    expect(asked.querySelector('details')).toBeNull()
    const granted = container.querySelector('#event-3')!
    const details = granted.querySelector('details')!
    expect(details.textContent).toContain('Recorded')
    expect(details.textContent).toMatch(/2026-10-05 \d{2}:00:00 \S+/)
  })
})
