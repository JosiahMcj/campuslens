// LOCAL STUB for group C's worktree only. Group B owns ui/src/errors.ts; on
// merge, keep group B's version. This copy matches the exports group B
// announced (COMMON.md signature plus the helpers) so group C compiles.

import { ApiError } from './auth'

export const NETWORK_MESSAGE = "We couldn't reach the Cabinet. Check your connection and try again."
export const BUSY_MESSAGE = 'The Cabinet is busy. Wait a minute and try again.'
export const SERVER_MESSAGE = 'Something went wrong on our side. Try again in a minute.'

/** True for text a non-technical reader can be shown as is. */
export function isPlainSentence(text: string): boolean {
  const trimmed = text.trim()
  if (trimmed.length === 0 || trimmed.length > 240) return false
  if (/HTTP|\bAPI\b|JSON|payload|make api|127\.0\.0\.1|localhost|Failed to fetch/i.test(trimmed)) {
    return false
  }
  // Field names, paths, ids, quoted codes, braces: technical.
  if (/[_{}[\]<>]|\/\w|\b\w+\.\w+\b|'[a-z_]+'/.test(trimmed)) return false
  return /^[A-Z]/.test(trimmed)
}

/** The text when it is plain, otherwise null. */
export function plainSentence(text: string | null | undefined): string | null {
  if (typeof text !== 'string') return null
  return isPlainSentence(text) ? text.trim() : null
}

function statusOf(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null
}

export function isRateLimited(error: unknown): boolean {
  return statusOf(error) === 429
}

export function retryAfterSeconds(error: unknown): number | null {
  const value = (error as { retryAfter?: unknown } | null)?.retryAfter
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** One plain sentence for any failure. `action` names what was being done. */
export function friendlyError(error: unknown, action: string): string {
  const status = statusOf(error)
  if (status === null) {
    if (error instanceof TypeError) return NETWORK_MESSAGE
    const message = error instanceof Error ? error.message : String(error)
    return plainSentence(message) ?? NETWORK_MESSAGE
  }
  if (status === 429) return BUSY_MESSAGE
  if (status >= 500) return SERVER_MESSAGE
  const detail = plainSentence(error instanceof Error ? error.message : null)
  return detail ?? `That didn't work. ${action} was not saved.`
}
