import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { PlusCircle, RefreshCw, BookOpen, Trash2, Download, HardDrive, Mic, Wand2 } from 'lucide-react'
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

export default function CourseLibrary() {
  const [courses, setCourses] = useState<Course[]>([])
  const [syncing, setSyncing] = useState<Set<number>>(new Set())
  const [showAdd, setShowAdd] = useState(false)
  const [loading, setLoading] = useState(true)
  const [storage, setStorage] = useState<StorageStats | null>(null)
  const [globalQueued, setGlobalQueued] = useState<number | null>(null)
  const [globalTranscribeQueued, setGlobalTranscribeQueued] = useState<number | null>(null)

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

  const handleDelete = async (id: number) => {
    if (!confirm('Remove this course?')) return
    await deleteCourse(id)
    load()
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-10">
      <div className="flex items-center justify-between mb-8">
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
            className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium"
          >
            <Wand2 size={16} />
            Fix Titles
          </button>
          <button
            onClick={handleTranscribeAllGlobal}
            className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium"
          >
            <Mic size={16} />
            Transcribe All
          </button>
          <button
            onClick={handleDownloadAllGlobal}
            className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium"
          >
            <Download size={16} />
            Download All
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium"
          >
            <PlusCircle size={16} />
            Add Course
          </button>
        </div>
      </div>

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
        <div className="flex flex-col gap-8">
          {Object.entries(
            courses.reduce<Record<string, Course[]>>((groups, c) => {
              const year = c.year ?? 'Unsynced'
              ;(groups[year] ??= []).push(c)
              return groups
            }, {})
          )
            .sort(([a], [b]) => b.localeCompare(a))
            .map(([year, group]) => {
              const sorted = [...group].sort((a, b) => a.name.localeCompare(b.name))
              return (
              <div key={year}>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">{year}</h2>
                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => group.forEach(c => transcribeAll(c.id))}
                      className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-white transition-colors"
                      title="Transcribe all lectures in this year"
                    >
                      <Mic size={12} />
                      Transcribe all
                    </button>
                    <button
                      onClick={() => group.forEach(c => downloadAll(c.id))}
                      className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-white transition-colors"
                      title="Download all lectures in this year"
                    >
                      <Download size={12} />
                      Download all
                    </button>
                    <button
                      onClick={() => group.forEach(c => handleSync(c.id))}
                      className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-white transition-colors"
                      title="Sync all courses in this year"
                    >
                      <RefreshCw size={12} />
                      Sync all
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {sorted.map(course => (
            <div
              key={course.id}
              className="bg-slate-800 rounded-xl p-5 flex flex-col gap-4 border border-slate-700 hover:border-slate-500 transition-colors"
            >
              <div className="flex-1 min-w-0">
                <h2 className="font-semibold text-white leading-snug">{course.display_name || course.name}</h2>
                <p className="text-sm text-slate-400 mt-1.5">
                  {course.lecture_count} lecture{course.lecture_count !== 1 ? 's' : ''}
                  {course.total_duration_seconds > 0 && (
                    <span className="text-slate-500"> · {formatTotalHours(course.total_duration_seconds)}</span>
                  )}
                </p>
                {course.lecture_count > 0 && (
                  <div className="flex items-center gap-3 mt-1.5">
                    <span className={`text-xs ${course.downloaded_count + course.no_media_count >= course.lecture_count ? 'text-emerald-400' : 'text-slate-500'}`}>
                      {course.downloaded_count}/{course.lecture_count - course.no_media_count} downloaded
                    </span>
                    <span className={`text-xs ${course.transcribed_count + course.no_media_count >= course.lecture_count ? 'text-violet-400' : 'text-slate-500'}`}>
                      {course.transcribed_count}/{course.lecture_count - course.no_media_count} transcribed
                    </span>
                  </div>
                )}
                {course.last_synced_at ? (
                  <p className="text-xs text-slate-600 mt-0.5">
                    Synced {new Date(course.last_synced_at).toLocaleDateString()}
                  </p>
                ) : (
                  <p className="text-xs text-amber-500/80 mt-0.5">Syncing…</p>
                )}
                {course.downloading_count > 0 && (
                  <div className="flex items-center gap-1.5 mt-1.5">
                    <div className="w-2.5 h-2.5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
                    <p className="text-xs text-indigo-400">
                      Downloading {course.downloading_count} lecture{course.downloading_count !== 1 ? 's' : ''}…
                    </p>
                  </div>
                )}
                {course.queued_count > 0 && (
                  <p className="text-xs text-slate-500 mt-1.5">
                    {course.queued_count} in queue
                  </p>
                )}
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleSync(course.id)}
                  disabled={syncing.has(course.id)}
                  title="Sync lectures"
                  className="p-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-400 hover:text-white transition-colors disabled:opacity-40"
                >
                  <RefreshCw size={14} className={syncing.has(course.id) ? 'animate-spin' : ''} />
                </button>

                <button
                  onClick={() => handleDelete(course.id)}
                  title="Remove course"
                  className="p-1.5 rounded-lg bg-slate-700 hover:bg-red-600/80 text-slate-400 hover:text-white transition-colors"
                >
                  <Trash2 size={14} />
                </button>

                <Link
                  to={`/courses/${course.id}`}
                  className="flex-1 text-center text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 px-3 py-1.5 rounded-lg transition-colors"
                >
                  Open →
                </Link>
              </div>
            </div>
                  ))}
                </div>
              </div>
              )
            })}
        </div>
      )}

      {showAdd && (
        <AddCourseModal onDone={handleDone} onClose={() => setShowAdd(false)} />
      )}
    </div>
  )
}
