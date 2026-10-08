import { useCallback, useEffect, useState } from 'react'

import { friendlyLoadError } from '../errors'
import { fetchStaff, type StaffEmployee } from '../staff'
import { DataAccessPanel, type AccessGrant } from './AccountPanels'

/**
 * The "AI employees and data access" page with its data: the AI staff from
 * GET /staff (loaded each time the page opens, so today's counts are
 * current), plus what the briefing's employees were given on the latest run
 * when the caller has it.
 */
export function AiStaffPanel({
  grants,
  error = null,
  onRetry,
}: {
  grants?: AccessGrant[] | null
  error?: string | null
  onRetry?: () => void
}) {
  const [staff, setStaff] = useState<StaffEmployee[] | null>(null)
  const [staffError, setStaffError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setStaffError(null)
    try {
      setStaff((await fetchStaff()).employees)
    } catch (failure) {
      setStaffError(friendlyLoadError(failure))
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the fetch's setState lands after an await
    void load()
  }, [load])

  return (
    <DataAccessPanel
      staff={staff}
      staffError={staffError}
      onRetryStaff={() => void load()}
      grants={grants}
      error={error}
      onRetry={onRetry}
    />
  )
}
