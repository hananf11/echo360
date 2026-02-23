import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Download, RefreshCw, Mic, Sparkles, Wand2, Check, X, CalendarClock, ExternalLink } from 'lucide-react'
import { getCourse, getLectures, fixTitles, updateCourseDisplayName, syncCourse, bulkDownload, bulkRedownload, bulkTranscribe, bulkGenerateNotes } from '../api'
import type { Course, Lecture, SSEMessage } from '../types'
import { useSSE } from '../hooks/useSSE'
import LectureRow from './LectureRow'

const TERMINAL_STATUSES = new Set(['done', 'error', 'pending', 'queued', 'downloaded'])

/** Try to split "Machine Learning COSC401" into [title, code] */
function splitCourseCode(name: string): [string, string | null] {
  const match = name.match(/^(.+?)\s+([A-Z]{3,5}\d{3,4})$/)
  if (match) return [match[1], match[2]]
  return [name, null]
}

export default function CourseDetail() {
  const { id } = useParams<{ id: string }>()
  const courseId = Number(id)

  const [course, setCourse] = useState<Course | null>(null)
  const [lectures, setLectures] = useState<Lecture[]>([])
  const [loading, setLoading] = useState(true)
  const [transcribeModel, setTranscribeModel] = useState('modal')
  const [notesModel, setNotesModel] = useState('auto')
  const [progressMap, setProgressMap] = useState<Record<number, SSEMessage['progress']>>({})
  const [editingName, setEditingName] = useState(false)
  const [editName, setEditName] = useState('')
  const [fixingTitles, setFixingTitles] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [showFuture, setShowFuture] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())

  const load = useCallback(() => {
    Promise.all([getCourse(courseId), getLectures(courseId)])
      .then(([c, ls]) => { setCourse(c); setLectures(ls); if (c.syncing) setSyncing(true) })
      .finally(() => setLoading(false))
  }, [courseId])

  useEffect(() => { load() }, [load])

  // Clear selection on navigation (courseId change)
  useEffect(() => { setSelectedIds(new Set()) }, [courseId])

  const handleSSE = useCallback((msg: SSEMessage) => {

    if (msg.type === 'lecture_update' && msg.lecture_id !== undefined) {
      if (msg.status !== undefined) {
        setLectures(prev =>
          prev.map(l => {
            if (l.id !== msg.lecture_id) return l
            const update: Partial<Lecture> = {}
            if (['downloading', 'downloaded', 'converting', 'done', 'error', 'pending', 'queued'].includes(msg.status!)) {
              update.audio_status = msg.status as Lecture['audio_status']
            }
            if (msg.status === 'transcribing') {
              update.transcript_status = 'transcribing'
            }
            if (msg.audio_path !== undefined) update.audio_path = msg.audio_path
            if (msg.frames_status) update.frames_status = msg.frames_status as Lecture['frames_status']
            if (msg.error) update.error_message = msg.error
            if (msg.status && msg.status !== 'error') update.error_message = null
            return { ...l, ...update }
          })
        )
      }
      if (msg.progress && msg.lecture_id !== undefined) {
        setProgressMap(prev => ({ ...prev, [msg.lecture_id!]: msg.progress! }))
      }
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
    if (msg.type === 'frames_start' && msg.lecture_id !== undefined) {
      setLectures(prev =>
        prev.map(l =>
          l.id === msg.lecture_id ? { ...l, frames_status: 'extracting' } : l
        )
      )
    }
    if (msg.type === 'frames_done' && msg.lecture_id !== undefined) {
      setLectures(prev =>
        prev.map(l =>
          l.id === msg.lecture_id ? { ...l, frames_status: 'done' } : l
        )
      )
    }
    if (msg.type === 'frames_error' && msg.lecture_id !== undefined) {
      setLectures(prev =>
        prev.map(l =>
          l.id === msg.lecture_id ? { ...l, frames_status: 'error', error_message: msg.error || l.error_message } : l
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
    if (msg.type === 'sync_start' && msg.course_id === courseId) {
      setSyncing(true)
    }
    if (msg.type === 'sync_done' && msg.course_id === courseId) {
      setSyncing(false)
      load()
    }
    if (msg.type === 'sync_error' && msg.course_id === courseId) {
      setSyncing(false)
    }
  }, [courseId, load])

  useSSE(handleSSE)

  const handleFixTitles = async () => {
    setFixingTitles(true)
    await fixTitles(courseId)
  }

  const handleSync = async () => {
    setSyncing(true)
    await syncCourse(courseId)
  }

  const handleSaveDisplayName = async () => {
    const trimmed = editName.trim()
    const updated = await updateCourseDisplayName(courseId, trimmed || null)
    setCourse(updated)
    setEditingName(false)
  }

  // Selection helpers
  const toggleSelection = useCallback((id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  // Filter out future lectures
  const tomorrow = useMemo(() => {
    const d = new Date()
    d.setDate(d.getDate() + 1)
    return d.toISOString().slice(0, 10)
  }, [])

  const futureLectureCount = lectures.filter(l => l.date > tomorrow).length
  const visibleLectures = showFuture ? lectures : lectures.filter(l => l.date <= tomorrow)

  const allVisibleSelected = visibleLectures.length > 0 && visibleLectures.every(l => selectedIds.has(l.id))

  const toggleSelectAll = useCallback(() => {
    if (allVisibleSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(visibleLectures.map(l => l.id)))
    }
  }, [allVisibleSelected, visibleLectures])

  // Derive selected lectures info for the bulk action bar
  const selectedLectures = useMemo(
    () => lectures.filter(l => selectedIds.has(l.id)),
    [lectures, selectedIds]
  )

  const selDownloadable = selectedLectures.filter(l => l.audio_status === 'pending' || l.audio_status === 'error').length
  const selRedownloadable = selectedLectures.filter(l => l.audio_status === 'done').length
  const selTranscribable = selectedLectures.filter(l => l.audio_status === 'done' && l.transcript_status !== 'queued' && l.transcript_status !== 'transcribing').length
  const selNotesReady = selectedLectures.filter(l => l.transcript_status === 'done' && l.notes_status !== 'queued' && l.notes_status !== 'generating').length

  const handleBulkDownload = async () => {
    const ids = selectedLectures.filter(l => l.audio_status === 'pending' || l.audio_status === 'error').map(l => l.id)
    await bulkDownload(ids)
  }

  const handleBulkRedownload = async () => {
    const ids = selectedLectures.filter(l => l.audio_status === 'done').map(l => l.id)
    await bulkRedownload(ids)
  }

  const handleBulkTranscribe = async () => {
    const ids = selectedLectures.filter(l => l.audio_status === 'done' && l.transcript_status !== 'queued' && l.transcript_status !== 'transcribing').map(l => l.id)
    await bulkTranscribe(ids, transcribeModel)
  }

  const handleBulkGenerateNotes = async () => {
    const ids = selectedLectures.filter(l => l.transcript_status === 'done' && l.notes_status !== 'queued' && l.notes_status !== 'generating').map(l => l.id)
    await bulkGenerateNotes(ids, notesModel)
  }

  // Stats
  const noMediaCount = lectures.filter(l => l.audio_status === 'no_media').length
  const effectiveCount = lectures.length - noMediaCount
  const doneCount = lectures.filter(l => l.audio_status === 'done').length
  const transcribedCount = lectures.filter(l => l.transcript_status === 'done').length
  const notesReadyCount = lectures.filter(l => l.notes_status === 'done').length

  const displayName = course?.display_name || course?.name || ''
  const [title, code] = splitCourseCode(displayName)

  const allDownloaded = effectiveCount > 0 && doneCount >= effectiveCount
  const allTranscribed = effectiveCount > 0 && transcribedCount >= effectiveCount
  const allNotes = effectiveCount > 0 && notesReadyCount >= effectiveCount

  const hasSelection = selectedIds.size > 0

  return (
    <div className="max-w-6xl mx-auto px-6 py-10">
      <Link
        to="/"
        className="inline-flex items-center gap-2 text-slate-500 hover:text-white mb-6 transition-colors text-sm"
      >
        <ArrowLeft size={15} />
        Back to library
      </Link>

      {loading ? (
        <p className="text-slate-400 text-sm">Loading…</p>
      ) : (
        <>
          {/* Header */}
          <div className="flex items-start justify-between mb-6 gap-4">
            <div>
              {editingName ? (
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={editName}
                    onChange={e => setEditName(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') handleSaveDisplayName(); if (e.key === 'Escape') setEditingName(false) }}
                    className="text-2xl font-bold text-white bg-slate-800 border border-slate-600 rounded-lg px-3 py-1 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
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
                  {title}
                  {code && <span className="ml-2 text-sm font-mono text-slate-500 font-normal">{code}</span>}
                </h1>
              )}
              {course?.display_name && (
                <p className="text-slate-600 text-xs mt-0.5">{course.name}</p>
              )}
              {course?.url && (
                <a
                  href={course.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-indigo-400 transition-colors mt-1"
                >
                  <ExternalLink size={11} />
                  Open in Echo360
                </a>
              )}
              {course?.last_synced_at && (
                <p className="text-xs text-slate-600 mt-1">
                  Last synced {new Date(course.last_synced_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                </p>
              )}

              {/* Stats row */}
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                <span className="text-sm text-slate-400">{lectures.length} lectures</span>

                {effectiveCount === 0 && lectures.length > 0 ? (
                  <>
                    <span className="text-slate-600">·</span>
                    <span className="text-xs text-slate-500">No media available</span>
                  </>
                ) : effectiveCount > 0 && (
                  <>
                    <span className="text-slate-600">·</span>
                    {allDownloaded ? (
                      <span className="inline-flex items-center gap-1 text-xs text-emerald-400/70">
                        <Check size={11} strokeWidth={3} />
                        All downloaded
                      </span>
                    ) : (
                      <span className="text-xs text-slate-500">{doneCount}/{effectiveCount} downloaded</span>
                    )}

                    <span className="text-slate-600">·</span>
                    {allTranscribed ? (
                      <span className="inline-flex items-center gap-1 text-xs text-violet-400/70">
                        <Check size={11} strokeWidth={3} />
                        All transcribed
                      </span>
                    ) : transcribedCount > 0 ? (
                      <span className="text-xs text-slate-500">{transcribedCount}/{effectiveCount} transcribed</span>
                    ) : null}

                    {notesReadyCount > 0 && (
                      <>
                        <span className="text-slate-600">·</span>
                        {allNotes ? (
                          <span className="inline-flex items-center gap-1 text-xs text-amber-400/70">
                            <Check size={11} strokeWidth={3} />
                            All notes
                          </span>
                        ) : (
                          <span className="text-xs text-slate-500">{notesReadyCount}/{effectiveCount} notes</span>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
            </div>

          </div>

          {/* Toolbar — sticky, matches card theming */}
          <div className="sticky top-0 z-40 bg-slate-800/70 rounded-xl border border-slate-700/50 px-4 py-2.5 mb-4 backdrop-blur-sm flex flex-col gap-2">
            {/* Top row: Sync, Fix Titles, selection info */}
            <div className="flex items-center gap-2 min-h-[28px]">
              <div className="flex items-center gap-1.5">
                <button
                  onClick={handleSync}
                  disabled={syncing}
                  className="flex items-center gap-1.5 text-slate-400 hover:text-white px-2 py-1 rounded-md hover:bg-slate-700/60 transition-colors text-xs disabled:opacity-40"
                  title="Re-sync lectures from Echo360"
                >
                  <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} />
                  {syncing ? 'Syncing...' : 'Sync'}
                </button>
                <button
                  onClick={handleFixTitles}
                  disabled={fixingTitles}
                  className="flex items-center gap-1.5 text-slate-400 hover:text-white px-2 py-1 rounded-md hover:bg-slate-700/60 transition-colors text-xs disabled:opacity-40"
                  title="Use AI to clean up course and lecture titles"
                >
                  <Wand2 size={13} className={fixingTitles ? 'animate-spin' : ''} />
                  {fixingTitles ? 'Fixing...' : 'Fix Titles'}
                </button>
              </div>

              {hasSelection && (
                <>
                  <div className="w-px h-4 bg-slate-700 mx-1" />
                  <span className="text-xs font-medium text-slate-200">
                    {selectedIds.size} selected
                  </span>
                  <button
                    onClick={() => setSelectedIds(new Set())}
                    className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
                  >
                    Clear
                  </button>
                  <button
                    onClick={toggleSelectAll}
                    className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
                  >
                    {allVisibleSelected ? 'Deselect all' : 'Select all'}
                  </button>
                </>
              )}
            </div>

            {/* Action row: bulk actions when selected */}
            {hasSelection && (
              <div className="flex items-center gap-2 flex-wrap">
                {selDownloadable > 0 && (
                  <button
                    onClick={handleBulkDownload}
                    className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-lg transition-colors text-xs font-medium"
                  >
                    <Download size={13} />
                    Download ({selDownloadable})
                  </button>
                )}

                {selRedownloadable > 0 && (
                  <button
                    onClick={handleBulkRedownload}
                    className="flex items-center gap-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 px-3 py-1.5 rounded-lg transition-colors text-xs font-medium"
                  >
                    <RefreshCw size={13} />
                    Re-download ({selRedownloadable})
                  </button>
                )}

                {selTranscribable > 0 && (
                  <>
                    <div className="w-px h-4 bg-slate-700" />
                    <select
                      value={transcribeModel}
                      onChange={e => setTranscribeModel(e.target.value)}
                      className="bg-slate-900/60 border border-slate-600 text-slate-300 text-xs rounded-lg px-2 py-1.5 focus:outline-none focus:border-violet-500"
                    >
                      <optgroup label="Remote">
                        <option value="cloud">Cloud auto (Groq → Modal)</option>
                        <option value="groq">Groq cloud (fast)</option>
                        <option value="groq:whisper-large-v3">Groq large-v3 (best)</option>
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
                      onClick={handleBulkTranscribe}
                      className="flex items-center gap-1.5 bg-violet-700 hover:bg-violet-600 text-white px-3 py-1.5 rounded-lg transition-colors text-xs font-medium"
                    >
                      <Mic size={13} />
                      Transcribe ({selTranscribable})
                    </button>
                  </>
                )}

                {selNotesReady > 0 && (
                  <>
                    <div className="w-px h-4 bg-slate-700" />
                    <select
                      value={notesModel}
                      onChange={e => setNotesModel(e.target.value)}
                      className="bg-slate-900/60 border border-slate-600 text-slate-300 text-xs rounded-lg px-2 py-1.5 focus:outline-none focus:border-amber-500"
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
                      onClick={handleBulkGenerateNotes}
                      className="flex items-center gap-1.5 bg-amber-700 hover:bg-amber-600 text-white px-3 py-1.5 rounded-lg transition-colors text-xs font-medium"
                    >
                      <Sparkles size={13} />
                      Notes ({selNotesReady})
                    </button>
                  </>
                )}
              </div>
            )}
          </div>

          {lectures.length === 0 ? (
            <p className="text-slate-500 text-sm py-8 text-center">
              No lectures found. Try syncing the course.
            </p>
          ) : (
            <div className="flex flex-col gap-6">
              {(() => {
                const sorted = [...visibleLectures].sort((a, b) => a.date.localeCompare(b.date))
                return (
                  <div className="bg-slate-800/70 rounded-xl border border-slate-700/50 overflow-hidden">
                    <div className="px-5 py-2.5 border-b border-slate-700/50">
                      <div className="flex items-center gap-3">
                        <input
                          type="checkbox"
                          checked={allVisibleSelected}
                          onChange={toggleSelectAll}
                          className="w-3.5 h-3.5 rounded border-slate-600 bg-slate-800 text-indigo-500 focus:ring-indigo-500 focus:ring-offset-0 cursor-pointer"
                        />
                        <span className="text-xs text-slate-500">{sorted.length} lecture{sorted.length !== 1 ? 's' : ''}</span>
                      </div>
                    </div>
                    <table className="w-full">
                      <tbody>
                        {sorted.map((lecture, i) => (
                          <LectureRow
                            key={lecture.id}
                            lecture={lecture}
                            hostname={course?.hostname ?? ''}
                            isLast={i === sorted.length - 1}
                            selected={selectedIds.has(lecture.id)}
                            onToggle={toggleSelection}
                            progress={progressMap[lecture.id]}
                          />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              })()}

              {/* Future lectures toggle */}
              {futureLectureCount > 0 && (
                <button
                  onClick={() => setShowFuture(f => !f)}
                  className="flex items-center justify-center gap-2 text-xs text-slate-500 hover:text-slate-300 py-3 transition-colors"
                >
                  <CalendarClock size={14} />
                  {showFuture
                    ? 'Hide future lectures'
                    : `${futureLectureCount} scheduled lecture${futureLectureCount !== 1 ? 's' : ''} hidden`
                  }
                </button>
              )}
            </div>
          )}

        </>
      )}
    </div>
  )
}
