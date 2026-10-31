// The maker's form, built from the case's open items: confirm low-confidence values (G1), disposition
// screening hits (G2), upload missing or requested documents, then submit as one MAKER_SUBMIT event.
import { useState } from 'react'
import type { CaseDetail, Field, MakerSubmitBody, UploadBody } from '../api'
import { fmtDate } from '../format'

type Json = string | number | boolean | null | Json[] | { [k: string]: Json }

/** Inputs shaped like the value: one per key for an object, a single input otherwise. */
function ValueEditor({ value, onChange }: { value: Json; onChange: (v: Json) => void }) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return (
      <div className="value-editor">
        {Object.entries(value).map(([k, v]) => (
          <label key={k}>
            <span className="muted small">{k}</span>
            <input type={typeof v === 'number' ? 'number' : 'text'} value={String(v ?? '')}
                   onChange={(e) => onChange({ ...value, [k]: typeof v === 'number' ? Number(e.target.value) : e.target.value })} />
          </label>
        ))}
      </div>
    )
  }
  if (Array.isArray(value)) {
    return <textarea rows={2} value={JSON.stringify(value)} onChange={(e) => { try { onChange(JSON.parse(e.target.value)) } catch { /* keep typing */ } }} />
  }
  return <input type={typeof value === 'number' ? 'number' : 'text'} value={String(value ?? '')}
                onChange={(e) => onChange(typeof value === 'number' ? Number(e.target.value) : e.target.value)} />
}

function show(v: unknown) {
  if (v && typeof v === 'object') return Object.entries(v).map(([k, x]) => `${k}: ${x}`).join(', ')
  return String(v)
}

interface UploadDraft extends UploadBody { id: number; fileName?: string }

interface Props {
  c: CaseDetail
  busy: boolean
  onOpenDoc: (id: number, page?: number | null) => void
  onSubmit: (body: MakerSubmitBody) => void
}

