import { useState, useEffect, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Play, ChevronDown, ChevronRight, RotateCcw } from 'lucide-react'
import type { PipelineStatus, Lecture, PipelineConfig, SSEMessage } from '../types'
import { getPipelineStatus, runLecturePipeline, runCoursePipeline, runGlobalPipeline } from '../api'
import { useSSE } from '../hooks/useSSE'

const STAGES = ['audio', 'transcript', 'notes', 'frames'] as const
type Stage = typeof STAGES[number]

const STAGE_STATUS_FIELD: Record<Stage, keyof Lecture> = {
  audio: 'audio_status',
  transcript: 'transcript_status',
  notes: 'notes_status',
  frames: 'frames_status',
}

const STAGE_MODEL_FIELD: Record<string, keyof Lecture> = {
  transcript: 'transcript_model',
  notes: 'notes_model',
}

const STAGE_LABELS: Record<Stage, string> = {
  audio: 'Audio',
  transcript: 'Transcript',
  notes: 'Notes',
  frames: 'Frames',
}

const IN_PROGRESS_STATUSES = new Set([
  'queued', 'downloading', 'downloaded', 'converting', 'transcribing', 'generating', 'extracting',
])

function statusColor(status: string): string {
  if (status === 'done') return 'bg-emerald-600 text-white'
  if (status === 'error') return 'bg-red-600 text-white'
  if (status === 'no_media') return 'bg-slate-600 text-slate-300'
  if (IN_PROGRESS_STATUSES.has(status)) return 'bg-blue-600 text-white animate-pulse'
  return 'bg-slate-700 text-slate-400'
}

function ProgressBar({ done, total, color }: { done: number; total: number; color: string }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="h-1.5 flex-1 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-400 tabular-nums whitespace-nowrap">{done}/{total}</span>
    </div>
  )
}

// ── Stage Pill ────────────────────────────────────────────────────────────────

