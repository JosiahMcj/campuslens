import { describe, expect, it } from 'vitest'

import { formatIsoDate, tidyNumbers } from './displayFormat'

describe('the house number style', () => {
  it('drops the space before a percent sign, keeping the minus sign', () => {
    expect(tidyNumbers('−4.8 %')).toBe('−4.8%')
    expect(tidyNumbers('down 2.7 % on last year')).toBe('down 2.7% on last year')
    expect(tidyNumbers('4.8%')).toBe('4.8%')
  })

  it('groups thousands, but never a year, a grouped number or a decimal', () => {
    expect(tidyNumbers('1872 credit hours')).toBe('1,872 credit hours')
    expect(tidyNumbers('12345')).toBe('12,345')
    expect(tidyNumbers('under $1,000')).toBe('under $1,000')
    expect(tidyNumbers('in 2025 and 2026')).toBe('in 2025 and 2026')
    expect(tidyNumbers('42')).toBe('42')
    expect(tidyNumbers('3.1416')).toBe('3.1416')
  })

  it('spells out ISO dates', () => {
    expect(formatIsoDate('2025-11-20')).toBe('Nov 20, 2025')
    expect(formatIsoDate('not a date')).toBe('not a date')
    expect(formatIsoDate('2025-13-01')).toBe('2025-13-01')
    expect(tidyNumbers('as of 2025-11-20, 1872 hours')).toBe('as of Nov 20, 2025, 1,872 hours')
  })

  it('leaves the missing-figure dashes alone', () => {
    expect(tidyNumbers('--')).toBe('--')
  })
})
