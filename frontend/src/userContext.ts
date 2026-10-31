import { createContext, useContext } from 'react'
import type { User } from './api'

export interface AuthCtx {
  user: User | null
  /** throws ApiError(401) on a wrong username or password */
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}
export const UserContext = createContext<AuthCtx>({ user: null, login: async () => {}, logout: async () => {} })

export function useUser() {
  return useContext(UserContext)
}
