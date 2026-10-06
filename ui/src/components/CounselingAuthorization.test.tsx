// @vitest-environment jsdom

// Institution settings, the counseling figure: the off state with its form,
// validation under the field with nothing sent, recording (the PUT body,
// the authorized state, the findings reload), revoking behind an inline
// confirmation, Cancel, a refused save, and a failed load with Retry. The
// API is a mocked fetch reached through the real ui/src/counseling.ts client.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CounselingAuthorizationSection } from './CounselingAuthorization'

// friendlyError belongs to ui/src/errors.ts (group B); stubbed here so the
// tests check that every failure goes through it, not its exact wording.
vi.mock('../errors', () => ({
  friendlyError: (_error: unknown, action: string) => `Friendly: ${action}`,
}))

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

function stubApi(
  options: { getStatus?: number; putStatus?: number; putRejects?: boolean } = {},
) {
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
      if (options.putRejects === true) throw new TypeError('Failed to fetch')
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
    await waitFor(() => screen.getByText('Nothing changed. Check both fields and try again.'))
    // The server's own lines sit folded under "Technical detail".
    const line = screen.getByText('enter the reference of the written authorization')
    expect(line.closest('details')!.querySelector('summary')!.textContent).toBe(
      'Technical detail',
    )
    expect(screen.queryByText(/the authorization was not recorded/)).toBeNull()
    expect(screen.getByText(/Not authorized/)).toBeTruthy()
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('a network failure clears the busy button and says so under it', async () => {
    stubApi({ putRejects: true })
    render(<CounselingAuthorizationSection onChanged={() => {}} />)
    await waitFor(() => screen.getByText(/Not authorized/))
    fireEvent.change(screen.getByLabelText('Authorized by (name and title)'), {
      target: { value: 'Dr. Example' },
    })
    fireEvent.change(screen.getByLabelText('Document reference'), {
      target: { value: 'memo' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Record the authorization' }))
    await waitFor(() => screen.getByText('Friendly: The authorization'))
    const button = screen.getByRole('button', { name: 'Record the authorization' })
    expect((button as HTMLButtonElement).disabled).toBe(false)
    expect(document.body.textContent).not.toContain('Failed to fetch')
  })

  it('makes Record the main button only once both fields are filled', async () => {
    stubApi()
    render(<CounselingAuthorizationSection onChanged={() => {}} />)
    await waitFor(() => screen.getByText(/Not authorized/))
    const button = screen.getByRole('button', { name: 'Record the authorization' })
    expect(button.className).toBe('btn-secondary')
    fireEvent.change(screen.getByLabelText('Authorized by (name and title)'), {
      target: { value: 'Dr. Example' },
    })
    expect(button.className).toBe('btn-secondary')
    fireEvent.change(screen.getByLabelText('Document reference'), {
      target: { value: 'memo' },
    })
    expect(button.className).toBe('btn-primary')
    expect(
      (screen.getByLabelText('Document reference') as HTMLInputElement).placeholder,
    ).toMatch(/^e\.g\. /)
  })

  it('moves focus into the revoke confirmation, and Escape cancels it', async () => {
    const api = stubApi()
    render(<CounselingAuthorizationSection onChanged={() => {}} />)
    await waitFor(() => screen.getByText(/Not authorized/))
    fireEvent.change(screen.getByLabelText('Authorized by (name and title)'), {
      target: { value: 'Dr. Example' },
    })
    fireEvent.change(screen.getByLabelText('Document reference'), {
      target: { value: 'memo' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Record the authorization' }))
    await waitFor(() => screen.getByText('Recorded. The briefing now shows the counseling count. The audit log records the change.'))
    fireEvent.click(screen.getByRole('button', { name: 'Revoke the authorization' }))
    const dialog = screen.getByRole('alertdialog')
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(document.activeElement).toBe(
      screen.getByRole('button', { name: 'Revoke the authorization' }),
    )
    expect(api.puts().length).toBe(1)
  })

  it('offers Retry when the authorization cannot be loaded', async () => {
    stubApi({ getStatus: 503 })
    render(<CounselingAuthorizationSection onChanged={() => {}} />)
    await waitFor(() => screen.getByText('We couldn’t load the authorization'))
    expect(screen.getByText('Friendly: The authorization')).toBeTruthy()
    expect(screen.queryByText(/unavailable/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})
