import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { Decision } from '../api'
import { DecisionPanel } from './DecisionPanel'

const DECISION: Decision = {
  id: 'D-spring-registration-1',
  title: 'Authorize the eligibility review',
  text: 'Authorize a focused review below the threshold.',
  follow_up: { office: 'Financial Aid', description: 'Report back in two weeks.' },
  approved: false,
}

function render(canApprove: boolean): string {
  return renderToStaticMarkup(
    <DecisionPanel
      decisions={[DECISION]}
      events={[]}
      canApprove={canApprove}
      approving={false}
      approveError={null}
      approvedTasks={{}}
      onApprove={() => {}}
    />,
  )
}

describe('DecisionPanel — role gating of Approve', () => {
  it('offers the Approve button to a role that may approve', () => {
    const html = render(true)

    expect(html).toContain('approve-button')
    expect(html).toContain('Approve the review')
    expect(html).not.toContain('Only an executive can approve this')
  })

  it('shows "Only an executive can approve this" instead of the button for staff and reviewer', () => {
    const html = render(false)

    expect(html).toContain('Only an executive can approve this')
    expect(html).not.toContain('approve-button')
    // The decision itself is never hidden.
    expect(html).toContain('Authorize the eligibility review')
  })
})
