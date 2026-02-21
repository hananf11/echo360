export interface Course {
  id: number
  name: string
  url: string
  section_id: string
  hostname: string
  last_synced_at: string | null
  lecture_count: number
  downloading_count: number
  queued_count: number
  year: string | null
  total_duration_seconds: number
}

export interface Lecture {
  id: number
  course_id: number
  echo_id: string
  title: string
  date: string
  audio_path: string | null
  audio_status: 'pending' | 'queued' | 'downloading' | 'downloaded' | 'converting' | 'done' | 'error'
  transcript_status: 'pending' | 'queued' | 'transcribing' | 'done' | 'error'
  transcript_model: string | null
  duration_seconds: number | null
}

export interface Transcript {
  model: string
  segments: { start: number; end: number; text: string }[]
  created_at: string
}

export interface SSEMessage {
  type: string
  course_id?: number
  lecture_id?: number
  status?: string
  error?: string
  course_name?: string
  count?: number
  audio_path?: string | null
  progress?: { done: number; total: number }
}
