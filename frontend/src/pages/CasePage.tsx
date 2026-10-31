// One case: header, the workflow diagram with this case's path, the decision timeline and a summary.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, type CaseDetail, type Timeline as TimelineData, type Workflow } from '../api'
import ActionPanel from '../components/ActionPanel'
import CaseSummary from '../components/CaseSummary'
import FlowGraph from '../components/FlowGraph'
import Timeline from '../components/Timeline'
import { fmtDate } from '../format'
import { StatusBadge } from '../ui'
import { useUser } from '../userContext'

const busy = (c?: CaseDetail) => !!c && c.status !== 'CLOSED' && c.waiting_on === null

export default function CasePage() {
  const { ref = '' } = useParams()
  const { user } = useUser()
  const [selectedSeq, setSelectedSeq] = useState<number | null>(null)

  // poll quickly while agents are working on the case; live updates replace this in step 3.5
  const interval = (c?: CaseDetail) => (busy(c) ? 2_000 : 15_000)
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
  const waitingPerson = c.waiting_on === 'maker' ? c.maker : c.waiting_on === 'checker' ? c.checker : null
  const isMe = waitingPerson?.username === user?.username

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
        </div>
      </div>

      <ActionPanel c={c} />

      <div className="card">
        <div className="card-head">
          <h2>Flow</h2>
          <span className="muted small">
            {selectedSeq ? `Highlighting step #${selectedSeq}` : 'Click a step in the timeline to highlight its route'}
          </span>
        </div>
        <FlowGraph workflow={workflow.data} events={timeline.data.events} status={c.status} working={busy(c)}
                   selectedSeq={selectedSeq} />
      </div>

      <div className="case-grid">
        <Timeline events={timeline.data.events} selectedSeq={selectedSeq} onSelect={setSelectedSeq} />
        <CaseSummary c={c} />
      </div>
    </div>
  )
}
