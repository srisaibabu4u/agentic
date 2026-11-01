// Lessons from past cases. The system proposes them when a case closes; a team lead (admin) approves,
// rewords, rejects or retires them. Only approved lessons reach the agents. The API enforces the same rules.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ApiError, post, type Lesson, type LessonStatus } from '../api'
import { AGENT_LABEL, fmtDate } from '../format'
import { useUser } from '../userContext'

const TABS: { id: LessonStatus; label: string; hint: string }[] = [
  { id: 'PROPOSED', label: 'Proposed', hint: 'waiting for a team lead' },
  { id: 'APPROVED', label: 'In use', hint: 'given to the agents' },
  { id: 'REJECTED', label: 'Rejected', hint: 'never used' },
  { id: 'RETIRED', label: 'Retired', hint: 'no longer used' },
]

export default function LessonsPage() {
  const { user } = useUser()
  const [params, setParams] = useSearchParams()
  const tab = (params.get('status') as LessonStatus) ?? 'PROPOSED'
  const { data, error, isLoading } = useQuery({ queryKey: ['lessons'], queryFn: () => api<Lesson[]>('/lessons') })
  const lead = user?.role === 'admin'

  if (isLoading) return <p className="muted">Loading lessons…</p>
  if (error) return <p className="notice error">Could not load lessons: {String(error)}</p>
  const rows = (data ?? []).filter((x) => x.status === tab)

  return (
    <div className="stack">
      <div>
        <h1>Lessons from past cases</h1>
        <p className="muted small">
          When a case closes, review findings the agents had to fix and checker returns become proposed lessons.
          {' '}Approved lessons are given to the drafting and review agents for the questions they concern. They are
          advice only: they cannot change policy, the guardrails or the result checks.
          {!lead && ' Only a team lead can review them.'}
        </p>
      </div>
      <div className="tiles" role="tablist" aria-label="Lesson status">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id}
                  className={`tile tone-${t.id === 'APPROVED' ? 'ok' : t.id === 'PROPOSED' ? 'maker' : 'neutral'}${tab === t.id ? ' active' : ''}`}
                  onClick={() => setParams({ status: t.id })}>
            <span className="tile-value">{(data ?? []).filter((x) => x.status === t.id).length}</span>
            <span className="tile-label">{t.label}</span>
            <span className="tile-hint">{t.hint}</span>
          </button>
        ))}
      </div>
      {rows.length === 0
        ? <div className="card empty">No {TABS.find((t) => t.id === tab)!.label.toLowerCase()} lessons.</div>
        : rows.map((x) => <LessonCard key={x.id} x={x} lead={lead} />)}
    </div>
  )
}

function LessonCard({ x, lead }: { x: Lesson; lead: boolean }) {
  const qc = useQueryClient()
  const [text, setText] = useState(x.text)
  const [note, setNote] = useState('')
  const act = useMutation({
    mutationFn: (action: 'approve' | 'reject' | 'retire') =>
      post<Lesson>(`/lessons/${x.id}/${action}`, action === 'approve' ? { text: text !== x.text ? text : null, note: note || null } : { note }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['lessons'] }),
  })
  const err = act.error instanceof ApiError ? act.error : null

  return (
    <div className="card">
      <div className="card-head">
        <strong>{x.id}</strong>
        <span className="chip rule">{x.q_id}</span>
        {x.policy_ref && <span className="chip guard">policy {x.policy_ref}</span>}
        <span className="small muted">{x.kind === 'CHECKER_RETURN' ? 'from a checker return' : 'from a review finding'}</span>
        <span className="header-spacer" />
        <span className="small muted">for {x.agents.map((a) => AGENT_LABEL[a] ?? a).join(' and ')}</span>
      </div>
      <div className="card-body stack">
        {lead && x.status === 'PROPOSED'
          ? <label className="stack small">
              <span>Lesson as the agents will read it. Reword it as a general rule if it names one client.</span>
              <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
            </label>
          : <p>{x.text}</p>}
        <div className="small">
          <strong>Seen in {x.occurrences} case{x.occurrences === 1 ? '' : 's'}:</strong>
          <ul className="plain">
            {x.evidence.map((e, i) => (
              <li key={i}><Link to={`/cases/${e.case_ref}`}>{e.case_ref}</Link> · {e.source}{e.detail ? `: ${e.detail}` : ''}</li>
            ))}
          </ul>
        </div>
        <div className="small muted">
          Proposed {fmtDate(x.proposed_at)}
          {x.reviewed_by && <> · {x.status.toLowerCase()} by {x.reviewed_by} on {fmtDate(x.reviewed_at!)}{x.review_note ? `: ${x.review_note}` : ''}</>}
          {x.status !== 'PROPOSED' && <> · given to agents {x.times_used} time{x.times_used === 1 ? '' : 's'}</>}
        </div>
        {lead && (x.status === 'PROPOSED' || x.status === 'APPROVED') && (
          <div className="stack">
            {err && <div className="notice error small" role="alert">{err.details.join('; ')}</div>}
            <input placeholder={x.status === 'PROPOSED' ? 'Note (required to reject)' : 'Why retire it (required)'}
                   aria-label="Review note" value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="row">
              {x.status === 'PROPOSED' && <>
                <button className="primary" disabled={act.isPending || text.trim().length < 10} onClick={() => act.mutate('approve')}>Approve</button>
                <button className="danger" disabled={act.isPending || !note.trim()} onClick={() => act.mutate('reject')}>Reject</button>
              </>}
              {x.status === 'APPROVED' &&
                <button className="danger" disabled={act.isPending || !note.trim()} onClick={() => act.mutate('retire')}>Retire</button>}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
