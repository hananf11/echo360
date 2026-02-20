import { useState } from 'react'
import { Download, CheckCircle, AlertCircle, Clock, Mic, FileText } from 'lucide-react'
import { downloadLecture, transcribeLecture } from '../api'
import type { Lecture } from '../types'
import TranscriptPanel from './TranscriptPanel'

interface Props {
  lecture: Lecture
  isLast: boolean
}

function AudioStatusIcon({ status }: { status: Lecture['audio_status'] }) {
  if (status === 'pending') return <Clock size={15} className="text-slate-500" />
  if (status === 'downloading')
    return (
      <div className="w-3.5 h-3.5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
    )
  if (status === 'done') return <CheckCircle size={15} className="text-emerald-400" />
  return <AlertCircle size={15} className="text-red-400" />
}

function TranscriptStatusIcon({ status }: { status: Lecture['transcript_status'] }) {
  if (status === 'queued' || status === 'transcribing')
    return (
      <div className="w-3.5 h-3.5 border-2 border-violet-400 border-t-transparent rounded-full animate-spin" />
    )
  if (status === 'done') return <FileText size={15} className="text-violet-400" />
  if (status === 'error') return <AlertCircle size={15} className="text-red-400" />
  return null
}

const AUDIO_STATUS_LABEL: Record<Lecture['audio_status'], string> = {
  pending: 'Pending',
  downloading: 'Downloading…',
  done: 'Done',
  error: 'Error',
}

const TRANSCRIPT_STATUS_LABEL: Record<Lecture['transcript_status'], string> = {
  pending: '',
  queued: 'Queued…',
  transcribing: 'Transcribing…',
  done: 'Transcript',
  error: 'Transcript error',
}

export default function LectureRow({ lecture, isLast }: Props) {
  const [transcriptOpen, setTranscriptOpen] = useState(false)

  const transcriptLabel = TRANSCRIPT_STATUS_LABEL[lecture.transcript_status]

  return (
    <>
      <tr
        className={`hover:bg-slate-700/40 transition-colors ${
          !isLast && !transcriptOpen ? 'border-b border-slate-700/60' : ''
        }`}
      >
        <td className="px-5 py-3 text-sm text-slate-400 whitespace-nowrap font-mono">
          {lecture.date}
        </td>
        <td className="px-5 py-3 text-sm text-slate-100">{lecture.title}</td>
        <td className="px-5 py-3">
          <div className="flex items-center justify-end gap-2.5">
            {/* Transcript section */}
            {transcriptLabel && (
              <span className="text-xs text-slate-500">{transcriptLabel}</span>
            )}
            <TranscriptStatusIcon status={lecture.transcript_status} />
            {lecture.transcript_status === 'done' && (
              <button
                onClick={() => setTranscriptOpen(o => !o)}
                className={`p-1.5 rounded-lg transition-colors ${
                  transcriptOpen
                    ? 'bg-violet-600 text-white'
                    : 'bg-slate-700 hover:bg-violet-600 text-slate-400 hover:text-white'
                }`}
                title={transcriptOpen ? 'Hide transcript' : 'Show transcript'}
              >
                <FileText size={13} />
              </button>
            )}
            {lecture.audio_status === 'done' &&
              (lecture.transcript_status === 'pending' ||
                lecture.transcript_status === 'error') && (
                <button
                  onClick={() => transcribeLecture(lecture.id)}
                  className="p-1.5 rounded-lg bg-slate-700 hover:bg-violet-600 text-slate-400 hover:text-white transition-colors"
                  title="Transcribe audio"
                >
                  <Mic size={13} />
                </button>
              )}

            {/* Audio section */}
            <span className="text-xs text-slate-500">
              {AUDIO_STATUS_LABEL[lecture.audio_status]}
            </span>
            <AudioStatusIcon status={lecture.audio_status} />
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
      {transcriptOpen && (
        <TranscriptPanel lectureId={lecture.id} isLast={isLast} />
      )}
    </>
  )
}
