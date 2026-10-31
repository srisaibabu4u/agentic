import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, type CaseDetail } from '../api'
import { StatusBadge } from '../ui'

// Step 3.1 placeholder; the flow view and timeline come in step 3.3.
export default function CasePage() {
  const { ref = '' } = useParams()
  const { data, error } = useQuery({ queryKey: ['case', ref], queryFn: () => api<CaseDetail>(`/cases/${ref}`) })
  if (error) return <p className="notice error">{String(error)}</p>
  if (!data) return <p className="muted">Loading…</p>
  return (
    <div className="stack">
      <Link to="/">← All cases</Link>
      <h1>{data.client.legal_name} <span className="mono muted">{data.case_ref}</span></h1>
      <div className="row"><StatusBadge status={data.status} /></div>
    </div>
  )
}
