export interface Course {
  id: number
  name: string
  display_name: string | null
  url: string
  section_id: string
  hostname: string
  last_synced_at: string | null
  lecture_count: number
  downloading_count: number
  queued_count: number
  downloaded_count: number
  no_media_count: number
  transcribed_count: number
  notes_count: number
  year: string | null
  total_duration_seconds: number
  syncing?: boolean
}

export interface Lecture {
  id: number
  course_id: number
  echo_id: string
  title: string
  date: string
  audio_path: string | null
  audio_status: 'pending' | 'queued' | 'downloading' | 'downloaded' | 'converting' | 'done' | 'error' | 'no_media'
  transcript_status: 'pending' | 'queued' | 'transcribing' | 'done' | 'error'
  transcript_model: string | null
  notes_status: 'pending' | 'queued' | 'generating' | 'done' | 'error'
  notes_model: string | null
  notes_generated_title: string | null
  frames_status: 'pending' | 'queued' | 'extracting' | 'done' | 'error'
  duration_seconds: number | null
  error_message: string | null
}

export interface Transcript {
  model: string
  segments: { start: number; end: number; text: string }[]
  created_at: string
}

export interface Note {
  model: string
  content_md: string
  frame_timestamps: { time: number; reason: string }[]
  created_at: string
}

export interface PipelineStatus {
  course_id: number
  course_name: string
  display_name: string | null
  year: string | null
  total: number
  audio_done: number
  no_media: number
  transcript_done: number
  notes_done: number
  frames_done: number
  error_count: number
  in_progress: number
  lectures: Lecture[]
}

export interface PipelineConfig {
  from_stage?: string
  transcript_model?: string
  notes_model?: string
  run_frames?: boolean
  force?: boolean
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
  frames_status?: string
  progress?: {
    done: number
    total: number
    stage?: 'download' | 'convert'
    speed_bps?: number
    eta_seconds?: number
  }
}
