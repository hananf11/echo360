import { useState, useEffect, useCallback, useRef } from 'react'

interface SessionStatus {
  valid: boolean
  cookiesExist: boolean
  checking: boolean
  refreshing: boolean
}

export function useSessionStatus() {
  const [status, setStatus] = useState<SessionStatus>({
    valid: true, // optimistic default
    cookiesExist: true,
    checking: true,
    refreshing: false,
  })
  const hasTriedRefresh = useRef(false)

  const checkStatus = useCallback(() => {
    fetch('/api/session/status')
      .then(r => r.json())
      .then((data: { valid: boolean; cookies_exist: boolean }) => {
        setStatus(prev => ({ ...prev, valid: data.valid, cookiesExist: data.cookies_exist, checking: false }))
      })
      .catch(() => {
        setStatus(prev => ({ ...prev, checking: false }))
      })
  }, [])

  const tryAutoRefresh = useCallback(() => {
    if (hasTriedRefresh.current) return
    hasTriedRefresh.current = true
    setStatus(prev => ({ ...prev, refreshing: true }))

    fetch('/api/session/refresh', { method: 'POST' })
      .then(r => r.json())
      .then((data: { success: boolean }) => {
        if (data.success) {
          setStatus({ valid: true, cookiesExist: true, checking: false, refreshing: false })
        } else {
          setStatus(prev => ({ ...prev, refreshing: false }))
        }
      })
      .catch(() => {
        setStatus(prev => ({ ...prev, refreshing: false }))
      })
  }, [])

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
    setStatus({ valid: true, cookiesExist: true, checking: false, refreshing: false })
  }, [])

  return { ...status, refresh: checkStatus, tryAutoRefresh, markExpired, markValid }
}
