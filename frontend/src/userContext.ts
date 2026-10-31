import { createContext, useContext } from 'react'
import type { User } from './api'

export interface UserCtx { user: User | null; users: User[]; setUser: (username: string) => void }
export const UserContext = createContext<UserCtx>({ user: null, users: [], setUser: () => {} })

export function useUser() {
  return useContext(UserContext)
}
