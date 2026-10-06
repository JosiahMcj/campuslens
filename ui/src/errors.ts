// TEMPORARY local copy so group D's worktree compiles. Group B owns
// ui/src/errors.ts; at merge, keep group B's version and drop this one.
//
// friendlyError turns any failure into one plain sentence for the screen:
// never "Failed to fetch", an HTTP code, a port, or raw server text.

import { ApiError, SessionEndedError } from './auth'

const NETWORK = "We couldn't reach the Cabinet. Check your connection and try again."
const BUSY = 'The Cabinet is busy. Wait a minute and try again.'
const OUR_SIDE = 'Something went wrong on our side. Try again in a minute.'

/** A server sentence that can go on screen as it is. */
function isPlain(detail: string): boolean {
  return /^[A-Z].*[.!?]$/.test(detail) && !/[{}[\]_<>]|HTTP|\bid\b/.test(detail)
}

export function friendlyError(error: unknown, action: string): string {
  if (error instanceof SessionEndedError) return error.message
  if (error instanceof ApiError) {
    if (error.status === 429) return BUSY
    if (error.status >= 500) return OUR_SIDE
    const detail = error.message.trim()
    if (detail !== '' && isPlain(detail)) return detail
    return `That didn't work. ${action} was not saved.`
  }
  return NETWORK
}
