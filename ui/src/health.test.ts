import { describe, expect, it } from 'vitest'

import { healthMessage } from './health'

describe('healthMessage', () => {
  it('describes each state', () => {
    expect(healthMessage({ kind: 'loading' })).toContain('Checking')
    expect(healthMessage({ kind: 'ok' })).toBe('The Cabinet is running.')
    expect(healthMessage({ kind: 'error' })).toContain("couldn't reach the Cabinet")
    expect(healthMessage({ kind: 'error' })).not.toMatch(/make api|API/)
  })
})
