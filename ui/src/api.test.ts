// Tests for the analyst briefing fetch logic: the once-per-page-load
// promises (the StrictMode double-effect guard) and the "Check again" refresh
// routes with their GET fallback. Fetch is stubbed; no server is needed.

import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  DENIED_REQUEST,
  fetchCabinetBriefingOnce,
  fetchDecisions,
  fetchEnrollmentBriefingOnce,
  fetchQuestions,
  fetchStudentSuccessBriefingOnce,
  postApprove,
  postGovernanceRequest,
  refreshEnrollmentBriefing,
  refreshStudentSuccessBriefing,
  resetBriefingOnce,
} from './api'
import { parseFlags } from './states'

const flags = parseFlags('')

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const availableBody = {
  available: true,
  text: 'Narrative.',
  claims: [{ text: 'Registration is down.', finding_ids: ['M1'] }],
  provider: 'fake',
  model_label: 'test stub',
  recorded: false,
}

afterEach(() => {
  vi.unstubAllGlobals()
  resetBriefingOnce()
})

describe('fetchEnrollmentBriefingOnce — once per page load', () => {
  it('fetches the route once when two mounts call at the same time', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, availableBody))
    vi.stubGlobal('fetch', fetchMock)

    const [first, second] = await Promise.all([
      fetchEnrollmentBriefingOnce(flags),
      fetchEnrollmentBriefingOnce(flags),
    ])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/briefing/enrollment')
    expect(first).toBe(second)
    expect(first.kind).toBe('available')
  })

  it('does not refetch for a later call after the first settles', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, availableBody))
    vi.stubGlobal('fetch', fetchMock)

    await fetchEnrollmentBriefingOnce(flags)
    const again = await fetchEnrollmentBriefingOnce(flags)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(again.kind).toBe('available')
  })

  it('caches the two briefing routes independently', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, availableBody))
    vi.stubGlobal('fetch', fetchMock)

    await fetchEnrollmentBriefingOnce(flags)
    await fetchStudentSuccessBriefingOnce(flags)
    await fetchEnrollmentBriefingOnce(flags)
    await fetchStudentSuccessBriefingOnce(flags)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock).toHaveBeenCalledWith('/api/briefing/enrollment')
    expect(fetchMock).toHaveBeenCalledWith('/api/briefing/student-success')
  })
})

describe('fetchStudentSuccessBriefingOnce — once per page load', () => {
  it('fetches the student-success route once across concurrent mounts', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, availableBody))
    vi.stubGlobal('fetch', fetchMock)

    const [first, second] = await Promise.all([
      fetchStudentSuccessBriefingOnce(flags),
      fetchStudentSuccessBriefingOnce(flags),
    ])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/briefing/student-success')
    expect(first).toBe(second)
    expect(first.kind).toBe('available')
  })
})

describe('refreshEnrollmentBriefing — "Check again"', () => {
  it('POSTs the refresh route when it exists', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, availableBody))
    vi.stubGlobal('fetch', fetchMock)

    const result = await refreshEnrollmentBriefing(flags)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/briefing/enrollment/refresh', {
      method: 'POST',
    })
    expect(result.kind).toBe('available')
  })

  it.each([404, 405])(
    'falls back to GET when the refresh route answers HTTP %i',
    async (status) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(status, { detail: 'Not Found' }))
        .mockResolvedValueOnce(
          jsonResponse(503, { available: false, reason: 'no key configured' }),
        )
      vi.stubGlobal('fetch', fetchMock)

      const result = await refreshEnrollmentBriefing(flags)

      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/briefing/enrollment')
      expect(result).toEqual({ kind: 'unavailable', reason: 'no key configured' })
    },
  )

  it('falls back to GET when the POST itself fails', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce(
        jsonResponse(503, { available: false, reason: 'provider offline' }),
      )
    vi.stubGlobal('fetch', fetchMock)

    const result = await refreshEnrollmentBriefing(flags)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(result).toEqual({ kind: 'unavailable', reason: 'provider offline' })
  })

  it('honors the ?model=down switch without any fetch', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const result = await refreshEnrollmentBriefing(parseFlags('?model=down'))

    expect(fetchMock).not.toHaveBeenCalled()
    expect(result.kind).toBe('unavailable')
  })
})

describe('refreshStudentSuccessBriefing — "Check again"', () => {
  it('POSTs the student-success refresh route', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, availableBody))
    vi.stubGlobal('fetch', fetchMock)

    const result = await refreshStudentSuccessBriefing(flags)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/briefing/student-success/refresh', {
      method: 'POST',
    })
    expect(result.kind).toBe('available')
  })

  it('carries the 503 reason when the model is unavailable', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(503, { available: false, reason: 'no key configured' }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await refreshStudentSuccessBriefing(flags)

    expect(result).toEqual({ kind: 'unavailable', reason: 'no key configured' })
  })

  it('honors the ?model=down switch without any fetch', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const result = await refreshStudentSuccessBriefing(parseFlags('?model=down'))

    expect(fetchMock).not.toHaveBeenCalled()
    expect(result.kind).toBe('unavailable')
  })
})

