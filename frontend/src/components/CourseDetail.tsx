import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Download } from 'lucide-react'
import { getCourse, getLectures, downloadAll } from '../api'
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

  const load = useCallback(() => {
    Promise.all([getCourse(courseId), getLectures(courseId)])
      .then(([c, ls]) => { setCourse(c); setLectures(ls) })
      .finally(() => setLoading(false))
  }, [courseId])

  useEffect(() => { load() }, [load])

  const handleSSE = useCallback((msg: SSEMessage) => {
    if (msg.type === 'lecture_update' && msg.lecture_id !== undefined) {
      setLectures(prev =>
        prev.map(l =>
          l.id === msg.lecture_id
            ? {
                ...l,
                audio_status: (msg.status as Lecture['audio_status']) ?? l.audio_status,
                audio_path: msg.audio_path !== undefined ? msg.audio_path : l.audio_path,
              }
            : l
        )
      )
    }
  }, [])

  useSSE(handleSSE)

  const handleDownloadAll = async () => {
    const result = await downloadAll(courseId)
    setQueuedCount(result.queued)
  }

  const pendingCount = lectures.filter(
    l => l.audio_status === 'pending' || l.audio_status === 'error'
  ).length
  const doneCount = lectures.filter(l => l.audio_status === 'done').length

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

          {queuedCount !== null && (
            <p className="text-sm text-indigo-400 mb-4">
              {queuedCount} download{queuedCount !== 1 ? 's' : ''} queued.
            </p>
          )}

          {lectures.length === 0 ? (
            <p className="text-slate-500 text-sm py-8 text-center">
              No lectures found. Try syncing the course.
            </p>
          ) : (
            <div className="bg-slate-800 rounded-xl border border-slate-700 overflow-hidden">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-slate-700 text-left">
                    <th className="px-5 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                      Date
                    </th>
                    <th className="px-5 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider">
                      Title
                    </th>
                    <th className="px-5 py-3 text-xs font-medium text-slate-500 uppercase tracking-wider text-right">
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {lectures.map((lecture, i) => (
                    <LectureRow
                      key={lecture.id}
                      lecture={lecture}
                      isLast={i === lectures.length - 1}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
