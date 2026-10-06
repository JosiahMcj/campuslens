import { describe, expect, it } from 'vitest'

import { fieldLabel, fieldLabels, normalizeField } from './fieldLabels'

describe('field labels', () => {
  it('names the record fields in plain words', () => {
    expect(fieldLabel('enrollment.registration_status')).toBe('Registration status')
    expect(fieldLabel('holds[].amount')).toBe('Hold amount')
    expect(fieldLabel('holds.amount')).toBe('Hold amount')
    expect(fieldLabel('hold.responsible_office')).toBe('Office responsible for the hold')
    expect(fieldLabel('counseling.chaplain_contact')).toBe('Chaplain contact')
    expect(fieldLabel('terms.in_session.start_date')).toBe('Start of the current term')
  })

  it('reads an unknown field as words, never the raw name', () => {
    expect(fieldLabel('advising.next_step')).toBe('Next step')
  })

  it('normalizes list markers and the singular hold prefix', () => {
    expect(normalizeField('holds[].category')).toBe('holds.category')
    expect(normalizeField('hold.amount')).toBe('holds.amount')
  })

  it('drops repeats that share a label, keeping order', () => {
    expect(
      fieldLabels([
        'comparison.prior_year_equivalent_date',
        'terms.prior_year.prior_year_equivalent_date',
        'holds.amount',
        'holds[].amount',
      ]),
    ).toEqual(['Same date last year', 'Hold amount'])
  })

  it('has a label for every field the AI employees are granted', () => {
    const granted = [
      'profile.continuing',
      'enrollment.term',
      'enrollment.registration_status',
      'enrollment.registered_credit_hours',
      'enrollment.registration_date',
      'comparison.prior_year_equivalent_date',
      'comparison.baseline',
      'holds.category',
      'holds.amount',
      'holds.responsible_office',
      'holds.hold_date',
      'holds.resolved',
      'advising.advisor_id',
      'advising.last_appointment_date',
      'advising.appointment_status',
      'terms.current.registration_close_date',
    ]
    for (const field of granted) {
      expect(fieldLabel(field)).not.toContain('_')
      expect(fieldLabel(field)).not.toContain('.')
    }
  })
})
