// Small shared components: status and rating badges.
import type { Band, CaseStatus } from './api'
import { RULES, STATUS_LABEL, statusFamily } from './format'

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <span className={`badge ${statusFamily(status)}`}>{STATUS_LABEL[status]}</span>
}

export function BandBadge({ band, final }: { band: Band | null; final?: boolean }) {
  if (!band) return <span className="muted">—</span>
  const cls = band === 'HIGH' ? 'danger' : band === 'MEDIUM' ? 'maker' : band === 'LOW' ? 'ok' : ''
  return <span className={`badge ${cls}`} title={final ? 'Final rating' : 'Proposed rating'}>{band}{final ? ' ✓' : ''}</span>
}

export function RuleChips({ rules, guards }: { rules: string[]; guards: string[] }) {
  return (
    <span className="chips">
      {rules.map((r) => <span key={r} className="chip rule" title={RULES[r] ?? r}>{r}</span>)}
      {guards.map((g) => <span key={g} className="chip guard" title={RULES[g] ?? g}>{g}</span>)}
    </span>
  )
}
