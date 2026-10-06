import { describe, expect, it } from 'vitest'

import {
  APPROVED_QUESTION,
  analystSource,
  analystSourceDetail,
  analystSourceLabel,
  documentTitle,
  friendlyTime,
  nextPollDelay,
  normalizeRoute,
  describeState,
  analystFromResponse,
  cabinetBriefingFrom,
  dispatchTasks,
  eventsAfter,
  filterEvents,
  findingDisplay,
  formatTimestamp,
  isApprovedQuestion,
  latestQuestionEventId,
  maxEventId,
  m3ThresholdLabel,
  modelSectionFrom,
  parseFlags,
  taskFromEvents,
  type AuditEvent,
} from './states'

describe('isApprovedQuestion', () => {
  it('accepts the exact approved question', () => {
    expect(isApprovedQuestion(APPROVED_QUESTION)).toBe(true)
  })

  it('is case-insensitive and trims whitespace', () => {
    expect(isApprovedQuestion('  what should i know about spring registration? ')).toBe(
      true,
    )
  })

  it('treats the trailing question mark as optional', () => {
    expect(isApprovedQuestion('What should I know about spring registration')).toBe(true)
  })

  it('rejects the demo refusal question', () => {
    expect(isApprovedQuestion('Which students are in counseling?')).toBe(false)
  })

  it('rejects an empty question', () => {
    expect(isApprovedQuestion('')).toBe(false)
    expect(isApprovedQuestion('   ')).toBe(false)
  })
})

describe('parseFlags', () => {
  it('defaults every switch off', () => {
    expect(parseFlags('')).toEqual({
      slow: false,
      failFindings: false,
      modelDown: false,
      evidence: null,
      demoRefusal: false,
    })
  })

  it('parses the three state switches and the evidence deep link', () => {
    expect(parseFlags('?slow=1')).toMatchObject({ slow: true })
    expect(parseFlags('?fail=findings')).toMatchObject({ failFindings: true })
    expect(parseFlags('?model=down')).toMatchObject({ modelDown: true })
    expect(parseFlags('?evidence=M2')).toMatchObject({ evidence: 'M2' })
    expect(parseFlags('?demo=refusal')).toMatchObject({ demoRefusal: true })
  })

  it('ignores unrelated values', () => {
    const flags = parseFlags('?fail=other&slow=0&model=up&demo=other')
    expect(flags.failFindings).toBe(false)
    expect(flags.slow).toBe(false)
    expect(flags.modelDown).toBe(false)
    expect(flags.demoRefusal).toBe(false)
  })

  it('parses combined switches', () => {
    const flags = parseFlags('?slow=1&model=down&evidence=M1')
    expect(flags).toEqual({
      slow: true,
      failFindings: false,
      modelDown: true,
      evidence: 'M1',
      demoRefusal: false,
    })
  })
})

describe('findingDisplay — the exact `--` rule', () => {
  it('renders `--` with its reason when the value is null', () => {
    const d = findingDisplay({ display: '--', reason: 'prior-year count is 0' })
    expect(d).toEqual({ text: '--', missing: true, reason: 'prior-year count is 0' })
  })

  it('renders a real zero as `0`, never `--`', () => {
    const d = findingDisplay({ display: '0', reason: null })
    expect(d.text).toBe('0')
    expect(d.missing).toBe(false)
  })

  it('renders API display strings verbatim', () => {
    expect(findingDisplay({ display: '−4.8 %', reason: null }).text).toBe('−4.8 %')
    expect(findingDisplay({ display: '28 unresolved holds', reason: null }).text).toBe(
      '28 unresolved holds',
    )
  })

  it('falls back to `--` when display is absent', () => {
    expect(findingDisplay({}).text).toBe('--')
    expect(findingDisplay({}).missing).toBe(true)
  })
})

