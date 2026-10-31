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

/** What each routing rule (R) and guardrail (G) in orchestrator/master.py means. */
export const RULES: Record<string, string> = {
  R1: 'New renewal: start with document discovery',
  R2: 'Documents found: ingest them; request any missing (non-identity) ones in parallel',
  R2a: 'Identity document missing, or too little found: the maker first, no drafting',
  R3: 'Fields extracted: draft every answer, carrying open items forward',
  R3a: 'An agent failed: retry it (escalate after 3 attempts, G4)',
  R4: 'Draft done but open items need a person: send to the maker before review',
  R4b: 'Draft has no open items: run the automated review',
  R5: 'Maker submitted: re-run only what the change affects',
  R5c: 'Maker submitted without changes: run the automated review',
  R6: 'Review passed: to the checker if the maker has submitted, otherwise back to the maker',
  R7: 'Fixable findings with the evidence on file: redraft, then review again',
  R7b: 'A finding needs information that is not on file: ask the maker',
  R8: 'Checker returned questions: suggest a redraft and notify the maker',
  R9: 'Checker approved: close the case and schedule the next review',
  G1: 'A field with confidence below 0.80 needs a person to confirm it',
  G2: 'A screening hit needs a maker disposition',
  G3: 'A HIGH review finding goes back to the maker, never to an agent',
  G4: 'Three failures on the same item: stop retrying and escalate',
  G5: 'Four-eyes: only the checker approves, and maker and checker differ',
  G6: 'Fewer than 5 business days to the due date: copy the team lead',
  WAIT_PENDING: 'Other dispatched tasks are still running; decide when they return',
  A5_ACK: 'Notification delivered; nothing changes until the recipient acts',
}

export const ACTOR_LABEL: Record<string, string> = {
  scheduler: 'Scheduler', agents: 'Agents', maker: 'Maker', checker: 'Checker', done: 'Done',
}
