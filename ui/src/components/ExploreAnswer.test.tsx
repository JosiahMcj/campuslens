// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { exploreResponseFrom, INSTRUCTOR_NOTE, type ExploreResponse } from '../explore'
import { ExploreAnswer, ExploreWorking } from './ExploreAnswer'

// Recorded from the real API (POST /explore, replay provider, the school
// database at scale 1.0): the owner's question as the executive and as staff.
const EXEC: ExploreResponse = exploreResponseFrom({
  "refused": false,
  "answer": [
    {
      "text": "Mechanical Engineering has the lowest average cumulative GPA, 2.623 across 250 students.",
      "claims": [
        {
          "table": 0,
          "row": 0,
          "column": "avg_gpa"
        },
        {
          "table": 0,
          "row": 0,
          "column": "students"
        }
      ]
    },
    {
      "text": "In Mechanical Engineering, the historically hardest required course is MEEN 3310 Thermodynamics I, with a DFW rate of 41.8 % (38 of 91 graded registrations over 10 sections).",
      "claims": [
        {
          "table": 1,
          "row": 0,
          "column": "course"
        },
        {
          "table": 1,
          "row": 0,
          "column": "dfw_rate"
        },
        {
          "table": 1,
          "row": 0,
          "column": "dfw"
        },
        {
          "table": 1,
          "row": 0,
          "column": "graded"
        },
        {
          "table": 1,
          "row": 0,
          "column": "sections"
        }
      ]
    },
    {
      "text": "I-0001 Alicia Shelby (fictional) has taught it most: 7 sections in 7 terms, with a DFW rate of 56.7 %.",
      "claims": [
        {
          "table": 2,
          "row": 0,
          "column": "instructor"
        },
        {
          "table": 2,
          "row": 0,
          "column": "sections"
        },
        {
          "table": 2,
          "row": 0,
          "column": "terms"
        },
        {
          "table": 2,
          "row": 0,
          "column": "dfw_rate"
        }
      ]
    },
    {
      "text": "I-0002 Anthony Jennings (fictional) taught 3 sections, with a DFW rate of 12.9 %.",
      "claims": [
        {
          "table": 2,
          "row": 1,
          "column": "instructor"
        },
        {
          "table": 2,
          "row": 1,
          "column": "sections"
        },
        {
          "table": 2,
          "row": 1,
          "column": "dfw_rate"
        }
      ]
    }
  ],
  "steps": [
    {
      "analysis_id": "gpa_by_major",
      "title": "Average GPA by major",
      "params_plain": [
        "Order: lowest first",
        "Minimum students to rank: 20",
        "Rows shown: 10"
      ],
      "fields_read": [
        "student_term_records.program_code",
        "student_term_records.term_code",
        "student_term_records.cumulative_gpa",
        "academic_programs.major_code",
        "academic_programs.name",
        "academic_programs.college_code"
      ],
      "aggregate_only": true,
      "table": {
        "columns": [
          {
            "key": "major",
            "label": "Major code"
          },
          {
            "key": "major_name",
            "label": "Major"
          },
          {
            "key": "college",
            "label": "College"
          },
          {
            "key": "students",
            "label": "Students"
          },
          {
            "key": "avg_gpa",
            "label": "Average cumulative GPA"
          }
        ],
        "rows": [
          [
            "MEEN",
            "Mechanical Engineering",
            "College of Engineering and Computing",
            250,
            2.623
          ],
          [
            "CHEM",
            "Chemistry",
            "College of Arts and Sciences",
            71,
            2.925
          ],
          [
            "THEA",
            "Theatre",
            "College of Theology, Ministry, and the Arts",
            52,
            2.931
          ],
          [
            "GNST",
            "General Studies",
            "College of Arts and Sciences",
            146,
            2.933
          ],
          [
            "CVEN",
            "Civil Engineering",
            "College of Engineering and Computing",
            89,
            2.934
          ],
          [
            "BIOL",
            "Biology",
            "College of Arts and Sciences",
            298,
            2.941
          ],
          [
            "ELEN",
            "Electrical Engineering",
            "College of Engineering and Computing",
            88,
            2.95
          ],
          [
            "PUBH",
            "Public Health",
            "College of Nursing and Health Sciences",
            89,
            2.955
          ],
          [
            "INFT",
            "Information Technology",
            "College of Engineering and Computing",
            118,
            2.96
          ],
          [
            "CSCI",
            "Computer Science",
            "College of Engineering and Computing",
            266,
            2.96
          ]
        ]
      },
      "notes": []
    },
    {
      "analysis_id": "dfw_by_course",
      "title": "DFW rate by course",
      "params_plain": [
        "Required by major: Mechanical Engineering (MEEN) (from step 1)",
        "Minimum sections: 8",
        "Minimum terms: 4",
        "Order: highest first",
        "Rows shown: 10"
      ],
      "fields_read": [
        "final_grades.grade",
        "section_registrations.section_id",
        "sections.course_id",
        "sections.term_code",
        "courses.grade_mode",
        "courses.course_level",
        "courses.subject_code",
        "program_requirements.course_id"
      ],
      "aggregate_only": true,
      "table": {
        "columns": [
          {
            "key": "course",
            "label": "Course"
          },
          {
            "key": "title",
            "label": "Title"
          },
          {
            "key": "sections",
            "label": "Sections"
          },
          {
            "key": "terms",
            "label": "Terms"
          },
          {
            "key": "graded",
            "label": "Graded registrations"
          },
          {
            "key": "dfw",
            "label": "D, F, or W"
          },
          {
            "key": "dfw_rate",
            "label": "DFW rate (%)"
          }
        ],
        "rows": [
          [
            "MEEN 3310",
            "Thermodynamics I",
            10,
            10,
            91,
            38,
            41.8
          ],
          [
            "MEEN 3350",
            "Manufacturing Processes",
            12,
            12,
            126,
            43,
            34.1
          ],
          [
            "MEEN 3311",
            "Thermodynamics II",
            12,
            12,
            110,
            31,
            28.2
          ],
          [
            "MEEN 3340",
            "Machine Design",
            12,
            12,
            99,
            24,
            24.2
          ],
          [
            "MEEN 3330",
            "Heat Transfer",
            12,
            12,
            83,
            20,
            24.1
          ],
          [
            "PHYS 2425",
            "University Physics I",
            22,
            12,
            372,
            88,
            23.7
          ],
          [
            "MEEN 4310",
            "Mechanical Vibrations",
            12,
            12,
            98,
            23,
            23.5
          ],
          [
            "EGR 2334",
            "Mechanics of Materials",
            15,
            15,
            112,
            26,
            23.2
          ],
          [
            "CHEM 1411",
            "General Chemistry I",
            74,
            12,
            1533,
            351,
            22.9
          ],
          [
            "EGR 1201",
            "Introduction to Engineering",
            30,
            17,
            634,
            133,
            21.0
          ]
        ]
      },
      "notes": [
        "Only courses with at least 8 sections in at least 4 terms are ranked."
      ]
    },
    {
      "analysis_id": "course_instructors",
      "title": "Instructors who taught a course",
      "params_plain": [
        "Course: MEEN 3310 Thermodynamics I (from step 2)"
      ],
      "fields_read": [
        "section_instructors.instructor_id",
        "instructors.first_name",
        "instructors.last_name",
        "instructors.academic_rank",
        "sections.course_id",
        "sections.term_code",
        "final_grades.grade"
      ],
      "aggregate_only": true,
      "table": {
        "columns": [
          {
            "key": "instructor",
            "label": "Instructor"
          },
          {
            "key": "name",
            "label": "Name (fictional)"
          },
          {
            "key": "rank",
            "label": "Rank"
          },
          {
            "key": "sections",
            "label": "Sections"
          },
          {
            "key": "terms",
            "label": "Terms"
          },
          {
            "key": "first_term",
            "label": "First term"
          },
          {
            "key": "last_term",
            "label": "Last term"
          },
          {
            "key": "graded",
            "label": "Graded registrations"
          },
          {
            "key": "dfw",
            "label": "D, F, or W"
          },
          {
            "key": "dfw_rate",
            "label": "DFW rate (%)"
          }
        ],
        "rows": [
          [
            "I-0001",
            "Alicia Shelby (fictional)",
            "Professor",
            7,
            7,
            "Fall 2021",
            "Spring 2026",
            60,
            34,
            56.7
          ],
          [
            "I-0002",
            "Anthony Jennings (fictional)",
            "Associate Professor",
            3,
            3,
            "Spring 2022",
            "Spring 2025",
            31,
            4,
            12.9
          ]
        ]
      },
      "notes": [
        "Instructor names are fictional."
      ]
    }
  ],
  "source": "Written from computed tables (no model)",
  "planner": "rule",
  "notes": [],
  "fallbacks": [],
  "institution": "Demonstration University (fictional)",
  "fictional": true
})

