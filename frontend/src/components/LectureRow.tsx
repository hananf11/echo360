import { useState } from 'react'
import { Download, CheckCircle, AlertCircle, Clock, Mic, Play, Pause } from 'lucide-react'
import { downloadLecture, transcribeLecture } from '../api'
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

interface Props {
  lecture: Lecture
  isLast: boolean
  transcribeModel?: string
  progress?: SSEMessage['progress']
}

function AudioStatusIcon({ status }: { status: Lecture['audio_status'] }) {
  if (status === 'pending') return null
  if (status === 'queued') return <Clock size={15} className="text-slate-500" />
  if (status === 'downloading')
    return <div className="w-3.5 h-3.5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
  if (status === 'downloaded')
    return <CheckCircle size={15} className="text-blue-400" />
  if (status === 'converting')
    return <div className="w-3.5 h-3.5 border-2 border-amber-400 border-t-transparent rounded-full animate-spin" />
  if (status === 'done') return <CheckCircle size={15} className="text-emerald-400" />
  return <AlertCircle size={15} className="text-red-400" />
}

function TranscriptStatusIcon({ status }: { status: Lecture['transcript_status'] }) {
  if (status === 'queued' || status === 'transcribing')
    return <div className="w-3.5 h-3.5 border-2 border-violet-400 border-t-transparent rounded-full animate-spin" />
  if (status === 'done') return <CheckCircle size={15} className="text-violet-400" />
  if (status === 'error') return <AlertCircle size={15} className="text-red-400" />
  return null
}

const AUDIO_STATUS_LABEL: Record<Lecture['audio_status'], string> = {
  pending: '',
  queued: 'Queued…',
  downloading: 'Downloading…',
  downloaded: 'Downloaded',
  converting: 'Converting…',
  done: 'Done',
  error: 'Error',
}

const TRANSCRIPT_STATUS_LABEL: Record<Lecture['transcript_status'], string> = {
  pending: '',
  queued: 'Queued…',
  transcribing: 'Transcribing…',
  done: 'Transcribed',
  error: 'Transcript error',
}

function ProgressBar({ progress }: { progress: NonNullable<SSEMessage['progress']> }) {
  if (!progress.total || progress.total <= 0) return null

  const pct = Math.min(100, (progress.done / progress.total) * 100)
  const stage = progress.stage ?? 'download'

  const colors = {
    download: { bar: 'bg-indigo-500', track: 'bg-slate-600' },
    convert: { bar: 'bg-amber-500', track: 'bg-slate-600' },
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
    <div className="mt-1">
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

export default function LectureRow({ lecture, isLast, transcribeModel = 'modal', progress }: Props) {
  const [playerOpen, setPlayerOpen] = useState(false)

  const transcriptLabel = TRANSCRIPT_STATUS_LABEL[lecture.transcript_status]
  const hasTranscript = lecture.transcript_status === 'done'
  const canPlay = lecture.audio_status === 'done'

  const showProgress = progress && progress.total > 0

  return (
    <>
      <tr
        className={`hover:bg-slate-700/40 transition-colors ${
          !isLast && !playerOpen ? 'border-b border-slate-700/60' : ''
        }`}
      >
        <td className="px-5 py-3 text-sm text-slate-400 whitespace-nowrap font-mono">
          {lecture.date}
        </td>
        <td className="px-5 py-3 text-sm text-slate-100">
          <div>{lecture.title}</div>
          {showProgress && <ProgressBar progress={progress} />}
        </td>
        <td className="px-5 py-3 text-sm text-slate-500 whitespace-nowrap">
          {lecture.duration_seconds ? formatDuration(lecture.duration_seconds) : '—'}
        </td>
        <td className="px-5 py-3">
          <div className="flex items-center justify-end gap-2.5">

            {/* Transcript status */}
            {transcriptLabel && (
              <span className="text-xs text-slate-500" title={lecture.transcript_status === 'error' && lecture.error_message ? lecture.error_message : undefined}>
                {transcriptLabel}
              </span>
            )}
            <TranscriptStatusIcon status={lecture.transcript_status} />

            {/* Transcribe button */}
            {canPlay && (lecture.transcript_status === 'pending' || lecture.transcript_status === 'error') && (
              <button
                onClick={() => transcribeLecture(lecture.id, transcribeModel)}
                className="p-1.5 rounded-lg bg-slate-700 hover:bg-violet-600 text-slate-400 hover:text-white transition-colors"
                title="Transcribe audio"
              >
                <Mic size={13} />
              </button>
            )}

            {/* Play / expand button */}
            {canPlay && (
              <button
                onClick={() => setPlayerOpen(o => !o)}
                className={`p-1.5 rounded-lg transition-colors ${
                  playerOpen
                    ? 'bg-indigo-600 text-white'
                    : 'bg-slate-700 hover:bg-indigo-600 text-slate-400 hover:text-white'
                }`}
                title={playerOpen ? 'Hide player' : 'Play audio'}
              >
                {playerOpen ? <Pause size={13} /> : <Play size={13} />}
              </button>
            )}

            {/* Audio status */}
            <span className="text-xs text-slate-500" title={lecture.error_message || undefined}>
              {AUDIO_STATUS_LABEL[lecture.audio_status]}
            </span>
            <AudioStatusIcon status={lecture.audio_status} />

            {/* Download button */}
            {(lecture.audio_status === 'pending' || lecture.audio_status === 'error') && (
              <button
                onClick={() => downloadLecture(lecture.id)}
                className="p-1.5 rounded-lg bg-slate-700 hover:bg-indigo-600 text-slate-400 hover:text-white transition-colors"
                title="Download audio"
              >
                <Download size={13} />
              </button>
            )}
          </div>
        </td>
      </tr>
      {playerOpen && (
        <LecturePlayer
          lectureId={lecture.id}
          hasTranscript={hasTranscript}
          isLast={isLast}
        />
      )}
    </>
  )
}
