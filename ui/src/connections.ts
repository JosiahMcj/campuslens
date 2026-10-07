// Typed client for Institution settings' outside connections
// (GET /api/admin/connections): whether the Ellucian import is configured
// and when it last ran, and where "Send to office" delivers. The API sends
// whether each setting is set, never a value, a key or a file path.

import { apiFailure } from './adminErrors'
import { apiFetch } from './auth'

export interface ConnectionSetting {
  label: string
  set: boolean
}

export interface Connections {
  ellucian: {
    configured: boolean
    settings: ConnectionSetting[]
    last_import: { name: string; at: string; in_use: boolean } | null
  }
  outbound: { provider: 'outbox' | 'smtp' }
}

export async function fetchConnections(): Promise<Connections> {
  const response = await apiFetch('/admin/connections')
  if (!response.ok) throw await apiFailure(response)
  return (await response.json()) as Connections
}