describe('LoadState machine', () => {
  it('describes the three states', () => {
    expect(describeState({ kind: 'loading' })).toBe('Loading')
    expect(describeState({ kind: 'error', message: 'API unreachable' })).toBe(
      'Error: API unreachable',
    )
    expect(describeState({ kind: 'ready', data: 42 })).toBe('Ready')
  })
})

describe('analystFromResponse — model unavailable handling', () => {
  const availableBody = {
    available: true,
    text: 'Narrative.',
    claims: [{ text: 'Registration is down.', finding_ids: ['M1'] }],
    provider: 'live',
    model_label: 'live model',
    recorded: false,
  }

  it('forces unavailable under ?model=down even when the API is healthy', () => {
    const result = analystFromResponse(200, availableBody, true)
    expect(result.kind).toBe('unavailable')
  })

  it('treats HTTP 404 (endpoint not landed yet) as unavailable', () => {
    const result = analystFromResponse(404, {}, false)
    expect(result).toMatchObject({ kind: 'unavailable' })
  })

  it('carries the reason from an HTTP 503 body', () => {
    const result = analystFromResponse(
      503,
      { available: false, reason: 'provider offline' },
      false,
    )
    expect(result).toEqual({ kind: 'unavailable', reason: 'provider offline' })
  })

  it('parses an available briefing with claims and provenance fields', () => {
    const result = analystFromResponse(200, availableBody, false)
    expect(result).toEqual({
      kind: 'available',
      text: 'Narrative.',
      claims: [{ text: 'Registration is down.', finding_ids: ['M1'] }],
      provider: 'live',
      model_label: 'live model',
      recorded: false,
    })
  })

  it('defaults provenance fields when the body omits them', () => {
    const result = analystFromResponse(
      200,
      { available: true, text: 'Narrative.', claims: [] },
      false,
    )
    expect(result).toMatchObject({ provider: null, model_label: null, recorded: false })
  })

  it('ignores a `model` id field — source labels never name a model', () => {
    const result = analystFromResponse(
      200,
      { ...availableBody, model: 'some-provider-model-id-7' },
      false,
    )
    expect(result).toMatchObject({ model_label: 'live model' })
    expect(JSON.stringify(result)).not.toContain('some-provider-model-id-7')
  })

  it('treats available:false on a 200 as unavailable', () => {
    const result = analystFromResponse(
      200,
      { available: false, reason: 'no model configured' },
      false,
    )
    expect(result).toEqual({ kind: 'unavailable', reason: 'no model configured' })
  })
})

describe('analystSource — honest labeling of the analyst text', () => {
  it('labels a live model run', () => {
    expect(analystSource({ provider: 'live', recorded: false })).toBe('live')
    expect(analystSourceLabel('live', 'the Enrollment Analyst')).toBe(
      'Written by the Enrollment Analyst',
    )
  })

  it('labels a recorded (replay) run, never stacking a second parenthetical', () => {
    expect(analystSource({ provider: 'live', recorded: true })).toBe('recorded')
    expect(analystSourceLabel('recorded', 'the Enrollment Analyst')).toBe(
      'Written by the Enrollment Analyst',
    )
    expect(analystSourceLabel('recorded', 'the Enrollment Analyst', 'live model')).toBe(
      'Written by the Enrollment Analyst',
    )
  })

  it('uses a configured model label for a live run only when it differs', () => {
    expect(analystSourceLabel('live', 'the Chief of Staff', 'campus GPT')).toBe(
      'Written by the Chief of Staff',
    )
    expect(analystSourceLabel('live', 'the Chief of Staff', 'live model')).toBe(
      'Written by the Chief of Staff',
    )
  })

  it('labels the fake provider as a test stub, never live', () => {
    expect(analystSource({ provider: 'fake', recorded: false })).toBe('fake')
    expect(analystSourceLabel('fake', 'the Enrollment Analyst')).toBe(
      'Test stub, not a live model',
    )
  })

  it('names the Student Success Analyst for its own section', () => {
    expect(analystSourceLabel('live', 'the Student Success Analyst')).toBe(
      'Written by the Student Success Analyst',
    )
    expect(analystSourceLabel('recorded', 'the Student Success Analyst')).toBe(
      'Written by the Student Success Analyst',
    )
  })
})

