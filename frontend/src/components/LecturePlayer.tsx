import { useState, useEffect, useRef } from 'react'
import { getTranscript } from '../api'
import type { Transcript } from '../types'

interface Props {
  lectureId: number
  hasTranscript: boolean
  isLast: boolean
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function LecturePlayer({ lectureId, hasTranscript, isLast }: Props) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const activeRef = useRef<HTMLDivElement>(null)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [transcriptError, setTranscriptError] = useState<string | null>(null)
  const [currentTime, setCurrentTime] = useState(0)

  useEffect(() => {
    if (!hasTranscript) return
    getTranscript(lectureId)
      .then(setTranscript)
      .catch(e => setTranscriptError(e.message))
  }, [lectureId, hasTranscript])

  // Scroll active segment into view
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [currentTime])

  const activeIndex = transcript
    ? [...transcript.segments].reduce((best, s, i) => s.start <= currentTime ? i : best, -1)
    : -1

  const seekTo = (time: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = time
      audioRef.current.play()
    }
  }

  const rowClass = !isLast ? 'border-b border-slate-700/60' : ''

  return (
    <tr className={rowClass}>
      <td colSpan={3} className="px-5 pb-4">
        <div className="bg-slate-900/60 rounded-lg border border-slate-700 overflow-hidden">
          {/* Audio player */}
          <div className="px-4 pt-4 pb-3 border-b border-slate-700/60">
            <audio
              ref={audioRef}
              src={`/api/lectures/${lectureId}/audio`}
              controls
              onTimeUpdate={() => setCurrentTime(audioRef.current?.currentTime ?? 0)}
              className="w-full h-8 [&::-webkit-media-controls-panel]:bg-slate-800 accent-indigo-500"
            />
          </div>

          {/* Transcript */}
          {hasTranscript && (
            <div className="max-h-64 overflow-y-auto">
              {transcriptError && (
                <p className="px-4 py-3 text-xs text-red-400">Failed to load transcript: {transcriptError}</p>
              )}
              {!transcript && !transcriptError && (
                <p className="px-4 py-3 text-xs text-slate-500">Loading transcript…</p>
              )}
              {transcript && (
                <>
                  <div className="flex items-center gap-2 px-4 pt-3 pb-2">
                    <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Transcript</span>
                    <span className="text-xs bg-slate-700 text-slate-400 px-2 py-0.5 rounded-full">{transcript.model}</span>
                  </div>
                  <div className="pb-3">
                    {transcript.segments.map((seg, i) => {
                      const isActive = i === activeIndex
                      return (
                        <div
                          key={i}
                          ref={isActive ? activeRef : undefined}
                          onClick={() => seekTo(seg.start)}
                          className={`flex gap-3 px-4 py-1 cursor-pointer rounded transition-colors text-sm ${
                            isActive
                              ? 'bg-indigo-600/20 text-white'
                              : 'hover:bg-slate-700/40 text-slate-300'
                          }`}
                        >
                          <span className={`font-mono shrink-0 text-xs pt-0.5 ${isActive ? 'text-indigo-400' : 'text-slate-500'}`}>
                            [{formatTime(seg.start)}]
                          </span>
                          <span className="leading-relaxed">{seg.text}</span>
                        </div>
                      )
                    })}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </td>
    </tr>
  )
}
