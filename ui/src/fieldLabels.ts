// Plain names for the student-record fields (SCHEMA.md) and the school records
// Explore reads (data/school/). The screen shows these; the raw names appear
// only inside a folded "Technical detail".
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
  // Demonstration University's school records (Explore's "fields read").
  'academic_periods.academic_year': 'Academic year',
  'academic_periods.season': 'Season (fall, spring, summer)',
  'academic_programs.college_code': 'College',
  'academic_programs.major_code': 'Major',
  'academic_programs.name': 'Major name',
  'academic_standings.standing': 'Academic standing',
  'academic_standings.term_code': 'Term of the standing',
  'colleges.name': 'College name',
  'courses.course_level': 'Course level',
  'courses.grade_mode': 'Grading mode',
  'courses.subject_code': 'Subject',
  'courses.title': 'Course title',
  'final_grades.grade': 'Final grade',
  'instructors.academic_rank': 'Academic rank',
  'instructors.first_name': 'Instructor first name (fictional)',
  'instructors.last_name': 'Instructor last name (fictional)',
  'person_holds.amount': 'Hold amount',
  'person_holds.category': 'Hold type',
  'person_holds.end_date': 'Date the hold ended',
  'person_holds.responsible_office': 'Office responsible for the hold',
  'person_holds.term_code': 'Term the hold was placed',
  'program_requirements.course_id': 'Courses each major requires',
  'section_instructors.instructor_id': 'Instructor of record',
  'section_instructors.instructor_id (counted)': 'Number of instructors (counted, not named)',
  'section_registrations.section_id': 'Section registered in',
  'section_registrations.student_id (counted)': 'Number of students (counted, not named)',
  'section_registrations.term_code': 'Term registered',
  'sections.course_id': 'Course of each section',
  'sections.modality': 'Teaching mode',
  'sections.term_code': 'Term of each section',
  'student_academic_programs.end_term': 'Last term in the major',
  'student_academic_programs.program_code': 'Major',
  'student_academic_programs.status': 'Status in the major',
  'student_appointments.status': 'Advising appointment status',
  'student_appointments.term_code': 'Term of the advising appointment',
  'student_term_records.attempted_hours': 'Credit hours attempted',
  'student_term_records.cumulative_gpa': 'Cumulative GPA',
  'student_term_records.earned_hours': 'Credit hours earned',
  'student_term_records.program_code': 'Major each term',
  'student_term_records.term_code': 'Term',
  'students.entry_term': 'Entry term',
  'students.entry_type': 'Entry type',
  'students.first_generation': 'First-generation student',
  'students.pell_recipient': 'Pell recipient',
  'students.residency': 'Residency',
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
