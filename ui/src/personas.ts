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
  /** The office's own AI employee(s) (cabinet.staff.LOGIN_EMPLOYEES in the
   * API), named on a department's home screen. */
  employees: string[]
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
    employees: ['Chief of Staff'],
  },
  finance: {
    name: 'Finance — Student Accounts',
    lede: 'Balances, account holds and the students they block, campus wide and in aggregate only.',
    examples: [
      HOLDS,
      'What is the 6-year graduation rate for Pell students by college?',
      REGISTRATION,
    ],
    employees: ['Student Accounts Analyst', 'Finance & Budget Analyst'],
  },
  registrar: {
    name: 'Registrar',
    lede: 'Registration, enrollment by level and academic standing, campus wide and in aggregate only.',
    examples: [
      REGISTRATION,
      'Which major grew fastest from Fall 2020 to Fall 2025?',
      'Which majors have the highest probation rates?',
    ],
    employees: ['Registrar Analyst'],
  },
  studentlife: {
    name: 'Student Life',
    lede: 'Retention, housing and advising, campus wide and in aggregate only.',
    examples: [
      RETENTION_FIRST_GEN,
      'What majors have the highest dropout rate?',
      'What is the average GPA of athletes vs non-athletes?',
    ],
    employees: ['Student Life Analyst', 'Advising Analyst'],
  },
  aid: {
    name: 'Financial Aid',
    lede: 'The review queue for the students the briefing flags, and alerts sent to your office.',
    examples: [],
    employees: ['Financial Aid Analyst'],
  },
  it: {
    name: 'IT',
    lede: 'Accounts, sign-in activity, the outside connections and the audit log. Nothing here is about students.',
    examples: [],
    employees: ['IT & Data Steward'],
  },
}

/** The persona for a role, or null for roles that keep the general home
 * screen (admin, staff, reviewer). */
export function personaFor(role: Role): Persona | null {
  return PERSONAS[role] ?? null
}
