// The checker's review: every answer with its score and evidence, the maker's inputs to check
// (four-eyes), then either return selected questions with comments or approve with a final rating.
import { useState } from 'react'
import type { Band, CaseDetail } from '../api'
import { BandBadge } from '../ui'

interface Props {
  c: CaseDetail
  busy: boolean
  onOpenDoc: (id: number, page?: number | null) => void
  onReturn: (returned: { q_id: string; comment: string }[]) => void
  onApprove: (final_rating: Band, comment: string) => void
}

export default function CheckerReview({ c, busy, onOpenDoc, onReturn, onApprove }: Props) {
  const fields = Object.fromEntries(c.fields.map((f) => [f.key, f]))
  const proposed = c.rating?.band && c.rating.band !== 'PROVISIONAL' ? c.rating.band : 'MEDIUM'
  const [returns, setReturns] = useState<Record<string, string>>({})
  const [rating, setRating] = useState<Band>(proposed)
  const [comment, setComment] = useState('')
  const [confirming, setConfirming] = useState(false)

  const selected = Object.entries(returns)
  const missingComment = selected.some(([, t]) => !t.trim())
  const overrideNeedsWhy = rating !== proposed && !comment.trim()
  const confirmed = c.fields.filter((f) => f.confirmed_by)
  const uploads = c.documents.filter((d) => d.uploaded_by)

  return (
    <div className="workbench">
      {(confirmed.length > 0 || c.screening.dispositions.length > 0 || uploads.length > 0) && (
        <section>
          <h3>Maker’s inputs to check <span className="chip guard" title="Four-eyes: the checker verifies what the maker did">G5</span></h3>
          <ul className="plain small">
            {confirmed.map((f) => (
              <li key={f.key}><code>{f.key}</code> set to <strong>{typeof f.value === 'object' ? JSON.stringify(f.value) : String(f.value)}</strong> by {f.confirmed_by!.full_name}: “{f.confirm_note}”</li>
            ))}
            {c.screening.dispositions.map((d) => (
              <li key={d.ref}><code>{d.ref}</code> <span className="badge">{d.outcome.replace('_', ' ').toLowerCase()}</span> by {d.decided_by.full_name}: “{d.rationale}”</li>
            ))}
            {uploads.map((d) => (
              <li key={d.id}>Uploaded <button className="link" onClick={() => onOpenDoc(d.id)}>{d.doc_ref} {d.type}</button> by {d.uploaded_by!.full_name}</li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3>Answers</h3>
        <ol className="review-answers">
          {c.answers.map((a) => {
            const ret = a.q_id in returns
            return (
              <li key={a.q_id} className={ret ? 'returning' : undefined}>
                <div className="row">
                  <strong>{a.q_id}</strong> <span className="muted">{a.question}</span>
                  <span className="header-spacer" />
                  <span className="score" title={a.score_reason ?? ''}>{a.score ?? '–'}</span>
                </div>
                <p>{a.answer}</p>
                <div className="row small">
                  <span className="muted">{a.score_reason}</span>
                  {/* one chip per source: several cited fields often come from the same page */}
                  {[...new Map(a.citations.map((k) => {
                    const f = fields[k]
                    const label = f?.source ?? (k.startsWith('maker.') ? 'maker input' : k)
                    return [label, { label, f, k }] as const
                  })).values()].map(({ label, f, k }) =>
                    f?.document_id
                      ? <button key={label} className="chip cite" title={`${k}: ${JSON.stringify(f.value)}`} onClick={() => onOpenDoc(f.document_id!, f.page)}>{label}</button>
                      : <span key={label} className="chip cite" title={f ? JSON.stringify(f.value) : k}>{label}</span>)}
                  <span className="header-spacer" />
                  <label className="row"><input type="checkbox" checked={ret}
                    onChange={(e) => setReturns((r) => { const n = { ...r }; if (e.target.checked) n[a.q_id] = ''; else delete n[a.q_id]; return n })} /> Return</label>
                </div>
                {ret && <textarea rows={2} autoFocus placeholder={`What should the maker do about ${a.q_id}?`}
                                  value={returns[a.q_id]} onChange={(e) => setReturns((r) => ({ ...r, [a.q_id]: e.target.value }))} />}
              </li>
            )
          })}
        </ol>
      </section>

      <div className="wb-footer">
        {selected.length > 0 ? (
          <div className="row">
            <span className="small">Return {selected.map(([q]) => q).join(', ')} to {c.maker.full_name}. The drafting agent prepares a suggested redraft (R8).</span>
            <span className="header-spacer" />
            <button className="primary" disabled={busy || missingComment} onClick={() => onReturn(selected.map(([q_id, t]) => ({ q_id, comment: t.trim() })))}>
              {busy ? 'Sending…' : `Return ${selected.length} question${selected.length > 1 ? 's' : ''}`}
            </button>
          </div>
        ) : (
          <div className="stack">
            <div className="row small">
              Proposed <BandBadge band={c.rating?.band ?? null} /> score {c.rating?.total}/{c.rating?.max}
              <span className="muted">({c.rating?.working})</span>
            </div>
            <div className="row">
              <label className="row small">Final rating
                <select value={rating} onChange={(e) => { setRating(e.target.value as Band); setConfirming(false) }}>
                  {(['LOW', 'MEDIUM', 'HIGH'] as Band[]).map((b) => <option key={b}>{b}</option>)}
                </select>
              </label>
              <input className="grow" placeholder="Comment for the audit pack (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
              {!confirming
                ? <button className="primary" disabled={busy || overrideNeedsWhy} onClick={() => setConfirming(true)}>Approve…</button>
                : <>
                    <button className="primary" disabled={busy} onClick={() => onApprove(rating, comment.trim())}>
                      {busy ? 'Approving…' : `Confirm: approve as ${rating}`}
                    </button>
                    <button onClick={() => setConfirming(false)}>Cancel</button>
                  </>}
            </div>
            {overrideNeedsWhy && <p className="small warn">You are overriding the proposed {proposed} rating: say why in the comment.</p>}
          </div>
        )}
      </div>
    </div>
  )
}
