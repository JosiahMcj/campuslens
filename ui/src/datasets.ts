// Typed client for the institution-admin dataset routes (GET/POST
// /api/admin/datasets, activate, soft delete), plus the pure mappers the
// Institution area renders. Upload validation errors come back from the API
// as a list of sentences and are rendered line by line, verbatim.

import { ApiError, apiDetail, apiFetch } from './auth'

export interface DatasetRow {
  id: number
  name: string
  uploaded_by: string
  uploaded_at: string
  sha256: string
  row_counts: Record<string, number>
  is_active: boolean
}

export interface UploadValidation {
  row_counts: Record<string, number>
  /** "present, will always be refused" or "absent", from the API. */
  counseling: string
  fictional: boolean
}

export type UploadResult =
  | { ok: true; dataset: DatasetRow; validation: UploadValidation }
  | { ok: false; errors: string[] }

export type ActionResult = { ok: true } | { ok: false; message: string }

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : []
}

function datasetFrom(value: unknown): DatasetRow | null {
  if (typeof value !== 'object' || value === null) return null
  const record = value as Record<string, unknown>
  if (typeof record.id !== 'number' || typeof record.name !== 'string') return null
  const rowCounts =
    typeof record.row_counts === 'object' && record.row_counts !== null
      ? (record.row_counts as Record<string, unknown>)
      : {}
  const counts: Record<string, number> = {}
  for (const [key, count] of Object.entries(rowCounts)) {
    if (typeof count === 'number') counts[key] = count
  }
  return {
    id: record.id,
    name: record.name,
    uploaded_by: typeof record.uploaded_by === 'string' ? record.uploaded_by : '',
    uploaded_at: typeof record.uploaded_at === 'string' ? record.uploaded_at : '',
    sha256: typeof record.sha256 === 'string' ? record.sha256 : '',
    row_counts: counts,
    is_active: record.is_active === true,
  }
}

/** Map an upload response onto the two render branches; exported for tests. */
export function uploadResultFrom(status: number, body: unknown): UploadResult {
  const record = typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {}
  if (status === 201) {
    const dataset = datasetFrom(record.dataset)
    const validation =
      typeof record.validation === 'object' && record.validation !== null
        ? (record.validation as Record<string, unknown>)
        : {}
    if (dataset !== null) {
      const rowCounts =
        typeof validation.row_counts === 'object' && validation.row_counts !== null
          ? (validation.row_counts as Record<string, unknown>)
          : {}
      const counts: Record<string, number> = {}
      for (const [key, count] of Object.entries(rowCounts)) {
        if (typeof count === 'number') counts[key] = count
      }
      return {
        ok: true,
        dataset,
        validation: {
          row_counts: counts,
          counseling: typeof validation.counseling === 'string' ? validation.counseling : 'absent',
          fictional: validation.fictional === true,
        },
      }
    }
  }
  const errors = stringList(record.errors)
  if (errors.length > 0) return { ok: false, errors }
  const detail = typeof record.detail === 'string' ? record.detail : null
  return {
    ok: false,
    errors: [detail ?? `The upload did not work (HTTP ${status}). Try again.`],
  }
}

export async function fetchDatasets(): Promise<DatasetRow[]> {
  const response = await apiFetch('/admin/datasets')
  if (!response.ok) {
    throw new ApiError(
      response.status,
      await apiDetail(response, `The dataset list failed to load (HTTP ${response.status}).`),
    )
  }
  const body: unknown = await response.json().catch(() => null)
  const list =
    typeof body === 'object' && body !== null
      ? (body as Record<string, unknown>).datasets
      : null
  if (!Array.isArray(list)) return []
  return list
    .map(datasetFrom)
    .filter((dataset): dataset is DatasetRow => dataset !== null)
}

/**
 * POST /api/admin/datasets with the file's bytes as the JSON body (the API
 * validates the document before anything is stored and answers 422 with
 * every problem listed, or 201 with the row counts and the counseling flag).
 */
export async function uploadDataset(bytes: ArrayBuffer): Promise<UploadResult> {
  const response = await apiFetch('/admin/datasets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: bytes,
  })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok && response.status !== 422) {
    const detail =
      typeof body === 'object' && body !== null
        ? (body as Record<string, unknown>).detail
        : null
    return {
      ok: false,
      errors: [
        typeof detail === 'string'
          ? detail
          : `The upload did not work (HTTP ${response.status}).`,
      ],
    }
  }
  return uploadResultFrom(response.status, body)
}

/** POST /api/admin/datasets/<id>/activate — the briefing recomputes. */
export async function activateDataset(id: number): Promise<ActionResult> {
  const response = await apiFetch(`/admin/datasets/${id}/activate`, { method: 'POST' })
  if (!response.ok) {
    return {
      ok: false,
      message: await apiDetail(response, `The dataset could not be activated (HTTP ${response.status}).`),
    }
  }
  return { ok: true }
}

/** DELETE /api/admin/datasets/<id> — a soft delete inside the retention window. */
export async function deleteDataset(id: number): Promise<ActionResult> {
  const response = await apiFetch(`/admin/datasets/${id}`, { method: 'DELETE' })
  if (!response.ok) {
    return {
      ok: false,
      message: await apiDetail(response, `The dataset could not be deleted (HTTP ${response.status}).`),
    }
  }
  return { ok: true }
}

/** A file size for the upload panel: "812 B", "41.2 KB", "1.1 MB". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return 'unknown size'
  if (bytes < 1024) return `${bytes} B`
  const kb = bytes / 1024
  if (kb < 1024) return `${kb.toFixed(1)} KB`
  return `${(kb / 1024).toFixed(1)} MB`
}

/** The row counts line for a dataset: "185 current students, 135 prior year". */
export function rowCountLabel(rowCounts: Record<string, number>): string {
  const current = rowCounts.students
  const prior = rowCounts.prior_year_students
  const parts: string[] = []
  if (typeof current === 'number') {
    parts.push(`${current} current student${current === 1 ? '' : 's'}`)
  }
  if (typeof prior === 'number') {
    parts.push(`${prior} prior year`)
  }
  return parts.length > 0 ? parts.join(', ') : 'row counts not reported'
}
