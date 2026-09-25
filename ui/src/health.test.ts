import { describe, expect, it } from 'vitest'

import { healthMessage } from './health'

describe('healthMessage', () => {
  it('describes each state', () => {
    expect(healthMessage({ kind: 'loading' })).toContain('Checking')
    expect(healthMessage({ kind: 'ok' })).toBe('API healthy')
    expect(healthMessage({ kind: 'error' })).toContain('unreachable')
  })
})
