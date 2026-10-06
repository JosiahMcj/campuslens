// One place that turns any failure into a sentence a non-technical reader
// can act on. Every panel, button and screen shows errors through
// friendlyError; nothing on screen ever carries "Failed to fetch", an HTTP
// code, a port, a command or raw server text.

import { ApiError, LoginError, SessionEndedError } from './auth'

export const NETWORK_MESSAGE =
  "We couldn't reach the Cabinet. Check your connection and try again."
export const BUSY_MESSAGE = 'The Cabinet is busy. Wait a minute and try again.'
export const SERVER_MESSAGE = 'Something went wrong on our side. Try again in a minute.'

/** Words and marks that never belong in a sentence shown to a reader. */
const TECHNICAL =
  /\bHTTP\b|\bAPI\b|\bJSON\b|payload|make api|localhost|127\.0\.0\.1|traceback|exception|failed to fetch|networkerror|content-type|\bbytes?\b|-byte\b|\bids?\b|\bnull\b|\bundefined\b|[{}[\]<>`_\\]|\/[a-z]|\b(GET|POST|PUT|PATCH|DELETE)\b/i

/** A bare HTTP reason phrase ("Not Found") is not an explanation. */
const REASON_PHRASE =
  /^(not found|method not allowed|forbidden|unauthori[sz]ed|bad request|unprocessable (entity|content)|conflict|gone|(request entity|content|payload) too large|unsupported media type|too many requests|internal server error|service unavailable|bad gateway|gateway timeout)\.?$/i

/** An identifier: a word mixing letters and digits ("dec-2026-04", "M1", "v2"). */
const CODE_LIKE = /\b[A-Za-z]+-?\d|\d+-[A-Za-z]|\b\d+-\d+-\d+\b/

/**
 * A server's detail as a sentence fit to show, or null when it is not.
 * The API writes its details in lower case without a full stop ("an
 * administrator cannot disable their own account"); those are plain words,
 * so the first letter is capitalised and a full stop added.
 */
export function plainSentence(text: string): string | null {
  const trimmed = text.trim().replace(/\s+/g, ' ')
  if (trimmed.length === 0 || trimmed.length > 240) return null
  if (TECHNICAL.test(trimmed)) return null
  if (REASON_PHRASE.test(trimmed)) return null
  if (CODE_LIKE.test(trimmed)) return null
  if (!/^["']?[A-Za-z]/.test(trimmed)) return null
  const capitalised = trimmed.charAt(0).toUpperCase() + trimmed.slice(1)
  return /[.?!]["']?$/.test(capitalised) ? capitalised : `${capitalised}.`
}

/** True when a server's detail can be shown (after plainSentence's tidy-up). */
export function isPlainSentence(text: string): boolean {
  return plainSentence(text) !== null
}

function isNetworkFailure(error: unknown): boolean {
  if (error instanceof TypeError) return true
  if (error instanceof DOMException && error.name === 'NetworkError') return true
  return false
}

/**
 * The sentence to show for a failed action. `action` names what the person
 * was doing, as a short noun phrase that starts with a capital letter
 * ("The office mailbox", "Your approval"); it is used when the server's own
 * detail is not fit to show: "That didn't work. <action> was not saved."
 */
export function friendlyError(error: unknown, action: string): string {
  if (error instanceof SessionEndedError) return error.message
  if (error instanceof LoginError) return plainSentence(error.message) ?? SERVER_MESSAGE
  if (error instanceof ApiError) {
    if (error.status === 429) return BUSY_MESSAGE
    if (error.status >= 500) return SERVER_MESSAGE
    if (error.status >= 400) {
      return plainSentence(error.message) ?? `That didn't work. ${action} was not saved.`
    }
    return SERVER_MESSAGE
  }
  if (isNetworkFailure(error)) return NETWORK_MESSAGE
  return SERVER_MESSAGE
}

/** Seconds to wait before retrying a 429, from the error (default 60, capped at 300). */
export function retryAfterSeconds(error: unknown): number {
  if (error instanceof ApiError && typeof error.retryAfter === 'number') {
    return Math.min(300, Math.max(1, error.retryAfter))
  }
  return 60
}

/** True for the "too many requests" answer. */
export function isRateLimited(error: unknown): boolean {
  return error instanceof ApiError && error.status === 429
}

/**
 * The reason a LOAD failed, to follow a "Couldn't load …" line: the same
 * sentences as friendlyError, except a technical 4xx reads "Try again in a
 * minute." (nothing was being saved, so "was not saved" would be wrong).
 */
export function friendlyLoadError(error: unknown): string {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 429) {
    return plainSentence(error.message) ?? 'Try again in a minute.'
  }
  return friendlyError(error, 'This')
}
