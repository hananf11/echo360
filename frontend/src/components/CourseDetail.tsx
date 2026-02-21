import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Download, Mic } from 'lucide-react'
import { getCourse, getLectures, downloadAll, transcribeAll } from '../api'
import type { Course, Lecture, SSEMessage } from '../types'
import { useSSE } from '../hooks/useSSE'
import LectureRow from './LectureRow'

export default function CourseDetail() {
  const { id } = useParams<{ id: string }>()
  const courseId = Number(id)

  const [course, setCourse] = useState<Course | null>(null)
  const [lectures, setLectures] = useState<Lecture[]>([])
  const [loading, setLoading] = useState(true)
  const [queuedCount, setQueuedCount] = useState<number | null>(null)
  const [transcribeQueuedCount, setTranscribeQueuedCount] = useState<number | null>(null)
  const [transcribeModel, setTranscribeModel] = useState('groq')
  const [progressMap, setProgressMap] = useState<Record<number, SSEMessage['progress']>>({})

  const load = useCallback(() => {
    Promise.all([getCourse(courseId), getLectures(courseId)])
      .then(([c, ls]) => { setCourse(c); setLectures(ls) })
      .finally(() => setLoading(false))
  }, [courseId])

  useEffect(() => { load() }, [load])

  const handleSSE = useCallback((msg: SSEMessage) => {
    const TERMINAL_STATUSES = new Set(['done', 'error', 'pending', 'queued', 'downloaded'])

    if (msg.type === 'lecture_update' && msg.lecture_id !== undefined) {
      if (msg.status !== undefined) {
        setLectures(prev =>
          prev.map(l => {
            if (l.id !== msg.lecture_id) return l
            const update: Partial<Lecture> = {}
            // Audio statuses
            if (['downloading', 'downloaded', 'converting', 'done', 'error', 'pending', 'queued'].includes(msg.status!)) {
              update.audio_status = msg.status as Lecture['audio_status']
            }
            // Transcription status from lecture_update
            if (msg.status === 'transcribing') {
              update.transcript_status = 'transcribing'
            }
            if (msg.audio_path !== undefined) update.audio_path = msg.audio_path
            return { ...l, ...update }
          })
        )
      }
      // Track progress for all active stages
      if (msg.progress && msg.lecture_id !== undefined) {
        setProgressMap(prev => ({ ...prev, [msg.lecture_id!]: msg.progress! }))
      }
      // Clear progress on terminal states
      if (msg.status && TERMINAL_STATUSES.has(msg.status) && msg.lecture_id !== undefined) {
        setProgressMap(prev => {
          const next = { ...prev }
          delete next[msg.lecture_id!]
          return next
        })
      }
    }
    if (msg.type === 'transcription_start' && msg.lecture_id !== undefined) {
      setLectures(prev =>
        prev.map(l =>
          l.id === msg.lecture_id ? { ...l, transcript_status: 'transcribing' } : l
        )
      )
    }
    if (msg.type === 'transcription_done' && msg.lecture_id !== undefined) {
      setLectures(prev =>
        prev.map(l =>
          l.id === msg.lecture_id ? { ...l, transcript_status: 'done' } : l
        )
      )
      setProgressMap(prev => {
        const next = { ...prev }
        delete next[msg.lecture_id!]
        return next
      })
    }
    if (msg.type === 'transcription_error' && msg.lecture_id !== undefined) {
      setLectures(prev =>
        prev.map(l =>
          l.id === msg.lecture_id ? { ...l, transcript_status: 'error' } : l
        )
      )
      setProgressMap(prev => {
        const next = { ...prev }
        delete next[msg.lecture_id!]
        return next
      })
    }
  }, [])

  useSSE(handleSSE)

  const handleDownloadAll = async () => {
    const result = await downloadAll(courseId)
    setQueuedCount(result.queued)
  }

  const handleTranscribeAll = async () => {
    const result = await transcribeAll(courseId, transcribeModel)
    setTranscribeQueuedCount(result.queued)
  }

  const pendingCount = lectures.filter(
    l => l.audio_status === 'pending' || l.audio_status === 'error'
  ).length

  const doneCount = lectures.filter(l => l.audio_status === 'done').length
  const pendingTranscriptCount = lectures.filter(
    l =>
      l.audio_status === 'done' &&
      (l.transcript_status === 'pending' || l.transcript_status === 'error')
  ).length

  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      <Link
        to="/"
        className="inline-flex items-center gap-2 text-slate-400 hover:text-white mb-6 transition-colors text-sm"
      >
        <ArrowLeft size={15} />
        Back to library
      </Link>

      {loading ? (
        <p className="text-slate-400 text-sm">Loading…</p>
      ) : (
        <>
          <div className="flex items-start justify-between mb-6 gap-4">
            <div>
              <h1 className="text-2xl font-bold text-white leading-tight">{course?.name}</h1>
              <p className="text-slate-400 text-sm mt-1.5">
                {lectures.length} lectures · {doneCount} downloaded
              </p>
            </div>

            <div className="flex items-center gap-2">
              {pendingTranscriptCount > 0 && (
                <>
                  <select
                    value={transcribeModel}
                    onChange={e => setTranscribeModel(e.target.value)}
                    className="bg-slate-700 border border-slate-600 text-slate-300 text-xs rounded-lg px-2 py-2 focus:outline-none focus:border-violet-500"
                  >
                    <optgroup label="Remote">
                      <option value="groq">Groq cloud (fast)</option>
                    </optgroup>
                    <optgroup label="Local">
                      <option value="tiny">tiny (fastest)</option>
                      <option value="base">base</option>
                      <option value="small">small</option>
                      <option value="turbo">turbo (best quality)</option>
                    </optgroup>
                  </select>
                  <button
                    onClick={handleTranscribeAll}
                    className="flex items-center gap-2 bg-violet-700 hover:bg-violet-600 text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium whitespace-nowrap"
                  >
                    <Mic size={15} />
                    Transcribe All ({pendingTranscriptCount})
                  </button>
                </>
              )}
              {pendingCount > 0 && (
                <button
                  onClick={handleDownloadAll}
                  className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium whitespace-nowrap"
                >
                  <Download size={15} />
                  Download All ({pendingCount})
                </button>
              )}
            </div>
          </div>

          {queuedCount !== null && (
            <p className="text-sm text-indigo-400 mb-4">
              {queuedCount} download{queuedCount !== 1 ? 's' : ''} queued.
            </p>
          )}
          {transcribeQueuedCount !== null && (
            <p className="text-sm text-violet-400 mb-4">
              {transcribeQueuedCount} transcription{transcribeQueuedCount !== 1 ? 's' : ''} queued.
            </p>
          )}

          {lectures.length === 0 ? (
            <p className="text-slate-500 text-sm py-8 text-center">
              No lectures found. Try syncing the course.
            </p>
          ) : (
            <div className="flex flex-col gap-6">
              {Object.entries(
                lectures.reduce<Record<string, Lecture[]>>((groups, l) => {
                  const year = l.date?.slice(0, 4) ?? 'Unknown'
                  ;(groups[year] ??= []).push(l)
                  return groups
                }, {})
              )
                .sort(([a], [b]) => b.localeCompare(a))
                .map(([year, group]) => {
                  const sorted = [...group].sort((a, b) => b.date.localeCompare(a.date))
                  return (
                  <div key={year} className="bg-slate-800 rounded-xl border border-slate-700 overflow-hidden">
                    <div className="px-5 py-2.5 border-b border-slate-700 bg-slate-700/40">
                      <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">{year}</span>
                    </div>
                    <table className="w-full">
                      <tbody>
                        {sorted.map((lecture, i) => (
                          <LectureRow
                            key={lecture.id}
                            lecture={lecture}
                            isLast={i === sorted.length - 1}
                            transcribeModel={transcribeModel}
                            progress={progressMap[lecture.id]}
                          />
                        ))}
                      </tbody>
                    </table>
                  </div>
                  )
                })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
