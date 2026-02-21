import { useState, useCallback } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ListOrdered } from 'lucide-react'
import CourseLibrary from './components/CourseLibrary'
import CourseDetail from './components/CourseDetail'
import QueuePanel from './components/QueuePanel'
import type { SSEMessage } from './types'
import { useSSE } from './hooks/useSSE'

function AppContent() {
  const [queueOpen, setQueueOpen] = useState(false)
  const [activeCount, setActiveCount] = useState(0)
  const [progressMap, setProgressMap] = useState<Record<number, SSEMessage['progress']>>({})

  const handleSSE = useCallback((msg: SSEMessage) => {
    const TERMINAL_STATUSES = new Set(['done', 'error', 'pending', 'queued'])

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
  }, [])

  useSSE(handleSSE)

  // Initial load of active count
  useState(() => {
    fetch('/api/queue').then(r => r.json()).then((items: unknown[]) => setActiveCount(items.length)).catch(() => {})
  })

  return (
    <>
      <div className="min-h-screen bg-slate-900 text-slate-100">
        {/* Fixed queue button */}
        <button
          onClick={() => setQueueOpen(true)}
          className="fixed bottom-5 right-5 z-40 flex items-center gap-2 bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-300 hover:text-white px-4 py-2.5 rounded-full shadow-lg transition-colors text-sm font-medium"
        >
          <ListOrdered size={16} />
          Queue
          {activeCount > 0 && (
            <span className="bg-indigo-600 text-white text-xs font-bold px-1.5 py-0.5 rounded-full min-w-[20px] text-center">
              {activeCount}
            </span>
          )}
        </button>

        <Routes>
          <Route path="/" element={<CourseLibrary />} />
          <Route path="/courses/:id" element={<CourseDetail />} />
        </Routes>
      </div>

      <QueuePanel open={queueOpen} onClose={() => setQueueOpen(false)} progressMap={progressMap} />
    </>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AppContent />
    </BrowserRouter>
  )
}
