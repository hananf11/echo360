import { useState } from 'react'
import { CheckCircle, AlertCircle, Clock, Play, Pause, Ban, ExternalLink } from 'lucide-react'
import { format } from 'timeago.js'
import type { Lecture, SSEMessage } from '../types'
import LecturePlayer from './LecturePlayer'

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function formatTimePair(done: number, total: number): string {
  const fmt = (s: number) => {
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}m ${sec}s`
  }
  return `${fmt(done)} / ${fmt(total)}`
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

function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  return d.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short' })
}


interface Props {
  lecture: Lecture
  hostname: string
  isLast: boolean
  selected: boolean
  onToggle: (id: number) => void
  progress?: SSEMessage['progress']
}

function StatusPill({ children, color, title }: { children: React.ReactNode; color: 'emerald' | 'violet' | 'amber' | 'indigo' | 'red' | 'slate'; title?: string }) {
  const colorMap = {
    emerald: 'text-emerald-400 bg-emerald-400/10',
    violet: 'text-violet-400 bg-violet-400/10',
    amber: 'text-amber-400 bg-amber-400/10',
    indigo: 'text-indigo-400 bg-indigo-400/10',
    red: 'text-red-400 bg-red-400/10',
    slate: 'text-slate-400 bg-slate-400/10',
  }
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full ${colorMap[color]}`} title={title}>
      {children}
    </span>
  )
}

function AudioStatus({ status, error }: { status: Lecture['audio_status']; error?: string | null }) {
  if (status === 'pending') return null
  if (status === 'queued') return <StatusPill color="slate"><Clock size={11} /> Queued</StatusPill>
  if (status === 'downloading') return (
    <StatusPill color="indigo">
      <div className="w-2.5 h-2.5 border-[1.5px] border-indigo-400 border-t-transparent rounded-full animate-spin" />
      Downloading
    </StatusPill>
  )
  if (status === 'downloaded') return <StatusPill color="indigo"><CheckCircle size={11} /> Downloaded</StatusPill>
  if (status === 'converting') return (
    <StatusPill color="amber">
      <div className="w-2.5 h-2.5 border-[1.5px] border-amber-400 border-t-transparent rounded-full animate-spin" />
      Converting
    </StatusPill>
  )
  if (status === 'done') return <StatusPill color="emerald"><CheckCircle size={11} /> Done</StatusPill>
  if (status === 'no_media') return <StatusPill color="slate"><Ban size={11} /> No media</StatusPill>
  return <StatusPill color="red" title={error || undefined}><AlertCircle size={11} /> Error</StatusPill>
}

function TranscriptStatus({ status, error }: { status: Lecture['transcript_status']; error?: string | null }) {
  if (status === 'pending') return null
  if (status === 'queued') return (
    <StatusPill color="violet">
      <Clock size={11} /> Transcription queued
    </StatusPill>
  )
  if (status === 'transcribing') return (
    <StatusPill color="violet">
      <div className="w-2.5 h-2.5 border-[1.5px] border-violet-400 border-t-transparent rounded-full animate-spin" />
      Transcribing
    </StatusPill>
  )
  if (status === 'done') return <StatusPill color="violet"><CheckCircle size={11} /> Transcribed</StatusPill>
  return <StatusPill color="red" title={error || undefined}><AlertCircle size={11} /> Transcript error</StatusPill>
}

function NotesStatus({ status, error }: { status: Lecture['notes_status']; error?: string | null }) {
  if (status === 'pending') return null
  if (status === 'queued') return (
    <StatusPill color="amber">
      <Clock size={11} /> Notes queued
    </StatusPill>
  )
  if (status === 'generating') return (
    <StatusPill color="amber">
      <div className="w-2.5 h-2.5 border-[1.5px] border-amber-400 border-t-transparent rounded-full animate-spin" />
      Generating
    </StatusPill>
  )
  if (status === 'done') return <StatusPill color="amber"><CheckCircle size={11} /> Notes</StatusPill>
  return <StatusPill color="red" title={error || undefined}><AlertCircle size={11} /> Notes error</StatusPill>
}

