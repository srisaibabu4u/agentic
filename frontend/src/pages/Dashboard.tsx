import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type CaseListItem } from '../api'
import { StatusBadge } from '../ui'
import { useUser } from '../userContext'

// Step 3.1 placeholder: proves browser -> Vite proxy -> FastAPI -> Postgres. The real dashboard is step 3.2.
export default function Dashboard() {
  const { user } = useUser()
  const { data, error, isLoading } = useQuery({
    queryKey: ['cases', user?.username],
    queryFn: () => api<{ total: number; items: CaseListItem[] }>('/cases'),
    enabled: !!user,
  })
  if (isLoading || !user) return <p className="muted">Loading…</p>
  if (error) return <p className="notice error">Could not reach the API: {String(error)}</p>
  return (
    <div className="stack">
      <h1>Cases <span className="muted">({data!.total})</span></h1>
      <div className="card">
        <table>
          <thead><tr><th>Case</th><th>Client</th><th>Status</th></tr></thead>
          <tbody>
            {data!.items.map((c) => (
              <tr key={c.case_ref}>
                <td><Link to={`/cases/${c.case_ref}`} className="mono">{c.case_ref}</Link></td>
                <td>{c.client.legal_name}</td>
                <td><StatusBadge status={c.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
