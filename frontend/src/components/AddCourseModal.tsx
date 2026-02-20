import { useState } from 'react'
import { X } from 'lucide-react'
import { addCourse, discoverCourses } from '../api'

interface Props {
  onDone: () => void
  onClose: () => void
}

function isCoursesPage(url: string) {
  return /\/courses[#?/]?/.test(url)
}

export default function AddCourseModal({ onDone, onClose }: Props) {
  const [url, setUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<string | null>(null)

  const bulk = isCoursesPage(url.trim())

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    setResult(null)
    try {
      if (bulk) {
        const res = await discoverCourses(url.trim())
        setResult(`${res.added} course${res.added !== 1 ? 's' : ''} added${res.skipped ? `, ${res.skipped} skipped` : ''}.`)
        onDone()
      } else {
        await addCourse(url.trim())
        onDone()
        onClose()
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to add course')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-slate-800 rounded-2xl p-6 w-full max-w-md border border-slate-700 shadow-2xl">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold text-white">Add Course</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors">
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="text-sm text-slate-400 block mb-1.5">Echo360 URL</label>
            <input
              type="url"
              value={url}
              onChange={e => setUrl(e.target.value)}
              placeholder="Paste a course URL or your /courses page URL"
              className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              autoFocus
              required
            />
            {bulk && (
              <p className="text-xs text-indigo-400 mt-1.5">
                Courses listing detected — all courses will be imported.
              </p>
            )}
          </div>

          {error && <p className="text-red-400 text-sm">{error}</p>}
          {result && <p className="text-emerald-400 text-sm">{result}</p>}

          <div className="flex justify-end gap-2 mt-1">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm text-slate-400 hover:text-white rounded-lg transition-colors"
            >
              {result ? 'Close' : 'Cancel'}
            </button>
            {!result && (
              <button
                type="submit"
                disabled={loading || !url.trim()}
                className="px-4 py-2 text-sm bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg disabled:opacity-50 transition-colors"
              >
                {loading
                  ? bulk ? 'Importing…' : 'Adding…'
                  : bulk ? 'Import All' : 'Add Course'}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  )
}
