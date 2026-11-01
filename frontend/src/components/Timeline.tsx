// Every event on the case, oldest first, with the master's decision on it: rules, guardrails,
// rationale and the tasks it dispatched. WAIT decisions (nothing changes) are folded away by default.
import { useEffect, useRef, useState } from 'react'
import type { Task, TimelineEvent } from '../api'
import { AGENT_LABEL, fmtTime, STATUS_LABEL } from '../format'
import { DecidedBy, RuleChips } from '../ui'

function title(e: TimelineEvent): string {
  const who = e.actor?.full_name
  const p = (e.payload ?? {}) as Record<string, unknown>
  switch (e.type) {
    case 'RENEWAL_DUE': return 'Renewal due'
    case 'AGENT_RESULT': return `${AGENT_LABEL[e.source] ?? e.source} ${e.task?.status === 'FAILED' ? 'failed' : 'finished'}`
    case 'MAKER_SUBMIT': {
      const parts = [
        [(p.uploads as unknown[] | undefined)?.length, 'upload'],
        [Object.keys((p.field_edits as object | undefined) ?? {}).length, 'confirmation'],
        [Object.keys((p.dispositions as object | undefined) ?? {}).length, 'disposition'],
      ].filter(([n]) => n).map(([n, w]) => `${n} ${w}${n === 1 ? '' : 's'}`)
      return `${who ?? 'Maker'} submitted${parts.length ? `: ${parts.join(', ')}` : ''}`
    }
    case 'CHECKER_RETURN':
      return `${who ?? 'Checker'} returned ${((p.returned as { q_id: string }[] | undefined) ?? []).map((r) => r.q_id).join(', ')}`
    case 'CHECKER_APPROVE': return `${who ?? 'Checker'} approved: final rating ${p.final_rating}`
    default: return e.type
  }
}

const ICON: Record<string, string> = {
  RENEWAL_DUE: '⏰', AGENT_RESULT: '⚙', MAKER_SUBMIT: '✎', CHECKER_RETURN: '↩', CHECKER_APPROVE: '✓',
}

function TaskLine({ t }: { t: Task }) {
  const scope = t.scope as { questions?: string[]; docs?: string[]; template?: string; mode?: string }
  const what = scope.template ? scope.template.toLowerCase().replace(/_/g, ' ')
    : scope.docs ? scope.docs.join(', ')
      : scope.questions ? `${scope.mode ?? ''} ${scope.questions.length > 3 ? `${scope.questions[0]}–${scope.questions.at(-1)}` : scope.questions.join(', ')}`.trim()
        : ''
  return (
    <li>
      <span className="arrow">→</span> {AGENT_LABEL[t.agent] ?? t.agent}
      {what && <span className="muted"> · {what}</span>}
      {t.depends_on.length > 0 && <span className="muted"> · after {t.depends_on.map((d) => d.split('-').at(-1)).join(', ')}</span>}
      <span className="mono muted small"> {t.task_ref.split('-').at(-1)}</span>
    </li>
  )
}

interface Props {
  events: TimelineEvent[]
  selectedSeq: number | null
  /** during replay: the step shown; later events are dimmed */
  replaySeq?: number | null
  onSelect: (seq: number | null) => void
}

export default function Timeline({ events, selectedSeq, replaySeq = null, onSelect }: Props) {
  const [showWaits, setShowWaits] = useState(false)
  const waits = events.filter((e) => e.decision?.decision === 'WAIT').length
  // a folded WAIT still shows when it is the selected (or replayed) step
  const shown = events.filter((e) => showWaits || e.decision?.decision !== 'WAIT' || e.seq === selectedSeq)
  const list = useRef<HTMLOListElement>(null)
  useEffect(() => { // keep the replayed step in view inside the scrolling list
    if (replaySeq !== null) list.current?.querySelector('.tl-item.selected')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [replaySeq])

  return (
    <div className="card">
      <div className="card-head">
        <h2>Timeline</h2>
        <span className="muted small">{events.length} events</span>
        <span className="header-spacer" />
        {waits > 0 && (
          <label className="row small">
            <input type="checkbox" checked={showWaits} onChange={(e) => setShowWaits(e.target.checked)} />
            Show {waits} wait{waits === 1 ? '' : 's'}
          </label>
        )}
      </div>
      <ol className="timeline" ref={list}>
        {shown.map((e) => {
          const d = e.decision
          const changed = d && d.prev_status !== d.new_status
          const future = replaySeq !== null && e.seq > replaySeq
          return (
            <li key={e.seq} className={`tl-item${selectedSeq === e.seq ? ' selected' : ''}${d?.decision === 'WAIT' ? ' wait' : ''}${future ? ' future' : ''}`}>
              <button className="tl-hit" onClick={() => onSelect(selectedSeq === e.seq ? null : e.seq)}
                      aria-pressed={selectedSeq === e.seq} aria-label={`Step ${e.seq}: ${title(e)}`} />
              <div className={`tl-icon src-${e.type === 'AGENT_RESULT' ? 'agent' : e.source}`} aria-hidden>{ICON[e.type] ?? '•'}</div>
              <div className="tl-body">
                <div className="tl-title">
                  <span className="muted mono small">#{e.seq}</span> <strong>{title(e)}</strong>
                  <span className="muted small tl-time">{fmtTime(e.occurred_at)}</span>
                </div>
                {!e.processed && <div className="small muted">Queued for the master…</div>}
                {d && (
                  <div className="tl-decision">
                    <div className="row">
                      <span className={`badge ${d.decision === 'ESCALATE' ? 'danger' : d.decision === 'CLOSE' ? 'ok' : d.decision === 'WAIT' ? '' : 'agent'}`}>
                        {d.decision}
                      </span>
                      {changed && <span className="small">{STATUS_LABEL[d.prev_status]} <span className="arrow">→</span> <strong>{STATUS_LABEL[d.new_status]}</strong></span>}
                      <RuleChips rules={d.rules_matched} guards={d.guardrails_triggered} />
                      <DecidedBy d={d} />
                    </div>
                    {d.decision !== 'WAIT' && <p className="tl-why">{d.rationale}</p>}
                    {d.tasks.length > 0 && <ul className="tl-tasks small">{d.tasks.map((t) => <TaskLine key={t.task_ref} t={t} />)}</ul>}
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
