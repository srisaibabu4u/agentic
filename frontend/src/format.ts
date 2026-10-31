// Labels, colour families and date formatting shared across pages.
import type { CaseStatus } from './api'

export const STATUS_LABEL: Record<CaseStatus, string> = {
  NEW: 'New', DISCOVERY: 'Discovery', INGESTION: 'Ingestion', IN_DRAFT: 'Drafting', WITH_MAKER: 'With maker',
  AUTO_REVIEW: 'Auto review', WITH_CHECKER: 'With checker', RETURNED: 'Returned', APPROVED: 'Approved', CLOSED: 'Closed',
}

export type Family = 'agent' | 'maker' | 'checker' | 'ok' | 'neutral'

/** Which colour family a status belongs to: agents working, a person, or done. */
export function statusFamily(s: CaseStatus): Family {
  if (s === 'WITH_MAKER' || s === 'RETURNED') return 'maker'
  if (s === 'WITH_CHECKER') return 'checker'
  if (s === 'APPROVED' || s === 'CLOSED') return 'ok'
  if (s === 'NEW') return 'neutral'
  return 'agent'
}

export const AGENT_LABEL: Record<string, string> = {
  A1_DISCOVERY: 'Agent 1 · Discovery', A2_INGEST: 'Agent 2 · Ingestion', A3_DRAFT: 'Agent 3 · Drafting',
  A4_REVIEW: 'Agent 4 · Review', A5_NOTIFY: 'Agent 5 · Notification',
}

const dateFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
const timeFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

export function fmtDate(s: string | null) {
  return s ? dateFmt.format(new Date(s)) : '—'
}
export function fmtTime(s: string | null) {
  return s ? timeFmt.format(new Date(s)) : '—'
}
