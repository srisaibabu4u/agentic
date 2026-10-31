import { useState, type FormEvent } from 'react'
import { ApiError } from '../api'
import { useUser } from '../userContext'

const DEMO = [
  ['priya.nair', 'Maker'], ['aaron.koh', 'Maker'], ['daniel.tan', 'Checker'], ['siti.rahman', 'Checker'], ['grace.lim', 'Team lead'],
]

export default function LoginPage() {
  const { login } = useUser()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? 'Wrong username or password.'
        : err instanceof ApiError && err.status === 429 ? err.details.join(' ') // locked after repeated failures
          : `Could not sign in: ${String(err)}`)
      setBusy(false)
    }
  }

  return (
    <main className="login">
      <form className="card login-card" onSubmit={submit} aria-labelledby="login-title">
        <div className="brand login-brand"><img src="/favicon.svg" alt="" /> KYC Renewals</div>
        <h1 id="login-title">Sign in</h1>
        <label>
          <span>Username</span>
          <input name="username" autoComplete="username" autoFocus required value={username}
                 onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label>
          <span>Password</span>
          <input name="password" type="password" autoComplete="current-password" required value={password}
                 onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="notice error small" role="alert">{error}</p>}
        <button className="primary" type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        <details className="small muted">
          <summary>Demo accounts (dummy data)</summary>
          <p>Every account uses the password <code>kyc-demo-123</code>.</p>
          <ul className="plain">
            {DEMO.map(([u, r]) => (
              <li key={u}><button type="button" className="link" onClick={() => setUsername(u)}>{u}</button> · {r}</li>
            ))}
          </ul>
        </details>
      </form>
    </main>
  )
}
