// Who is acting. Until JWT login (Phase 4) the user picks an identity from the
// seeded users; the choice is remembered in this browser only.
import { useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, setActingUser, type User } from './api'
import { UserContext } from './userContext'

const KEY = 'kyc.user'

function remembered(): string | null {
  try { return localStorage.getItem(KEY) } catch { return null }
}

export function UserProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => api<User[]>('/users') })
  const [username, setUsername] = useState<string | null>(remembered)

  // fall back to the first maker when nothing (or an unknown name) is remembered
  const user = users.find((u) => u.username === username) ?? users.find((u) => u.role === 'maker') ?? users[0] ?? null
  setActingUser(user?.username ?? null)

  const setUser = (name: string) => {
    try { localStorage.setItem(KEY, name) } catch { /* private mode */ }
    setActingUser(name)
    setUsername(name)
    qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'users' })
  }
  return <UserContext.Provider value={{ user, users, setUser }}>{children}</UserContext.Provider>
}
