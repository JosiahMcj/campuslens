// The Institution area's write failures as ApiErrors whose message is a
// plain sentence (or empty), ready for friendlyError (ui/src/errors.ts).
// The API's own detail strings are lowercase and technical ("unknown user
// id 3", "a user with email 'x' already exists"), so they never reach the
// screen as they are: a known one is replaced by its plain sentence, and
// any other becomes empty so friendlyError says what was not saved.

import { ApiError } from './auth'

/** A known server detail (matched by a fragment) and what the screen says. */
export type KnownDetails = ReadonlyArray<readonly [fragment: string, sentence: string]>

/** The ApiError for a failed status and its parsed body. */
export function failureFrom(status: number, body: unknown, known: KnownDetails = []): ApiError {
  const detail =
    typeof body === 'object' && body !== null &&
    typeof (body as Record<string, unknown>).detail === 'string'
      ? ((body as Record<string, unknown>).detail as string)
      : ''
  const match = known.find(([fragment]) => detail.includes(fragment))
  return new ApiError(status, match === undefined ? '' : match[1])
}

/** The ApiError for a failed response, with a plain sentence when known. */
export async function apiFailure(response: Response, known: KnownDetails = []): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null)
  return failureFrom(response.status, body, known)
}
