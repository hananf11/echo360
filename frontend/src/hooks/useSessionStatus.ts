import { useState, useEffect, useCallback, useRef } from 'react'

// Refresh proactively once the token has less than this long to live (seconds).
const REFRESH_THRESHOLD_S = 3600 // 1h

interface SessionStatus {
  valid: boolean
  cookiesExist: boolean
  checking: boolean
  refreshing: boolean
  expiresIn: number | null
}

export function useSessionStatus() {
  const [status, setStatus] = useState<SessionStatus>({
    valid: true, // optimistic default
    cookiesExist: true,
    checking: true,
    refreshing: false,
    expiresIn: null,
  })
  const hasTriedRefresh = useRef(false)

  const tryAutoRefresh = useCallback(() => {
    if (hasTriedRefresh.current) return
    hasTriedRefresh.current = true
    setStatus(prev => ({ ...prev, refreshing: true }))

    fetch('/api/session/refresh', { method: 'POST' })
      .then(r => r.json())
      .then((data: { success: boolean }) => {
        if (data.success) {
          // Re-arm so the next time the new token nears expiry we refresh again
          hasTriedRefresh.current = false
          checkStatus()
        } else {
          setStatus(prev => ({ ...prev, refreshing: false }))
        }
      })
      .catch(() => {
        setStatus(prev => ({ ...prev, refreshing: false }))
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const checkStatus = useCallback(() => {
    fetch('/api/session/status')
      .then(r => r.json())
      .then((data: { valid: boolean; cookies_exist: boolean; expires_in: number | null }) => {
        setStatus(prev => ({
          ...prev,
          valid: data.valid,
          cookiesExist: data.cookies_exist,
          expiresIn: data.expires_in,
          checking: false,
          refreshing: false,
        }))
        const exp = data.expires_in
        if (exp !== null && exp > REFRESH_THRESHOLD_S) {
          // Comfortable window — re-arm proactive refresh for the next cycle
          hasTriedRefresh.current = false
        } else if (data.valid && exp !== null && exp <= REFRESH_THRESHOLD_S) {
          // Valid but expiring soon — renew now, before any scrape fails
          tryAutoRefresh()
        }
      })
      .catch(() => {
        setStatus(prev => ({ ...prev, checking: false }))
      })
  }, [tryAutoRefresh])

  useEffect(() => {
    checkStatus()
    const interval = setInterval(checkStatus, 60_000)
    return () => clearInterval(interval)
  }, [checkStatus])

  const markExpired = useCallback(() => {
    setStatus(prev => ({ ...prev, valid: false }))
    // Auto-try silent refresh once when we detect expiration
    tryAutoRefresh()
  }, [tryAutoRefresh])

  const markValid = useCallback(() => {
    hasTriedRefresh.current = false
    setStatus({ valid: true, cookiesExist: true, checking: false, refreshing: false, expiresIn: null })
  }, [])

  return { ...status, refresh: checkStatus, tryAutoRefresh, markExpired, markValid }
}
