import type { Course, Lecture } from './types'

const BASE = '/api'

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export const getCourses = (): Promise<Course[]> =>
  fetch(`${BASE}/courses`).then(r => _json(r))

export const addCourse = (url: string): Promise<Course> =>
  fetch(`${BASE}/courses`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  }).then(r => _json(r))

export const discoverCourses = (url: string): Promise<{ added: number; skipped: number }> =>
  fetch(`${BASE}/courses/discover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  }).then(r => _json(r))

export const getCourse = (id: number): Promise<Course> =>
  fetch(`${BASE}/courses/${id}`).then(r => _json(r))

export const syncCourse = (id: number): Promise<void> =>
  fetch(`${BASE}/courses/${id}/sync`, { method: 'POST' }).then(r => _json(r))

export const deleteCourse = (id: number): Promise<void> =>
  fetch(`${BASE}/courses/${id}`, { method: 'DELETE' }).then(() => undefined)

export const getLectures = (courseId: number): Promise<Lecture[]> =>
  fetch(`${BASE}/courses/${courseId}/lectures`).then(r => _json(r))

export const downloadLecture = (id: number): Promise<void> =>
  fetch(`${BASE}/lectures/${id}/download`, { method: 'POST' }).then(r => _json(r))

export const downloadAll = (courseId: number): Promise<{ queued: number }> =>
  fetch(`${BASE}/courses/${courseId}/download-all`, { method: 'POST' }).then(r => _json(r))
