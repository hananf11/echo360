import { useState, useEffect, useCallback, useRef } from 'react'
import { X, Download, Mic, Sparkles, AlertCircle, Clock } from 'lucide-react'
import { getQueue, type QueueItem } from '../api'
import type { SSEMessage } from '../types'
import { useSSE } from '../hooks/useSSE'

// ── Shared helpers ──────────────────────────────────────────────────────────

function ElapsedTimer({ startTime }: { startTime: number }) {
  const [elapsed, setElapsed] = useState(0)
  const intervalRef = useRef<ReturnType<typeof setInterval>>()

  useEffect(() => {
    const tick = () => setElapsed(Math.floor((Date.now() - startTime) / 1000))
    tick()
    intervalRef.current = setInterval(tick, 1000)
    return () => clearInterval(intervalRef.current)
  }, [startTime])

  const m = Math.floor(elapsed / 60)
  const s = elapsed % 60
  return (
    <span className="text-[10px] text-slate-500 tabular-nums">
      {m}:{s.toString().padStart(2, '0')}
    </span>
  )
}

function formatSpeed(bps: number): string {
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(1)} MB/s`
  if (bps >= 1_000) return `${(bps / 1_000).toFixed(0)} KB/s`
  return `${bps} B/s`
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  if (m < 60) return `${m}m ${s}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

function formatTimePair(done: number, total: number): string {
  const fmt = (s: number) => {
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}m ${sec}s`
  }
  return `${fmt(done)} / ${fmt(total)}`
}

function ProgressBar({ progress }: { progress: NonNullable<SSEMessage['progress']> }) {
  if (!progress.total || progress.total <= 0) return null

  const pct = Math.min(100, (progress.done / progress.total) * 100)
  const stage = progress.stage ?? 'download'

  const colors = { download: 'bg-indigo-500', convert: 'bg-amber-500' }

  let label = ''
  if (stage === 'download') {
    label = `${Math.round(pct)}%`
    if (progress.speed_bps) label += ` · ${formatSpeed(progress.speed_bps)}`
    if (progress.eta_seconds !== undefined) label += ` · ETA ${formatEta(progress.eta_seconds)}`
  } else {
    label = formatTimePair(progress.done, progress.total)
  }

  return (
    <div className="mt-1.5">
      <div className="flex items-center justify-between text-[10px] text-slate-500 mb-0.5">
        <span>{label}</span>
        <span>{Math.round(pct)}%</span>
      </div>
      <div className="h-1 bg-slate-600 rounded-full overflow-hidden">
        <div
          className={`h-full ${colors[stage]} rounded-full transition-all duration-300`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// ── Lane types ──────────────────────────────────────────────────────────────

interface LaneItem {
  item: QueueItem
  status: 'active' | 'queued' | 'error'
  statusLabel: string
}

function buildLanes(items: QueueItem[]) {
  const downloads: LaneItem[] = []
  const transcriptions: LaneItem[] = []
  const notes: LaneItem[] = []

  for (const item of items) {
    // Downloads lane
    if (!['pending', 'done', 'no_media'].includes(item.audio_status)) {
      const isActive = ['downloading', 'converting', 'downloaded'].includes(item.audio_status)
      const isError = item.audio_status === 'error'
      const statusLabel =
        item.audio_status === 'downloading' ? 'Downloading' :
        item.audio_status === 'converting' ? 'Converting' :
        item.audio_status === 'downloaded' ? 'Downloaded' :
        item.audio_status === 'queued' ? 'Queued' :
        item.audio_status === 'error' ? 'Error' : item.audio_status
      downloads.push({ item, status: isError ? 'error' : isActive ? 'active' : 'queued', statusLabel })
    }

    // Transcriptions lane
    if (!['pending', 'done'].includes(item.transcript_status)) {
      const isActive = item.transcript_status === 'transcribing'
      const isError = item.transcript_status === 'error'
      const statusLabel =
        item.transcript_status === 'transcribing' ? 'Transcribing' :
        item.transcript_status === 'queued' ? 'Queued' :
        item.transcript_status === 'error' ? 'Error' : item.transcript_status
      transcriptions.push({ item, status: isError ? 'error' : isActive ? 'active' : 'queued', statusLabel })
    }

    // Notes lane
    if (!['pending', 'done'].includes(item.notes_status)) {
      const isActive = item.notes_status === 'generating'
      const isError = item.notes_status === 'error'
      const statusLabel =
        item.notes_status === 'generating' ? 'Generating' :
        item.notes_status === 'queued' ? 'Queued' :
        item.notes_status === 'error' ? 'Error' : item.notes_status
      notes.push({ item, status: isError ? 'error' : isActive ? 'active' : 'queued', statusLabel })
    }
  }

  // Sort each lane: active first, then queued, then errors
  const sortOrder = { active: 0, queued: 1, error: 2 }
  const sort = (a: LaneItem, b: LaneItem) => sortOrder[a.status] - sortOrder[b.status] || a.item.id - b.item.id

  downloads.sort(sort)
  transcriptions.sort(sort)
  notes.sort(sort)

  return { downloads, transcriptions, notes }
}

// ── Lane component ──────────────────────────────────────────────────────────

interface LaneProps {
  label: string
  icon: React.ReactNode
  color: string
  accentColor: string
  items: LaneItem[]
  progressMap: Record<number, SSEMessage['progress']>
  startTimes: Record<number, number>
}

function Lane({ label, icon, color, accentColor, items, progressMap, startTimes }: LaneProps) {
  if (items.length === 0) return null

  const activeCount = items.filter(i => i.status === 'active').length
  const queuedCount = items.filter(i => i.status === 'queued').length
  const errorCount = items.filter(i => i.status === 'error').length

  const summary = [
    activeCount > 0 && `${activeCount} active`,
    queuedCount > 0 && `${queuedCount} queued`,
    errorCount > 0 && `${errorCount} error`,
  ].filter(Boolean).join(', ')

  return (
    <div>
      <div className={`flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider mb-2 ${color}`}>
        {icon}
        {label}
        <span className="text-slate-500 font-normal normal-case">— {summary}</span>
      </div>
      <div className="flex flex-col gap-1">
        {items.map(({ item, status, statusLabel }) => {
          const progress = progressMap[item.id]
          const started = startTimes[item.id]
          return (
            <div key={`${label}-${item.id}`} className="bg-slate-700/50 rounded-lg px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm text-slate-200 truncate">{item.title}</p>
                    {status === 'active' && started && <ElapsedTimer startTime={started} />}
                  </div>
                  <p className="text-xs text-slate-500 truncate">{item.course_name}</p>
                </div>
                <StatusPill status={status} label={statusLabel} accentColor={accentColor} />
              </div>
              {status === 'active' && progress && <ProgressBar progress={progress} />}
              {status === 'error' && item.error_message && (
                <p className="text-xs text-red-400/80 mt-1 truncate" title={item.error_message}>
                  {item.error_message}
                </p>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function StatusPill({ status, label, accentColor }: { status: 'active' | 'queued' | 'error'; label: string; accentColor: string }) {
  if (status === 'active') {
    return (
      <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${accentColor}`}>
        <div className="w-2 h-2 border border-current border-t-transparent rounded-full animate-spin" />
        {label}
      </span>
    )
  }
  if (status === 'error') {
    return (
      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-red-900/60 text-red-300">
        <AlertCircle size={10} /> {label}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-slate-700 text-slate-400">
      <Clock size={10} /> {label}
    </span>
  )
}

