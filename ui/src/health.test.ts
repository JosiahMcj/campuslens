import { describe, expect, it } from 'vitest'

import { healthMessage } from './health'

describe('healthMessage', () => {
  it('describes each state', () => {
    expect(healthMessage({ kind: 'loading' })).toContain('Checking')
    expect(healthMessage({ kind: 'ok' })).toBe('CampusLens is running.')
    expect(healthMessage({ kind: 'error' })).toContain("couldn't reach CampusLens")
    expect(healthMessage({ kind: 'error' })).not.toMatch(/make api|API/)
  })
})