describe('m3ThresholdLabel — the M3 balance threshold from the finding', () => {
  it('formats a numeric comparison.threshold_usd as dollars', () => {
    expect(m3ThresholdLabel({ comparison: { threshold_usd: 1000 } })).toBe('$1,000')
  })

  it('passes a string threshold through verbatim', () => {
    expect(m3ThresholdLabel({ comparison: { threshold_usd: '$1,000' } })).toBe(
      '$1,000',
    )
  })

  it('returns null when the field is absent (the title text is shown instead)', () => {
    expect(m3ThresholdLabel({ comparison: null })).toBeNull()
    expect(m3ThresholdLabel({})).toBeNull()
    expect(m3ThresholdLabel({ comparison: { baseline: '202620' } })).toBeNull()
  })

  it('returns null for unusable values — never a NaN dollar amount', () => {
    expect(m3ThresholdLabel({ comparison: { threshold_usd: Number.NaN } })).toBeNull()
    expect(m3ThresholdLabel({ comparison: { threshold_usd: true } })).toBeNull()
  })
})

describe('audit log helpers', () => {
  const events: AuditEvent[] = [
    { id: 1, ts: '2026-09-24T22:00:00+00:00', type: 'question.asked', actor: 'executive', payload: {} },
    { id: 2, ts: '2026-09-24T22:00:01+00:00', type: 'data.refused', actor: 'chief_of_staff', payload: {} },
    {
      id: 3,
      ts: '2026-09-24T22:00:02+00:00',
      type: 'task.created',
      actor: 'chief_of_staff',
      payload: { decision_id: 'D-1', task: { id: 'TASK-D-1', status: 'simulated, nothing sent' } },
    },
  ]

  it('filters by event type and keeps order (newest last)', () => {
    expect(filterEvents(events, null)).toHaveLength(3)
    expect(filterEvents(events, 'data.refused').map((e) => e.id)).toEqual([2])
  })

  it('recovers a simulated task from the audit log', () => {
    expect(taskFromEvents(events, 'D-1')).toMatchObject({ id: 'TASK-D-1' })
    expect(taskFromEvents(events, 'D-2')).toBeUndefined()
  })

  it('formats timestamps in the viewer\'s zone, named', () => {
    const recorded = '2026-09-24T22:24:46.535823+00:00'
    expect(formatTimestamp(recorded, 'America/Chicago')).toBe(
      '2026-09-24 17:24:46 CDT',
    )
    expect(formatTimestamp('2026-12-24T22:24:46+00:00', 'America/Chicago')).toBe(
      '2026-12-24 16:24:46 CST',
    )
    expect(formatTimestamp(recorded, 'UTC')).toBe('2026-09-24 22:24:46 UTC')
    // Midnight stays 00, never 24.
    expect(formatTimestamp('2026-09-25T05:00:00+00:00', 'America/Chicago')).toBe(
      '2026-09-25 00:00:00 CDT',
    )
  })

  it('leaves an unparseable timestamp as it came', () => {
    expect(formatTimestamp('not a time')).toBe('not a time')
  })
})

describe('maxEventId — the run base for a new Ask', () => {
  const event = (id: number): AuditEvent => ({
    id,
    ts: '2026-09-24T22:00:00+00:00',
    type: 'question.asked',
    actor: 'executive',
    payload: {},
  })

  it('is the highest id, however the list is ordered', () => {
    expect(maxEventId([event(3), event(9), event(4)])).toBe(9)
    expect(maxEventId([event(1)])).toBe(1)
  })

  it('is 0 for an empty log, so the first run starts from nothing', () => {
    expect(maxEventId([])).toBe(0)
  })
})

