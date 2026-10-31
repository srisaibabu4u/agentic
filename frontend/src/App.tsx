import { Link, Route, Routes } from 'react-router-dom'
import { useUser } from './userContext'
import Dashboard from './pages/Dashboard'
import CasePage from './pages/CasePage'

function UserSwitcher() {
  const { user, users, setUser } = useUser()
  return (
    <label className="row small" title="Stand-in for login until Phase 4">
      <span className="muted">Acting as</span>
      <select value={user?.username ?? ''} onChange={(e) => setUser(e.target.value)} aria-label="Acting user">
        {users.map((u) => (
          <option key={u.username} value={u.username}>
            {u.full_name} · {u.title}
          </option>
        ))}
      </select>
    </label>
  )
}

export default function App() {
  return (
    <>
      <header className="app-header">
        <Link to="/" className="brand">
          <img src="/favicon.svg" alt="" />
          KYC Renewals <small>client risk assessment · dummy data</small>
        </Link>
        <span className="header-spacer" />
        <UserSwitcher />
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/cases/:ref" element={<CasePage />} />
          <Route path="*" element={<p>Page not found. <Link to="/">Back to cases</Link></p>} />
        </Routes>
      </main>
    </>
  )
}