const STAFF: ExploreResponse = exploreResponseFrom({
  "refused": false,
  "answer": [
    {
      "text": "Mechanical Engineering has the lowest average cumulative GPA, 2.623 across 250 students.",
      "claims": [
        {
          "table": 0,
          "row": 0,
          "column": "avg_gpa"
        },
        {
          "table": 0,
          "row": 0,
          "column": "students"
        }
      ]
    },
    {
      "text": "In Mechanical Engineering, the historically hardest required course is MEEN 3310 Thermodynamics I, with a DFW rate of 41.8 % (38 of 91 graded registrations over 10 sections).",
      "claims": [
        {
          "table": 1,
          "row": 0,
          "column": "course"
        },
        {
          "table": 1,
          "row": 0,
          "column": "dfw_rate"
        },
        {
          "table": 1,
          "row": 0,
          "column": "dfw"
        },
        {
          "table": 1,
          "row": 0,
          "column": "graded"
        },
        {
          "table": 1,
          "row": 0,
          "column": "sections"
        }
      ]
    },
    {
      "text": "Next is MEEN 3350 Manufacturing Processes at 34.1 %.",
      "claims": [
        {
          "table": 1,
          "row": 1,
          "column": "course"
        },
        {
          "table": 1,
          "row": 1,
          "column": "dfw_rate"
        }
      ]
    },
    {
      "text": "Instructor-level results are available to the executive and admin roles only, so this shows Thermodynamics I as a whole: 10 sections in 10 terms with a DFW rate of 41.8 %.",
      "claims": [
        {
          "table": 2,
          "row": 0,
          "column": "sections"
        },
        {
          "table": 2,
          "row": 0,
          "column": "terms"
        },
        {
          "table": 2,
          "row": 0,
          "column": "dfw_rate"
        }
      ]
    }
  ],
  "steps": [
    {
      "analysis_id": "gpa_by_major",
      "title": "Average GPA by major",
      "params_plain": [
        "Order: lowest first",
        "Minimum students to rank: 20",
        "Rows shown: 10"
      ],
      "fields_read": [
        "student_term_records.program_code",
        "student_term_records.term_code",
        "student_term_records.cumulative_gpa",
        "academic_programs.major_code",
        "academic_programs.name",
        "academic_programs.college_code"
      ],
      "aggregate_only": true,
      "table": {
        "columns": [
          {
            "key": "major",
            "label": "Major code"
          },
          {
            "key": "major_name",
            "label": "Major"
          },
          {
            "key": "college",
            "label": "College"
          },
          {
            "key": "students",
            "label": "Students"
          },
          {
            "key": "avg_gpa",
            "label": "Average cumulative GPA"
          }
        ],
        "rows": [
          [
            "MEEN",
            "Mechanical Engineering",
            "College of Engineering and Computing",
            250,
            2.623
          ],
          [
            "CHEM",
            "Chemistry",
            "College of Arts and Sciences",
            71,
            2.925
          ],
          [
            "THEA",
            "Theatre",
            "College of Theology, Ministry, and the Arts",
            52,
            2.931
          ],
          [
            "GNST",
            "General Studies",
            "College of Arts and Sciences",
            146,
            2.933
          ],
          [
            "CVEN",
            "Civil Engineering",
            "College of Engineering and Computing",
            89,
            2.934
          ],
          [
            "BIOL",
            "Biology",
            "College of Arts and Sciences",
            298,
            2.941
          ],
          [
            "ELEN",
            "Electrical Engineering",
            "College of Engineering and Computing",
            88,
            2.95
          ],
          [
            "PUBH",
            "Public Health",
            "College of Nursing and Health Sciences",
            89,
            2.955
          ],
          [
            "INFT",
            "Information Technology",
            "College of Engineering and Computing",
            118,
            2.96
          ],
          [
            "CSCI",
            "Computer Science",
            "College of Engineering and Computing",
            266,
            2.96
          ]
        ]
      },
      "notes": []
    },
    {
      "analysis_id": "dfw_by_course",
      "title": "DFW rate by course",
      "params_plain": [
        "Required by major: Mechanical Engineering (MEEN) (from step 1)",
        "Minimum sections: 8",
        "Minimum terms: 4",
        "Order: highest first",
        "Rows shown: 10"
      ],
      "fields_read": [
        "final_grades.grade",
        "section_registrations.section_id",
        "sections.course_id",
        "sections.term_code",
        "courses.grade_mode",
        "courses.course_level",
        "courses.subject_code",
        "program_requirements.course_id"
      ],
      "aggregate_only": true,
      "table": {
        "columns": [
          {
            "key": "course",
            "label": "Course"
          },
          {
            "key": "title",
            "label": "Title"
          },
          {
            "key": "sections",
            "label": "Sections"
          },
          {
            "key": "terms",
            "label": "Terms"
          },
          {
            "key": "graded",
            "label": "Graded registrations"
          },
          {
            "key": "dfw",
            "label": "D, F, or W"
          },
          {
            "key": "dfw_rate",
            "label": "DFW rate (%)"
          }
        ],
        "rows": [
          [
            "MEEN 3310",
            "Thermodynamics I",
            10,
            10,
            91,
            38,
            41.8
          ],
          [
            "MEEN 3350",
            "Manufacturing Processes",
            12,
            12,
            126,
            43,
            34.1
          ],
          [
            "MEEN 3311",
            "Thermodynamics II",
            12,
            12,
            110,
            31,
            28.2
          ],
          [
            "MEEN 3340",
            "Machine Design",
            12,
            12,
            99,
            24,
            24.2
          ],
          [
            "MEEN 3330",
            "Heat Transfer",
            12,
            12,
            83,
            20,
            24.1
          ],
          [
            "PHYS 2425",
            "University Physics I",
            22,
            12,
            372,
            88,
            23.7
          ],
          [
            "MEEN 4310",
            "Mechanical Vibrations",
            12,
            12,
            98,
            23,
            23.5
          ],
          [
            "EGR 2334",
            "Mechanics of Materials",
            15,
            15,
            112,
            26,
            23.2
          ],
          [
            "CHEM 1411",
            "General Chemistry I",
            74,
            12,
            1533,
            351,
            22.9
          ],
          [
            "EGR 1201",
            "Introduction to Engineering",
            30,
            17,
            634,
            133,
            21.0
          ]
        ]
      },
      "notes": [
        "Only courses with at least 8 sections in at least 4 terms are ranked."
      ]
    },
    {
      "analysis_id": "course_instructors",
      "title": "Instructors who taught a course",
      "params_plain": [
        "Course: MEEN 3310 Thermodynamics I (from step 2)"
      ],
      "fields_read": [
        "sections.course_id",
        "sections.term_code",
        "section_instructors.instructor_id (counted)",
        "final_grades.grade"
      ],
      "aggregate_only": true,
      "table": {
        "columns": [
          {
            "key": "course",
            "label": "Course"
          },
          {
            "key": "title",
            "label": "Title"
          },
          {
            "key": "sections",
            "label": "Sections"
          },
          {
            "key": "terms",
            "label": "Terms"
          },
          {
            "key": "instructors",
            "label": "Instructors"
          },
          {
            "key": "graded",
            "label": "Graded registrations"
          },
          {
            "key": "dfw",
            "label": "D, F, or W"
          },
          {
            "key": "dfw_rate",
            "label": "DFW rate (%)"
          }
        ],
        "rows": [
          [
            "MEEN 3310",
            "Thermodynamics I",
            10,
            10,
            2,
            91,
            38,
            41.8
          ]
        ]
      },
      "notes": [
        "Instructor-level results are available to the executive and admin roles only, so this table shows the course as a whole."
      ],
      "instructor_rows_withheld": true
    }
  ],
  "source": "Written from computed tables (no model)",
  "planner": "rule",
  "notes": [],
  "fallbacks": [],
  "institution": "Demonstration University (fictional)",
  "fictional": true
})

