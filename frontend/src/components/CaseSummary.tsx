// Right-hand panel of the case page: the client and its ownership, rating, open items, documents, answers.
import type { ReactNode } from 'react'
import type { CaseDetail } from '../api'
import { fmtDate } from '../format'
import { BandBadge } from '../ui'

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="sum-section">
      <div className="row"><h3>{title}</h3><span className="header-spacer" />{aside}</div>
      {children}
    </section>
  )
}

export default function CaseSummary({ c }: { c: CaseDetail }) {
  const owners = c.client.parties.filter((p) => p.shareholder)
  const directors = c.client.parties.filter((p) => p.director)
  const behind = (id: number) => c.client.parties.filter((p) => p.parent_id === id)
  const selected = c.documents.filter((d) => d.selected)
  const openItems = Object.entries(c.open_items)

  return (
    <div className="card summary">
      <Section title="Client">
        <dl className="kv">
          <dt>UEN</dt><dd className="mono">{c.client.uen}</dd>
          <dt>CIF</dt><dd className="mono">{c.client.cif}</dd>
          <dt>Activity</dt><dd>{c.client.principal_activity} {c.client.ssic_code && <span className="muted">(SSIC {c.client.ssic_code})</span>}</dd>
          <dt>Prior rating</dt><dd>{c.client.prior_rating ?? '—'} <span className="muted">· {fmtDate(c.client.prior_assessment_date)}</span></dd>
          <dt>Directors</dt><dd>{directors.map((d) => `${d.name} (${d.nationality})`).join(', ')}</dd>
        </dl>
      </Section>

      <Section title="Ownership">
        <ul className="owners">
          {owners.map((p) => (
            <li key={p.id}>
              <span className="pct">{p.ownership_pct != null ? `${Number(p.ownership_pct)}%` : '?'}</span>
              {p.name} <span className="muted small">{p.type === 'ENTITY' ? 'company' : 'person'} · {p.nationality}</span>
              {behind(p.id).map((u) => (
                <div key={u.id} className="owner-behind small">
                  ↳ {u.name} owns {u.ownership_pct != null ? `${Number(u.ownership_pct)}%` : '?'} <span className="muted">· {u.nationality}</span>
                </div>
              ))}
            </li>
          ))}
        </ul>
        <p className="muted small">On the client record. The agents work from the documents, which may differ.</p>
      </Section>

      <Section title="Rating" aside={<BandBadge band={c.final?.rating ?? c.rating?.band ?? null} final={!!c.final} />}>
        {c.rating ? (
          <p className="small">
            Score <strong>{c.rating.total}</strong> of {c.rating.max} <span className="muted mono">({c.rating.working})</span>
            {c.final?.approved_by && <><br />Approved by {c.final.approved_by.full_name}{c.final.comment && <>: “{c.final.comment}”</>}</>}
          </p>
        ) : <p className="muted small">Not drafted yet.</p>}
      </Section>

      <Section title="Open items" aside={<span className={`badge ${openItems.length ? 'maker' : 'ok'}`}>{openItems.length}</span>}>
        {openItems.length ? (
          <ul className="plain small">{openItems.map(([k, why]) => <li key={k}><code>{k}</code> {why}</li>)}</ul>
        ) : <p className="muted small">Nothing needs a person right now.</p>}
        {c.findings.length > 0 && (
          <ul className="plain small findings">
            {c.findings.map((f, i) => (
              <li key={i}><span className={`badge ${f.severity === 'HIGH' ? 'danger' : 'maker'}`}>{f.severity}</span> {f.q_id}: {f.issue}</li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Documents" aside={<span className="muted small">{selected.length} in use</span>}>
        <ul className="plain small docs">
          {selected.map((d) => (
            <li key={d.id}>
              <span className="mono muted">{d.doc_ref}</span> {d.type}
              <span className="muted"> · {fmtDate(d.date)}</span>
              {d.fresh === false && <span className="badge maker">stale</span>}
              {d.uploaded_by && <span className="badge checker">uploaded</span>}
            </li>
          ))}
          {c.missing_documents.filter((m) => !selected.some((d) => d.item === m.checklist_item && d.fresh)).map((m) => (
            <li key={m.checklist_item} className="missing">
              <span className={`badge ${m.identity_critical ? 'danger' : 'maker'}`}>missing</span> {m.label}
              <div className="muted">{m.reason}</div>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Answers" aside={<span className="muted small">{c.answers.length} of 8</span>}>
        {c.answers.length ? (
          <ol className="answers small">
            {c.answers.map((a) => (
              <li key={a.q_id}>
                <div className="row">
                  <strong>{a.q_id}</strong> <span className="muted">{a.question}</span>
                  <span className="header-spacer" />
                  <span className="score" title={a.score_reason ?? ''}>{a.score ?? '–'}</span>
                </div>
                <div>{a.answer}</div>
                {a.flags.length > 0 && <div className="chips">{a.flags.map((f) => <span key={f} className="chip guard">{f}</span>)}</div>}
              </li>
            ))}
          </ol>
        ) : <p className="muted small">Not drafted yet.</p>}
      </Section>
    </div>
  )
}
