import { NETWORK_MESSAGE } from './errors'

export type HealthState =
  | { kind: 'loading' }
  | { kind: 'ok' }
  | { kind: 'error' }

export async function fetchHealth(): Promise<boolean> {
  const response = await fetch('/api/health')
  if (!response.ok) {
    throw new Error('The health check did not answer.')
  }
  const body: unknown = await response.json()
  return (
    typeof body === 'object' &&
    body !== null &&
    'ok' in body &&
    (body as { ok: unknown }).ok === true
  )
}

export function healthMessage(state: HealthState): string {
  switch (state.kind) {
    case 'loading':
      return 'Checking CampusLens…'
    case 'ok':
      return 'CampusLens is running.'
    case 'error':
      return NETWORK_MESSAGE
  }
}
