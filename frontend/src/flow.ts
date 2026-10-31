// Derive the path a case has taken from its timeline: every status change, with the step that caused it.
import type { CaseStatus, TimelineEvent } from './api'

export interface Transition { seq: number; from: CaseStatus; to: CaseStatus; rules: string[] }

export function transitions(events: TimelineEvent[], uptoSeq?: number): Transition[] {
  return events
    .filter((e) => e.decision && e.decision.prev_status !== e.decision.new_status && (uptoSeq === undefined || e.seq <= uptoSeq))
    .map((e) => ({ seq: e.seq, from: e.decision!.prev_status, to: e.decision!.new_status, rules: e.decision!.rules_matched }))
}

export const edgeId = (from: string, to: string) => `${from}->${to}`

/** Grid positions for the statuses: agent work on the top row, people below it. */
export const STEP = 200
const ROW2 = 260
export const LAYOUT: Record<CaseStatus, { x: number; y: number }> = {
  NEW: { x: 0, y: 0 },
  DISCOVERY: { x: STEP, y: 0 },
  INGESTION: { x: 2 * STEP, y: 0 },
  IN_DRAFT: { x: 3 * STEP, y: 0 },
  AUTO_REVIEW: { x: 4 * STEP, y: 0 },
  WITH_CHECKER: { x: 5 * STEP, y: 0 },
  CLOSED: { x: 6 * STEP, y: 0 },
  WITH_MAKER: { x: 2.5 * STEP, y: ROW2 },
  RETURNED: { x: 5 * STEP, y: ROW2 },
  APPROVED: { x: 6 * STEP, y: ROW2 },
}