// ── Main panel ──────────────────────────────────────────────────────────────

const ACTIVE_STATUSES = new Set(['downloading', 'converting', 'transcribing', 'generating'])

interface Props {
  open: boolean
  onClose: () => void
  progressMap: Record<number, SSEMessage['progress']>
}

export default function QueuePanel({ open, onClose, progressMap }: Props) {
  const [items, setItems] = useState<QueueItem[]>([])
  const [startTimes, setStartTimes] = useState<Record<number, number>>({})

  const load = useCallback(() => {
    getQueue().then(setItems).catch(() => {})
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const handleSSE = useCallback((msg: SSEMessage) => {
    if (msg.type === 'lecture_update' || msg.type === 'transcription_start' ||
        msg.type === 'transcription_done' || msg.type === 'transcription_error' ||
        msg.type === 'notes_start' || msg.type === 'notes_done' || msg.type === 'notes_error') {
      load()
    }
    // Track when items become active
    if (msg.lecture_id !== undefined) {
      const s = msg.status ?? (msg.type === 'notes_start' ? 'generating' : msg.type === 'transcription_start' ? 'transcribing' : undefined)
      if (s && ACTIVE_STATUSES.has(s)) {
        setStartTimes(prev => prev[msg.lecture_id!] ? prev : { ...prev, [msg.lecture_id!]: Date.now() })
      }
      if (msg.type === 'transcription_done' || msg.type === 'transcription_error' ||
          msg.type === 'notes_done' || msg.type === 'notes_error' ||
          (msg.status && !ACTIVE_STATUSES.has(msg.status))) {
        setStartTimes(prev => {
          const next = { ...prev }
          delete next[msg.lecture_id!]
          return next
        })
      }
    }
  }, [load])

  useSSE(handleSSE)

  if (!open) return null

  const { downloads, transcriptions, notes } = buildLanes(items)
  const totalItems = downloads.length + transcriptions.length + notes.length

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
          {totalItems === 0 ? (
            <p className="text-sm text-slate-500 text-center py-8">Nothing in the queue.</p>
          ) : (
            <div className="flex flex-col gap-5">
              <Lane
                label="Downloads"
                icon={<Download size={13} />}
                color="text-indigo-400"
                accentColor="bg-indigo-900/60 text-indigo-300"
                items={downloads}
                progressMap={progressMap}
                startTimes={startTimes}
              />
              <Lane
                label="Transcriptions"
                icon={<Mic size={13} />}
                color="text-violet-400"
                accentColor="bg-violet-900/60 text-violet-300"
                items={transcriptions}
                progressMap={progressMap}
                startTimes={startTimes}
              />
              <Lane
                label="Notes"
                icon={<Sparkles size={13} />}
                color="text-amber-400"
                accentColor="bg-amber-900/60 text-amber-300"
                items={notes}
                progressMap={progressMap}
                startTimes={startTimes}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
