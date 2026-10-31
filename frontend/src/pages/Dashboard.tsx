// Case dashboard: summary tiles that double as filters, then the case table.
// The list is loaded once (up to 500 cases) and filtered in the browser; filters live in the URL.
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type CaseListItem, type User } from '../api'
import { fmtDate, statusFamily, type Family } from '../format'
import { BandBadge, StatusBadge } from '../ui'
import { useUser } from '../userContext'

type View = 'all' | 'mine' | 'agents' | 'due' | 'closed'
type SortKey = 'due' | 'client' | 'status'

const waitingOnMe = (c: CaseListItem, u: User) =>
  (c.waiting_on === 'maker' && c.maker.username === u.username) ||
  (c.waiting_on === 'checker' && c.checker.username === u.username)
const agentsWorking = (c: CaseListItem) => c.status !== 'CLOSED' && c.waiting_on === null
const dueSoon = (c: CaseListItem) => c.status !== 'CLOSED' && (c.overdue || c.business_days_left < 5)
const isMine = (c: CaseListItem, u: User) =>
  c.maker.username === u.username || c.checker.username === u.username || u.role === 'admin'

const FAMILY_ORDER: Family[] = ['maker', 'checker', 'agent', 'neutral', 'ok']

export default function Dashboard() {
  const { user } = useUser()
  const [params, setParams] = useSearchParams()
  const view = (params.get('view') as View) ?? (user?.role === 'admin' ? 'all' : 'mine')
  const q = params.get('q') ?? ''
  const sort = (params.get('sort') as SortKey) ?? 'due'
  const onlyMine = params.get('assigned') === '1'

  const { data, error, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ['cases', user?.username],
    queryFn: () => api<{ total: number; items: CaseListItem[] }>('/cases?limit=500'),
    enabled: !!user,
    // poll fast while agents are moving cases, slowly once everything waits on a person
    refetchInterval: (query) => (query.state.data?.items.some(agentsWorking) ? 3_000 : 30_000),
  })

  const all = useMemo(() => data?.items ?? [], [data])
  const counts = useMemo(() => user && {
    mine: all.filter((c) => waitingOnMe(c, user)).length,
    agents: all.filter(agentsWorking).length,
    due: all.filter(dueSoon).length,
    closed: all.filter((c) => c.status === 'CLOSED').length,
  }, [all, user])

  const rows = useMemo(() => {
    if (!user) return []
    const needle = q.trim().toLowerCase()
    const out = all.filter((c) =>
      (view === 'all' ||
        (view === 'mine' && waitingOnMe(c, user)) ||
        (view === 'agents' && agentsWorking(c)) ||
        (view === 'due' && dueSoon(c)) ||
        (view === 'closed' && c.status === 'CLOSED')) &&
      (!onlyMine || isMine(c, user)) &&
      (!needle || [c.case_ref, c.client.legal_name, c.client.cif].some((s) => s.toLowerCase().includes(needle))))
    const by: Record<SortKey, (a: CaseListItem, b: CaseListItem) => number> = {
      due: (a, b) => a.due_date.localeCompare(b.due_date),
      client: (a, b) => a.client.legal_name.localeCompare(b.client.legal_name),
      status: (a, b) => FAMILY_ORDER.indexOf(statusFamily(a.status)) - FAMILY_ORDER.indexOf(statusFamily(b.status)),
    }
    return out.sort((a, b) => by[sort](a, b) || a.due_date.localeCompare(b.due_date))
  }, [all, user, view, q, sort, onlyMine])

  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value === null || value === '') next.delete(key)
    else next.set(key, value)
    setParams(next, { replace: key === 'q' })
  }

  if (!user || isLoading) return <p className="muted">Loading cases…</p>
  if (error) return <p className="notice error">Could not load cases: {String(error)}</p>

  const tiles: { id: View; label: string; value: number; hint: string; tone: string }[] = [
    { id: 'mine', label: 'Waiting on me', value: counts!.mine, hint: `as ${user.title ?? user.role}`, tone: user.role === 'checker' ? 'checker' : 'maker' },
    { id: 'agents', label: 'Agents working', value: counts!.agents, hint: 'no action needed', tone: 'agent' },
    { id: 'due', label: 'Due within 5 business days', value: counts!.due, hint: 'team lead copied (G6)', tone: 'danger' },
    { id: 'closed', label: 'Closed', value: counts!.closed, hint: 'approved', tone: 'ok' },
  ]

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h1>Renewal cases</h1>
        <span className="muted small" aria-live="polite">
          {all.length} cases · updated {new Date(dataUpdatedAt).toLocaleTimeString()}
        </span>
      </div>

      <div className="tiles" role="tablist" aria-label="Views">
        {tiles.map((t) => (
          <button key={t.id} role="tab" aria-selected={view === t.id}
                  className={`tile tone-${t.tone}${view === t.id ? ' active' : ''}`}
                  onClick={() => set('view', view === t.id ? 'all' : t.id)}>
            <span className="tile-value">{t.value}</span>
            <span className="tile-label">{t.label}</span>
            <span className="tile-hint">{t.hint}</span>
          </button>
        ))}
      </div>

      <div className="card">
        <div className="card-head toolbar">
          <input type="search" placeholder="Search client, case or CIF…" value={q} aria-label="Search"
                 onChange={(e) => set('q', e.target.value)} />
          <label className="row small">
            <input type="checkbox" checked={onlyMine} onChange={(e) => set('assigned', e.target.checked ? '1' : null)} />
            My cases only
          </label>
          <span className="header-spacer" />
          {view !== 'all' && <button onClick={() => set('view', 'all')}>Show all cases</button>}
          <span className="muted small">{rows.length} shown</span>
        </div>
        <div className="table-scroll">
          <table className="cases">
            <thead>
              <tr>
                <th>Case</th>
                <SortTh k="client" sort={sort} set={set}>Client</SortTh>
                <SortTh k="status" sort={sort} set={set}>Status</SortTh>
                <th>Waiting on</th>
                <SortTh k="due" sort={sort} set={set}>Due</SortTh>
                <th>Rating</th>
                <th className="num">Open items</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const mine = waitingOnMe(c, user)
                const person = c.waiting_on === 'maker' ? c.maker : c.waiting_on === 'checker' ? c.checker : null
                return (
                  <tr key={c.case_ref} className={mine ? 'mine' : undefined}>
                    <td>
                      <Link to={`/cases/${c.case_ref}`} className="mono">{c.case_ref}</Link>
                      {c.scenario && <div className="muted small">{c.scenario.replace(/_/g, ' ')}</div>}
                    </td>
                    <td>
                      <div>{c.client.legal_name}</div>
                      <div className="muted small mono">{c.client.cif}</div>
                    </td>
                    <td><StatusBadge status={c.status} /></td>
                    <td>
                      {person ? <span className={mine ? 'you' : undefined}>{mine ? 'You' : person.full_name}</span>
                        : c.status === 'CLOSED' ? <span className="muted">—</span>
                          : <span className="muted">agents</span>}
                    </td>
                    <td>
                      <div>{fmtDate(c.due_date)}</div>
                      {c.status !== 'CLOSED' && (
                        <div className={`small ${c.overdue ? 'text-danger' : c.business_days_left < 5 ? 'text-warn' : 'muted'}`}>
                          {c.overdue ? 'overdue' : `${c.business_days_left} business days`}
                        </div>
                      )}
                    </td>
                    <td><BandBadge band={c.final_rating ?? c.rating} final={!!c.final_rating} /></td>
                    <td className="num">{c.open_items || <span className="muted">0</span>}</td>
                  </tr>
                )
              })}
              {rows.length === 0 && (
                <tr><td colSpan={7} className="empty">
                  {view === 'mine' ? 'Nothing is waiting on you right now.' : 'No cases match these filters.'}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function SortTh({ k, sort, set, children }: { k: SortKey; sort: SortKey; set: (k: string, v: string | null) => void; children: string }) {
  return (
    <th aria-sort={sort === k ? 'ascending' : undefined}>
      <button className="th-sort" onClick={() => set('sort', k === 'due' ? null : k)}>
        {children}{sort === k ? ' ↑' : ''}
      </button>
    </th>
  )
}