function StagePill({ stage, lecture }: { stage: Stage; lecture: Lecture }) {
  const status = lecture[STAGE_STATUS_FIELD[stage]] as string
  const modelField = STAGE_MODEL_FIELD[stage]
  const model = modelField ? (lecture[modelField] as string | null) : null

  let tooltip = `${STAGE_LABELS[stage]}: ${status}`
  if (status === 'done' && model) tooltip += ` (${model})`
  if (status === 'error' && lecture.error_message) tooltip += ` — ${lecture.error_message}`

  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-[11px] font-medium ${statusColor(status)}`}
      title={tooltip}
    >
      {STAGE_LABELS[stage]}
    </span>
  )
}

// ── Re-run Dropdown ───────────────────────────────────────────────────────────

function RerunDropdown({ onRerun }: { onRerun: (fromStage: Stage) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="text-xs text-slate-400 hover:text-white px-1.5 py-0.5 rounded hover:bg-slate-700 flex items-center gap-0.5"
      >
        <RotateCcw size={11} />
        <ChevronDown size={11} />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 bg-slate-800 border border-slate-600 rounded shadow-lg z-50 py-1 min-w-[160px]">
          {STAGES.map(s => (
            <button
              key={s}
              onClick={() => { onRerun(s); setOpen(false) }}
              className="block w-full text-left text-xs px-3 py-1.5 hover:bg-slate-700 text-slate-300"
            >
              Re-run from {STAGE_LABELS[s]}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Lecture Node ───────────────────────────────────────────────────────────────

function LectureNode({ lecture, config }: { lecture: Lecture; config: PipelineConfig }) {
  const handleRun = () => runLecturePipeline(lecture.id, config)
  const handleRerun = (fromStage: Stage) =>
    runLecturePipeline(lecture.id, { ...config, from_stage: fromStage, force: true })

  return (
    <div className="flex items-center gap-3 py-1.5 px-3 hover:bg-slate-800/50 rounded group">
      <span className="text-xs text-slate-500 w-20 shrink-0 tabular-nums">{lecture.date}</span>
      <span className="text-sm text-slate-300 truncate flex-1 min-w-0">{lecture.title}</span>
      <div className="flex items-center gap-1">
        {STAGES.map(s => (
          <StagePill key={s} stage={s} lecture={lecture} />
        ))}
      </div>
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={handleRun}
          className="text-xs text-slate-400 hover:text-white px-1.5 py-0.5 rounded hover:bg-slate-700"
          title="Run pipeline from first incomplete stage"
        >
          <Play size={12} />
        </button>
        <RerunDropdown onRerun={handleRerun} />
      </div>
    </div>
  )
}

// ── Course Node ───────────────────────────────────────────────────────────────

function CourseNode({ course, config }: { course: PipelineStatus; config: PipelineConfig }) {
  const [expanded, setExpanded] = useState(false)
  const handleRun = () => runCoursePipeline(course.course_id, config)

  const eligible = course.total - course.no_media

  return (
    <div className="bg-slate-800/40 rounded-lg border border-slate-700/50 mb-3">
      <div className="flex items-center gap-3 px-4 py-3">
        <button onClick={() => setExpanded(!expanded)} className="text-slate-400 hover:text-white">
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <span className="text-sm font-semibold text-slate-200 flex-shrink-0 truncate max-w-[240px]">
          {course.display_name || course.course_name}
        </span>
        <div className="flex-1 grid grid-cols-4 gap-3">
          <ProgressBar done={course.audio_done} total={eligible} color="bg-indigo-500" />
          <ProgressBar done={course.transcript_done} total={eligible} color="bg-cyan-500" />
          <ProgressBar done={course.notes_done} total={eligible} color="bg-amber-500" />
          <ProgressBar done={course.frames_done} total={eligible} color="bg-purple-500" />
        </div>
        <span className="text-xs text-slate-500 w-16 text-right">{course.total} lec</span>
        <button
          onClick={handleRun}
          className="text-xs text-slate-400 hover:text-white px-2 py-1 rounded hover:bg-slate-700 flex items-center gap-1"
          title="Run pipeline for entire course"
        >
          <Play size={12} /> Run
        </button>
      </div>
      {expanded && (
        <div className="pb-3 px-2">
          {course.lectures.map(lec => (
            <LectureNode key={lec.id} lecture={lec} config={config} />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Platform Summary ──────────────────────────────────────────────────────────

function PlatformSummary({ data }: { data: PipelineStatus[] }) {
  const totals = useMemo(() => {
    return data.reduce(
      (acc, c) => ({
        total: acc.total + c.total,
        no_media: acc.no_media + c.no_media,
        audio: acc.audio + c.audio_done,
        transcript: acc.transcript + c.transcript_done,
        notes: acc.notes + c.notes_done,
        frames: acc.frames + c.frames_done,
      }),
      { total: 0, no_media: 0, audio: 0, transcript: 0, notes: 0, frames: 0 },
    )
  }, [data])

  const eligible = totals.total - totals.no_media

  const bars: { label: string; done: number; color: string }[] = [
    { label: 'Audio', done: totals.audio, color: 'bg-indigo-500' },
    { label: 'Transcript', done: totals.transcript, color: 'bg-cyan-500' },
    { label: 'Notes', done: totals.notes, color: 'bg-amber-500' },
    { label: 'Frames', done: totals.frames, color: 'bg-purple-500' },
  ]

  return (
    <div className="grid grid-cols-4 gap-4 mb-6">
      {bars.map(b => (
        <div key={b.label} className="bg-slate-800/60 rounded-lg p-3 border border-slate-700/50">
          <div className="text-xs text-slate-400 mb-1">{b.label}</div>
          <ProgressBar done={b.done} total={eligible} color={b.color} />
        </div>
      ))}
    </div>
  )
}

// ── Toolbar ───────────────────────────────────────────────────────────────────

function PipelineToolbar({
  config,
  setConfig,
  onRunAll,
}: {
  config: PipelineConfig
  setConfig: (c: PipelineConfig) => void
  onRunAll: () => void
}) {
  return (
    <div className="sticky top-0 z-30 bg-slate-900/95 backdrop-blur border-b border-slate-700/50 px-6 py-3 flex items-center gap-4 flex-wrap">
      <label className="text-xs text-slate-400 flex items-center gap-1.5">
        Transcript:
        <select
          value={config.transcript_model || 'groq'}
          onChange={e => setConfig({ ...config, transcript_model: e.target.value })}
          className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200"
        >
          <optgroup label="Remote">
            <option value="cloud">Cloud auto (Groq → Modal)</option>
            <option value="groq">Groq cloud (fast)</option>
            <option value="groq:whisper-large-v3">Groq large-v3 (best)</option>
            <option value="modal">Modal GPU (no limits)</option>
          </optgroup>
          <optgroup label="Local">
            <option value="tiny">tiny (fastest)</option>
            <option value="base">base</option>
            <option value="small">small</option>
            <option value="turbo">turbo (best quality)</option>
          </optgroup>
        </select>
      </label>
      <label className="text-xs text-slate-400 flex items-center gap-1.5">
        Notes:
        <select
          value={config.notes_model || 'openrouter/meta-llama/llama-3.3-70b-instruct'}
          onChange={e => setConfig({ ...config, notes_model: e.target.value })}
          className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-slate-200"
        >
          <option value="auto">Auto (free first)</option>
          <optgroup label="Free">
            <option value="openrouter/meta-llama/llama-3.3-70b-instruct:free">Llama 3.3 70B</option>
            <option value="openrouter/google/gemma-3-27b-it:free">Gemma 3 27B</option>
            <option value="openrouter/mistralai/mistral-small-3.1-24b-instruct:free">Mistral Small 3.1</option>
            <option value="openrouter/qwen/qwen3-next-80b-a3b-instruct:free">Qwen3 Next 80B</option>
            <option value="openrouter/deepseek/deepseek-r1-0528:free">DeepSeek R1</option>
            <option value="openrouter/nousresearch/hermes-3-llama-3.1-405b:free">Hermes 3 405B</option>
          </optgroup>
          <optgroup label="Cheap">
            <option value="openrouter/google/gemini-2.5-flash-lite">Gemini 2.5 Flash Lite</option>
            <option value="openrouter/minimax/minimax-m2.1">MiniMax M2.1</option>
          </optgroup>
          <optgroup label="Paid">
            <option value="openrouter/meta-llama/llama-3.3-70b-instruct">Llama 3.3 70B</option>
            <option value="openrouter/anthropic/claude-sonnet-4">Claude Sonnet</option>
            <option value="openrouter/openai/gpt-4o-mini">GPT-4o Mini</option>
          </optgroup>
        </select>
      </label>
      <label className="text-xs text-slate-400 flex items-center gap-1.5">
        <input
          type="checkbox"
          checked={config.run_frames ?? true}
          onChange={e => setConfig({ ...config, run_frames: e.target.checked })}
          className="rounded bg-slate-700 border-slate-600"
        />
        Frames
      </label>
      <div className="flex-1" />

      <div className="hidden lg:grid grid-cols-4 gap-3 w-[320px] mr-[88px]">
        <span className="text-[10px] text-indigo-400 text-center">Audio</span>
        <span className="text-[10px] text-cyan-400 text-center">Transcript</span>
        <span className="text-[10px] text-amber-400 text-center">Notes</span>
        <span className="text-[10px] text-purple-400 text-center">Frames</span>
      </div>

      <button
        onClick={onRunAll}
        className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-1.5 rounded-lg flex items-center gap-1.5 font-medium"
      >
        <Play size={14} /> Run All
      </button>
    </div>
  )
}

// ── Main PipelineView ─────────────────────────────────────────────────────────

export default function PipelineView() {
  const [data, setData] = useState<PipelineStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [config, setConfig] = useState<PipelineConfig>({
    transcript_model: 'modal',
    notes_model: 'auto',
    run_frames: true,
  })

  const fetchData = useCallback(() => {
    getPipelineStatus().then(setData).catch(console.error).finally(() => setLoading(false))
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  // SSE: update individual lecture statuses in-place
  const handleSSE = useCallback((msg: SSEMessage) => {
    if (!msg.lecture_id) return

    const updateLecture = (lec: Lecture): Lecture => {
      if (lec.id !== msg.lecture_id) return lec
      const updated = { ...lec }

      if (msg.type === 'lecture_update' && msg.status) {
        updated.audio_status = msg.status as Lecture['audio_status']
        if (msg.audio_path !== undefined) updated.audio_path = msg.audio_path
        if (msg.error) updated.error_message = msg.error
      }
      if (msg.type === 'transcription_start') updated.transcript_status = 'transcribing'
      if (msg.type === 'transcription_done') updated.transcript_status = 'done'
      if (msg.type === 'transcription_error') {
        updated.transcript_status = 'error'
        if (msg.error) updated.error_message = msg.error
      }
      if (msg.type === 'notes_start') updated.notes_status = 'generating'
      if (msg.type === 'notes_done') updated.notes_status = 'done'
      if (msg.type === 'notes_error') {
        updated.notes_status = 'error'
        if (msg.error) updated.error_message = msg.error
      }
      if (msg.type === 'frames_start') updated.frames_status = 'extracting'
      if (msg.type === 'frames_done') updated.frames_status = 'done'
      if (msg.type === 'frames_error') {
        updated.frames_status = 'error'
        if (msg.error) updated.error_message = msg.error
      }
      return updated
    }

    setData(prev =>
      prev.map(course => {
        const updatedLectures = course.lectures.map(updateLecture)
        return {
          ...course,
          lectures: updatedLectures,
          audio_done: updatedLectures.filter(l => l.audio_status === 'done').length,
          no_media: updatedLectures.filter(l => l.audio_status === 'no_media').length,
          transcript_done: updatedLectures.filter(l => l.transcript_status === 'done').length,
          notes_done: updatedLectures.filter(l => l.notes_status === 'done').length,
          frames_done: updatedLectures.filter(l => l.frames_status === 'done').length,
          error_count: updatedLectures.filter(l => l.error_message).length,
          in_progress: updatedLectures.filter(l =>
            ['queued', 'downloading', 'downloaded', 'converting'].includes(l.audio_status)
            || ['queued', 'transcribing'].includes(l.transcript_status)
            || ['queued', 'generating'].includes(l.notes_status)
            || ['queued', 'extracting'].includes(l.frames_status)
          ).length,
        }
      }),
    )
  }, [])

  useSSE(handleSSE)

  // Group courses by year, sorted descending (same as main page)
  const groupedByYear = useMemo(() => {
    const groups: Record<string, PipelineStatus[]> = {}
    for (const course of data) {
      const year = course.year ?? 'Unsynced'
      ;(groups[year] ??= []).push(course)
    }
    return Object.entries(groups)
      .sort(([a], [b]) => b.localeCompare(a))
      .map(([year, courses]) => ({
        year,
        courses: [...courses].sort((a, b) =>
          (a.display_name || a.course_name).localeCompare(b.display_name || b.course_name)
        ),
      }))
  }, [data])

  const handleRunAll = () => runGlobalPipeline(config)

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-slate-400">Loading pipeline status...</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100">
      <PipelineToolbar config={config} setConfig={setConfig} onRunAll={handleRunAll} />

      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="flex items-center gap-3 mb-6">
          <Link to="/" className="text-slate-400 hover:text-white">
            <ArrowLeft size={18} />
          </Link>
          <h1 className="text-xl font-semibold">Pipeline</h1>
        </div>

        <PlatformSummary data={data} />

        {data.length === 0 ? (
          <div className="text-center text-slate-500 py-12">No courses found. Add courses first.</div>
        ) : (
          <div className="flex flex-col gap-8">
            {groupedByYear.map(({ year, courses }) => (
              <div key={year}>
                <h2 className="text-lg font-bold text-slate-300 mb-3">{year}</h2>
                {courses.map(course => (
                  <CourseNode key={course.course_id} course={course} config={config} />
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
