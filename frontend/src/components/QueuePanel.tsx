import { useState, useEffect, useCallback } from 'react'
import { X, Download, RefreshCw, Mic, AlertCircle, CheckCircle, Clock } from 'lucide-react'
import { getQueue, type QueueItem } from '../api'
import type { SSEMessage } from '../types'
import { useSSE } from '../hooks/useSSE'

function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case 'queued':
      return (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-400">
          <Clock size={10} /> Queued
        </span>
      )
    case 'downloading':
      return (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-indigo-900/60 text-indigo-300">
          <div className="w-2 h-2 border border-indigo-400 border-t-transparent rounded-full animate-spin" />
          Downloading
        </span>
      )
    case 'downloaded':
      return (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-blue-900/60 text-blue-300">
          <CheckCircle size={10} /> Downloaded
        </span>
      )
    case 'converting':
      return (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-amber-900/60 text-amber-300">
          <div className="w-2 h-2 border border-amber-400 border-t-transparent rounded-full animate-spin" />
          Converting
        </span>
      )
    case 'transcribing':
      return (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-violet-900/60 text-violet-300">
          <div className="w-2 h-2 border border-violet-400 border-t-transparent rounded-full animate-spin" />
          Transcribing
        </span>
      )
    case 'error':
      return (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-red-900/60 text-red-300">
          <AlertCircle size={10} /> Error
        </span>
      )
    default:
      return null
  }
}

function activeStatus(item: QueueItem): string {
  // Show the most interesting non-idle status
  if (!['pending', 'done'].includes(item.audio_status)) return item.audio_status
  if (!['pending', 'done'].includes(item.transcript_status)) return item.transcript_status
  return item.audio_status
}

interface Props {
  open: boolean
  onClose: () => void
  progressMap: Record<number, { done: number; total: number }>
}

export default function QueuePanel({ open, onClose, progressMap }: Props) {
  const [items, setItems] = useState<QueueItem[]>([])

  const load = useCallback(() => {
    getQueue().then(setItems).catch(() => {})
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const handleSSE = useCallback((msg: SSEMessage) => {
    if (msg.type === 'lecture_update' || msg.type === 'transcription_start' ||
        msg.type === 'transcription_done' || msg.type === 'transcription_error') {
      load()
    }
  }, [load])

  useSSE(handleSSE)

  if (!open) return null

  const downloading = items.filter(i => activeStatus(i) === 'downloading')
  const converting = items.filter(i => activeStatus(i) === 'converting' || activeStatus(i) === 'downloaded')
  const queued = items.filter(i => activeStatus(i) === 'queued')
  const transcribing = items.filter(i => activeStatus(i) === 'transcribing')
  const errors = items.filter(i => activeStatus(i) === 'error')

  const sections = [
    { label: 'Downloading', icon: <Download size={13} />, items: downloading, color: 'text-indigo-400' },
    { label: 'Converting', icon: <RefreshCw size={13} />, items: converting, color: 'text-amber-400' },
    { label: 'Queued', icon: <Clock size={13} />, items: queued, color: 'text-slate-400' },
    { label: 'Transcribing', icon: <Mic size={13} />, items: transcribing, color: 'text-violet-400' },
    { label: 'Errors', icon: <AlertCircle size={13} />, items: errors, color: 'text-red-400' },
  ].filter(s => s.items.length > 0)

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-full max-w-md bg-slate-800 border-l border-slate-700 shadow-2xl flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700">
          <h2 className="text-base font-semibold text-white">Queue</h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-slate-700 text-slate-400 hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {sections.length === 0 ? (
            <p className="text-sm text-slate-500 text-center py-8">Nothing in the queue.</p>
          ) : (
            <div className="flex flex-col gap-5">
              {sections.map(section => (
                <div key={section.label}>
                  <div className={`flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider mb-2 ${section.color}`}>
                    {section.icon}
                    {section.label} ({section.items.length})
                  </div>
                  <div className="flex flex-col gap-1.5">
                    {section.items.map(item => {
                      const progress = progressMap[item.id]
                      const status = activeStatus(item)
                      return (
                        <div key={item.id} className="bg-slate-700/50 rounded-lg px-3 py-2">
                          <div className="flex items-center justify-between gap-2">
                            <div className="min-w-0 flex-1">
                              <p className="text-sm text-slate-200 truncate">{item.title}</p>
                              <p className="text-xs text-slate-500 truncate">{item.course_name}</p>
                            </div>
                            <StatusBadge status={status} />
                          </div>
                          {status === 'downloading' && progress && (
                            <div className="mt-1.5">
                              <div className="flex items-center justify-between text-xs text-slate-500 mb-0.5">
                                <span>{progress.done}/{progress.total} segments</span>
                                <span>{Math.round((progress.done / progress.total) * 100)}%</span>
                              </div>
                              <div className="h-1 bg-slate-600 rounded-full overflow-hidden">
                                <div
                                  className="h-full bg-indigo-500 rounded-full transition-all duration-300"
                                  style={{ width: `${(progress.done / progress.total) * 100}%` }}
                                />
                              </div>
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
