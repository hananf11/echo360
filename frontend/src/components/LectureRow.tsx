import { Download, CheckCircle, AlertCircle, Clock } from 'lucide-react'
import { downloadLecture } from '../api'
import type { Lecture } from '../types'

interface Props {
  lecture: Lecture
  isLast: boolean
}

function StatusIcon({ status }: { status: Lecture['audio_status'] }) {
  if (status === 'pending') return <Clock size={15} className="text-slate-500" />
  if (status === 'downloading')
    return (
      <div className="w-3.5 h-3.5 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
    )
  if (status === 'done') return <CheckCircle size={15} className="text-emerald-400" />
  return <AlertCircle size={15} className="text-red-400" />
}

const STATUS_LABEL: Record<Lecture['audio_status'], string> = {
  pending: 'Pending',
  downloading: 'Downloading…',
  done: 'Done',
  error: 'Error',
}

export default function LectureRow({ lecture, isLast }: Props) {
  return (
    <tr
      className={`hover:bg-slate-700/40 transition-colors ${
        !isLast ? 'border-b border-slate-700/60' : ''
      }`}
    >
      <td className="px-5 py-3 text-sm text-slate-400 whitespace-nowrap font-mono">
        {lecture.date}
      </td>
      <td className="px-5 py-3 text-sm text-slate-100">{lecture.title}</td>
      <td className="px-5 py-3">
        <div className="flex items-center justify-end gap-2.5">
          <span className="text-xs text-slate-500">{STATUS_LABEL[lecture.audio_status]}</span>
          <StatusIcon status={lecture.audio_status} />
          {(lecture.audio_status === 'pending' || lecture.audio_status === 'error') && (
            <button
              onClick={() => downloadLecture(lecture.id)}
              className="p-1.5 rounded-lg bg-slate-700 hover:bg-indigo-600 text-slate-400 hover:text-white transition-colors"
              title="Download audio"
            >
              <Download size={13} />
            </button>
          )}
        </div>
      </td>
    </tr>
  )
}
