import { describe, expect, it } from 'vitest'

import { canSeeDataPage, formatTick, formatValue, niceTicks, seriesQuery, valueDomain } from './dataPage'

describe('dataPage helpers', () => {
  it('gates the page by the same roles as the API', () => {
    expect(['executive', 'aid', 'staff', 'reviewer'].every(canSeeDataPage)).toBe(true)
    expect(canSeeDataPage('admin')).toBe(false)
    expect(canSeeDataPage(null)).toBe(false)
  })

  it('builds a stable query: chart, comparison, then filters in key order', () => {
    expect(seriesQuery('dfw', 'gender', { pell: 'pell', college: 'COE', major: '' })).toBe(
      'chart=dfw&compare=gender&college=COE&pell=pell',
    )
  })

  it('formats values the way DESIGN.md says numbers read', () => {
    expect(formatValue(14589, 'count')).toBe('14,589')
    expect(formatValue(81.3, 'pct')).toBe('81.3%')
    expect(formatValue(3.0712, 'gpa')).toBe('3.07')
    expect(formatValue(1262867.53, 'dollars')).toBe('$1,262,868')
    expect(formatTick(1500000, 'dollars')).toBe('$1.5M')
    expect(formatTick(15000, 'count')).toBe('15k')
  })

  it('starts counts and bars at zero and keeps rates from looking steep', () => {
    expect(valueDomain([12000, 16000], 'count', 'line')[0]).toBe(0)
    expect(valueDomain([80, 82], 'pct', 'bar')[0]).toBe(0)
    const [lo, hi] = valueDomain([81, 82], 'pct', 'line')
    expect(hi - lo).toBeGreaterThanOrEqual(10)
    expect(valueDomain([97, 99], 'pct', 'line')[1]).toBeLessThanOrEqual(100)
    expect(niceTicks(0, 100)).toEqual([0, 25, 50, 75, 100])
  })
})