export default function MakerWorkbench({ c, busy, onOpenDoc, onSubmit }: Props) {
  const fieldsByKey = Object.fromEntries(c.fields.map((f) => [f.key, f]))
  const lowConf: Field[] = Object.keys(c.open_items).filter((k) => fieldsByKey[k]?.low_confidence || c.open_items[k].startsWith('LOW_CONF'))
    .map((k) => fieldsByKey[k]).filter(Boolean)
  const hitOpen = 'screening.hits' in c.open_items
  const openReturns = c.checker_returns.filter((r) => !r.resolved)
  const missing = c.missing_documents.filter((m) => !c.documents.some((d) => d.item === m.checklist_item && d.selected && d.fresh))

  const [edits, setEdits] = useState<Record<string, { value: Json; note: string; on: boolean }>>(() =>
    Object.fromEntries(lowConf.map((f) => [f.key, { value: f.value as Json, note: '', on: true }])))
  const [disp, setDisp] = useState({ outcome: 'FALSE_POSITIVE', rationale: '' })
  const [uploads, setUploads] = useState<UploadDraft[]>([])
  const [nextId, setNextId] = useState(1)

  const addUpload = (item = missing[0]?.checklist_item ?? c.upload_items[0]?.item) => {
    const label = c.upload_items.find((u) => u.item === item)?.label ?? item
    setUploads((u) => [...u, { id: nextId, item, type: label, date: new Date().toISOString().slice(0, 10), text: '' }])
    setNextId((n) => n + 1)
  }
  const patchUpload = (id: number, patch: Partial<UploadDraft>) =>
    setUploads((u) => u.map((x) => (x.id === id ? { ...x, ...patch } : x)))
  const readFile = async (id: number, file: File | undefined) => {
    if (file) patchUpload(id, { text: await file.text(), fileName: file.name })
  }

  // ---- what will be sent, and what is still missing
  const field_edits = Object.fromEntries(Object.entries(edits).filter(([, e]) => e.on).map(([k, e]) => [k, { value: e.value, note: e.note.trim() }]))
  const dispositions: Record<string, string> = hitOpen && disp.rationale.trim()
    ? { 'screening.hits': `${disp.outcome === 'FALSE_POSITIVE' ? 'False positive.' : 'True match.'} ${disp.rationale.trim()}` } : {}
  const ready = uploads.filter((u) => u.text.trim())
  const problems = [
    ...Object.entries(field_edits).filter(([, e]) => !e.note).map(([k]) => `Add an evidence note for ${k}`),
    ...uploads.filter((u) => !u.text.trim()).map((u) => `Choose a file or paste text for the ${u.type} upload`),
    ...(hitOpen && !disp.rationale.trim() ? ['Give a rationale for the screening disposition (G2)'] : []),
  ]
  const counts = [[ready.length, 'upload'], [Object.keys(field_edits).length, 'confirmation'], [Object.keys(dispositions).length, 'disposition']]
    .filter(([n]) => n).map(([n, w]) => `${n} ${w}${n === 1 ? '' : 's'}`)

  const submit = () => onSubmit({
    uploads: ready.map(({ item, type, date, fy_end, expiry, text }) => ({ item, type, date, text, ...(fy_end ? { fy_end } : {}), ...(expiry ? { expiry } : {}) })),
    field_edits, dispositions,
  })

  return (
    <div className="workbench">
      {lowConf.length > 0 && (
        <section>
          <h3>Confirm values <span className="chip guard" title="Confidence below 0.80 needs a person">G1</span></h3>
          {lowConf.map((f) => (
            <div key={f.key} className="wb-item">
              <div className="row">
                <code>{f.key}</code>
                <span className="badge maker">confidence {Number(f.confidence).toFixed(2)}</span>
                {f.document_id && <button className="link" onClick={() => onOpenDoc(f.document_id!, f.page)}>{f.source}</button>}
              </div>
              <div className="small">Extracted: <strong>{show(f.value)}</strong></div>
              {f.note && <div className="small muted">{f.note}</div>}
              <label className="row small"><input type="checkbox" checked={edits[f.key]?.on}
                onChange={(e) => setEdits((x) => ({ ...x, [f.key]: { ...x[f.key], on: e.target.checked } }))} /> Confirm this value</label>
              {edits[f.key]?.on && (
                <div className="wb-grid">
                  <ValueEditor value={edits[f.key].value} onChange={(v) => setEdits((x) => ({ ...x, [f.key]: { ...x[f.key], value: v } }))} />
                  <textarea rows={2} placeholder="Evidence, e.g. 'Confirmed against client-signed register dated 2026-02-10'"
                            value={edits[f.key].note} onChange={(e) => setEdits((x) => ({ ...x, [f.key]: { ...x[f.key], note: e.target.value } }))} />
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      {hitOpen && (
        <section>
          <h3>Screening disposition <span className="chip guard" title="A screening hit needs a maker disposition">G2</span></h3>
          {c.screening.hits.map((h, i) => {
            const p = c.client.parties.find((x) => x.name === h.party)
            const dobDiffers = p?.date_of_birth && h.listed_dob && p.date_of_birth !== h.listed_dob
            const natDiffers = p?.nationality && h.listed_nationality && p.nationality !== h.listed_nationality
            return (
              <div key={i} className="wb-item">
                <table className="compare small">
                  <thead><tr><th /><th>Our record</th><th>{h.list} {h.record}</th></tr></thead>
                  <tbody>
                    <tr><th>Name</th><td>{h.party}</td><td>{h.listed_name}</td></tr>
                    <tr className={dobDiffers ? 'differs' : ''}><th>Date of birth</th><td>{fmtDate(p?.date_of_birth ?? null)}</td><td>{fmtDate(h.listed_dob)}</td></tr>
                    <tr className={natDiffers ? 'differs' : ''}><th>Nationality</th><td>{p?.nationality ?? '—'}</td><td>{h.listed_nationality ?? '—'}</td></tr>
                  </tbody>
                </table>
                {(dobDiffers || natDiffers) && (
                  <button className="link small" onClick={() => setDisp((d) => ({ ...d, rationale:
                    [dobDiffers && `DOB ${p!.date_of_birth} vs listed ${h.listed_dob}`, natDiffers && `nationality ${p!.nationality} vs listed ${h.listed_nationality}`]
                      .filter(Boolean).join('; ') + '.' }))}>
                    Use these differences as the rationale
                  </button>
                )}
              </div>
            )
          })}
          <div className="row small" role="radiogroup" aria-label="Outcome">
            <label className="row"><input type="radio" checked={disp.outcome === 'FALSE_POSITIVE'} onChange={() => setDisp((d) => ({ ...d, outcome: 'FALSE_POSITIVE' }))} /> False positive</label>
            <label className="row"><input type="radio" checked={disp.outcome === 'TRUE_MATCH'} onChange={() => setDisp((d) => ({ ...d, outcome: 'TRUE_MATCH' }))} /> True match</label>
          </div>
          <textarea rows={2} placeholder="Rationale (policy 7.2.4: the agents add the list name and record ID)"
                    value={disp.rationale} onChange={(e) => setDisp((d) => ({ ...d, rationale: e.target.value }))} />
        </section>
      )}

      {(openReturns.length > 0 || missing.length > 0 || uploads.length > 0) && (
        <section>
          <h3>Documents</h3>
          {openReturns.map((r) => {
            const s = c.suggestions.find((x) => x.q_id === r.q_id)
            return (
              <div key={r.q_id} className="wb-item returned">
                <div><span className="badge checker">{r.q_id} returned</span> “{r.comment}” <span className="muted small">— {r.returned_by.full_name}</span></div>
                {s && <div className="small"><span className="muted">Suggested redraft:</span> {s.answer}</div>}
                {s?.evidence_needed?.length ? <div className="small"><span className="muted">Evidence needed:</span> {s.evidence_needed.join('; ')}</div> : null}
              </div>
            )
          })}
          {missing.map((m) => (
            <div key={m.checklist_item} className="row small">
              <span className={`badge ${m.identity_critical ? 'danger' : 'maker'}`}>needed</span> {m.label}
              <span className="muted">{m.reason}</span>
              {!uploads.some((u) => u.item === m.checklist_item) && <button className="link" onClick={() => addUpload(m.checklist_item)}>Add</button>}
            </div>
          ))}
          {uploads.map((u) => (
            <div key={u.id} className="wb-item upload">
              <div className="wb-grid">
                <label><span className="muted small">Document</span>
                  <select value={u.item} onChange={(e) => patchUpload(u.id, { item: e.target.value, type: c.upload_items.find((x) => x.item === e.target.value)?.label ?? e.target.value })}>
                    {c.upload_items.map((x) => <option key={x.item} value={x.item}>{x.label}</option>)}
                  </select></label>
                <label><span className="muted small">Type / title</span><input value={u.type} onChange={(e) => patchUpload(u.id, { type: e.target.value })} /></label>
                <label><span className="muted small">Document date</span><input type="date" value={u.date} onChange={(e) => patchUpload(u.id, { date: e.target.value })} /></label>
                {u.item === 'audited_fs' && <label><span className="muted small">Financial year end</span><input type="date" value={u.fy_end ?? ''} onChange={(e) => patchUpload(u.id, { fy_end: e.target.value })} /></label>}
                {u.item === 'director_ids' && <label><span className="muted small">Earliest expiry</span><input type="date" value={u.expiry ?? ''} onChange={(e) => patchUpload(u.id, { expiry: e.target.value })} /></label>}
              </div>
              <div className="row small">
                <input type="file" accept=".txt,text/plain" aria-label="Text file" onChange={(e) => readFile(u.id, e.target.files?.[0])} />
                {u.fileName && <span className="muted">{u.fileName} · {u.text.length} characters</span>}
                <span className="header-spacer" />
                <button className="link danger" onClick={() => setUploads((x) => x.filter((y) => y.id !== u.id))}>Remove</button>
              </div>
              <textarea rows={3} className="mono" placeholder="…or paste the document text. Mark pages with [p1], [p2] so answers can cite them."
                        value={u.text} onChange={(e) => patchUpload(u.id, { text: e.target.value, fileName: undefined })} />
            </div>
          ))}
          <button onClick={() => addUpload()}>+ Add a document</button>
        </section>
      )}

      <div className="wb-footer">
        {problems.length > 0 && <ul className="plain small warn">{problems.map((p) => <li key={p}>• {p}</li>)}</ul>}
        <div className="row">
          <span className="muted small">{counts.length ? `Will send ${counts.join(', ')}.` : 'Nothing changed: the draft goes to the automated review as it is.'}</span>
          <span className="header-spacer" />
          <button className="primary" disabled={busy || problems.length > 0} onClick={submit}>
            {busy ? 'Submitting…' : 'Submit to the master'}
          </button>
        </div>
      </div>
    </div>
  )
}
