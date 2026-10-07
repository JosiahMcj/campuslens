// Tests for the dataset-upload client: the 422 validation errors kept
// line by line (for the folded technical detail), failures thrown as plain
// ApiErrors, the 201 summary (row counts, counseling flag), the CSRF
// header on the upload POST, and the display formatters.

import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, clearSession, setSession } from './auth'
import {
  activateDataset,
  deleteDataset,
  formatBytes,
  rowCountLabel,
  uploadDataset,
  uploadResultFrom,
} from './datasets'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const createdBody = {
  dataset: {
    id: 2,
    name: 'Fall export',
    uploaded_by: 'admin@example.edu',
    uploaded_at: '2026-09-25T12:00:00+00:00',
    sha256: 'abc',
    row_counts: { students: 185, prior_year_students: 135 },
    is_active: false,
  },
  validation: {
    row_counts: { students: 185, prior_year_students: 135 },
    counseling: 'present, will always be refused',
    fictional: false,
  },
}

afterEach(() => {
  vi.unstubAllGlobals()
  clearSession()
})

describe('uploadResultFrom — the API answer mapped onto the render branches', () => {
  it('maps a 201 to the dataset, its row counts, and the counseling flag', () => {
    const result = uploadResultFrom(201, createdBody)

    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.dataset.name).toBe('Fall export')
      expect(result.validation.row_counts).toEqual({
        students: 185,
        prior_year_students: 135,
      })
      expect(result.validation.counseling).toBe('present, will always be refused')
    }
  })

  it('lists every validation error from a 422, verbatim and in order', () => {
    const errors = [
      "$.students[3].profile.student_id: 'Jane Doe' is not a pseudonymous id",
      "$.students[4].profile.email: looks like a PII column (email)",
      '$.meta: field \'extra\' is not in the SCHEMA.md shape',
    ]
    const result = uploadResultFrom(422, {
      detail: 'the dataset failed validation',
      errors,
    })

    expect(result).toEqual({ ok: false, errors })
  })

  it('throws a plain sentence for a file over the size cap', () => {
    let thrown: unknown = null
    try {
      uploadResultFrom(413, { detail: 'request body exceeds the 20971520-byte cap' })
    } catch (error) {
      thrown = error
    }
    expect(thrown).toBeInstanceOf(ApiError)
    expect((thrown as ApiError).status).toBe(413)
    expect((thrown as ApiError).message).toBe('The file is larger than 20 MB. Nothing was saved.')
  })

  it('throws with no server text (never an HTTP code) for any other failure', () => {
    let thrown: unknown = null
    try {
      uploadResultFrom(500, { detail: 'rate limit exceeded' })
    } catch (error) {
      thrown = error
    }
    expect(thrown).toBeInstanceOf(ApiError)
    expect((thrown as ApiError).status).toBe(500)
    expect((thrown as ApiError).message).toBe('')
  })
})

describe('activate and delete — failures throw for friendlyError', () => {
  it('words the refusal to delete the active data plainly', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          detail: 'the active dataset cannot be deleted; activate another dataset first',
        }),
      ),
    )
    await expect(deleteDataset(1)).rejects.toMatchObject({
      status: 409,
      message: "The active data can't be deleted. Activate another upload first.",
    })
  })

  it('passes a network failure through untouched', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(activateDataset(1)).rejects.toBeInstanceOf(TypeError)
  })
})

describe('uploadDataset — the POST itself', () => {
  const fileBytes = new TextEncoder().encode('{"meta": {}}').buffer

  it('sends the file bytes as JSON with the session CSRF token', async () => {
    setSession({
      user: {
        id: 1,
        email: 'admin@example.edu',
        role: 'admin',
        institution_id: 1,
        institution: null,
      },
      csrfToken: 'upload-token',
    })
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, createdBody))
    vi.stubGlobal('fetch', fetchMock)

    const result = await uploadDataset(fileBytes)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/admin/datasets')
    expect(init.method).toBe('POST')
    expect((init.headers as Headers).get('Content-Type')).toBe('application/json')
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('upload-token')
    expect(result.ok).toBe(true)
  })

  it('returns the 422 error list for the line-by-line rendering', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(422, {
        detail: 'the dataset failed validation',
        errors: ['$: not a JSON document (Expecting value: line 1 column 1 (char 0))'],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await uploadDataset(fileBytes)

    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.errors).toHaveLength(1)
      expect(result.errors[0]).toContain('not a JSON document')
    }
  })
})

describe('display formatters', () => {
  it('formats file sizes', () => {
    expect(formatBytes(812)).toBe('812 B')
    expect(formatBytes(42_100)).toBe('41.1 KB')
    expect(formatBytes(1_150_000)).toBe('1.1 MB')
    expect(formatBytes(Number.NaN)).toBe('unknown size')
  })

  it('labels row counts for both student lists', () => {
    expect(rowCountLabel({ students: 185, prior_year_students: 135 })).toBe(
      '185 current students, 135 prior year',
    )
    expect(rowCountLabel({ students: 1 })).toBe('1 current student')
    expect(rowCountLabel({})).toBe('record counts not reported')
  })
})