function ProgressBar({ progress }: { progress: NonNullable<SSEMessage['progress']> }) {
  if (!progress.total || progress.total <= 0) return null

  const pct = Math.min(100, (progress.done / progress.total) * 100)
  const stage = progress.stage ?? 'download'

  const colors = {
    download: { bar: 'bg-indigo-500', track: 'bg-slate-700' },
    convert: { bar: 'bg-amber-500', track: 'bg-slate-700' },
  }
  const { bar, track } = colors[stage]

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
      <div className={`h-1 ${track} rounded-full overflow-hidden`}>
        <div
          className={`h-full ${bar} rounded-full transition-all duration-300`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export default function LectureRow({ lecture, hostname, isLast, selected, onToggle, progress }: Props) {
  const [playerOpen, setPlayerOpen] = useState(false)

  const hasTranscript = lecture.transcript_status === 'done'
  const canPlay = lecture.audio_status === 'done'
  const showProgress = progress && progress.total > 0

  return (
    <>
      <tr
        className={`hover:bg-slate-700/30 transition-colors ${
          !isLast && !playerOpen ? 'border-b border-slate-700/40' : ''
        } ${selected ? 'bg-indigo-500/5' : ''}`}
      >
        {/* Checkbox */}
        <td className="pl-4 pr-1 py-3 w-8">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggle(lecture.id)}
            className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-0 cursor-pointer"
          />
        </td>

        {/* Date */}
        <td className="px-4 py-3 whitespace-nowrap">
          <span className="font-mono text-xs text-slate-500">{formatDate(lecture.date)}</span>
          <span className="text-[10px] text-slate-600 ml-1.5">{format(lecture.date)}</span>
        </td>

        {/* Title + progress */}
        <td className="px-4 py-3 text-sm text-slate-200">
          <div>{lecture.title}</div>
          {lecture.notes_generated_title && (
            <div className="text-xs text-slate-400 mt-0.5">{lecture.notes_generated_title}</div>
          )}
          {showProgress && <ProgressBar progress={progress} />}
        </td>

        {/* Duration */}
        <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
          {lecture.duration_seconds ? formatDuration(lecture.duration_seconds) : '—'}
        </td>

        {/* Status pills */}
        <td className="px-4 py-3">
          <div className="flex items-center gap-1.5 flex-wrap justify-end">
            <NotesStatus status={lecture.notes_status} error={lecture.error_message} />
            <TranscriptStatus status={lecture.transcript_status} error={lecture.error_message} />
            <AudioStatus status={lecture.audio_status} error={lecture.error_message} />
          </div>
        </td>

        {/* Actions */}
        <td className="px-4 py-3">
          <div className="flex items-center justify-end gap-1">
            {hostname && (
              <a
                href={`${hostname}/lesson/${lecture.echo_id}/classroom`}
                target="_blank"
                rel="noopener noreferrer"
                className="p-1.5 rounded-md transition-colors bg-slate-700/50 hover:bg-slate-600 text-slate-500 hover:text-white"
                title="Open in Echo360"
              >
                <ExternalLink size={13} />
              </a>
            )}
            {canPlay && (
              <button
                onClick={() => setPlayerOpen(o => !o)}
                className={`p-1.5 rounded-md transition-colors ${
                  playerOpen
                    ? 'bg-indigo-600 text-white'
                    : 'bg-slate-700/50 hover:bg-indigo-600 text-slate-500 hover:text-white'
                }`}
                title={playerOpen ? 'Hide player' : 'Play audio'}
              >
                {playerOpen ? <Pause size={13} /> : <Play size={13} />}
              </button>
            )}
          </div>
        </td>
      </tr>
      {playerOpen && (
        <LecturePlayer
          lectureId={lecture.id}
          hasTranscript={hasTranscript}
          hasNotes={lecture.notes_status === 'done'}
          framesStatus={lecture.frames_status}
          isLast={isLast}
        />
      )}
    </>
  )
}
