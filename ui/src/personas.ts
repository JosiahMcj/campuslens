// Who each sign-in is, for the home screen: the persona's name, one line on
// what CampusLens does for that office, and three questions worth asking
// first. The questions are plain Explore questions; each one was checked to
// answer from Demonstration University's records (the rule planner maps
// every one of them, so they answer even without the model). docs/ROLES.md
// has the full table of who sees what.

import type { Role } from './auth'

export interface Persona {
  /** The greeting: the office the person works in. */
  name: string
  /** One sentence under the greeting (roles that do not run the briefing). */
  lede: string
  /** Three questions for the "Try" row; empty keeps the catalog's examples
   * (the president) or means the role does not ask. */
  examples: string[]
}

const REGISTRATION = 'How much did continuing spring registration change in Spring 2026?'
const HOLDS = 'Which offices hold the most active holds?'
const RETENTION_FIRST_GEN = 'What is first-year retention by first-generation status?'

const PERSONAS: Partial<Record<Role, Persona>> = {
  // The president keeps the briefing home screen (the approved question
  // cards and the catalog's Try row, which carries the owner's example);
  // the persona adds the greeting only.
  executive: {
    name: 'President',
    lede: "Every page and every department's overview. Ask anything about Demonstration University, and send an alert to any office before the meeting.",
    examples: [],
  },
  finance: {
    name: 'Finance',
    lede: "The university's budget against actual, revenue and the tuition discount rate. The university's own books, in totals.",
    examples: [
      'What is our budget vs actual this year?',
      'Where does our money come from?',
      'What is our tuition discount rate trend?',
    ],
  },
  studentaccounts: {
    name: 'Student Accounts',
    lede: 'Student balances, account holds, past-due amounts and payment plans, campus wide and in aggregate only.',
    examples: [
      HOLDS,
      'How much is past due?',
      'How many students are on payment plans?',
    ],
  },
  registrar: {
    name: 'Registrar',
    lede: 'Registration, enrollment by level and academic standing, campus wide and in aggregate only.',
    examples: [
      REGISTRATION,
      'Which major grew fastest from Fall 2020 to Fall 2025?',
      'Which majors have the highest probation rates?',
    ],
  },
  studentlife: {
    name: 'Student Life',
    lede: 'Retention, housing and advising, campus wide and in aggregate only.',
    examples: [
      RETENTION_FIRST_GEN,
      'What majors have the highest dropout rate?',
      'What is the average GPA of athletes vs non-athletes?',
    ],
  },
  admissions: {
    name: 'Admissions',
    lede: 'Entering classes by admit type, residency and college, year over year, in aggregate only.',
    examples: [
      'How many new first-time students entered each fall?',
      'What is first-year retention by residency?',
      'What is the enrollment trend by year?',
    ],
  },
  advising: {
    name: 'Advising and Student Success',
    lede: 'Advising coverage, appointments, changes of major and who leaves without a degree, in aggregate only.',
    examples: [
      'How many students have advising holds?',
      'How many students changed major?',
      'What is first-year retention by first-generation status?',
    ],
  },
  provost: {
    name: 'Academic Affairs',
    lede: 'D, F and withdrawal rates, the hardest courses, section sizes and instructors, in aggregate only.',
    examples: [
      'Which courses have the highest DFW rates?',
      'What is the DFW rate by college?',
      'Which majors have the highest probation rates?',
    ],
  },
  ir: {
    name: 'Institutional Research',
    lede: 'Enrollment, retention and degrees awarded over the years, in aggregate only.',
    examples: [
      'What is the enrollment trend by year?',
      'What is first-year retention by college?',
      'What is the graduation rate by college?',
    ],
  },
  careers: {
    name: 'Career Services',
    lede: 'What graduates did first, starting salaries and graduate school, in aggregate only.',
    examples: [
      'What is the median starting salary by college?',
      'What share of graduates are employed full time by college?',
      'What share of graduates went to medical school?',
    ],
  },
  advancement: {
    name: 'Advancement',
    lede: 'Alumni giving participation and gifts by college and year, in aggregate only.',
    examples: [
      'How much have alumni given by fiscal year?',
      'How many alumni have donated by college?',
      'How much have alumni given by designation?',
    ],
  },
  international: {
    name: 'International Student Services',
    lede: 'International students by college and term, in aggregate only.',
    examples: [
      'How many international students are there by college?',
      'How many international students are there by term?',
      'What is first-year retention by residency?',
    ],
  },
  athletics: {
    name: 'Athletics',
    lede: 'Athletes compared with other students: enrollment, GPA, standing and retention, in aggregate only.',
    examples: [
      'What is the average GPA of athletes vs non-athletes?',
      'What is first-year retention for athletes?',
      'How many athletes are on academic probation?',
    ],
  },
  aid: {
    name: 'Financial Aid',
    lede: 'The review queue for the students the briefing flags, and alerts sent to your office.',
    examples: [],
  },
  it: {
    name: 'IT',
    lede: 'Accounts, sign-in activity, the outside connections and the audit log. Nothing here is about students.',
    examples: [],
  },
}

/** The persona for a role, or null for roles that keep the general home
 * screen (admin, staff, reviewer). */
export function personaFor(role: Role): Persona | null {
  return PERSONAS[role] ?? null
}
