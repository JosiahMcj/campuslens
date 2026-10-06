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
    expect(html).toContain('Approved questions only. Every number is checked.')
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
