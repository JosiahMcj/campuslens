// @vitest-environment jsdom

// Institution settings, the counseling figure: the off state with its form,
// validation under the field with nothing sent, recording (the PUT body,
// the authorized state, the findings reload), revoking behind an inline
// confirmation, Cancel, a refused save, and a failed load with Retry. The
// API is a mocked fetch reached through the real ui/src/counseling.ts client.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CounselingAuthorizationSection } from './CounselingAuthorization'

const URL = '/api/admin/institution/counseling-authorization'

interface Call {
  method: string
  body: unknown
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const OFF = {
  authorized: false,
  authorized_by: null,
  document_reference: null,
  recorded_by: null,
  recorded_at: null,
}

function stubApi(options: { getStatus?: number; putStatus?: number } = {}) {
  let record: Record<string, unknown> = { ...OFF }
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url !== URL) return jsonResponse({ detail: 'unhandled' }, 500)
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
      calls.push({ method, body })
      if (method === 'GET') {
        if (options.getStatus !== undefined) {
          return jsonResponse({ detail: 'the authorization is unavailable' }, options.getStatus)
        }
        return jsonResponse(record)
      }
      if (options.putStatus !== undefined) {
        return jsonResponse(
          {
            detail: 'the authorization was not recorded',
            errors: ['enter the reference of the written authorization'],
          },
          options.putStatus,
        )
      }
      const sent = body as Record<string, unknown>
      record = sent.authorized
        ? {
            authorized: true,
            authorized_by: sent.authorized_by,
            document_reference: sent.document_reference,
            recorded_by: 'admin@example.edu',
            recorded_at: '2026-09-26T15:00:00+00:00',
          }
        : { ...record, authorized: false }
      return jsonResponse(record)
    }),
  )
  return { calls, puts: () => calls.filter((call) => call.method === 'PUT') }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Institution settings, the counseling figure', () => {
  it('starts off, with one sentence, the state, and the Record action', async () => {
    stubApi()
    render(<CounselingAuthorizationSection onChanged={() => {}} />)
    await waitFor(() => screen.getByText(/Not authorized/))
    expect(screen.getByRole('heading', { name: 'Counseling figure' })).toBeTruthy()
    expect(screen.getByText(/written authorization, the briefing shows one count/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Record the authorization' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Revoke/ })).toBeNull()
  })

  it('checks both fields before sending anything', async () => {
    const api = stubApi()
    render(<CounselingAuthorizationSection onChanged={() => {}} />)
    await waitFor(() => screen.getByText(/Not authorized/))
    fireEvent.click(screen.getByRole('button', { name: 'Record the authorization' }))
    expect(
      screen.getByText('Enter the name and title of the person who authorized it.'),
    ).toBeTruthy()
    expect(screen.getByText('Enter the reference of the written authorization.')).toBeTruthy()
    expect(api.puts()).toEqual([])
  })

  it('records the authorization as typed and reloads the findings', async () => {
    const api = stubApi()
    const onChanged = vi.fn()
    render(<CounselingAuthorizationSection onChanged={onChanged} />)
    await waitFor(() => screen.getByText(/Not authorized/))
    fireEvent.change(screen.getByLabelText('Authorized by (name and title)'), {
      target: { value: ' Dr. Example, Director of Counseling ' },
    })
    fireEvent.change(screen.getByLabelText('Document reference'), {
      target: { value: 'memo 2026-09-26' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Record the authorization' }))
    await waitFor(() =>
      screen.getByText('Authorized by Dr. Example, Director of Counseling, memo 2026-09-26.'),
    )
    expect(api.puts()).toEqual([
      {
        method: 'PUT',
        body: {
          authorized: true,
          authorized_by: 'Dr. Example, Director of Counseling',
          document_reference: 'memo 2026-09-26',
        },
      },
    ])
    expect(screen.getByText(/Recorded by admin@example.edu/)).toBeTruthy()
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('revokes behind an inline confirmation, and Cancel sends nothing', async () => {
    const api = stubApi()
    const onChanged = vi.fn()
    render(<CounselingAuthorizationSection onChanged={onChanged} />)
    await waitFor(() => screen.getByText(/Not authorized/))
    fireEvent.change(screen.getByLabelText('Authorized by (name and title)'), {
      target: { value: 'Dr. Example, Director of Counseling' },
    })
    fireEvent.change(screen.getByLabelText('Document reference'), {
      target: { value: 'memo 2026-09-26' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Record the authorization' }))
    await waitFor(() => screen.getByRole('button', { name: 'Revoke the authorization' }))

    fireEvent.click(screen.getByRole('button', { name: 'Revoke the authorization' }))
    expect(screen.getByText(/The count leaves the briefing at once/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(api.puts().length).toBe(1)

    fireEvent.click(screen.getByRole('button', { name: 'Revoke the authorization' }))
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }))
    await waitFor(() => screen.getByText(/Not authorized/))
    expect(api.puts()[1].body).toEqual({ authorized: false })
    expect(onChanged).toHaveBeenCalledTimes(2)
  })

  it('shows a refused save with its reasons and changes nothing', async () => {
    stubApi({ putStatus: 422 })
    const onChanged = vi.fn()
    render(<CounselingAuthorizationSection onChanged={onChanged} />)
    await waitFor(() => screen.getByText(/Not authorized/))
    fireEvent.change(screen.getByLabelText('Authorized by (name and title)'), {
      target: { value: 'Dr. Example, Director of Counseling' },
    })
    fireEvent.change(screen.getByLabelText('Document reference'), {
      target: { value: 'memo 2026-09-26' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Record the authorization' }))
    await waitFor(() => screen.getByText(/Nothing changed: the authorization was not recorded/))
    expect(screen.getByText('enter the reference of the written authorization')).toBeTruthy()
    expect(screen.getByText(/Not authorized/)).toBeTruthy()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('offers Retry when the authorization cannot be loaded', async () => {
    stubApi({ getStatus: 503 })
    render(<CounselingAuthorizationSection onChanged={() => {}} />)
    await waitFor(() => screen.getByText('The authorization could not be loaded'))
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})
