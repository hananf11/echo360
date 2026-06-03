import { Link } from '@tanstack/react-router'
import { BookOpen, GitBranch, ListOrdered, AlertTriangle, Loader2 } from 'lucide-react'

function NavTab({ to, icon: Icon, label }: { to: string; icon: typeof BookOpen; label: string }) {
  return (
    <Link
      to={to}
      activeOptions={{ exact: true }}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors text-slate-400 hover:text-white hover:bg-slate-800"
      activeProps={{ className: 'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors bg-slate-700/80 text-white' }}
    >
      <Icon size={15} />
      {label}
    </Link>
  )
}

export default function Layout({
  activeCount,
  onOpenQueue,
  sessionValid,
  sessionRefreshing,
  onOpenLogin,
  children,
}: {
  activeCount: number
  onOpenQueue: () => void
  sessionValid: boolean
  sessionRefreshing: boolean
  onOpenLogin: () => void
  children: React.ReactNode
}) {
  return (
    <div className="min-h-screen bg-slate-900 text-slate-100">
      <header className="sticky top-0 z-50 bg-slate-900/95 backdrop-blur border-b border-slate-700/50">
        <div className="max-w-7xl mx-auto px-6 flex items-center h-12 gap-2">
          <span className="text-sm font-bold text-slate-300 mr-3">Echo360</span>
          <nav className="flex items-center gap-1">
            <NavTab to="/" icon={BookOpen} label="Library" />
            <NavTab to="/pipeline" icon={GitBranch} label="Pipeline" />
          </nav>
          <div className="flex-1" />
          <button
            onClick={onOpenQueue}
            className="flex items-center gap-1.5 text-slate-400 hover:text-white px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-slate-800 transition-colors"
          >
            <ListOrdered size={15} />
            Queue
            {activeCount > 0 && (
              <span className="bg-indigo-600 text-white text-xs font-bold px-1.5 py-0.5 rounded-full min-w-[20px] text-center">
                {activeCount}
              </span>
            )}
          </button>
        </div>
      </header>
      {!sessionValid && (
        <div className="bg-amber-900/50 border-b border-amber-700/50">
          <div className="max-w-7xl mx-auto px-6 py-2 flex items-center gap-3">
            {sessionRefreshing ? (
              <>
                <Loader2 size={16} className="text-amber-400 shrink-0 animate-spin" />
                <span className="text-sm text-amber-200">Attempting to refresh session...</span>
              </>
            ) : (
              <>
                <AlertTriangle size={16} className="text-amber-400 shrink-0" />
                <span className="text-sm text-amber-200">Echo360 session expired or missing</span>
                <button
                  onClick={onOpenLogin}
                  className="ml-auto px-3 py-1 bg-amber-600 hover:bg-amber-500 text-white text-xs font-medium rounded-md transition-colors"
                >
                  Re-authenticate
                </button>
              </>
            )}
          </div>
        </div>
      )}
      {children}
    </div>
  )
}
