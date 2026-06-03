import { useEffect, useRef, useState, useCallback } from 'react'
import { X, Loader2, CheckCircle2 } from 'lucide-react'

const VIEWPORT_WIDTH = 1280
const VIEWPORT_HEIGHT = 800

interface LoginModalProps {
  open: boolean
  onClose: () => void
  onLoginSuccess: () => void
  loginUrl?: string
}

export default function LoginModal({ open, onClose, onLoginSuccess, loginUrl = 'https://echo360.net.au' }: LoginModalProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const imgRef = useRef(new Image())
  const [status, setStatus] = useState<'connecting' | 'streaming' | 'success' | 'error'>('connecting')
  const [statusMessage, setStatusMessage] = useState('Launching browser...')

  const cleanup = useCallback(() => {
    if (wsRef.current) {
      try {
        wsRef.current.send(JSON.stringify({ type: 'close' }))
        wsRef.current.close()
      } catch { /* ignore */ }
      wsRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!open) return

    setStatus('connecting')
    setStatusMessage('Launching browser...')

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${location.host}/api/browser-stream`)
    wsRef.current = ws

    ws.onopen = () => {
      ws.send(JSON.stringify({ url: loginUrl }))
    }

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)

        if (msg.type === 'status') {
          setStatusMessage(msg.message)
          if (msg.message === 'Connected to browser') {
            setStatus('streaming')
          }
        }

        if (msg.type === 'frame') {
          setStatus('streaming')
          const img = imgRef.current
          img.onload = () => {
            const ctx = canvasRef.current?.getContext('2d')
            if (ctx) {
              ctx.drawImage(img, 0, 0, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
            }
          }
          img.src = 'data:image/jpeg;base64,' + msg.data
        }

        if (msg.type === 'login_success') {
          setStatus('success')
          setStatusMessage('Login detected! Session saved.')
          setTimeout(() => {
            onLoginSuccess()
            onClose()
          }, 1500)
        }

        if (msg.type === 'error') {
          setStatus('error')
          setStatusMessage(msg.message || 'An error occurred')
        }
      } catch { /* ignore malformed */ }
    }

    ws.onerror = () => {
      setStatus('error')
      setStatusMessage('WebSocket connection failed')
    }

    ws.onclose = () => {
      if (status !== 'success') {
        // Only set error if we didn't close intentionally
      }
    }

    return cleanup
  }, [open, loginUrl, cleanup, onLoginSuccess, onClose])

  const sendMouseEvent = useCallback((e: React.MouseEvent, type: string) => {
    const canvas = canvasRef.current
    const ws = wsRef.current
    if (!canvas || !ws || ws.readyState !== WebSocket.OPEN) return

    const rect = canvas.getBoundingClientRect()
    const scaleX = VIEWPORT_WIDTH / rect.width
    const scaleY = VIEWPORT_HEIGHT / rect.height
    const x = (e.clientX - rect.left) * scaleX
    const y = (e.clientY - rect.top) * scaleY

    ws.send(JSON.stringify({
      type: 'mouse',
      params: {
        type,
        x: Math.round(x),
        y: Math.round(y),
        button: type === 'mouseMoved' ? 'none' : 'left',
        clickCount: type === 'mousePressed' ? 1 : 0,
      },
    }))
  }, [])

  const sendKeyEvent = useCallback((e: React.KeyboardEvent, type: 'keyDown' | 'keyUp') => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return

    e.preventDefault()

    let modifiers = 0
    if (e.altKey) modifiers |= 1
    if (e.ctrlKey) modifiers |= 2
    if (e.metaKey) modifiers |= 4
    if (e.shiftKey) modifiers |= 8

    const params: Record<string, unknown> = {
      type,
      key: e.key,
      code: e.code,
      modifiers,
      windowsVirtualKeyCode: e.keyCode,
      nativeVirtualKeyCode: e.keyCode,
    }

    if (type === 'keyDown' && e.key.length === 1) {
      params.text = e.key
    }

    ws.send(JSON.stringify({ type: 'key', params }))
  }, [])

  const sendScroll = useCallback((e: React.WheelEvent) => {
    const canvas = canvasRef.current
    const ws = wsRef.current
    if (!canvas || !ws || ws.readyState !== WebSocket.OPEN) return

    const rect = canvas.getBoundingClientRect()
    const scaleX = VIEWPORT_WIDTH / rect.width
    const scaleY = VIEWPORT_HEIGHT / rect.height

    ws.send(JSON.stringify({
      type: 'scroll',
      x: Math.round((e.clientX - rect.left) * scaleX),
      y: Math.round((e.clientY - rect.top) * scaleY),
      deltaX: Math.round(e.deltaX),
      deltaY: Math.round(e.deltaY),
    }))
  }, [])

  const handleClose = () => {
    cleanup()
    onClose()
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-slate-800 rounded-xl border border-slate-700 shadow-2xl flex flex-col max-w-[95vw] max-h-[95vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
          <div className="flex items-center gap-2">
            {status === 'connecting' && <Loader2 size={16} className="animate-spin text-amber-400" />}
            {status === 'streaming' && <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />}
            {status === 'success' && <CheckCircle2 size={16} className="text-green-400" />}
            <span className="text-sm text-slate-300">
              {status === 'connecting' && statusMessage}
              {status === 'streaming' && 'Log in to Echo360 below'}
              {status === 'success' && statusMessage}
              {status === 'error' && statusMessage}
            </span>
          </div>
          <button
            onClick={handleClose}
            className="text-slate-400 hover:text-white p-1 rounded hover:bg-slate-700 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Canvas */}
        <div className="relative overflow-hidden">
          {status === 'connecting' && (
            <div className="absolute inset-0 flex items-center justify-center bg-slate-900">
              <div className="text-center">
                <Loader2 size={32} className="animate-spin text-slate-400 mx-auto mb-2" />
                <p className="text-slate-400 text-sm">{statusMessage}</p>
              </div>
            </div>
          )}
          {status === 'success' && (
            <div className="absolute inset-0 flex items-center justify-center bg-slate-900/80 z-10">
              <div className="text-center">
                <CheckCircle2 size={48} className="text-green-400 mx-auto mb-3" />
                <p className="text-green-400 font-medium">Session saved successfully</p>
              </div>
            </div>
          )}
          {status === 'error' && (
            <div className="absolute inset-0 flex items-center justify-center bg-slate-900">
              <div className="text-center">
                <p className="text-red-400 text-sm mb-3">{statusMessage}</p>
                <button
                  onClick={handleClose}
                  className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm text-slate-200 transition-colors"
                >
                  Close
                </button>
              </div>
            </div>
          )}
          <canvas
            ref={canvasRef}
            width={VIEWPORT_WIDTH}
            height={VIEWPORT_HEIGHT}
            tabIndex={0}
            className="block cursor-default outline-none"
            style={{ width: '80vw', maxWidth: `${VIEWPORT_WIDTH}px`, height: 'auto', aspectRatio: `${VIEWPORT_WIDTH}/${VIEWPORT_HEIGHT}` }}
            onMouseDown={(e) => sendMouseEvent(e, 'mousePressed')}
            onMouseUp={(e) => sendMouseEvent(e, 'mouseReleased')}
            onMouseMove={(e) => sendMouseEvent(e, 'mouseMoved')}
            onKeyDown={(e) => sendKeyEvent(e, 'keyDown')}
            onKeyUp={(e) => sendKeyEvent(e, 'keyUp')}
            onWheel={sendScroll}
          />
        </div>
      </div>
    </div>
  )
}
