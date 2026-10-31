// Replay a case step by step: a slider over its events, play/pause, and what the master saw at that step
// (the state snapshot stored with every decision). seq === null means "live" (the current state).
import { useEffect, useState } from 'react'
import type { TimelineEvent } from '../api'
import { fmtTime, STATUS_LABEL } from '../format'
import { RuleChips } from '../ui'

interface Props {
  events: TimelineEvent[]
  seq: number | null
  onSeq: (seq: number | null) => void
}

type Snapshot = {
  status?: string; docs?: string[]; missing?: string[]; fields?: number; open_items?: Record<string, string>
  answers?: string[]; findings?: string[]; maker_submitted?: boolean; pending_tasks?: string[]
}

export default function Replay({ events, seq, onSeq }: Props) {
  const [playing, setPlaying] = useState(false)
  const last = events.length - 1
  const idx = seq === null ? last : Math.max(0, events.findIndex((e) => e.seq === seq))
  const go = (i: number) => onSeq(i >= last ? null : events[Math.max(0, i)].seq)

  // advance one step every 0.9 s; stop at the end
  useEffect(() => {
    if (!playing) return
    const t = setTimeout(() => (idx >= last ? setPlaying(false) : go(idx + 1)), 900)
    return () => clearTimeout(t)
  })

  const ev = events[idx]
  const d = ev?.decision
  const s = (d?.state ?? {}) as Snapshot
  if (!ev) return null
  return (
    <div className="replay">
      <div className="replay-bar">
        <button aria-label="First step" onClick={() => { setPlaying(false); go(0) }} disabled={idx === 0}>⏮</button>
        <button aria-label="Previous step" onClick={() => { setPlaying(false); go(idx - 1) }} disabled={idx === 0}>◀</button>
        <button aria-label={playing ? 'Pause' : 'Play'} className="primary"
                onClick={() => { if (!playing && idx >= last) go(0); setPlaying(!playing) }}>{playing ? '❚❚' : '▶'}</button>
        <button aria-label="Next step" onClick={() => { setPlaying(false); go(idx + 1) }} disabled={idx >= last}>▶|</button>
        <button aria-label="Back to live" onClick={() => { setPlaying(false); onSeq(null) }} disabled={seq === null}>⏭ Live</button>
        <input type="range" min={0} max={last} value={idx} aria-label="Replay step" aria-valuetext={`Step ${idx + 1} of ${last + 1}`}
               onChange={(e) => { setPlaying(false); go(Number(e.target.value)) }} />
        <span className="small nowrap">
          {seq === null ? <strong>Live</strong> : <>Step <strong>{idx + 1}</strong> of {last + 1}</>}
          <span className="muted"> · {fmtTime(ev.occurred_at)}</span>
        </span>
      </div>
      {seq !== null && d && (
        <div className="replay-state small">
          <div className="row">
            <strong>#{ev.seq}</strong>
            {d.prev_status !== d.new_status
              ? <span>{STATUS_LABEL[d.prev_status]} → <strong>{STATUS_LABEL[d.new_status]}</strong></span>
              : <span className="muted">stays {STATUS_LABEL[d.new_status]}</span>}
            <RuleChips rules={d.rules_matched} guards={d.guardrails_triggered} />
          </div>
          <div className="muted">What the master saw when it decided:</div>
          <dl className="kv">
            <dt>Documents</dt><dd>{s.docs?.length ? s.docs.join(', ') : '—'}{s.missing?.length ? <span className="warn"> · missing {s.missing.join(', ')}</span> : null}</dd>
            <dt>Fields</dt><dd>{s.fields ?? 0} extracted</dd>
            <dt>Open items</dt><dd>{s.open_items && Object.keys(s.open_items).length ? Object.keys(s.open_items).join(', ') : 'none'}</dd>
            <dt>Answers</dt><dd>{s.answers?.length ? `${s.answers.length} drafted` : 'none'}{s.findings?.length ? <span className="warn"> · findings {s.findings.join(', ')}</span> : null}</dd>
            <dt>Maker submitted</dt><dd>{s.maker_submitted ? 'yes' : 'no'}</dd>
            <dt>Tasks in flight</dt><dd>{s.pending_tasks?.length ? s.pending_tasks.map((t) => t.split('-').at(-1)).join(', ') : 'none'}</dd>
          </dl>
        </div>
      )}
    </div>
  )
}
