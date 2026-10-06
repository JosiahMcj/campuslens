// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'

import {
  answerNotes,
  canExplore,
  claimColumn,
  dedupeLabels,
  displayText,
  exploreResponseFrom,
  formatCell,
  INSTRUCTOR_NOTE,
  linkSentence,
  loadExploreHistory,
  pickExamples,
  redactQuestion,
  roundHalfUp,
  saveExploreHistory,
  sourceLabel,
  visibleColumns,
  withQuestion,
  type ExploreResponse,
  type ExploreStep,
} from './explore'

// Recorded from the real API (POST /explore, replay provider, the school
// database at scale 1.0): the owner's question as the executive and as staff.
const EXEC_RAW = {
  "refused": false,
  "answer": [
    {
      "text": "Mechanical Engineering has the lowest average cumulative GPA, 2.62 across 250 students.",
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
      "text": "In Mechanical Engineering, the historically hardest required course is MEEN 3310 Thermodynamics I, with a DFW rate of 41.8% (38 of 91 graded registrations over 10 sections).",
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
      "text": "I-0001 Alicia Shelby (fictional) has taught it most: 7 sections in 7 terms, with a DFW rate of 56.7%.",
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
      "text": "I-0002 Anthony Jennings (fictional) taught 3 sections, with a DFW rate of 12.9%.",
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
        "Ranked: lowest first",
        "Only majors with at least 20 students"
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
        "Required by major: Mechanical Engineering (from step 1)",
        "Only courses with at least 8 sections",
        "Only courses taught in at least 4 terms",
        "Ranked: highest first"
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
  "source": "Calculated directly from the records",
  "planner": "rule",
  "notes": [],
  "fallbacks": [],
  "institution": "Demonstration University (fictional)",
  "fictional": true
}

const STAFF_RAW = {
  "refused": false,
  "answer": [
    {
      "text": "Mechanical Engineering has the lowest average cumulative GPA, 2.62 across 250 students.",
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
      "text": "In Mechanical Engineering, the historically hardest required course is MEEN 3310 Thermodynamics I, with a DFW rate of 41.8% (38 of 91 graded registrations over 10 sections).",
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
      "text": "Next is MEEN 3350 Manufacturing Processes at 34.1%.",
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
      "text": "Instructor results are shown to the executive and admin only, so this shows Thermodynamics I as a whole: 10 sections in 10 terms with a DFW rate of 41.8%.",
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
        "Ranked: lowest first",
        "Only majors with at least 20 students"
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
        "Required by major: Mechanical Engineering (from step 1)",
        "Only courses with at least 8 sections",
        "Only courses taught in at least 4 terms",
        "Ranked: highest first"
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
  "source": "Calculated directly from the records",
  "planner": "rule",
  "notes": [],
  "fallbacks": [],
  "institution": "Demonstration University (fictional)",
  "fictional": true
}

const EXEC: ExploreResponse = exploreResponseFrom(EXEC_RAW)
const STAFF: ExploreResponse = exploreResponseFrom(STAFF_RAW)

const linkedText = (response: ExploreResponse, index: number) =>
  linkSentence(response.answer[index], response.steps)
    .filter((part) => 'claim' in part)
    .map((part) => part.text)

afterEach(() => window.sessionStorage.clear())

describe('the role gate', () => {
  it('lets every role but Financial Aid ask Explore questions', () => {
    expect(canExplore('executive')).toBe(true)
    expect(canExplore('admin')).toBe(true)
    expect(canExplore('staff')).toBe(true)
    expect(canExplore('reviewer')).toBe(true)
    expect(canExplore('aid')).toBe(false)
  })
})

describe('reading the response', () => {
  it('keeps the steps, tables, claims and source of a real answer', () => {
    expect(EXEC.refused).toBe(false)
    expect(EXEC.steps.map((step) => step.title)).toEqual([
      'Average GPA by major',
      'DFW rate by course',
      'Instructors who taught a course',
    ])
    expect(EXEC.answer[0].claims.length).toBeGreaterThan(0)
    expect(EXEC.steps[0].fields_read.length).toBeGreaterThan(0)
  })

  it('reads a malformed body as an empty answer, never a crash', () => {
    const empty = exploreResponseFrom('nonsense')
    expect(empty).toMatchObject({ refused: false, answer: [], steps: [], source: null })
    const partial = exploreResponseFrom({ answer: [{ text: 1 }], steps: [{ nope: true }] })
    expect(partial.answer).toEqual([])
    expect(partial.steps).toEqual([])
  })
})

describe('linking numbers to cells', () => {
  it('links the planted values in the owner\'s answer', () => {
    expect(linkedText(EXEC, 0)).toEqual(['2.62', '250'])
    expect(linkedText(EXEC, 1)).toEqual(['MEEN 3310', '41.8%', '38', '91', '10'])
    expect(linkedText(EXEC, 2)).toEqual(['Alicia Shelby (fictional)', '7', '7', '56.7%'])
  })

  it('sends "7 sections in 7 terms" to two different cells', () => {
    const links = linkSentence(EXEC.answer[2], EXEC.steps).filter((part) => 'claim' in part)
    const columns = links.map((part) => ('claim' in part ? part.claim.column : ''))
    expect(columns).toEqual(['name', 'sections', 'terms', 'dfw_rate'])
  })

  it('never changes the words of a sentence, only drops the instructor id', () => {
    const joined = linkSentence(EXEC.answer[2], EXEC.steps)
      .map((part) => part.text)
      .join('')
    expect(joined).toBe(
      'Alicia Shelby (fictional) has taught it most: 7 sections in 7 terms, with a DFW rate of 56.7%.',
    )
  })

  it('does not link a number inside a longer number', () => {
    const steps = [
      {
        analysis_id: 'x',
        title: 'X',
        params_plain: [],
        fields_read: [],
        notes: [],
        table: { columns: [{ key: 'n', label: 'N' }], rows: [[1.5], [41]] },
      },
    ]
    const parts = linkSentence(
      { text: 'Rates of 41.5 % and 1.5 %, then 41 students.', claims: [
        { table: 0, row: 0, column: 'n' },
        { table: 0, row: 1, column: 'n' },
      ] },
      steps,
    )
    const links = parts.filter((part) => 'claim' in part).map((part) => part.text)
    expect(links).toEqual(['1.5 %', '41'])
  })

  it('links a withheld figure and formats 0.0 the way the sentence writes it', () => {
    const steps = [
      {
        analysis_id: 'x',
        title: 'X',
        params_plain: [],
        fields_read: [],
        notes: [],
        table: {
          columns: [
            { key: 'a', label: 'A' },
            { key: 'b', label: 'B' },
          ],
          rows: [['fewer than 10', 0]],
        },
      },
    ]
    const parts = linkSentence(
      {
        text: 'A rate of withheld (fewer than 10 students) and 0.0 %.',
        claims: [
          { table: 0, row: 0, column: 'a' },
          { table: 0, row: 0, column: 'b' },
        ],
      },
      steps,
    )
    expect(parts.filter((part) => 'claim' in part).map((part) => part.text)).toEqual([
      'withheld (fewer than 10 students)',
      '0.0 %',
    ])
  })

  it('never links a positive value to the digits of a negative number', () => {
    const steps = [
      {
        analysis_id: 'x',
        title: 'X',
        params_plain: [],
        fields_read: [],
        notes: [],
        table: { columns: [{ key: 'n', label: 'N' }, { key: 'm', label: 'M' }], rows: [[5, -5]] },
      },
    ]
    const parts = linkSentence(
      { text: 'A change of −5 points, then 5 more.', claims: [{ table: 0, row: 0, column: 'n' }] },
      steps,
    )
    expect(parts.filter((part) => 'claim' in part).map((part) => part.text)).toEqual(['5'])
    expect(parts.map((part) => part.text).join('')).toBe('A change of −5 points, then 5 more.')
    // The link is the "5" after "then", not the one inside "−5".
    expect(parts[0].text).toBe('A change of −5 points, then ')
  })

  it('leaves a claim it cannot find as plain text', () => {
    const parts = linkSentence(
      { text: 'Nothing here.', claims: [{ table: 9, row: 0, column: 'x' }] },
      EXEC.steps,
    )
    expect(parts).toEqual([{ text: 'Nothing here.' }])
  })
})

describe('words and tables on screen', () => {
  it('drops instructor ids and never shows a student id', () => {
    expect(displayText('I-0001 Alicia Shelby (fictional) taught it.')).toBe(
      'Alicia Shelby (fictional) taught it.',
    )
    expect(displayText('Student S-123456 enrolled.')).toBe('Student a student enrolled.')
    expect(redactQuestion('  Is S-0042   on probation? ')).toBe('Is a student on probation?')
    expect(redactQuestion('What about S1234 and 1234567?')).toBe('What about a student and a number?')
    // Term codes stay: they are not ids.
    expect(redactQuestion('Who enrolled in 202620?')).toBe('Who enrolled in 202620?')
  })

  it('hides the instructor id column when the name is shown', () => {
    const step = EXEC.steps[2]
    const keys = visibleColumns(step).map((index) => step.table.columns[index].key)
    expect(keys).not.toContain('instructor')
    expect(keys).toContain('name')
    expect(claimColumn(step, 'instructor')).toBe('name')
    expect(claimColumn(EXEC.steps[0], 'avg_gpa')).toBe('avg_gpa')
  })

  it('hides code columns (term codes, major codes) when the name is shown', () => {
    const gpa = EXEC.steps[0]
    const keys = visibleColumns(gpa).map((index) => gpa.table.columns[index].key)
    expect(keys).not.toContain('major')
    expect(keys[0]).toBe('major_name')
    expect(claimColumn(gpa, 'major')).toBe('major_name')
    const trend: ExploreStep = {
      analysis_id: 'course_dfw_trend',
      title: "A course's D, F or withdrawal trend by term",
      params_plain: [],
      fields_read: [],
      notes: [],
      table: {
        columns: [
          { key: 'course', label: 'Course' },
          { key: 'term', label: 'Term code' },
          { key: 'term_name', label: 'Term' },
          { key: 'dfw_rate', label: 'D, F or withdrawal rate (%)' },
        ],
        rows: [
          ['MEEN 3310', '202120', 'Spring 2021', 41.2],
          ['MEEN 3310', '202210', 'Fall 2021', 38.5],
        ],
      },
    }
    // No term code, and the term (which tells the rows apart) comes first,
    // so it is the column that stays in view on a phone.
    expect(visibleColumns(trend).map((index) => trend.table.columns[index].key)).toEqual([
      'term_name',
      'course',
      'dfw_rate',
    ])
    expect(claimColumn(trend, 'term')).toBe('term_name')
  })

  it('names each thing read once', () => {
    expect(
      dedupeLabels([
        'Major each term',
        'Term',
        'Cumulative GPA',
        'Major',
        'Major name',
        'College',
        'Instructor first name (fictional)',
        'Instructor last name (fictional)',
      ]),
    ).toEqual([
      'Major each term',
      'Term',
      'Cumulative GPA',
      'Major',
      'College',
      'Instructor name (fictional)',
    ])
  })

  it('rounds half up on the decimal value, as the API rounds a sentence', () => {
    expect(roundHalfUp(2.615, 2)).toBe('2.62')
    expect(roundHalfUp(2.625, 2)).toBe('2.63')
    expect(roundHalfUp(2.623, 2)).toBe('2.62')
    expect(roundHalfUp(3.1, 2)).toBe('3.10')
    expect(roundHalfUp(110.8, 0)).toBe('111')
    expect(roundHalfUp(110.5, 0)).toBe('111')
    expect(roundHalfUp(99.96, 1)).toBe('100.0')
    expect(roundHalfUp(0.004, 2)).toBe('0.00')
  })

  it('links a rounded GPA and a whole-number growth to their cells', () => {
    const step: ExploreStep = {
      analysis_id: 'headcount_growth',
      title: 'Headcount by major over time',
      params_plain: [],
      fields_read: [],
      notes: [],
      table: {
        columns: [
          { key: 'major_name', label: 'Major' },
          { key: 'avg_gpa', label: 'Average cumulative GPA' },
          { key: 'growth', label: 'Growth (%)' },
        ],
        rows: [['Computer Science', 2.615, 110.8]],
      },
    }
    const parts = linkSentence(
      {
        text: 'Computer Science grew the fastest, 111% (more than doubled), and averages 2.62.',
        claims: [
          { table: 0, row: 0, column: 'growth' },
          { table: 0, row: 0, column: 'avg_gpa' },
        ],
      },
      [step],
    )
    const links = parts.flatMap((part) => ('claim' in part ? [part.text] : []))
    expect(links).toEqual(['111%', '2.62'])
  })

  it('formats cells plainly', () => {
    expect(formatCell(null)).toBe('—')
    expect(formatCell(12345)).toBe('12,345')
    expect(formatCell(-3)).toBe('−3')
    expect(formatCell(2.623)).toBe('2.623')
    expect(formatCell('fewer than 10')).toBe('fewer than 10')
    expect(formatCell('I-0002 Anthony Jennings (fictional)')).toBe('Anthony Jennings (fictional)')
  })

  it('writes the source line without the "(no model)" aside', () => {
    expect(sourceLabel('Calculated directly from the records')).toBe(
      'Calculated directly from the records',
    )
    expect(sourceLabel('Written by the Chief of Staff from the records')).toBe(
      'Written by the Chief of Staff from the records',
    )
    // Answers recorded under the older wording read the same way.
    expect(sourceLabel('Written from computed tables (no model)')).toBe(
      'Calculated directly from the records',
    )
    expect(sourceLabel('Written by the Chief of Staff from computed tables')).toBe(
      'Written by the Chief of Staff from the records',
    )
    expect(sourceLabel(null)).toBeNull()
  })

  it('says the instructor rule once: the note only when no sentence says it', () => {
    // The staff answer's sentence already says it.
    expect(answerNotes(STAFF)).toEqual([])
    expect(answerNotes(EXEC)).toEqual([])
    // A rewording that dropped the sentence still gets the note.
    const reworded = {
      ...STAFF,
      answer: STAFF.answer.filter((sentence) => !/executive and admin/.test(sentence.text)),
    }
    expect(answerNotes(reworded)).toEqual([INSTRUCTOR_NOTE])
  })

  it('keeps no student id anywhere in a real answer', () => {
    const text = JSON.stringify(EXEC) + JSON.stringify(STAFF)
    expect(text).not.toMatch(/\bS-\d+/)
  })
})

describe('the example questions', () => {
  it('always includes the owner\'s question, then short ones', () => {
    const picked = pickExamples([
      'What is the average GPA by college?',
      'Which major has the lowest GPA? In that major, what is historically the hardest class, and which instructor has historically taught it?',
      'Which term had the largest gap between online and in-person withdrawal rates?',
      'Which majors have the highest average GPA?',
    ])
    expect(picked).toHaveLength(3)
    expect(picked[0]).toMatch(/^Which major has the lowest GPA/)
    expect(picked).toContain('What is the average GPA by college?')
    expect(picked).toContain('Which majors have the highest average GPA?')
  })

  it('copes with fewer than three examples', () => {
    expect(pickExamples([])).toEqual([])
    expect(pickExamples(['One?'])).toEqual(['One?'])
  })
})

describe('the session\'s question list', () => {
  it('survives a reload in the same tab, per user', () => {
    saveExploreHistory(2, withQuestion([], 'Which offices hold the most active holds?'))
    expect(loadExploreHistory(2)).toEqual(['Which offices hold the most active holds?'])
    expect(loadExploreHistory(3)).toEqual([])
  })

  it('moves a repeated question to the end and keeps at most 20', () => {
    let list: string[] = []
    for (let n = 0; n < 25; n += 1) list = withQuestion(list, `Question ${n}?`)
    expect(list).toHaveLength(20)
    list = withQuestion(list, 'Question 10?')
    expect(list.at(-1)).toBe('Question 10?')
    expect(list.filter((q) => q === 'Question 10?')).toHaveLength(1)
  })

  it('reads damaged storage as an empty list', () => {
    window.sessionStorage.setItem('cabinet.explore.questions.2', '{not json')
    expect(loadExploreHistory(2)).toEqual([])
  })
})
