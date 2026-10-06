// Plain names for the student-record fields (SCHEMA.md). The screen shows
// these; the raw names appear only inside a folded "Technical detail".
// Keys are normalized: list markers dropped (holds[].amount -> holds.amount)
// and the singular hold. prefix made plural, as the backend normalizes them.

export const FIELD_LABELS: Record<string, string> = {
  'profile.student_id': 'Student ID (pseudonymous)',
  'profile.program': 'Program',
  'profile.class_level': 'Class level',
  'profile.continuing': 'Continuing student',
  'enrollment.term': 'Term',
  'enrollment.registration_status': 'Registration status',
  'enrollment.registered_credit_hours': 'Registered credit hours',
  'enrollment.registration_date': 'Registration date',
  'holds.category': 'Hold type',
  'holds.amount': 'Hold amount',
  'holds.responsible_office': 'Office responsible for the hold',
  'holds.hold_date': 'Date the hold was placed',
  'holds.resolved': 'Hold resolved',
  'advising.advisor_id': 'Advisor (pseudonymous)',
  'advising.last_appointment_date': 'Last advising appointment',
  'advising.appointment_status': 'Advising appointment status',
  'comparison.prior_year_equivalent_date': 'Same date last year',
  'comparison.prior_term_status': 'Status at the same point last year',
  'comparison.baseline': 'Comparison term',
  'counseling.counseling_notes': 'Counseling notes',
  'counseling.chaplain_contact': 'Chaplain contact',
  'terms.current.registration_close_date': 'Registration closing date',
  'terms.in_session.start_date': 'Start of the current term',
  'terms.prior_year.prior_year_equivalent_date': 'Same date last year',
}

/** The raw name in the form FIELD_LABELS is keyed by. */
export function normalizeField(raw: string): string {
  const name = raw.trim().replaceAll('[]', '')
  return name.startsWith('hold.') ? `holds.${name.slice('hold.'.length)}` : name
}

/**
 * The plain name of a field. An unknown field still reads as words: its last
 * part with underscores as spaces ("advising.next_step" -> "Next step").
 */
export function fieldLabel(raw: string): string {
  const known = FIELD_LABELS[normalizeField(raw)]
  if (known !== undefined) return known
  const last = normalizeField(raw).split('.').pop() ?? raw
  const words = last.replaceAll('_', ' ').trim()
  return words.length > 0 ? words.charAt(0).toUpperCase() + words.slice(1) : raw
}

/** Plain names for a list of fields, without repeats, in their given order. */
export function fieldLabels(raw: readonly string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const field of raw) {
    const label = fieldLabel(field)
    if (!seen.has(label)) {
      seen.add(label)
      out.push(label)
    }
  }
  return out
}