const EXAMPLES = [
  'Which majors have the highest average GPA?',
  'What is the average GPA by college?',
  'Which offices hold the most active holds?',
]

function show(response: ExploreResponse, props: Partial<Parameters<typeof ExploreAnswer>[0]> = {}) {
  const onAsk = vi.fn()
  const view = render(
    <ExploreAnswer
      answerKey="7"
      response={response}
      fallbackSuggestions={EXAMPLES}
      onAsk={onAsk}
      busy={false}
      onSeeAuditLog={null}
      {...props}
    />,
  )
  return { onAsk, view }
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => cleanup())

describe('an Explore answer', () => {
  it('shows the planted values as links, the source line, and no ids', () => {
    show(EXEC)
    for (const value of ['2.623', '41.8 %', '56.7 %', 'Alicia Shelby (fictional)']) {
      expect(screen.getAllByRole('button', { name: value }).length).toBeGreaterThan(0)
    }
    expect(screen.getByText('Written from computed tables')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/I-\d{4}|\bS-\d+|no model/)
  })

  it('opens "How this was answered" at the cell a number came from', async () => {
    show(EXEC)
    const fold = document.querySelector('details.explore-how') as HTMLDetailsElement
    expect(fold.open).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '41.8 %' }))
    expect(fold.open).toBe(true)
    const cell = document.getElementById('explore-7-t1-r0-dfw_rate') as HTMLElement
    expect(cell.textContent).toBe('41.8')
    expect(cell.className).toContain('explore-cell-lit')
    await waitFor(() => expect(document.activeElement).toBe(cell))
  })

  it('lists each step with its parameters, table and the data it read', () => {
    show(EXEC)
    const steps = document.querySelectorAll('.explore-step')
    expect(steps).toHaveLength(3)
    expect(steps[0].querySelector('h4')?.textContent).toBe('Step 1. Average GPA by major')
    expect(steps[0].textContent).toContain('Order: lowest first')
    expect(steps[0].textContent).toContain('Data it read:')
    expect(steps[0].textContent).toContain('Cumulative GPA')
    expect(steps[0].textContent).not.toContain('student_term_records')
    // No field reads as an id.
    for (const step of steps) expect(step.textContent).not.toMatch(/\bid\b/i)
    // The instructor table names people (fictional) without their ids.
    const instructors = within(steps[2] as HTMLElement)
    expect(instructors.getByText('Name (fictional)')).toBeTruthy()
    expect(instructors.queryByText('Instructor')).toBeNull()
    expect(steps[2].textContent).toContain('Alicia Shelby (fictional)')
  })

  it('gives staff the course as a whole and the instructor note', () => {
    show(STAFF)
    expect(screen.getByText(INSTRUCTOR_NOTE)).toBeTruthy()
    // Said once under the answer, not again under the step's table.
    expect(document.body.textContent).not.toContain('so this table shows the course as a whole')
    expect(document.body.textContent).not.toContain('Alicia Shelby')
  })

  it('shows the first 10 rows, then all of them on "Show all"', () => {
    const step = EXEC.steps[0]
    const rows = [...step.table.rows, ...step.table.rows.slice(0, 2)]
    const long: ExploreResponse = {
      ...EXEC,
      answer: [
        { text: 'The eleventh row has 250 students.', claims: [{ table: 0, row: 10, column: 'students' }] },
      ],
      steps: [{ ...step, table: { ...step.table, rows } }],
    }
    show(long)
    const table = document.querySelector('.explore-table') as HTMLTableElement
    expect(table.tBodies[0].rows).toHaveLength(10)
    expect(screen.getByRole('button', { name: 'Show all 12' })).toBeTruthy()
    // Opening a number in row 11 shows every row first.
    fireEvent.click(screen.getByRole('button', { name: '250' }))
    expect(table.tBodies[0].rows).toHaveLength(12)
    expect(screen.queryByRole('button', { name: 'Show all 12' })).toBeNull()
  })

  it('reads a withheld cell as "fewer than 10"', () => {
    const step = EXEC.steps[0]
    const withheld: ExploreResponse = {
      ...EXEC,
      steps: [
        {
          ...step,
          table: { ...step.table, rows: [[...step.table.rows[0].slice(0, 3), 'fewer than 10', 'fewer than 10']] },
        },
      ],
      answer: [],
    }
    show({ ...withheld, answer: [{ text: 'Withheld.', claims: [] }] })
    expect(screen.getAllByText('fewer than 10')).toHaveLength(2)
  })
})