describe('cabinetBriefingFrom / modelSectionFrom — the produced briefing', () => {
  const availableSection = {
    kind: 'available',
    text: 'Spring registration is down 4.8 % [M1].',
    claims: [{ text: 'Spring registration is down 4.8 %', finding_ids: ['M1'] }],
    provenance: {
      source: 'chief_of_staff',
      provider: 'fake',
      model_label: 'test stub',
      recorded: false,
    },
  }
  const briefingBody = {
    question_id: 'spring-registration',
    question: 'What should I know about spring registration?',
    question_event_id: 7,
    sections: {
      1: availableSection,
      2: availableSection,
      3: { kind: 'unavailable', reason: 'provider is down' },
      4: {
        findings: {
          M1: { title: 'Spring registration', display: '−4.8 %', source_fields: ['profile.continuing'] },
        },
      },
      5: { actions: [{ office: 'Student Success', text: 'Review students.', finding_ids: ['M4'] }] },
      6: {
        decisions: [
          { id: 'D-1', title: 'Review', text: 'Decide.', follow_up: { office: 'Financial Aid', description: 'Conduct.' } },
        ],
      },
      7: availableSection,
    },
  }

  it('parses a full briefing with provenance per model-written section', () => {
    const briefing = cabinetBriefingFrom(briefingBody)
    expect(briefing).not.toBeNull()
    expect(briefing?.question_id).toBe('spring-registration')
    expect(briefing?.question_event_id).toBe(7)
    const section1 = briefing?.sections[1]
    expect(section1?.kind).toBe('available')
    if (section1?.kind === 'available') {
      expect(section1.claims).toHaveLength(1)
      expect(section1.provenance.source).toBe('chief_of_staff')
      expect(section1.provenance.recorded).toBe(false)
    }
    expect(briefing?.sections[3]).toEqual({ kind: 'unavailable', reason: 'provider is down' })
    expect(briefing?.sections[4].findings.M1.display).toBe('−4.8 %')
    expect(briefing?.sections[5].actions[0].office).toBe('Student Success')
    expect(briefing?.sections[6].decisions[0].follow_up.office).toBe('Financial Aid')
  })

  it('returns null when the body is not a briefing', () => {
    expect(cabinetBriefingFrom(null)).toBeNull()
    expect(cabinetBriefingFrom({ available: false })).toBeNull()
  })

  it('marks an unrecognized section unavailable rather than inventing one', () => {
    expect(modelSectionFrom(undefined).kind).toBe('unavailable')
    expect(modelSectionFrom({ kind: 'mystery' }).kind).toBe('unavailable')
    const section = modelSectionFrom({ kind: 'unavailable', reason: 'no key' })
    expect(section).toEqual({ kind: 'unavailable', reason: 'no key' })
  })
})

