import { useState } from 'react'
import { Download, CheckCircle, AlertCircle, Clock, Mic, Play, Pause } from 'lucide-react'
import { downloadLecture, transcribeLecture } from '../api'
import type { Lecture } from '../types'
import LecturePlayer from './LecturePlayer'

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

interface Props {
  lecture: Lecture
  isLast: boolean
  transcribeModel?: string
  progress?: { done: number; total: number }
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
  done: '',
  error: 'Transcript error',
}

export default function LectureRow({ lecture, isLast, transcribeModel = 'tiny', progress }: Props) {
  const [playerOpen, setPlayerOpen] = useState(false)

  const transcriptLabel = TRANSCRIPT_STATUS_LABEL[lecture.transcript_status]
  const hasTranscript = lecture.transcript_status === 'done'
  const canPlay = lecture.audio_status === 'done'

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
        <td className="px-5 py-3 text-sm text-slate-100">{lecture.title}</td>
        <td className="px-5 py-3 text-sm text-slate-500 whitespace-nowrap">
          {lecture.duration_seconds ? formatDuration(lecture.duration_seconds) : '—'}
        </td>
        <td className="px-5 py-3">
          <div className="flex items-center justify-end gap-2.5">

            {/* Transcript status */}
            {transcriptLabel && (
              <span className="text-xs text-slate-500">{transcriptLabel}</span>
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
            <span className="text-xs text-slate-500">
              {lecture.audio_status === 'downloading' && progress
                ? `${progress.done}/${progress.total} segments`
                : AUDIO_STATUS_LABEL[lecture.audio_status]}
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
