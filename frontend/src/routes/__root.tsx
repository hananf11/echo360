import { useState, useEffect, useCallback, createContext, useContext } from 'react'
import { createRootRoute, Outlet } from '@tanstack/react-router'
import QueuePanel from '../components/QueuePanel'
import LoginModal from '../components/LoginModal'
import type { SSEMessage } from '../types'
import { useSSE } from '../hooks/useSSE'
import { useSessionStatus } from '../hooks/useSessionStatus'

const TERMINAL_STATUSES = new Set(['done', 'error', 'pending', 'queued', 'downloaded'])

export interface RootContext {
  activeCount: number
  onOpenQueue: () => void
  sessionValid: boolean
  sessionRefreshing: boolean
  onOpenLogin: () => void
}

const RootCtx = createContext<RootContext>({ activeCount: 0, onOpenQueue: () => {}, sessionValid: true, sessionRefreshing: false, onOpenLogin: () => {} })
export const useRootContext = () => useContext(RootCtx)

export const Route = createRootRoute({
  component: RootComponent,
})

function RootComponent() {
  const [queueOpen, setQueueOpen] = useState(false)
  const [loginOpen, setLoginOpen] = useState(false)
  const [activeCount, setActiveCount] = useState(0)
  const [progressMap, setProgressMap] = useState<Record<number, SSEMessage['progress']>>({})
  const session = useSessionStatus()

  const handleSSE = useCallback((msg: SSEMessage) => {
    if (msg.type === 'session_expired') {
      session.markExpired()
    }
    if (msg.type === 'lecture_update' && msg.lecture_id !== undefined) {
      if (msg.progress) {
        setProgressMap(prev => ({ ...prev, [msg.lecture_id!]: msg.progress! }))
      }
      if (msg.status && TERMINAL_STATUSES.has(msg.status)) {
        setProgressMap(prev => {
          const next = { ...prev }
          delete next[msg.lecture_id!]
          return next
        })
      }
      if (msg.status) {
        fetch('/api/queue').then(r => r.json()).then((items: unknown[]) => setActiveCount(items.length)).catch(() => {})
      }
    }
    if (msg.type === 'transcription_done' || msg.type === 'transcription_error') {
      if (msg.lecture_id !== undefined) {
        setProgressMap(prev => {
          const next = { ...prev }
          delete next[msg.lecture_id!]
          return next
        })
      }
      fetch('/api/queue').then(r => r.json()).then((items: unknown[]) => setActiveCount(items.length)).catch(() => {})
    }
    if (msg.type === 'transcription_start') {
      fetch('/api/queue').then(r => r.json()).then((items: unknown[]) => setActiveCount(items.length)).catch(() => {})
    }
    if (msg.type === 'notes_start' || msg.type === 'notes_done' || msg.type === 'notes_error') {
      fetch('/api/queue').then(r => r.json()).then((items: unknown[]) => setActiveCount(items.length)).catch(() => {})
    }
  }, [session])

  useSSE(handleSSE)

  useEffect(() => {
    fetch('/api/queue').then(r => r.json()).then((items: unknown[]) => setActiveCount(items.length)).catch(() => {})
  }, [])

  const handleLoginSuccess = useCallback(() => {
    session.markValid()
  }, [session])

  return (
    <RootCtx.Provider value={{
      activeCount,
      onOpenQueue: () => setQueueOpen(true),
      sessionValid: session.valid,
      sessionRefreshing: session.refreshing,
      onOpenLogin: () => setLoginOpen(true),
    }}>
      <Outlet />
      <QueuePanel open={queueOpen} onClose={() => setQueueOpen(false)} progressMap={progressMap} />
      <LoginModal
        open={loginOpen}
        onClose={() => setLoginOpen(false)}
        onLoginSuccess={handleLoginSuccess}
      />
    </RootCtx.Provider>
  )
}