describe('dispatchTasks — the Beat 2 task cards from the audit events', () => {
  const asked: AuditEvent = {
    id: 10,
    ts: '2026-09-24T22:00:00+00:00',
    type: 'question.asked',
    actor: 'executive',
    payload: { question: 'q' },
  }
  const assigned = (role: string): AuditEvent => ({
    id: 11,
    ts: '2026-09-24T22:00:01+00:00',
    type: 'task.assigned',
    actor: 'chief_of_staff',
    payload: { task_id: `task-${role}-10`, role },
  })
  const granted = (role: string, fields: string[], level?: string): AuditEvent => ({
    id: 12,
    ts: '2026-09-24T22:00:02+00:00',
    type: 'data.granted',
    actor: role,
    payload: { task_id: `task-${role}-10`, granted_fields: fields, ...(level ? { level } : {}) },
  })

  it('lists the analysts first, the chief only once assigned, and resolves done', () => {
    // Only the enrollment analyst has been dispatched so far.
    let tasks = dispatchTasks([asked, assigned('enrollment_analyst')], 10)
    expect(tasks.map((t) => t.role)).toEqual(['enrollment_analyst'])
    expect(tasks[0].granted_fields).toBeNull()
    expect(tasks[0].status).toBe('working')

    // Both analysts granted; enrollment produced; chief not yet assigned.
    const events: AuditEvent[] = [
      asked,
      assigned('enrollment_analyst'),
      assigned('student_success_analyst'),
      granted('enrollment_analyst', ['profile.continuing']),
      {
        id: 13,
        ts: '2026-09-24T22:00:03+00:00',
        type: 'finding.produced',
        actor: 'enrollment_analyst',
        payload: { task_id: 'task-enrollment_analyst-10' },
      },
      granted('student_success_analyst', ['holds.amount']),
    ]
    tasks = dispatchTasks(events, 10)
    expect(tasks.map((t) => t.role)).toEqual(['enrollment_analyst', 'student_success_analyst'])
    expect(tasks[0].granted_fields).toEqual(['profile.continuing'])
    expect(tasks[0].status).toBe('done')
    expect(tasks[1].status).toBe('working')

    // The chief's aggregate grant appears; briefing.produced resolves it.
    events.push(
      assigned('chief_of_staff'),
      granted('chief_of_staff', ['profile.continuing'], 'aggregate'),
      {
        id: 20,
        ts: '2026-09-24T22:00:09+00:00',
        type: 'briefing.produced',
        actor: 'chief_of_staff',
        payload: {
          question_id: 'spring-registration',
          question_event_id: 10,
          chief_task_id: 'task-chief_of_staff-10',
          sources: { 1: { available: true }, 2: { available: true }, 3: { available: true } },
        },
      },
    )
    tasks = dispatchTasks(events, 10)
    expect(tasks.map((t) => t.role)).toEqual([
      'enrollment_analyst',
      'student_success_analyst',
      'chief_of_staff',
    ])
    expect(tasks[2].level).toBe('aggregate')
    expect(tasks[2].status).toBe('done')
    // The student success analyst never produced, but its section is
    // available (a cache-served run): done, honestly derived from sources.
    expect(tasks[1].status).toBe('done')
  })

  it('marks a task the run completed without as unavailable, never done', () => {
    const events: AuditEvent[] = [
      asked,
      assigned('enrollment_analyst'),
      assigned('student_success_analyst'),
      granted('enrollment_analyst', ['profile.continuing']),
      granted('student_success_analyst', ['holds.amount']),
      {
        id: 13,
        ts: '2026-09-24T22:00:03+00:00',
        type: 'finding.produced',
        actor: 'enrollment_analyst',
        payload: { task_id: 'task-enrollment_analyst-10' },
      },
      {
        id: 20,
        ts: '2026-09-24T22:00:09+00:00',
        type: 'briefing.produced',
        actor: 'chief_of_staff',
        payload: {
          question_id: 'spring-registration',
          question_event_id: 10,
          chief_task_id: null,
          sources: { 2: { available: true }, 3: { available: false } },
        },
      },
    ]
    const tasks = dispatchTasks(events, 10)
    // No chief card: its task was never assigned in the degraded run.
    expect(tasks.map((t) => [t.role, t.status])).toEqual([
      ['enrollment_analyst', 'done'],
      ['student_success_analyst', 'unavailable'],
    ])
  })

  it('marks the chief card from section 1 availability, never from its task id', () => {
    // A failed Chief of Staff run still carries chief_task_id in
    // briefing.produced (it is set before the run), so the id alone must not
    // mean done: section 1 is unavailable, so the card is "Unavailable".
    const events: AuditEvent[] = [
      asked,
      assigned('enrollment_analyst'),
      assigned('student_success_analyst'),
      assigned('chief_of_staff'),
      granted('chief_of_staff', ['profile.continuing'], 'aggregate'),
      {
        id: 20,
        ts: '2026-09-24T22:00:09+00:00',
        type: 'briefing.produced',
        actor: 'chief_of_staff',
        payload: {
          question_event_id: 10,
          chief_task_id: 'task-chief_of_staff-10',
          sources: {
            1: { available: false, reason: 'output failed validation' },
            2: { available: true },
            3: { available: true },
            7: { available: false, reason: 'output failed validation' },
          },
        },
      },
    ]
    const chief = dispatchTasks(events, 10).find((t) => t.role === 'chief_of_staff')
    expect(chief?.status).toBe('unavailable')
  })

  it('ignores tasks from an earlier question', () => {
    const tasks = dispatchTasks([asked, assigned('enrollment_analyst')], 99)
    expect(tasks).toEqual([])
  })

  it('matches briefing.produced by the event id, not the registry id', () => {
    // question_id in the payload is the registry id (a string); the run's
    // cards key on question_event_id, the question.asked audit event id that
    // the task ids are built from.
    const events: AuditEvent[] = [
      asked,
      assigned('enrollment_analyst'),
      {
        id: 20,
        ts: '2026-09-24T22:00:09+00:00',
        type: 'briefing.produced',
        actor: 'chief_of_staff',
        payload: {
          question_id: 'spring-registration',
          question_event_id: 10,
          sources: { 2: { available: true } },
        },
      },
    ]
    expect(dispatchTasks(events, 10)[0]?.status).toBe('done')
    expect(dispatchTasks(events, 11)).toEqual([])
  })

  it('finds the latest question event id, scoped to the current run', () => {
    const later: AuditEvent = { ...asked, id: 30 }
    expect(latestQuestionEventId([asked, later])).toBe(30)
    expect(latestQuestionEventId(eventsAfter([asked, later], 25))).toBe(30)
    expect(latestQuestionEventId(eventsAfter([asked, later], 30))).toBeNull()
    expect(latestQuestionEventId([])).toBeNull()
  })
})

