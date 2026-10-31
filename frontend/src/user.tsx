// Who is signed in. GET /me answers from the session cookie; a 401 anywhere (expired or ended
// session) sends the app back to the login page.
import { useEffect, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, post, SIGNED_OUT, type User } from './api'
import { UserContext } from './userContext'

async function me(): Promise<User | null> {
  try {
    return await api<User>('/me')
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null
    throw e
  }
}

export function UserProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const { data: user, isPending, error } = useQuery({ queryKey: ['me'], queryFn: me, staleTime: Infinity, retry: false })

  useEffect(() => {
    const out = () => { qc.setQueryData(['me'], null) }
    window.addEventListener(SIGNED_OUT, out)
    return () => window.removeEventListener(SIGNED_OUT, out)
  }, [qc])

  const login = async (username: string, password: string) => {
    const r = await post<{ user: User }>('/auth/login', { username, password })
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' }) // nothing cached from another user
    qc.setQueryData(['me'], r.user)
  }
  const logout = async () => {
    await post('/auth/logout', {})
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'me' })
    qc.setQueryData(['me'], null)
  }

  if (isPending) return <p className="page muted">Loading…</p>
  if (error) return <p className="page notice error">Cannot reach the API: {String(error)}</p>
  return <UserContext.Provider value={{ user: user ?? null, login, logout }}>{children}</UserContext.Provider>
}
