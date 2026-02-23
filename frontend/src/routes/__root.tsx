import { useState, useEffect, useCallback, createContext, useContext } from 'react'
import { createRootRoute, Outlet } from '@tanstack/react-router'
import QueuePanel from '../components/QueuePanel'
import type { SSEMessage } from '../types'
import { useSSE } from '../hooks/useSSE'

const TERMINAL_STATUSES = new Set(['done', 'error', 'pending', 'queued', 'downloaded'])

export interface RootContext {
  activeCount: number
  onOpenQueue: () => void
}

const RootCtx = createContext<RootContext>({ activeCount: 0, onOpenQueue: () => {} })
export const useRootContext = () => useContext(RootCtx)

export const Route = createRootRoute({
  component: RootComponent,
})

function RootComponent() {
  const [queueOpen, setQueueOpen] = useState(false)
  const [activeCount, setActiveCount] = useState(0)
  const [progressMap, setProgressMap] = useState<Record<number, SSEMessage['progress']>>({})

  const handleSSE = useCallback((msg: SSEMessage) => {
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
  }, [])

  useSSE(handleSSE)

  useEffect(() => {
    fetch('/api/queue').then(r => r.json()).then((items: unknown[]) => setActiveCount(items.length)).catch(() => {})
  }, [])

  return (
    <RootCtx.Provider value={{ activeCount, onOpenQueue: () => setQueueOpen(true) }}>
      <Outlet />
      <QueuePanel open={queueOpen} onClose={() => setQueueOpen(false)} progressMap={progressMap} />
    </RootCtx.Provider>
  )
}
