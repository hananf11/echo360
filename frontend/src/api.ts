import type { Course, Lecture, Transcript } from './types'

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

export const transcribeLecture = (id: number, model = 'tiny'): Promise<{ status: string }> =>
  fetch(`${BASE}/lectures/${id}/transcribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  }).then(r => _json(r))

export const getTranscript = (id: number): Promise<Transcript> =>
  fetch(`${BASE}/lectures/${id}/transcript`).then(r => _json(r))

export interface QueueItem {
  id: number
  title: string
  date: string
  audio_status: string
  transcript_status: string
  course_id: number
  course_name: string
}

export const getQueue = (): Promise<QueueItem[]> =>
  fetch(`${BASE}/queue`).then(r => _json(r))

export const downloadAllGlobal = (): Promise<{ queued: number }> =>
  fetch(`${BASE}/download-all`, { method: 'POST' }).then(r => _json(r))

export interface StorageStats {
  size_bytes: number
  total_lectures: number
  downloaded_lectures: number
}

export const getStorage = (): Promise<StorageStats> =>
  fetch(`${BASE}/storage`).then(r => _json(r))

export const transcribeAll = (courseId: number, model = 'tiny'): Promise<{ queued: number }> =>
  fetch(`${BASE}/courses/${courseId}/transcribe-all`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  }).then(r => _json(r))
