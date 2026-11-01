import { Link, NavLink, Route, Routes } from 'react-router-dom'
import { useUser } from './userContext'
import CasePage from './pages/CasePage'
import Dashboard from './pages/Dashboard'
import LessonsPage from './pages/LessonsPage'
import LoginPage from './pages/LoginPage'

function UserMenu() {
  const { user, logout } = useUser()
  if (!user) return null
  return (
    <div className="row small">
      <span><strong>{user.full_name}</strong> <span className="muted">· {user.title ?? user.role}</span></span>
      <button onClick={() => logout()}>Sign out</button>
    </div>
  )
}

export default function App() {
  const { user } = useUser()
  if (!user) return <LoginPage />
  return (
    <>
      <header className="app-header">
        <Link to="/" className="brand">
          <img src="/favicon.svg" alt="" />
          KYC Renewals <small>client risk assessment · dummy data</small>
        </Link>
        <nav className="row small">
          <NavLink to="/" end>Cases</NavLink>
          <NavLink to="/lessons">Lessons</NavLink>
        </nav>
        <span className="header-spacer" />
        <UserMenu />
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/cases/:ref" element={<CasePage />} />
          <Route path="/lessons" element={<LessonsPage />} />
          <Route path="*" element={<p>Page not found. <Link to="/">Back to cases</Link></p>} />
        </Routes>
      </main>
    </>
  )
}
