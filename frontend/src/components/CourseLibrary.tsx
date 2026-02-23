import { useState, useEffect, useCallback, useMemo } from 'react'
import { Link } from '@tanstack/react-router'
import { PlusCircle, RefreshCw, BookOpen, Trash2, Download, HardDrive, Mic, Wand2, Check, Search, ChevronRight } from 'lucide-react'
import { getCourses, syncCourse, deleteCourse, downloadAll, downloadAllGlobal, transcribeAll, transcribeAllGlobal, getStorage, fixTitles, type StorageStats } from '../api'
import type { Course, SSEMessage } from '../types'
import { useSSE } from '../hooks/useSSE'
import AddCourseModal from './AddCourseModal'

function formatTotalHours(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

/** Try to split "Machine Learning COSC401" into [title, code] */
function splitCourseCode(name: string): [string, string | null] {
  const match = name.match(/^(.+?)\s+([A-Z]{3,5}\d{3,4})$/)
  if (match) return [match[1], match[2]]
  return [name, null]
}

function StepIndicator({ current, total, label, done, colorDone, colorPartial }: {
  current: number; total: number; label: string; done: boolean
  colorDone: string; colorPartial: string
}) {
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      {done ? (
        <Check size={12} strokeWidth={3} className={colorDone} />
      ) : (
        <span className={`text-[10px] font-mono ${colorPartial}`}>{current}/{total}</span>
      )}
      <span className={`text-[11px] ${done ? colorDone : 'text-slate-500'}`}>{label}</span>
    </div>
  )
}

function StatusBadge({ downloaded, transcribed, notes, total, noMedia }: { downloaded: number; transcribed: number; notes: number; total: number; noMedia: number }) {
  const effective = total - noMedia
  if (effective === 0) {
    return <span className="text-[11px] text-slate-500">No media available</span>
  }

  const allDownloaded = downloaded >= effective
  const allTranscribed = transcribed >= effective
  const allNotes = notes >= effective

  return (
    <div className="flex items-center gap-3">
      <StepIndicator current={downloaded} total={effective} label="Audio" done={allDownloaded} colorDone="text-emerald-400" colorPartial="text-emerald-400/60" />
      <span className="text-slate-700">·</span>
      <StepIndicator current={transcribed} total={effective} label="Transcript" done={allTranscribed} colorDone="text-violet-400" colorPartial="text-violet-400/60" />
      <span className="text-slate-700">·</span>
      <StepIndicator current={notes} total={effective} label="Notes" done={allNotes} colorDone="text-amber-400" colorPartial="text-amber-400/60" />
    </div>
  )
}

