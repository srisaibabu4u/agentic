// Live updates for one case from GET /cases/{ref}/stream (server-sent events), read with fetch() so a
// lost session (401) and reconnects are under our control; the session cookie goes with it. Each message calls
// onChange; while connected the page stops polling. Reconnects with backoff; stops once the case closes.
import { useEffect, useRef, useState } from 'react'
import { SIGNED_OUT } from './api'

export interface StreamMessage {
  case_ref: string; status: string; waiting_on: string | null; version: number
  last_seq: number | null; undecided_events: number; running_tasks: number
}

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => { const t = setTimeout(resolve, ms); signal.addEventListener('abort', () => { clearTimeout(t); resolve() }) })

export function useCaseStream(ref: string, username: string | undefined, onChange: (m: StreamMessage) => void): boolean {
  const [live, setLive] = useState(false)
  const handler = useRef(onChange)
  useEffect(() => { handler.current = onChange }, [onChange])

  useEffect(() => {
    if (!username) return
    const ctrl = new AbortController()
    ;(async () => {
      let retry = 1_000
      let closed = false
      while (!ctrl.signal.aborted && !closed) {
        try {
          const res = await fetch(`/api/cases/${ref}/stream`, { signal: ctrl.signal })
          if (res.status === 401) { window.dispatchEvent(new Event(SIGNED_OUT)); return }
          if (!res.ok || !res.body) throw new Error(`stream ${res.status}`)
          setLive(true)
          retry = 1_000
          const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
          let buf = ''
          for (;;) {
            const { value, done } = await reader.read()
            if (done) break
            buf += value
            let end
            while ((end = buf.indexOf('\n\n')) >= 0) { // one SSE block per blank-line-terminated chunk
              const data = buf.slice(0, end).split('\n').filter((l) => l.startsWith('data: ')).map((l) => l.slice(6)).join('\n')
              buf = buf.slice(end + 2)
              if (!data) continue
              const msg = JSON.parse(data) as StreamMessage
              if (msg.status === 'CLOSED') closed = true
              handler.current(msg)
            }
          }
        } catch {
          if (ctrl.signal.aborted) return
        }
        setLive(false)
        if (!closed) {
          await sleep(retry, ctrl.signal)
          retry = Math.min(retry * 2, 15_000)
        }
      }
    })()
    return () => ctrl.abort()
  }, [ref, username])

  return live
}
