import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Download, Mic, Sparkles, Wand2, Check, X } from 'lucide-react'
import { getCourse, getLectures, downloadAll, transcribeAll, generateNotesAll, fixTitles, updateCourseDisplayName } from '../api'
import type { Course, Lecture, SSEMessage } from '../types'
import { useSSE } from '../hooks/useSSE'
import LectureRow from './LectureRow'

const TERMINAL_STATUSES = new Set(['done', 'error', 'pending', 'queued', 'downloaded'])

export default function CourseDetail() {
  const { id } = useParams<{ id: string }>()
  const courseId = Number(id)

  const [course, setCourse] = useState<Course | null>(null)
  const [lectures, setLectures] = useState<Lecture[]>([])
  const [loading, setLoading] = useState(true)
  const [queuedCount, setQueuedCount] = useState<number | null>(null)
  const [transcribeQueuedCount, setTranscribeQueuedCount] = useState<number | null>(null)
  const [notesQueuedCount, setNotesQueuedCount] = useState<number | null>(null)
  const [transcribeModel, setTranscribeModel] = useState('modal')
  const [notesModel, setNotesModel] = useState('auto')
  const [progressMap, setProgressMap] = useState<Record<number, SSEMessage['progress']>>({})
  const [editingName, setEditingName] = useState(false)
  const [editName, setEditName] = useState('')
  const [fixingTitles, setFixingTitles] = useState(false)

  const load = useCallback(() => {
    Promise.all([getCourse(courseId), getLectures(courseId)])
      .then(([c, ls]) => { setCourse(c); setLectures(ls) })
      .finally(() => setLoading(false))
  }, [courseId])

  useEffect(() => { load() }, [load])

  const handleSSE = useCallback((msg: SSEMessage) => {

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
            if (msg.error) update.error_message = msg.error
            if (msg.status && msg.status !== 'error') update.error_message = null
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
          l.id === msg.lecture_id ? { ...l, transcript_status: 'error', error_message: msg.error || l.error_message } : l
        )
      )
      setProgressMap(prev => {
        const next = { ...prev }
        delete next[msg.lecture_id!]
        return next
      })
    }
    if (msg.type === 'notes_start' && msg.lecture_id !== undefined) {
      setLectures(prev =>
        prev.map(l =>
          l.id === msg.lecture_id ? { ...l, notes_status: 'generating' } : l
        )
      )
    }
    if (msg.type === 'notes_done' && msg.lecture_id !== undefined) {
      setLectures(prev =>
        prev.map(l =>
          l.id === msg.lecture_id ? { ...l, notes_status: 'done' } : l
        )
      )
    }
    if (msg.type === 'notes_error' && msg.lecture_id !== undefined) {
      setLectures(prev =>
        prev.map(l =>
          l.id === msg.lecture_id ? { ...l, notes_status: 'error', error_message: msg.error || l.error_message } : l
        )
      )
    }
    if (msg.type === 'titles_fixing' && msg.course_id === courseId) {
      setFixingTitles(true)
    }
    if (msg.type === 'titles_fixed' && msg.course_id === courseId) {
      setFixingTitles(false)
      load()
    }
    if (msg.type === 'titles_error' && msg.course_id === courseId) {
      setFixingTitles(false)
    }
  }, [courseId, load])

  useSSE(handleSSE)

  const handleDownloadAll = async () => {
    const result = await downloadAll(courseId)
    setQueuedCount(result.queued)
  }

  const handleTranscribeAll = async () => {
    const result = await transcribeAll(courseId, transcribeModel)
    setTranscribeQueuedCount(result.queued)
  }

  const handleGenerateNotesAll = async () => {
    const result = await generateNotesAll(courseId, notesModel)
    setNotesQueuedCount(result.queued)
  }

  const handleFixTitles = async () => {
    setFixingTitles(true)
    await fixTitles(courseId)
  }

  const handleSaveDisplayName = async () => {
    const trimmed = editName.trim()
    const updated = await updateCourseDisplayName(courseId, trimmed || null)
    setCourse(updated)
    setEditingName(false)
  }

  const pendingCount = lectures.filter(
    l => l.audio_status === 'pending' || l.audio_status === 'error'
  ).length

  const doneCount = lectures.filter(l => l.audio_status === 'done').length
  const transcribedCount = lectures.filter(l => l.transcript_status === 'done').length
  const pendingTranscriptCount = lectures.filter(
    l =>
      l.audio_status === 'done' &&
      (l.transcript_status === 'pending' || l.transcript_status === 'error')
  ).length
  const notesReadyCount = lectures.filter(l => l.notes_status === 'done').length
  const pendingNotesCount = lectures.filter(
    l =>
      l.transcript_status === 'done' &&
      (l.notes_status === 'pending' || l.notes_status === 'error')
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
              {editingName ? (
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={editName}
                    onChange={e => setEditName(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') handleSaveDisplayName(); if (e.key === 'Escape') setEditingName(false) }}
                    className="text-2xl font-bold text-white bg-slate-700 border border-slate-600 rounded-lg px-3 py-1 focus:outline-none focus:border-indigo-500"
                    autoFocus
                  />
                  <button onClick={handleSaveDisplayName} className="p-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white transition-colors">
                    <Check size={16} />
                  </button>
                  <button onClick={() => setEditingName(false)} className="p-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-400 hover:text-white transition-colors">
                    <X size={16} />
                  </button>
                </div>
              ) : (
                <h1
                  className="text-2xl font-bold text-white leading-tight cursor-pointer hover:text-slate-300 transition-colors"
                  onClick={() => { setEditName(course?.display_name || course?.name || ''); setEditingName(true) }}
                  title="Click to edit display name"
                >
                  {course?.display_name || course?.name}
                </h1>
              )}
              {course?.display_name && (
                <p className="text-slate-600 text-xs mt-0.5">{course.name}</p>
              )}
              <p className="text-slate-400 text-sm mt-1.5">
                {lectures.length} lectures · {doneCount} downloaded{transcribedCount > 0 && ` · ${transcribedCount} transcribed`}{notesReadyCount > 0 && ` · ${notesReadyCount} notes`}
              </p>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={handleFixTitles}
                disabled={fixingTitles}
                className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium whitespace-nowrap disabled:opacity-40"
                title="Use AI to clean up course and lecture titles"
              >
                <Wand2 size={15} className={fixingTitles ? 'animate-spin' : ''} />
                {fixingTitles ? 'Fixing…' : 'Fix Titles'}
              </button>
              {pendingNotesCount > 0 && (
                <>
                  <select
                    value={notesModel}
                    onChange={e => setNotesModel(e.target.value)}
                    className="bg-slate-700 border border-slate-600 text-slate-300 text-xs rounded-lg px-2 py-2 focus:outline-none focus:border-amber-500"
                  >
                    <option value="auto">Auto (free first)</option>
                    <optgroup label="Free">
                      <option value="openrouter/meta-llama/llama-3.3-70b-instruct:free">Llama 3.3 70B</option>
                      <option value="openrouter/google/gemma-3-27b-it:free">Gemma 3 27B</option>
                      <option value="openrouter/mistralai/mistral-small-3.1-24b-instruct:free">Mistral Small 3.1</option>
                      <option value="openrouter/qwen/qwen3-next-80b-a3b-instruct:free">Qwen3 Next 80B</option>
                      <option value="openrouter/deepseek/deepseek-r1-0528:free">DeepSeek R1</option>
                      <option value="openrouter/nousresearch/hermes-3-llama-3.1-405b:free">Hermes 3 405B</option>
                    </optgroup>
                    <optgroup label="Cheap">
                      <option value="openrouter/google/gemini-2.5-flash-lite">Gemini 2.5 Flash Lite</option>
                      <option value="openrouter/minimax/minimax-m2.1">MiniMax M2.1</option>
                    </optgroup>
                    <optgroup label="Paid">
                      <option value="openrouter/meta-llama/llama-3.3-70b-instruct">Llama 3.3 70B</option>
                      <option value="openrouter/anthropic/claude-sonnet-4">Claude Sonnet</option>
                      <option value="openrouter/openai/gpt-4o-mini">GPT-4o Mini</option>
                    </optgroup>
                  </select>
                  <button
                    onClick={handleGenerateNotesAll}
                    className="flex items-center gap-2 bg-amber-700 hover:bg-amber-600 text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium whitespace-nowrap"
                  >
                    <Sparkles size={15} />
                    Generate Notes ({pendingNotesCount})
                  </button>
                </>
              )}
              {pendingTranscriptCount > 0 && (
                <>
                  <select
                    value={transcribeModel}
                    onChange={e => setTranscribeModel(e.target.value)}
                    className="bg-slate-700 border border-slate-600 text-slate-300 text-xs rounded-lg px-2 py-2 focus:outline-none focus:border-violet-500"
                  >
                    <optgroup label="Remote">
                      <option value="cloud">Cloud auto (Groq → Modal)</option>
                      <option value="groq">Groq cloud (fast)</option>
                      <option value="modal">Modal GPU (no limits)</option>
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
          {notesQueuedCount !== null && (
            <p className="text-sm text-amber-400 mb-4">
              {notesQueuedCount} note generation{notesQueuedCount !== 1 ? 's' : ''} queued.
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
                            notesModel={notesModel}
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
