import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { ApprovedQuestion } from '../api'
import { ChatComposer } from './ChatComposer'

const QUESTIONS: ApprovedQuestion[] = [
  { id: 'spring-registration', text: 'What should I know about spring registration?' },
  { id: 'unresolved-holds', text: 'Where are unresolved holds affecting continued enrollment?' },
]

const render = (starters: boolean, questions: ApprovedQuestion[] | null = QUESTIONS) =>
  renderToStaticMarkup(
    <ChatComposer questions={questions} sending={false} starters={starters} onAsk={() => {}} />,
  )

describe('ChatComposer', () => {
  it('shows the approved questions as starter cards before the first answer', () => {
    const html = render(true)
    expect(html).toContain('What should I know about spring registration?')
    expect(html).toContain('Where are unresolved holds affecting continued enrollment?')
    // Two starter cards plus the send button.
    expect(html.match(/<button/g)?.length).toBe(3)
  })

  it('is the question field alone after an answer (no chips under it)', () => {
    const html = render(false)
    expect(html).not.toContain('Where are unresolved holds')
    expect(html).not.toContain('class="chip"')
    expect(html.match(/<button/g)?.length).toBe(1)
    expect(html).not.toContain('quick-questions')
  })

  it('keeps the free-text field (an off-registry question is still refused)', () => {
    const html = render(true)
    expect(html).toContain('id="question-input"')
    expect(html).toContain('Ask the Cabinet')
  })

  it('renders no starter cards before the questions load', () => {
    expect(render(true, null).match(/<button/g)?.length).toBe(1)
  })

  it('keeps the footnote to one short line', () => {
    const html = render(false)
    expect(html).toContain('Every number is computed from the records and checked.')
    expect(html).not.toContain('Approved questions only')
  })

  it('takes any question: an example placeholder and no "will be refused" warning', () => {
    const html = render(true)
    expect(html).toContain('placeholder="Ask about students, courses or majors"')
    expect(html).toContain('Ask the Cabinet a question')
    expect(html).not.toContain('will be refused')
  })
})

describe('ChatComposer — the "Try" row of Explore examples', () => {
  const EXAMPLES = [
    'Which major has the lowest GPA? In that major, what is historically the hardest class, and which instructor has historically taught it?',
    'Which majors have the highest average GPA?',
    'What is the average GPA by college?',
  ]
  const withExamples = (
    starters: boolean,
    examples: string[] | null,
    examplesFailed = false,
  ) =>
    renderToStaticMarkup(
      <ChatComposer
        questions={QUESTIONS}
        sending={false}
        starters={starters}
        onAsk={() => {}}
        examples={examples}
        examplesFailed={examplesFailed}
        onRetryExamples={() => {}}
      />,
    )

  it('shows three example questions under the approved cards', () => {
    const html = withExamples(true, EXAMPLES)
    expect(html).toContain('>Try<')
    for (const text of EXAMPLES) expect(html).toContain(text)
    // The approved cards come first.
    expect(html.indexOf('Approved question')).toBeLessThan(html.indexOf('>Try<'))
    // Two cards, three examples, and the send button.
    expect(html.match(/<button/g)?.length).toBe(6)
  })

  it('hides the examples once an answer is on screen', () => {
    expect(withExamples(false, EXAMPLES)).not.toContain('>Try<')
  })

  it('shows one skeleton line while loading and a quiet Retry line on failure', () => {
    expect(withExamples(true, null)).toContain('Loading example questions')
    const failed = withExamples(true, null, true)
    expect(failed).toContain('The example questions didn’t load.')
    expect(failed).toMatch(/<button[^>]*class="link-button"[^>]*>Retry<\/button>/)
    expect(failed).not.toContain('Loading example questions')
  })
})

describe('ChatComposer after a failed ask', () => {
  it('shows the plain sentence and Ask again above the field', () => {
    const html = renderToStaticMarkup(
      <ChatComposer
        questions={QUESTIONS}
        sending={false}
        starters={false}
        onAsk={() => {}}
        error="We couldn't reach the Cabinet. Check your connection and try again."
        onAskAgain={() => {}}
      />,
    )
    expect(html).toContain('role="alert"')
    expect(html).toContain('Check your connection')
    expect(html).toContain('Ask again')
    expect(html).not.toContain('Failed to fetch')
  })
})

describe('ChatComposer — the approved questions failed to load', () => {
  const failed = (starters: boolean, onRetry: (() => void) | null = () => {}) =>
    renderToStaticMarkup(
      <ChatComposer
        questions={null}
        sending={false}
        starters={starters}
        onAsk={() => {}}
        questionsFailed
        onRetryQuestions={onRetry}
      />,
    )

  it('shows a quiet line with Retry in the starter cards’ place', () => {
    const html = failed(true)
    expect(html).toContain('questions-retry')
    expect(html).toContain('The approved questions didn’t load.')
    expect(html).toMatch(/<button[^>]*class="link-button"[^>]*>Retry<\/button>/)
    // The field itself still works.
    expect(html).toContain('id="question-input"')
  })

  it('says nothing about it once an answer is on screen', () => {
    expect(failed(false)).not.toContain('questions-retry')
  })

  it('keeps the error block styled from the stylesheet, not inline', () => {
    const html = renderToStaticMarkup(
      <ChatComposer
        questions={null}
        sending={false}
        starters={false}
        onAsk={() => {}}
        error="The Cabinet is busy. Wait a minute and try again."
      />,
    )
    expect(html).toContain('class="ask-error state-error"')
    expect(html).not.toContain('style=')
  })
})
