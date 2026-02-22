import { useState, useEffect, useRef } from 'react'
import { getTranscript, getNotes } from '../api'
import type { Transcript, Note } from '../types'
import { Play, Pause, Volume2, VolumeX, SkipBack, SkipForward, FileText, BookOpen, Clock } from 'lucide-react'

interface Props {
  lectureId: number
  hasTranscript: boolean
  hasNotes: boolean
  isLast: boolean
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function LecturePlayer({ lectureId, hasTranscript, hasNotes, isLast }: Props) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const activeRef = useRef<HTMLDivElement>(null)
  const progressRef = useRef<HTMLDivElement>(null)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [transcriptError, setTranscriptError] = useState<string | null>(null)
  const [notes, setNotes] = useState<Note | null>(null)
  const [notesError, setNotesError] = useState<string | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [volume, setVolume] = useState(1)
  const [activeTab, setActiveTab] = useState<'transcript' | 'notes'>(hasNotes ? 'notes' : 'transcript')

  useEffect(() => {
    if (!hasTranscript) return
    getTranscript(lectureId)
      .then(setTranscript)
      .catch(e => setTranscriptError(e.message))
  }, [lectureId, hasTranscript])

  useEffect(() => {
    if (!hasNotes) return
    getNotes(lectureId)
      .then(setNotes)
      .catch(e => setNotesError(e.message))
  }, [lectureId, hasNotes])