export default function CourseLibrary() {
  const [courses, setCourses] = useState<Course[]>([])
  const [syncing, setSyncing] = useState<Set<number>>(new Set())
  const [showAdd, setShowAdd] = useState(false)
  const [loading, setLoading] = useState(true)
  const [storage, setStorage] = useState<StorageStats | null>(null)
  const [globalQueued, setGlobalQueued] = useState<number | null>(null)
  const [globalTranscribeQueued, setGlobalTranscribeQueued] = useState<number | null>(null)
  const [search, setSearch] = useState('')

  const load = useCallback(() => {
    getCourses()
      .then(setCourses)
      .finally(() => setLoading(false))
    getStorage().then(setStorage).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  const handleSSE = useCallback((msg: SSEMessage) => {
    if (msg.type === 'sync_start' && msg.course_id !== undefined) {
      setSyncing(s => new Set(s).add(msg.course_id!))
    }
    if ((msg.type === 'sync_done' || msg.type === 'sync_error') && msg.course_id !== undefined) {
      setSyncing(s => { const n = new Set(s); n.delete(msg.course_id!); return n })
      load()
    }
    if (msg.type === 'titles_fixed') {
      load()
    }
    if (msg.type === 'lecture_update' && msg.course_id !== undefined) {
      const cid = msg.course_id
      setCourses(prev => prev.map(c => {
        if (c.id !== cid) return c
        let { downloading_count, queued_count } = c
        if (msg.status === 'queued') {
          queued_count += 1
        } else if (msg.status === 'downloading') {
          downloading_count += 1
          queued_count = Math.max(0, queued_count - 1)
        } else if (msg.status === 'downloaded' || msg.status === 'converting') {
          // phase change within download — keep downloading_count
        } else if (msg.status === 'done') {
          downloading_count = Math.max(0, downloading_count - 1)
          getStorage().then(setStorage).catch(() => {})
        } else if (msg.status === 'error') {
          downloading_count = Math.max(0, downloading_count - 1)
        }
        return { ...c, downloading_count, queued_count }
      }))
    }
  }, [load])

  useSSE(handleSSE)

  const filteredCourses = useMemo(() => {
    if (!search.trim()) return courses
    const q = search.toLowerCase()
    return courses.filter(c =>
      (c.display_name || c.name).toLowerCase().includes(q)
    )
  }, [courses, search])

  const handleDownloadAllGlobal = async () => {
    const result = await downloadAllGlobal()
    setGlobalQueued(result.queued)
  }

  const handleFixTitlesAll = async () => {
    courses.forEach(c => fixTitles(c.id))
  }

  const handleTranscribeAllGlobal = async () => {
    const result = await transcribeAllGlobal()
    setGlobalTranscribeQueued(result.queued)
  }

  const handleDone = () => {
    load()
  }

  const handleSync = async (id: number) => {
    setSyncing(s => new Set(s).add(id))
    await syncCourse(id)
  }

  const handleDelete = async (id: number, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!confirm('Remove this course?')) return
    await deleteCourse(id)
    load()
  }

  const handleSyncClick = (id: number, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    handleSync(id)
  }

  return (
    <div className="max-w-6xl mx-auto px-6 py-10">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-white">Echo360 Library</h1>
          <div className="flex items-center gap-3 mt-1.5">
            <p className="text-slate-400 text-sm">Your lecture audio archive</p>
            {storage && (
              <span className="inline-flex items-center gap-1.5 text-xs text-slate-500 bg-slate-800 border border-slate-700 px-2.5 py-1 rounded-full">
                <HardDrive size={11} />
                {formatSize(storage.size_bytes)}
                <span className="text-slate-600">·</span>
                {storage.downloaded_lectures}/{storage.total_lectures} lectures
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleFixTitlesAll}
            className="flex items-center gap-2 bg-slate-700/60 hover:bg-slate-600 text-slate-400 hover:text-white px-3 py-2 rounded-lg transition-colors text-sm"
            title="Use AI to clean up lecture titles"
          >
            <Wand2 size={15} />
            <span className="hidden xl:inline">Fix Titles</span>
          </button>
          <button
            onClick={handleTranscribeAllGlobal}
            className="flex items-center gap-2 bg-slate-700/60 hover:bg-slate-600 text-slate-400 hover:text-white px-3 py-2 rounded-lg transition-colors text-sm"
            title="Transcribe all downloaded lectures"
          >
            <Mic size={15} />
            <span className="hidden xl:inline">Transcribe All</span>
          </button>
          <button
            onClick={handleDownloadAllGlobal}
            className="flex items-center gap-2 bg-slate-700/60 hover:bg-slate-600 text-slate-400 hover:text-white px-3 py-2 rounded-lg transition-colors text-sm"
            title="Download all pending lectures"
          >
            <Download size={15} />
            <span className="hidden xl:inline">Download All</span>
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium"
          >
            <PlusCircle size={15} />
            Add Course
          </button>
        </div>
      </div>

      {/* Search */}
      {courses.length > 0 && (
        <div className="relative mb-6">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search courses…"
            className="w-full bg-slate-800/50 border border-slate-700 rounded-lg pl-10 pr-4 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-slate-500 focus:ring-1 focus:ring-slate-500 transition-colors"
          />
        </div>
      )}

      {globalQueued !== null && globalQueued > 0 && (
        <p className="text-sm text-indigo-400 mb-4">
          {globalQueued} download{globalQueued !== 1 ? 's' : ''} queued.
        </p>
      )}
      {globalTranscribeQueued !== null && globalTranscribeQueued > 0 && (
        <p className="text-sm text-violet-400 mb-4">
          {globalTranscribeQueued} transcription{globalTranscribeQueued !== 1 ? 's' : ''} queued.
        </p>
      )}

      {loading ? (
        <div className="text-slate-400 text-sm">Loading…</div>
      ) : courses.length === 0 ? (
        <div className="text-center py-24 text-slate-600">
          <BookOpen size={52} className="mx-auto mb-4 opacity-30" />
          <p className="text-lg font-medium text-slate-500">No courses yet</p>
          <p className="text-sm mt-1">Click "Add Course" and paste in an Echo360 URL.</p>
        </div>
      ) : (
        <div className="flex flex-col gap-10">
          {Object.entries(
            filteredCourses.reduce<Record<string, Course[]>>((groups, c) => {
              const year = c.year ?? 'Unsynced'
              ;(groups[year] ??= []).push(c)
              return groups
            }, {})
          )
            .sort(([a], [b]) => b.localeCompare(a))
            .map(([year, group]) => {
              const sorted = [...group].sort((a, b) => (a.display_name || a.name).localeCompare(b.display_name || b.name))
              const yearDuration = group.reduce((s, c) => s + c.total_duration_seconds, 0)
              return (
              <div key={year}>
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <h2 className="text-lg font-bold text-slate-300">{year}</h2>
                    <span className="text-xs text-slate-500">
                      {group.length} course{group.length !== 1 ? 's' : ''}
                      {yearDuration > 0 && <> · {formatTotalHours(yearDuration)}</>}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => group.forEach(c => transcribeAll(c.id))}
                      className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-violet-400 hover:bg-slate-800 px-2.5 py-1.5 rounded-md transition-colors"
                      title="Transcribe all lectures in this year"
                    >
                      <Mic size={12} />
                      Transcribe
                    </button>
                    <button
                      onClick={() => group.forEach(c => downloadAll(c.id))}
                      className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-indigo-400 hover:bg-slate-800 px-2.5 py-1.5 rounded-md transition-colors"
                      title="Download all lectures in this year"
                    >
                      <Download size={12} />
                      Download
                    </button>
                    <button
                      onClick={() => group.forEach(c => handleSync(c.id))}
                      className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-white hover:bg-slate-800 px-2.5 py-1.5 rounded-md transition-colors"
                      title="Sync all courses in this year"
                    >
                      <RefreshCw size={12} />
                      Sync
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {sorted.map(course => {
                    const displayName = course.display_name || course.name
                    const [title, code] = splitCourseCode(displayName)
                    return (
                    <Link
                      key={course.id}
                      to="/courses/$id"
                      params={{ id: String(course.id) }}
                      className="group bg-slate-800/70 rounded-xl p-5 flex flex-col border border-slate-700/50 hover:border-slate-500 hover:bg-slate-800 transition-all"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <h3 className="font-semibold text-white leading-snug group-hover:text-indigo-300 transition-colors">{title}</h3>
                            {code && (
                              <span className="text-xs text-slate-500 font-mono mt-0.5 inline-block">{code}</span>
                            )}
                          </div>
                          <ChevronRight size={16} className="text-slate-600 group-hover:text-slate-400 mt-0.5 shrink-0 transition-colors" />
                        </div>

                        <p className="text-sm text-slate-400 mt-2">
                          {course.lecture_count} lecture{course.lecture_count !== 1 ? 's' : ''}
                          {course.total_duration_seconds > 0 && (
                            <span className="text-slate-500"> · {formatTotalHours(course.total_duration_seconds)}</span>
                          )}
                        </p>

                        {course.lecture_count > 0 && (
                          <div className="mt-2">
                            <StatusBadge
                              downloaded={course.downloaded_count}
                              transcribed={course.transcribed_count}
                              notes={course.notes_count}
                              total={course.lecture_count}
                              noMedia={course.no_media_count}
                            />
                          </div>
                        )}

                        {course.downloading_count > 0 && (
                          <div className="flex items-center gap-1.5 mt-2">
                            <div className="w-2.5 h-2.5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
                            <p className="text-xs text-indigo-400">
                              Downloading {course.downloading_count} lecture{course.downloading_count !== 1 ? 's' : ''}…
                            </p>
                          </div>
                        )}
                        {course.queued_count > 0 && (
                          <p className="text-xs text-slate-500 mt-2">
                            {course.queued_count} in queue
                          </p>
                        )}
                      </div>

                      <div className="flex items-center gap-2 mt-4 pt-3 border-t border-slate-700/50">
                        <button
                          onClick={(e) => handleSyncClick(course.id, e)}
                          disabled={syncing.has(course.id)}
                          title="Sync lectures"
                          className="p-1.5 rounded-md bg-slate-700/50 hover:bg-slate-600 text-slate-500 hover:text-white transition-colors disabled:opacity-40"
                        >
                          <RefreshCw size={13} className={syncing.has(course.id) ? 'animate-spin' : ''} />
                        </button>

                        <button
                          onClick={(e) => handleDelete(course.id, e)}
                          title="Remove course"
                          className="p-1.5 rounded-md bg-slate-700/50 hover:bg-red-600/80 text-slate-500 hover:text-white transition-colors"
                        >
                          <Trash2 size={13} />
                        </button>

                        {course.last_synced_at && (
                          <span className="ml-auto text-[10px] text-slate-600" title={`Synced ${new Date(course.last_synced_at).toLocaleString()}`}>
                            Synced {new Date(course.last_synced_at).toLocaleDateString()}
                          </span>
                        )}
                        {!course.last_synced_at && (
                          <span className="ml-auto text-[10px] text-amber-500/80">Syncing…</span>
                        )}
                      </div>
                    </Link>
                    )
                  })}
                </div>
              </div>
              )
            })}
          {filteredCourses.length === 0 && search.trim() && (
            <div className="text-center py-12 text-slate-500">
              <p className="text-sm">No courses match "{search}"</p>
            </div>
          )}
        </div>
      )}

      {showAdd && (
        <AddCourseModal onDone={handleDone} onClose={() => setShowAdd(false)} />
      )}
    </div>
  )
}
