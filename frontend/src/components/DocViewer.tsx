// A modal showing one document's text, opened from any source or citation link.
import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { fmtDate } from '../format'

interface DocText {
  id: number; doc_id: string; type: string; source: string; date: string; text: string; sha256: string
  fy_end?: string; expiry?: string
}

export default function DocViewer({ docId, page, onClose }: { docId: number | null; page?: number | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const { data, error } = useQuery({ queryKey: ['document', docId], enabled: docId !== null,
    queryFn: () => api<DocText>(`/documents/${docId}`), staleTime: Infinity })

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (docId !== null && !d.open) d.showModal()
    if (docId === null && d.open) d.close()
  }, [docId])

  // split on the [pN] page markers the agents cite, so a cited page can be highlighted
  const pages = (data?.text ?? '').split(/(?=\[p\d+\])/)
  return (
    <dialog ref={ref} className="doc-viewer" onClose={onClose} aria-label="Document">
      {error && <p className="notice error">{String(error)}</p>}
      {!data ? <p className="muted">Loading…</p> : (
        <>
          <div className="row">
            <h2><span className="mono muted">{data.doc_id}</span> {data.type}</h2>
            <span className="header-spacer" />
            <button onClick={() => ref.current?.close()} autoFocus>Close</button>
          </div>
          <p className="muted small">
            {data.source} · dated {fmtDate(data.date)}
            {data.fy_end && <> · FYE {fmtDate(data.fy_end)}</>}
            {data.expiry && <> · expires {fmtDate(data.expiry)}</>}
            {' · '}<span className="mono" title="SHA-256 of the stored file">{data.sha256.slice(0, 12)}…</span>
          </p>
          <div className="doc-text mono">
            {pages.map((p, i) => {
              const n = Number(/^\[p(\d+)\]/.exec(p)?.[1])
              return <div key={i} className={page && n === page ? 'cited' : undefined}>{p}</div>
            })}
          </div>
        </>
      )}
    </dialog>
  )
}
