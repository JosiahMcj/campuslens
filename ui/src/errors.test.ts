import { describe, expect, it } from 'vitest'
import { ApiError, LoginError, SessionEndedError } from './auth'
import {
  BUSY_MESSAGE,
  NETWORK_MESSAGE,
  SERVER_MESSAGE,
  friendlyError,
  friendlyLoadError,
  isPlainSentence,
  plainSentence,
  isRateLimited,
  retryAfterSeconds,
} from './errors'

describe('friendlyError', () => {
  it('turns a network failure into the connection sentence', () => {
    expect(friendlyError(new TypeError('Failed to fetch'), 'Your question')).toBe(
      NETWORK_MESSAGE,
    )
  })

  it('turns 429 into the busy sentence', () => {
    expect(friendlyError(new ApiError(429, 'Too Many Requests'), 'x')).toBe(BUSY_MESSAGE)
  })

  it('turns any 5xx into the server sentence', () => {
    expect(friendlyError(new ApiError(500, 'Internal Server Error'), 'x')).toBe(SERVER_MESSAGE)
    expect(friendlyError(new ApiError(503, 'The model is down.'), 'x')).toBe(SERVER_MESSAGE)
  })

  it('shows a plain 4xx detail as it is', () => {
    const plain = 'That email address is already used by another person.'
    expect(friendlyError(new ApiError(409, plain), 'The person')).toBe(plain)
    expect(
      friendlyError(new ApiError(409, 'this message was already sent; it will not be sent twice'), 'x'),
    ).toBe('This message was already sent; it will not be sent twice.')
  })

  it('replaces a technical 4xx detail with the action sentence', () => {
    expect(
      friendlyError(new ApiError(404, 'GET /decisions failed: HTTP 404'), 'The office mailbox'),
    ).toBe("That didn't work. The office mailbox was not saved.")
    expect(friendlyError(new ApiError(422, "field 'office_id' required"), 'The office')).toBe(
      "That didn't work. The office was not saved.",
    )
  })

  it('never shows raw text from an unknown error', () => {
    const shown = friendlyError(new Error('Traceback: KeyError M1'), 'x')
    expect(shown).toBe(SERVER_MESSAGE)
  })

  it('keeps the session-ended and plain sign-in sentences', () => {
    expect(friendlyError(new SessionEndedError(), 'x')).toBe('Your session ended. Sign in again.')
    expect(friendlyError(new LoginError('Too many sign in attempts. Try again in about 15 minutes.'), 'x')).toBe(
      'Too many sign in attempts. Try again in about 15 minutes.',
    )
  })

  it('never returns HTTP codes, ports, or make api', () => {
    const cases: unknown[] = [
      new TypeError('Failed to fetch'),
      new ApiError(400, 'Check that `make api` is running on 127.0.0.1:8910'),
      new ApiError(418, 'HTTP 418'),
      new ApiError(200, 'The session check answer was not understood.'),
      'a string',
      null,
    ]
    for (const error of cases) {
      const shown = friendlyError(error, 'Your change')
      expect(shown).not.toMatch(/HTTP|\d{3}|make api|8910|Failed to fetch/)
    }
  })
})

describe('isPlainSentence', () => {
  it('tidies lower-case server details into sentences', () => {
    expect(plainSentence('an administrator cannot disable their own account')).toBe(
      'An administrator cannot disable their own account.',
    )
    expect(plainSentence('Upload is limited to 500 records.')).toBe(
      'Upload is limited to 500 records.',
    )
    expect(plainSentence('Try again after 10:30')).toBe('Try again after 10:30.')
    expect(plainSentence('rate limit exceeded')).toBe('Rate limit exceeded.')
    expect(plainSentence('unknown user id 7')).toBeNull()
    expect(plainSentence('request body exceeds the 1048576-byte cap')).toBeNull()
    expect(plainSentence('expected Content-Type: application/json')).toBeNull()
    expect(plainSentence('Not Found')).toBeNull()
    expect(plainSentence('Method Not Allowed')).toBeNull()
    expect(plainSentence('Forbidden')).toBeNull()
    expect(plainSentence('decision dec-2026-04 not found')).toBeNull()
    expect(plainSentence('finding M1 is closed')).toBeNull()
  })

  it('accepts a plain sentence and rejects technical text', () => {
    expect(isPlainSentence('That office already exists.')).toBe(true)
    expect(isPlainSentence('{"detail": "x"}')).toBe(false)
    expect(isPlainSentence('enrollment.registration_status missing')).toBe(false)
    expect(isPlainSentence('')).toBe(false)
  })
})

describe('retry helpers', () => {
  it('reads Retry-After from a 429 and caps it', () => {
    expect(isRateLimited(new ApiError(429, 'busy', 7))).toBe(true)
    expect(retryAfterSeconds(new ApiError(429, 'busy', 7))).toBe(7)
    expect(retryAfterSeconds(new ApiError(429, 'busy', 9999))).toBe(300)
    expect(retryAfterSeconds(new ApiError(429, 'busy'))).toBe(60)
    expect(isRateLimited(new ApiError(500, 'x'))).toBe(false)
  })
})

describe('friendlyLoadError', () => {
  it('never says "was not saved" for a failed load', () => {
    expect(friendlyLoadError(new ApiError(404, 'GET /events failed: HTTP 404'))).toBe(
      'Try again in a minute.',
    )
    expect(friendlyLoadError(new TypeError('Failed to fetch'))).toBe(NETWORK_MESSAGE)
    expect(friendlyLoadError(new ApiError(429, 'rate limit exceeded'))).toBe(BUSY_MESSAGE)
    expect(friendlyLoadError(new ApiError(502, 'Bad Gateway'))).toBe(SERVER_MESSAGE)
  })
})
