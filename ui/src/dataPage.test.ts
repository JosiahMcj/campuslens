import { describe, expect, it } from 'vitest'

import { canSeeDataPage, formatTick, formatValue, niceTicks, seriesQuery, validChoices, valueDomain, type DataCatalog } from './dataPage'

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

  it('starts counts, rates and bars at zero and keeps averages from looking steep', () => {
    expect(valueDomain([12000, 16000], 'count', 'line')[0]).toBe(0)
    expect(valueDomain([80, 82], 'pct', 'bar')[0]).toBe(0)
    // Rates start at 0% too; averages fit the data with a minimum span.
    expect(valueDomain([81, 82], 'pct', 'line')[0]).toBe(0)
    const [lo, hi] = valueDomain([3.04, 3.08], 'gpa', 'line')
    expect(lo).toBeGreaterThan(0)
    expect(hi - lo).toBeGreaterThanOrEqual(0.5)
    expect(hi).toBeLessThanOrEqual(4)
    expect(niceTicks(0, 100)).toEqual([0, 25, 50, 75, 100])
  })

  it('drops a saved comparison by an attribute that is also a filter', () => {
    const catalog = {
      dashboards: [{ id: 'students', title: 'Students', intro: '', charts: [] }],
      filters: [{ key: 'gender', label: 'Gender', options: [{ value: 'male', label: 'Men' }] }],
      compare: [{ key: 'gender', label: 'Gender' }],
      years: ['2020-2021'],
    } as unknown as DataCatalog
    const out = validChoices({ dashboard: 'x', from: '', to: '', compare: 'gender', filters: { gender: 'male' } }, catalog)
    expect(out).toEqual({ dashboard: 'students', from: '', to: '', compare: '', filters: { gender: 'male' } })
  })
})
