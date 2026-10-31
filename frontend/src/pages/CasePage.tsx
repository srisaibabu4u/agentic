// One case: header, the workflow diagram with this case's path (live, or replayed step by step),
// the decision timeline and a summary.
import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, type CaseDetail, type Timeline as TimelineData, type Workflow } from '../api'
import ActionPanel from '../components/ActionPanel'
import CaseSummary from '../components/CaseSummary'
import FlowGraph from '../components/FlowGraph'
import Replay from '../components/Replay'
import Timeline from '../components/Timeline'
import { fmtDate } from '../format'
import { StatusBadge } from '../ui'
import { useCaseStream } from '../useCaseStream'
import { useUser } from '../userContext'

const busy = (c?: CaseDetail) => !!c && c.status !== 'CLOSED' && c.waiting_on === null

export default function CasePage() {
  const { ref = '' } = useParams()
  const { user } = useUser()
  const [selectedSeq, setSelectedSeq] = useState<number | null>(null)
  const [replaying, setReplaying] = useState(false)
  const [replaySeq, setReplaySeq] = useState<number | null>(null)

  // live updates: each change the worker makes refetches the case; poll only while the stream is down
  const qc = useQueryClient()
  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['case', ref] })
    qc.invalidateQueries({ queryKey: ['timeline', ref] })
  }, [qc, ref])
  const live = useCaseStream(ref, user?.username, refresh)
  const interval = (c?: CaseDetail) => (live ? false : busy(c) ? 2_000 : 15_000)

  const detail = useQuery({
    queryKey: ['case', ref, user?.username], enabled: !!user,
    queryFn: () => api<CaseDetail>(`/cases/${ref}`),
    refetchInterval: (q) => interval(q.state.data),
  })
  const timeline = useQuery({
    queryKey: ['timeline', ref, user?.username], enabled: !!user,
    queryFn: () => api<TimelineData>(`/cases/${ref}/timeline`),
    refetchInterval: interval(detail.data),
  })
  const workflow = useQuery({ queryKey: ['workflow'], enabled: !!user, staleTime: Infinity,
    queryFn: () => api<Workflow>('/workflow') })

  const err = detail.error ?? timeline.error ?? workflow.error
  if (err) return <p className="notice error">{String(err)}</p>
  if (!detail.data || !timeline.data || !workflow.data) return <p className="muted">Loading case…</p>
  const c = detail.data
  const events = timeline.data.events
  const waitingPerson = c.waiting_on === 'maker' ? c.maker : c.waiting_on === 'checker' ? c.checker : null
  const isMe = waitingPerson?.username === user?.username

  // in replay, the diagram shows the case as it was after the chosen step
  const shownSeq = replaying ? replaySeq : null
  const statusAt = shownSeq === null ? c.status
    : events.filter((e) => e.decision && e.seq <= shownSeq).at(-1)?.decision?.new_status ?? 'NEW'
  const toggleReplay = () => {
    setReplaySeq(replaying ? null : events[0]?.seq ?? null)
    setReplaying(!replaying)
  }

  return (
    <div className="stack">
      <Link to="/" className="small">← All cases</Link>
      <div className="case-head">
        <div>
          <h1>{c.client.legal_name}</h1>
          <div className="row small muted">
            <span className="mono">{c.case_ref}</span>·<span>{c.client.segment}</span>·
            <span>due {fmtDate(c.due_date)}{c.status !== 'CLOSED' && ` (${c.business_days_left} business days)`}</span>·
            <span>maker {c.maker.full_name}</span>·<span>checker {c.checker.full_name}</span>
          </div>
        </div>
        <span className="header-spacer" />
        <div className="row">
          <StatusBadge status={c.status} />
          {waitingPerson ? (
            <span className={`badge ${c.waiting_on}`}>{isMe ? 'Waiting on you' : `Waiting on ${waitingPerson.full_name}`}</span>
          ) : busy(c) ? <span className="badge agent pulse">Agents working</span> : null}
          {c.status !== 'CLOSED' && (
            <span className={`badge ${live ? 'ok' : ''}`}
                  title={live ? 'Updates arrive as the worker changes the case' : 'Live stream unavailable; refreshing periodically'}>
              {live ? '● Live' : '○ Polling'}
            </span>
          )}
        </div>
      </div>

      <ActionPanel c={c} />

      <div className="card">
        <div className="card-head">
          <h2>Flow</h2>
          <span className="muted small">
            {replaying ? 'Replay: the diagram shows the case as it was after the chosen step'
              : selectedSeq ? `Highlighting step #${selectedSeq}` : 'Click a step in the timeline to highlight its route'}
          </span>
          <span className="header-spacer" />
          <button aria-pressed={replaying} onClick={toggleReplay}>{replaying ? 'Close replay' : '⟲ Replay'}</button>
        </div>
        {replaying && <Replay events={events} seq={replaySeq} onSeq={setReplaySeq} />}
        <FlowGraph workflow={workflow.data} events={events} status={statusAt} working={!replaying && busy(c)}
                   selectedSeq={shownSeq ?? selectedSeq} uptoSeq={shownSeq ?? undefined} />
      </div>

      <div className="case-grid">
        <Timeline events={events} selectedSeq={shownSeq ?? selectedSeq} replaySeq={shownSeq}
                  onSelect={(s) => (replaying ? setReplaySeq(s ?? null) : setSelectedSeq(s))} />
        <CaseSummary c={c} />
      </div>
    </div>
  )
}