describe('analystSourceDetail', () => {
  it('keeps the replay and live distinction honest in the detail', () => {
    expect(analystSourceDetail('recorded')).toContain('earlier live run')
    expect(analystSourceDetail('live')).toContain('just now')
    expect(analystSourceDetail('live', 'campus GPT')).toContain('campus GPT')
    expect(analystSourceDetail('fake')).toContain('test stub')
  })
})

describe('documentTitle', () => {
  it('puts the screen before the product name', () => {
    expect(documentTitle(null)).toBe('Golden Eagle AI Cabinet')
    expect(documentTitle('Sign in')).toBe('Sign in · Golden Eagle AI Cabinet')
    expect(documentTitle('Audit log')).toBe('Audit log · Golden Eagle AI Cabinet')
  })
})

describe('normalizeRoute', () => {
  it('keeps known routes and sends anything else to the conversation', () => {
    expect(normalizeRoute('/')).toBe('/')
    expect(normalizeRoute('/login')).toBe('/login')
    expect(normalizeRoute('/institution/')).toBe('/institution')
    expect(normalizeRoute('/nowhere')).toBe('/')
    expect(normalizeRoute('/institution/users')).toBe('/')
  })
})

describe('nextPollDelay', () => {
  it('polls every 3 s, waits out a 429, and backs off on failures', () => {
    expect(nextPollDelay({ ok: true })).toBe(3000)
    expect(
      nextPollDelay({ ok: false, rateLimited: true, retryAfterSeconds: 20, failures: 1 }),
    ).toBe(20_000)
    expect(
      nextPollDelay({ ok: false, rateLimited: true, retryAfterSeconds: null, failures: 1 }),
    ).toBe(60_000)
    expect(
      nextPollDelay({ ok: false, rateLimited: false, retryAfterSeconds: null, failures: 1 }),
    ).toBe(6000)
    expect(
      nextPollDelay({ ok: false, rateLimited: false, retryAfterSeconds: null, failures: 9 }),
    ).toBe(30_000)
  })
})

describe('friendlyTime', () => {
  it('formats a timestamp for a reader, or null', () => {
    expect(friendlyTime('2026-10-05T14:02:00Z', 'UTC')).toBe('Oct 5, 2:02 PM')
    expect(friendlyTime('not a time')).toBeNull()
  })
})
