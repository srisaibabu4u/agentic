// Small shared components: status and rating badges.
import type { Band, CaseStatus, Decision } from './api'
import { RULES, STATUS_LABEL, statusFamily } from './format'

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <span className={`badge ${statusFamily(status)}`}>{STATUS_LABEL[status]}</span>
}

export function BandBadge({ band, final }: { band: Band | null; final?: boolean }) {
  if (!band) return <span className="muted">—</span>
  const cls = band === 'HIGH' ? 'danger' : band === 'MEDIUM' ? 'maker' : band === 'LOW' ? 'ok' : ''
  return <span className={`badge ${cls}`} title={final ? 'Final rating' : 'Proposed rating'}>{band}{final ? ' ✓' : ''}</span>
}

/** Who made a master decision. Nothing is shown for the rule playbook, the default. */
export function DecidedBy({ d }: { d: Decision }) {
  if (d.decided_by === 'master_agent') {
    const own = d.agrees_with_playbook === false
    const title = own && d.playbook
      ? `The master agent used its own judgement. The playbook would have chosen ${d.playbook.decision} → ${d.playbook.new_status} (${d.playbook.rules_matched.join(', ')}).`
      : 'Decided by the master agent; the same as the playbook.'
    return <span className={`chip ${own ? 'guard' : 'rule'}`} title={title}>{own ? 'agent: own judgement' : 'agent'}</span>
  }
  if (d.decided_by === 'playbook_fallback') {
    return <span className="chip guard" title="The master agent gave no decision the guardrails allow, so the playbook decided.">playbook fallback</span>
  }
  return null
}

export function RuleChips({ rules, guards }: { rules: string[]; guards: string[] }) {
  return (
    <span className="chips">
      {rules.map((r) => <span key={r} className="chip rule" title={RULES[r] ?? r}>{r}</span>)}
      {guards.map((g) => <span key={g} className="chip guard" title={RULES[g] ?? g}>{g}</span>)}
    </span>
  )
}
