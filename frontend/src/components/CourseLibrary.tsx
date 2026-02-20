import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { PlusCircle, RefreshCw, BookOpen, Trash2 } from 'lucide-react'
import { getCourses, addCourse, syncCourse, deleteCourse } from '../api'
import type { Course, SSEMessage } from '../types'
import { useSSE } from '../hooks/useSSE'
import AddCourseModal from './AddCourseModal'

export default function CourseLibrary() {
  const [courses, setCourses] = useState<Course[]>([])
  const [syncing, setSyncing] = useState<Set<number>>(new Set())
  const [showAdd, setShowAdd] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    getCourses()
      .then(setCourses)
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const handleSSE = useCallback((msg: SSEMessage) => {
    if (msg.course_id === undefined) return
    if (msg.type === 'sync_start') {
      setSyncing(s => new Set(s).add(msg.course_id!))
    }
    if (msg.type === 'sync_done' || msg.type === 'sync_error') {
      setSyncing(s => { const n = new Set(s); n.delete(msg.course_id!); return n })
      load()
    }
  }, [load])

  useSSE(handleSSE)

  const handleAdd = async (url: string) => {
    await addCourse(url)
    setShowAdd(false)
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
          <p className="text-slate-400 text-sm mt-1">Your lecture audio archive</p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium"
        >
          <PlusCircle size={16} />
          Add Course
        </button>
      </div>

      {loading ? (
        <div className="text-slate-400 text-sm">Loading…</div>
      ) : courses.length === 0 ? (
        <div className="text-center py-24 text-slate-600">
          <BookOpen size={52} className="mx-auto mb-4 opacity-30" />
          <p className="text-lg font-medium text-slate-500">No courses yet</p>
          <p className="text-sm mt-1">Click "Add Course" and paste in an Echo360 URL.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {courses.map(course => (
            <div
              key={course.id}
              className="bg-slate-800 rounded-xl p-5 flex flex-col gap-4 border border-slate-700 hover:border-slate-500 transition-colors"
            >
              <div className="flex-1 min-w-0">
                <h2 className="font-semibold text-white truncate leading-snug">{course.name}</h2>
                <p className="text-sm text-slate-400 mt-1.5">
                  {course.lecture_count} lecture{course.lecture_count !== 1 ? 's' : ''}
                </p>
                {course.last_synced_at ? (
                  <p className="text-xs text-slate-600 mt-0.5">
                    Synced {new Date(course.last_synced_at).toLocaleDateString()}
                  </p>
                ) : (
                  <p className="text-xs text-amber-500/80 mt-0.5">Syncing…</p>
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
      )}

      {showAdd && (
        <AddCourseModal onAdd={handleAdd} onClose={() => setShowAdd(false)} />
      )}
    </div>
  )
}