describe('postGovernanceRequest — the Beat 6 denied data request', () => {
  it('POSTs the Enrollment Analyst hold-amount request to the gate', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        granted: false,
        role: 'enrollment_analyst',
        refused_fields: ['holds.amount'],
        reason:
          "Role 'enrollment_analyst' is not permitted to access holds.amount; " +
          'the request was refused before any model call.',
        event: {
          id: 9,
          ts: '2026-09-24T22:00:00+00:00',
          type: 'data.refused',
          actor: 'enrollment_analyst',
          payload: {},
        },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await postGovernanceRequest(flags)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/governance/request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(DENIED_REQUEST),
    })
    expect(result).toMatchObject({
      granted: false,
      refused_fields: ['holds.amount'],
      event: { id: 9, type: 'data.refused' },
    })
  })
})

describe('fetchCabinetBriefingOnce — the produced briefing on reload', () => {
  const briefingBody = {
    question_id: 'spring-registration',
    question: 'What should I know about spring registration?',
    question_event_id: 3,
    sections: {
      1: {
        kind: 'available',
        text: 'Summary [M1].',
        claims: [{ text: 'Summary', finding_ids: ['M1'] }],
        provenance: { source: 'chief_of_staff', provider: 'fake', model_label: 'test stub', recorded: false },
      },
      2: { kind: 'unavailable', reason: 'down' },
      3: { kind: 'unavailable', reason: 'down' },
      4: { findings: {} },
      5: { actions: [] },
      6: { decisions: [] },
      7: { kind: 'unavailable', reason: 'down' },
    },
  }

  it('maps 404 (no briefing yet) to null and fetches once per load', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(404, { available: false, reason: 'none yet' }))
    vi.stubGlobal('fetch', fetchMock)
    const [first, second] = await Promise.all([
      fetchCabinetBriefingOnce(flags),
      fetchCabinetBriefingOnce(flags),
    ])
    expect(first).toBeNull()
    expect(second).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('parses a produced briefing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, briefingBody)))
    const briefing = await fetchCabinetBriefingOnce(flags)
    expect(briefing?.question_id).toBe('spring-registration')
    expect(briefing?.question_event_id).toBe(3)
    expect(briefing?.sections[1].kind).toBe('available')
    expect(briefing?.sections[7]).toEqual({ kind: 'unavailable', reason: 'down' })
  })

  it('never fetches under ?model=down', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const briefing = await fetchCabinetBriefingOnce(parseFlags('?model=down'))
    expect(briefing).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('fetchQuestions — the approved-question registry', () => {
  it('returns one entry per approved question from GET /questions', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, [
        { id: 'spring-registration', text: 'What should I know about spring registration?' },
        { id: 'unresolved-holds', text: 'Where are unresolved holds affecting continued enrollment?' },
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const questions = await fetchQuestions(flags)

    expect(fetchMock).toHaveBeenCalledWith('/api/questions')
    expect(questions).toEqual([
      { id: 'spring-registration', text: 'What should I know about spring registration?' },
      { id: 'unresolved-holds', text: 'Where are unresolved holds affecting continued enrollment?' },
    ])
  })

  it('drops malformed entries instead of failing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(200, [{ id: 'q1', text: 'One?' }, { id: 7 }, 'nope', null]),
      ),
    )
    const questions = await fetchQuestions(flags)
    expect(questions).toEqual([{ id: 'q1', text: 'One?' }])
  })
})

describe('fetchDecisions — the latest question’s decision', () => {
  it('unwraps {question_id, decisions} and returns the decisions', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        question_id: 'unresolved-holds',
        decisions: [
          {
            id: 'D-unresolved-holds-1',
            title: 'Hold resolution push',
            text: 'Decide.',
            follow_up: { office: 'Bursar', description: 'Resolve.' },
            approved: false,
          },
        ],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const decisions = await fetchDecisions(flags)

    expect(fetchMock).toHaveBeenCalledWith('/api/decisions')
    expect(decisions).toHaveLength(1)
    expect(decisions[0].id).toBe('D-unresolved-holds-1')
  })
})

describe('postApprove — approval by the decision’s own id', () => {
  it('POSTs the id the decisions response carried', async () => {
    const decisionId = 'D-unresolved-holds-1'
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        task: {
          id: `TASK-${decisionId}`,
          decision_id: decisionId,
          office: 'Bursar',
          description: 'Resolve.',
          status: 'simulated',
        },
        created: true,
        event_ids: [21, 22],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const response = await postApprove(decisionId, flags)

    expect(fetchMock).toHaveBeenCalledWith('/api/decisions/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision_id: decisionId }),
    })
    expect(response.task.decision_id).toBe(decisionId)
    expect(response.created).toBe(true)
  })
})
