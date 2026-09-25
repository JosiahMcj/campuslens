import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { ApprovedQuestion } from '../api'
import { QuestionBar } from './QuestionBar'

const QUESTIONS: ApprovedQuestion[] = [
  { id: 'spring-registration', text: 'What should I know about spring registration?' },
  { id: 'unresolved-holds', text: 'Where are unresolved holds affecting continued enrollment?' },
]

describe('QuestionBar — the approved-question chooser', () => {
  const html = renderToStaticMarkup(
    <QuestionBar state={{ kind: 'idle' }} questions={QUESTIONS} onAsk={() => {}} />,
  )

  it('renders one button per approved question from GET /questions', () => {
    expect(html).toContain('What should I know about spring registration?')
    expect(html).toContain('Where are unresolved holds affecting continued enrollment?')
    // The two chooser buttons plus the submit button.
    expect(html.match(/<button/g)?.length).toBe(3)
  })

  it('labels the field for the approved questions, plural', () => {
    expect(html).toContain('The approved questions for this prototype')
  })

  it('keeps the free-text field (an off-registry question is still refused)', () => {
    expect(html).toContain('id="question-input"')
    expect(html).toContain('Ask the Cabinet')
  })

  it('renders no chooser buttons before the questions load', () => {
    const loading = renderToStaticMarkup(
      <QuestionBar state={{ kind: 'idle' }} questions={null} onAsk={() => {}} />,
    )
    expect(loading.match(/<button/g)?.length).toBe(1)
  })
})
