export type HealthState =
  | { kind: 'loading' }
  | { kind: 'ok' }
  | { kind: 'error' }

export async function fetchHealth(): Promise<boolean> {
  const response = await fetch('/api/health')
  if (!response.ok) {
    throw new Error(`health check failed: ${response.status}`)
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
      return 'Checking API health…'
    case 'ok':
      return 'API healthy'
    case 'error':
      return 'API unreachable. Is `make api` running?'
  }
}
