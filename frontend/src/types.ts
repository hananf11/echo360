export interface Course {
  id: number
  name: string
  url: string
  section_id: string
  hostname: string
  last_synced_at: string | null
  lecture_count: number
}

export interface Lecture {
  id: number
  course_id: number
  echo_id: string
  title: string
  date: string
  audio_path: string | null
  audio_status: 'pending' | 'downloading' | 'done' | 'error'
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
}
