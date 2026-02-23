import type { Course, Lecture, Note, Transcript, PipelineStatus, PipelineConfig } from './types'

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

export const transcribeLecture = (id: number, model = 'cloud'): Promise<{ status: string }> =>
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
  notes_status: string
  course_id: number
  course_name: string
  error_message: string | null
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

export const transcribeAll = (courseId: number, model = 'modal'): Promise<{ queued: number }> =>
  fetch(`${BASE}/courses/${courseId}/transcribe-all`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  }).then(r => _json(r))

export const transcribeAllGlobal = (model = 'modal'): Promise<{ queued: number }> =>
  fetch(`${BASE}/transcribe-all`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  }).then(r => _json(r))

export const generateNotes = (id: number, model: string): Promise<{ status: string }> =>
  fetch(`${BASE}/lectures/${id}/generate-notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  }).then(r => _json(r))

export const getNotes = (id: number): Promise<Note> =>
  fetch(`${BASE}/lectures/${id}/notes`).then(r => _json(r))

export const generateNotesAll = (courseId: number, model: string): Promise<{ queued: number }> =>
  fetch(`${BASE}/courses/${courseId}/generate-notes-all`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  }).then(r => _json(r))

export const extractFrames = (id: number): Promise<{ status: string }> =>
  fetch(`${BASE}/lectures/${id}/extract-frames`, { method: 'POST' }).then(r => _json(r))

export interface FrameInfo {
  url: string
  time: number
  reason: string
}

export const getFrames = (id: number): Promise<FrameInfo[]> =>
  fetch(`${BASE}/lectures/${id}/frames`).then(r => _json(r))

export const redownloadLecture = (id: number): Promise<{ status: string }> =>
  fetch(`${BASE}/lectures/${id}/redownload`, { method: 'POST' }).then(r => _json(r))

export const bulkRedownload = (ids: number[]): Promise<{ queued: number }> =>
  fetch(`${BASE}/lectures/bulk-redownload`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lecture_ids: ids }),
  }).then(r => _json(r))

export const bulkDownload = (ids: number[]): Promise<{ queued: number }> =>
  fetch(`${BASE}/lectures/bulk-download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lecture_ids: ids }),
  }).then(r => _json(r))

export const bulkTranscribe = (ids: number[], model = 'modal'): Promise<{ queued: number }> =>
  fetch(`${BASE}/lectures/bulk-transcribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lecture_ids: ids, model }),
  }).then(r => _json(r))

export const bulkGenerateNotes = (ids: number[], model: string): Promise<{ queued: number }> =>
  fetch(`${BASE}/lectures/bulk-generate-notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lecture_ids: ids, model }),
  }).then(r => _json(r))

export const fixTitles = (courseId: number): Promise<{ status: string }> =>
  fetch(`${BASE}/courses/${courseId}/fix-titles`, { method: 'POST' }).then(r => _json(r))

export const updateCourseDisplayName = (courseId: number, displayName: string | null): Promise<Course> =>
  fetch(`${BASE}/courses/${courseId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: displayName }),
  }).then(r => _json(r))

// ── Pipeline ──────────────────────────────────────────────────────────────────

export const getPipelineStatus = (): Promise<PipelineStatus[]> =>
  fetch(`${BASE}/pipeline/status`).then(r => _json(r))

export const runLecturePipeline = (id: number, config?: PipelineConfig): Promise<{ status: string }> =>
  fetch(`${BASE}/lectures/${id}/pipeline`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config ?? {}),
  }).then(r => _json(r))

export const runCoursePipeline = (courseId: number, config?: PipelineConfig): Promise<{ queued: number }> =>
  fetch(`${BASE}/courses/${courseId}/pipeline`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config ?? {}),
  }).then(r => _json(r))

export const runGlobalPipeline = (config?: PipelineConfig): Promise<{ queued: number }> =>
  fetch(`${BASE}/pipeline`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config ?? {}),
  }).then(r => _json(r))
