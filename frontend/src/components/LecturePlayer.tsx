import { useState, useEffect, useRef } from 'react'
import { getTranscript, getNotes } from '../api'
import type { Transcript, Note } from '../types'

interface Props {
  lectureId: number
  hasTranscript: boolean
  hasNotes: boolean
  isLast: boolean
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function LecturePlayer({ lectureId, hasTranscript, hasNotes, isLast }: Props) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const activeRef = useRef<HTMLDivElement>(null)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [transcriptError, setTranscriptError] = useState<string | null>(null)
  const [notes, setNotes] = useState<Note | null>(null)
  const [notesError, setNotesError] = useState<string | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
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
    }
  }

  const rowClass = !isLast ? 'border-b border-slate-700/60' : ''
  const hasTabs = hasTranscript || hasNotes

  return (
    <tr className={rowClass}>
      <td colSpan={4} className="px-5 pb-4">
        <div className="bg-slate-900/60 rounded-lg border border-slate-700 overflow-hidden">
          {/* Audio player */}
          <div className="px-4 pt-4 pb-3 border-b border-slate-700/60">
            <audio
              ref={audioRef}
              src={`/api/lectures/${lectureId}/audio`}
              controls
              onTimeUpdate={() => setCurrentTime(audioRef.current?.currentTime ?? 0)}
              className="w-full h-8 [&::-webkit-media-controls-panel]:bg-slate-800 accent-indigo-500"
            />
          </div>

          {/* Tab bar */}
          {hasTabs && (
            <div className="flex border-b border-slate-700/60">
              {hasTranscript && (
                <button
                  onClick={() => setActiveTab('transcript')}
                  className={`px-4 py-2 text-xs font-semibold uppercase tracking-wider transition-colors ${
                    activeTab === 'transcript'
                      ? 'text-indigo-400 border-b-2 border-indigo-400'
                      : 'text-slate-500 hover:text-slate-300'
                  }`}
                >
                  Transcript
                </button>
              )}
              {hasNotes && (
                <button
                  onClick={() => setActiveTab('notes')}
                  className={`px-4 py-2 text-xs font-semibold uppercase tracking-wider transition-colors ${
                    activeTab === 'notes'
                      ? 'text-amber-400 border-b-2 border-amber-400'
                      : 'text-slate-500 hover:text-slate-300'
                  }`}
                >
                  Notes
                </button>
              )}
            </div>
          )}

          {/* Transcript tab */}
          {activeTab === 'transcript' && hasTranscript && (
            <div className="max-h-64 overflow-y-auto">
              {transcriptError && (
                <p className="px-4 py-3 text-xs text-red-400">Failed to load transcript: {transcriptError}</p>
              )}
              {!transcript && !transcriptError && (
                <p className="px-4 py-3 text-xs text-slate-500">Loading transcript…</p>
              )}
              {transcript && (
                <>
                  <div className="flex items-center gap-2 px-4 pt-3 pb-2">
                    <span className="text-xs bg-slate-700 text-slate-400 px-2 py-0.5 rounded-full">{transcript.model}</span>
                  </div>
                  <div className="pb-3">
                    {transcript.segments.map((seg, i) => {
                      const isActive = i === activeIndex
                      return (
                        <div
                          key={i}
                          ref={isActive ? activeRef : undefined}
                          onClick={() => seekTo(seg.start)}
                          className={`flex gap-3 px-4 py-1 cursor-pointer rounded transition-colors text-sm ${
                            isActive
                              ? 'bg-indigo-600/20 text-white'
                              : 'hover:bg-slate-700/40 text-slate-300'
                          }`}
                        >
                          <span className={`font-mono shrink-0 text-xs pt-0.5 ${isActive ? 'text-indigo-400' : 'text-slate-500'}`}>
                            [{formatTime(seg.start)}]
                          </span>
                          <span className="leading-relaxed">{seg.text}</span>
                        </div>
                      )
                    })}
                  </div>
                </>
              )}
            </div>
          )}

          {/* Notes tab */}
          {activeTab === 'notes' && hasNotes && (
            <div className="max-h-96 overflow-y-auto">
              {notesError && (
                <p className="px-4 py-3 text-xs text-red-400">Failed to load notes: {notesError}</p>
              )}
              {!notes && !notesError && (
                <p className="px-4 py-3 text-xs text-slate-500">Loading notes…</p>
              )}
              {notes && (
                <>
                  <div className="flex items-center gap-2 px-4 pt-3 pb-2">
                    <span className="text-xs bg-slate-700 text-slate-400 px-2 py-0.5 rounded-full">{notes.model}</span>
                  </div>

                  {/* Markdown notes */}
                  <div
                    className="px-4 pb-4 prose prose-invert prose-sm max-w-none
                      prose-headings:text-slate-200 prose-headings:font-semibold prose-headings:mt-4 prose-headings:mb-2
                      prose-p:text-slate-300 prose-p:leading-relaxed
                      prose-li:text-slate-300
                      prose-strong:text-white
                      prose-code:text-indigo-300 prose-code:bg-slate-800 prose-code:px-1 prose-code:rounded"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(notes.content_md) }}
                  />

                  {/* Frame timestamps */}
                  {notes.frame_timestamps.length > 0 && (
                    <div className="px-4 pb-4 border-t border-slate-700/60 pt-3">
                      <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Frame Timestamps</p>
                      <div className="flex flex-wrap gap-2">
                        {notes.frame_timestamps.map((ft, i) => (
                          <button
                            key={i}
                            onClick={() => seekTo(ft.time)}
                            className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-slate-800 hover:bg-amber-600/30 border border-slate-700 hover:border-amber-500/50 rounded-lg text-xs transition-colors"
                            title={ft.reason}
                          >
                            <span className="font-mono text-amber-400">{formatTime(ft.time)}</span>
                            <span className="text-slate-400 max-w-[200px] truncate">{ft.reason}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
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
