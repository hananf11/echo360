import { useState, useEffect } from 'react'
import { getTranscript } from '../api'
import type { Transcript } from '../types'

interface Props {
  lectureId: number
  isLast: boolean
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function TranscriptPanel({ lectureId, isLast }: Props) {
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getTranscript(lectureId)
      .then(setTranscript)
      .catch(e => setError(e.message))
  }, [lectureId])

  const rowClass = `${!isLast ? 'border-b border-slate-700/60' : ''}`

  if (error) {
    return (
      <tr className={rowClass}>
        <td colSpan={3} className="px-5 pb-4">
          <p className="text-xs text-red-400">Failed to load transcript: {error}</p>
        </td>
      </tr>
    )
  }

  if (!transcript) {
    return (
      <tr className={rowClass}>
        <td colSpan={3} className="px-5 pb-4">
          <p className="text-xs text-slate-500">Loading transcript…</p>
        </td>
      </tr>
    )
  }

  return (
    <tr className={rowClass}>
      <td colSpan={3} className="px-5 pb-4">
        <div className="bg-slate-900/60 rounded-lg border border-slate-700 p-4 max-h-72 overflow-y-auto">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Transcript
            </span>
            <span className="text-xs bg-slate-700 text-slate-400 px-2 py-0.5 rounded-full">
              {transcript.model}
            </span>
          </div>
          <div className="space-y-1.5">
            {transcript.segments.map((seg, i) => (
              <div key={i} className="flex gap-3 text-sm">
                <span className="text-slate-500 font-mono shrink-0 text-xs pt-0.5">
                  [{formatTime(seg.start)}]
                </span>
                <span className="text-slate-300 leading-relaxed">{seg.text}</span>
              </div>
            ))}
          </div>
        </div>
      </td>
    </tr>
  )
}
