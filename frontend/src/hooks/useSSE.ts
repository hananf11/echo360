import { useEffect } from 'react'
import type { SSEMessage } from '../types'

export function useSSE(onMessage: (msg: SSEMessage) => void, onReconnect?: () => void) {
  useEffect(() => {
    const es = new EventSource('/api/sse')
    es.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data) as SSEMessage)
      } catch {
        // ignore malformed messages
      }
    }
    es.onerror = () => {
      // SSE will auto-reconnect; re-fetch state in case we missed messages
      if (onReconnect) onReconnect()
    }
    return () => es.close()
  }, [onMessage, onReconnect])
}
