// Shows the right form when the case waits on the current user, sends the action to the API and
// refreshes the page. The API enforces the same rules (403 wrong person, 409 wrong moment, 422 bad content).
import { useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, post, type Accepted, type Band, type CaseDetail, type MakerSubmitBody } from '../api'
import { useUser } from '../userContext'
import CheckerReview from './CheckerReview'
import DocViewer from './DocViewer'
import MakerWorkbench from './MakerWorkbench'

export default function ActionPanel({ c }: { c: CaseDetail }) {
  const { user } = useUser()
  const qc = useQueryClient()
  const [done, setDone] = useState<string | null>(null)
  const [doc, setDoc] = useState<{ id: number; page?: number | null } | null>(null)

  const action = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown; message: string }) => post<Accepted>(`/cases/${c.case_ref}/${path}`, body),
    onSuccess: (_r, v) => {
      setDone(v.message)
      qc.invalidateQueries({ queryKey: ['case', c.case_ref] })
      qc.invalidateQueries({ queryKey: ['timeline', c.case_ref] })
      qc.invalidateQueries({ queryKey: ['cases'] })
    },
  })
  const err = action.error instanceof ApiError ? action.error : null

  const person = c.waiting_on === 'maker' ? c.maker : c.waiting_on === 'checker' ? c.checker : null
  const mine = !!person && person.username === user?.username
  const openDoc = (id: number, page?: number | null) => setDoc({ id, page })

  let body: ReactNode = null
  if (!person) {
    body = done && c.status !== 'CLOSED' ? <p className="notice ok">{done} The agents are working on it; this page updates as they go.</p> : null
  } else if (!mine) {
    body = (
      <p className="notice small">
        Waiting on <strong>{person.full_name}</strong> ({c.waiting_on}). Only they can act on this case now;
        switch to them in “Acting as” to try it.
      </p>
    )
  } else {
    body = (
      <div className={`card action-card ${c.waiting_on}`}>
        <div className="card-head">
          <h2>{c.waiting_on === 'maker' ? 'Your turn: maker workbench' : 'Your turn: checker review'}</h2>
          <span className="muted small">{c.status === 'RETURNED' ? 'Returned by the checker' : `${Object.keys(c.open_items).length} open item(s)`}</span>
        </div>
        <div className="card-body">
          {err && (
            <div className="notice error small" role="alert">
              <strong>{err.status === 409 ? 'Not your turn any more' : err.status === 403 ? 'Not allowed' : 'Please fix'}:</strong>
              <ul className="plain">{err.details.map((d) => <li key={d}>{d}</li>)}</ul>
            </div>
          )}
          {c.waiting_on === 'maker'
            ? <MakerWorkbench key={c.version} c={c} busy={action.isPending} onOpenDoc={openDoc}
                              onSubmit={(b: MakerSubmitBody) => action.mutate({ path: 'maker/submit', body: b, message: 'Submitted.' })} />
            : <CheckerReview key={c.version} c={c} busy={action.isPending} onOpenDoc={openDoc}
                             onReturn={(returned) => action.mutate({ path: 'checker/return', body: { returned }, message: 'Returned to the maker.' })}
                             onApprove={(final_rating: Band, comment: string) => action.mutate({ path: 'checker/approve', body: { final_rating, comment: comment || null }, message: 'Approved.' })} />}
        </div>
      </div>
    )
  }
  return (
    <>
      {body}
      <DocViewer docId={doc?.id ?? null} page={doc?.page} onClose={() => setDoc(null)} />
    </>
  )
}