  // Scroll active segment into view
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [currentTime])

  const activeIndex = transcript
    ? [...transcript.segments].reduce((best, s, i) => s.start <= currentTime ? i : best, -1)
    : -1

  const seekTo = (time: number) => {
    if (audioRef.current) {
      audioRef.current.currentTime = time
      audioRef.current.play()
      setPlaying(true)
    }
  }

  const togglePlay = () => {
    if (!audioRef.current) return
    if (playing) { audioRef.current.pause() } else { audioRef.current.play() }
    setPlaying(!playing)
  }

  const skip = (seconds: number) => {
    if (!audioRef.current) return
    audioRef.current.currentTime = Math.max(0, Math.min(duration, audioRef.current.currentTime + seconds))
  }

  const handleProgressClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!progressRef.current || !audioRef.current) return
    const rect = progressRef.current.getBoundingClientRect()
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    audioRef.current.currentTime = pct * duration
  }

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseFloat(e.target.value)
    setVolume(v)
    if (audioRef.current) audioRef.current.volume = v
    setMuted(v === 0)
  }

  const toggleMute = () => {
    if (!audioRef.current) return
    const newMuted = !muted
    setMuted(newMuted)
    audioRef.current.muted = newMuted
  }

  const pct = duration > 0 ? (currentTime / duration) * 100 : 0
  const rowClass = !isLast ? 'border-b border-slate-700/40' : ''
  const hasTabs = hasTranscript || hasNotes

  return (
    <tr className={rowClass}>
      <td colSpan={5} className="px-4 pb-4">
        <audio
          ref={audioRef}
          src={`/api/lectures/${lectureId}/audio`}
          onTimeUpdate={() => setCurrentTime(audioRef.current?.currentTime ?? 0)}
          onLoadedMetadata={() => setDuration(audioRef.current?.duration ?? 0)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          preload="metadata"
        />

        <div className="bg-slate-900/80 rounded-xl border border-slate-700/50 overflow-hidden">
          {/* Custom audio player */}
          <div className="px-5 pt-4 pb-3">
            {/* Progress bar */}
            <div
              ref={progressRef}
              onClick={handleProgressClick}
              className="group relative h-1.5 bg-slate-700 rounded-full cursor-pointer mb-3 hover:h-2 transition-all"
            >
              <div
                className="absolute inset-y-0 left-0 bg-indigo-500 rounded-full transition-all"
                style={{ width: `${pct}%` }}
              />
              <div
                className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full shadow-md opacity-0 group-hover:opacity-100 transition-opacity"
                style={{ left: `${pct}%`, marginLeft: '-6px' }}
              />
            </div>

            {/* Controls */}
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1">
                <button onClick={() => skip(-15)} className="p-1.5 rounded-md text-slate-400 hover:text-white transition-colors" title="Back 15s">
                  <SkipBack size={14} />
                </button>
                <button
                  onClick={togglePlay}
                  className="p-2.5 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
                >
                  {playing ? <Pause size={16} /> : <Play size={16} className="ml-0.5" />}
                </button>
                <button onClick={() => skip(15)} className="p-1.5 rounded-md text-slate-400 hover:text-white transition-colors" title="Forward 15s">
                  <SkipForward size={14} />
                </button>
              </div>

              <span className="text-xs font-mono text-slate-400 min-w-[100px]">
                {formatTime(currentTime)} <span className="text-slate-600">/</span> {formatTime(duration)}
              </span>

              <div className="flex-1" />

              <div className="flex items-center gap-1.5">
                <button onClick={toggleMute} className="p-1 text-slate-400 hover:text-white transition-colors">
                  {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
                </button>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={muted ? 0 : volume}
                  onChange={handleVolumeChange}
                  className="w-20 h-1 accent-indigo-500 bg-slate-700 rounded-full appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:h-2.5 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white"
                />
              </div>
            </div>
          </div>

          {/* Tabs + Content */}
          {hasTabs && (
            <>
              <div className="flex items-center gap-1 px-4 border-t border-slate-700/40">
                {hasTranscript && (
                  <button
                    onClick={() => setActiveTab('transcript')}
                    className={`flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium transition-colors border-b-2 -mb-px ${
                      activeTab === 'transcript'
                        ? 'text-violet-400 border-violet-400'
                        : 'text-slate-500 border-transparent hover:text-slate-300'
                    }`}
                  >
                    <FileText size={13} />
                    Transcript
                    {transcript && <span className="text-[10px] text-slate-600 ml-1">{transcript.model}</span>}
                  </button>
                )}
                {hasNotes && (
                  <button
                    onClick={() => setActiveTab('notes')}
                    className={`flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium transition-colors border-b-2 -mb-px ${
                      activeTab === 'notes'
                        ? 'text-amber-400 border-amber-400'
                        : 'text-slate-500 border-transparent hover:text-slate-300'
                    }`}
                  >
                    <BookOpen size={13} />
                    Notes
                    {notes && <span className="text-[10px] text-slate-600 ml-1">{notes.model}</span>}
                  </button>
                )}
              </div>

              {/* Transcript content */}
              {activeTab === 'transcript' && hasTranscript && (
                <div className="max-h-80 overflow-y-auto overscroll-contain">
                  {transcriptError && (
                    <p className="px-5 py-4 text-xs text-red-400">Failed to load transcript: {transcriptError}</p>
                  )}
                  {!transcript && !transcriptError && (
                    <p className="px-5 py-4 text-xs text-slate-500">Loading transcript…</p>
                  )}
                  {transcript && (
                    <div className="py-2">
                      {transcript.segments.map((seg, i) => {
                        const isActive = i === activeIndex
                        return (
                          <div
                            key={i}
                            ref={isActive ? activeRef : undefined}
                            onClick={() => seekTo(seg.start)}
                            className={`group flex gap-3 px-5 py-1.5 cursor-pointer transition-colors ${
                              isActive
                                ? 'bg-violet-500/10 border-l-2 border-violet-400'
                                : 'border-l-2 border-transparent hover:bg-slate-800/60'
                            }`}
                          >
                            <span className={`font-mono shrink-0 text-[11px] pt-0.5 tabular-nums ${
                              isActive ? 'text-violet-400' : 'text-slate-600 group-hover:text-slate-400'
                            }`}>
                              {formatTime(seg.start)}
                            </span>
                            <span className={`text-sm leading-relaxed ${
                              isActive ? 'text-slate-100' : 'text-slate-400'
                            }`}>
                              {seg.text}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )}

              {/* Notes content */}
              {activeTab === 'notes' && hasNotes && (
                <div className="max-h-[32rem] overflow-y-auto overscroll-contain">
                  {notesError && (
                    <p className="px-5 py-4 text-xs text-red-400">Failed to load notes: {notesError}</p>
                  )}
                  {!notes && !notesError && (
                    <p className="px-5 py-4 text-xs text-slate-500">Loading notes…</p>
                  )}
                  {notes && (
                    <>
                      <div
                        className="px-6 py-4 prose prose-invert prose-sm max-w-none
                          prose-headings:text-slate-100 prose-headings:font-semibold
                          prose-h1:text-lg prose-h1:mt-6 prose-h1:mb-3 prose-h1:pb-2 prose-h1:border-b prose-h1:border-slate-700/50
                          prose-h2:text-base prose-h2:mt-5 prose-h2:mb-2
                          prose-h3:text-sm prose-h3:mt-4 prose-h3:mb-2 prose-h3:text-slate-200
                          prose-p:text-slate-300 prose-p:leading-relaxed prose-p:my-2
                          prose-li:text-slate-300 prose-li:my-0.5
                          prose-ul:my-2
                          prose-strong:text-white prose-strong:font-semibold
                          prose-code:text-indigo-300 prose-code:bg-slate-800/80 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs
                          prose-pre:bg-slate-800/80 prose-pre:border prose-pre:border-slate-700/50 prose-pre:rounded-lg"
                        dangerouslySetInnerHTML={{ __html: renderMarkdown(notes.content_md) }}
                      />

                      {/* Frame timestamps */}
                      {notes.frame_timestamps.length > 0 && (
                        <div className="px-5 pb-4 border-t border-slate-700/40 pt-4 mx-1">
                          <p className="flex items-center gap-1.5 text-[11px] font-medium text-slate-500 uppercase tracking-wider mb-3">
                            <Clock size={12} />
                            Key Moments
                          </p>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                            {notes.frame_timestamps.map((ft, i) => (
                              <button
                                key={i}
                                onClick={() => seekTo(ft.time)}
                                className="flex items-start gap-2 px-3 py-2 bg-slate-800/50 hover:bg-amber-500/10 border border-slate-700/40 hover:border-amber-500/30 rounded-lg text-left transition-colors group"
                                title={ft.reason}
                              >
                                <span className="font-mono text-[11px] text-amber-400 shrink-0 pt-0.5 tabular-nums group-hover:text-amber-300">{formatTime(ft.time)}</span>
                                <span className="text-xs text-slate-400 group-hover:text-slate-300 line-clamp-2">{ft.reason}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </td>
    </tr>
  )
}

/** Minimal markdown → HTML renderer for notes content. */
function renderMarkdown(md: string): string {
  let html = md
    // Escape HTML
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  // Code blocks (``` ... ```)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) =>
    `<pre><code>${code.trim()}</code></pre>`
  )

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')

  // Headers
  html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>')
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>')
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>')

  // Bold and italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')

  // Unordered lists (- item)
  html = html.replace(/^(\s*)- (.+)$/gm, (_m, indent, text) => {
    const level = Math.floor(indent.length / 2)
    return `<li style="margin-left:${level * 1.5}em">${text}</li>`
  })
  // Wrap consecutive <li> in <ul>
  html = html.replace(/((?:<li[^>]*>.*<\/li>\n?)+)/g, '<ul>$1</ul>')

  // Paragraphs: wrap non-tag lines separated by blank lines
  html = html.replace(/\n{2,}/g, '\n\n')
  html = html.split('\n\n').map(block => {
    block = block.trim()
    if (!block) return ''
    if (block.startsWith('<')) return block
    return `<p>${block.replace(/\n/g, '<br>')}</p>`
  }).join('\n')

  return html
}