describe('a refusal and a question no analysis answers', () => {
  it('reads a refusal like the briefing\'s refusals, with three questions to try', () => {
    const { onAsk } = show(
      exploreResponseFrom({
        refused: true,
        message: 'The Cabinet does not answer questions about counseling or spiritual care.',
        answer: [],
        steps: [],
        source: null,
      }),
      { onSeeAuditLog: vi.fn() },
    )
    const card = screen.getByRole('alert')
    expect(within(card).getByText('Refused')).toBeTruthy()
    expect(card.textContent).toContain('recorded in the audit log')
    expect(within(card).getByRole('button', { name: 'See the audit log' })).toBeTruthy()
    fireEvent.click(screen.getByText(EXAMPLES[1]))
    expect(onAsk).toHaveBeenCalledWith(EXAMPLES[1])
  })

  it('offers the API\'s suggestions when no analysis answers the question', () => {
    const { onAsk } = show(
      exploreResponseFrom({
        refused: false,
        message: "The Cabinet can't answer that from the approved analyses yet.",
        answer: [],
        steps: [],
        suggestions: ['Which majors have the highest average GPA?', 'What is the average GPA by college?', 'Who has taught Organic Chemistry I?'],
        source: null,
      }),
    )
    expect(screen.getByText("The Cabinet can't answer that from the approved analyses yet.")).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.click(screen.getByText('Who has taught Organic Chemistry I?'))
    expect(onAsk).toHaveBeenCalledWith('Who has taught Organic Chemistry I?')
  })

  it('disables the suggestions while another question is answered', () => {
    show(
      exploreResponseFrom({ refused: true, message: 'No.', answer: [], steps: [], source: null }),
      { busy: true },
    )
    const chips = document.querySelectorAll<HTMLButtonElement>('.explore-chip')
    expect(chips).toHaveLength(3)
    for (const chip of chips) expect(chip.disabled).toBe(true)
    // Each chip is a button inside a list item.
    expect(chips[0].closest('li')).not.toBeNull()
  })
})

describe('while working', () => {
  it('says "Working it out…"', () => {
    render(<ExploreWorking />)
    expect(screen.getByRole('status').textContent).toContain('Working it out…')
  })
})
