// Typed client for the FastAPI backend (backend/kyc/api). The session is an httpOnly cookie set by
// POST /auth/login, which the browser sends with every same-origin request; scripts never see the token.

export type Role = 'maker' | 'checker' | 'admin'
export type CaseStatus =
  | 'NEW' | 'DISCOVERY' | 'INGESTION' | 'IN_DRAFT' | 'WITH_MAKER' | 'AUTO_REVIEW'
  | 'WITH_CHECKER' | 'RETURNED' | 'APPROVED' | 'CLOSED'
export type Band = 'LOW' | 'MEDIUM' | 'HIGH' | 'PROVISIONAL'

export interface UserRef { username: string; full_name: string; title: string | null }
export interface User extends UserRef { role: Role }

export interface CaseListItem {
  case_ref: string
  status: CaseStatus
  waiting_on: 'maker' | 'checker' | null
  client: { cif: string; legal_name: string; short_name: string }
  due_date: string
  business_days_left: number
  overdue: boolean
  maker: UserRef
  checker: UserRef
  rating: Band | null
  final_rating: Band | null
  open_items: number
  scenario: string | null
  updated_at: string
}

export interface Answer {
  q_id: string; question?: string; answer: string; score: number | null; score_reason: string | null
  citations: string[]; flags: string[]; confidence: number | null; resolves?: string
  evidence_needed?: string[]; version: number; mode?: string
}
export interface Field {
  key: string; value: unknown; confidence: number; source: string | null; document_id: number | null
  page: number | null; note: string | null; low_confidence: boolean
  confirmed_by: UserRef | null; confirmed_at: string | null; confirm_note: string | null
}
export interface Doc {
  id: number; doc_ref: string; item: string; type: string; source: string; date: string
  fy_end: string | null; expiry: string | null; size_bytes: number; uploaded_by: UserRef | null
  selected: boolean; fresh: boolean | null
}
export interface Finding {
  q_id: string; check: string; severity: 'HIGH' | 'MEDIUM' | 'LOW'; excerpt: string | null
  issue: string; policy_ref: string | null; evidence_available: boolean
}
export interface Rating { total: number; max: number; working: string; band: Band }
export interface Task {
  task_ref: string; agent: string; status: string; scope: Record<string, unknown>; depends_on: string[]
  retry: number; live: boolean; claimed_by: string | null; claimed_at: string | null
  finished_at: string | null; error: string | null
}

export interface CaseDetail {
  case_ref: string; status: CaseStatus; waiting_on: 'maker' | 'checker' | null; scenario: string | null
  due_date: string; business_days_left: number; opened_at: string; closed_at: string | null
  next_review_date: string | null; version: number
  maker: UserRef; checker: UserRef; team_lead: UserRef | null
  config: { checklist: string; questionnaire: string }
  client: {
    cif: string; legal_name: string; short_name: string; uen: string; segment: string
    principal_activity: string; ssic_code: string | null; prior_rating: string | null
    prior_assessment_date: string | null
    parties: { id: number; parent_id: number | null; type: 'PERSON' | 'ENTITY'; name: string
      nationality: string | null; date_of_birth: string | null; director: boolean; shareholder: boolean; ubo: boolean
      ownership_pct: number | null }[]
  }
  documents: Doc[]
  missing_documents: { checklist_item: string; label: string; identity_critical: boolean; reason: string }[]
  upload_items: { item: string; label: string }[]
  fields: Field[]
  open_items: Record<string, string>
  answers: Answer[]
  suggestions: Answer[]
  rating: Rating | null
  final: { rating: Band; score: Rating; approved_by: UserRef | null; comment: string | null } | null
  findings: Finding[]
  checker_returns: { q_id: string; comment: string; returned_by: UserRef; returned_at: string; resolved: boolean }[]
  screening: {
    hits: { party: string; list: string; record: string; listed_name: string | null
      listed_dob: string | null; listed_nationality: string | null }[]
    dispositions: { ref: string; outcome: string; rationale: string; decided_by: UserRef; decided_at: string }[]
  }
  open_tasks: Task[]
  notifications: { to: UserRef; cc: UserRef[]; template: string; subject: string; body: string; sent_at: string }[]
}

export interface Decision {
  decision: 'DISPATCH' | 'WAIT' | 'CLOSE' | 'ESCALATE'; prev_status: CaseStatus; new_status: CaseStatus
  rules_matched: string[]; guardrails_triggered: string[]; rationale: string; decided_at: string
  config_version: string | null; state: Record<string, unknown>; tasks: Task[]
}
export interface TimelineEvent {
  seq: number; type: string; source: string; occurred_at: string; actor: UserRef | null
  processed: boolean; task: Task | null; payload?: Record<string, unknown>; decision?: Decision
}
export interface Timeline { case_ref: string; status: CaseStatus; events: TimelineEvent[] }

export interface Accepted { case_ref: string; event_seq: number; status: 'accepted'; detail: string }

export class ApiError extends Error {
  status: number
  details: string[]
  constructor(status: number, details: string[]) {
    super(details.join('; '))
    this.status = status
    this.details = details
  }
}

/** Fired when the API says the session is missing or over; the auth provider shows the login page. */
export const SIGNED_OUT = 'kyc:signed-out'

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  const res = await fetch(`/api${path}`, { ...init, headers })
  if (res.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event(SIGNED_OUT))
  if (!res.ok) {
    let details = [res.statusText]
    try {
      const body = await res.json()
      const d = body.detail
      // FastAPI sends a string, a list of strings (our checks) or a list of {loc, msg} (body validation)
      details = Array.isArray(d)
        ? d.map((x) => (typeof x === 'string' ? x : `${(x.loc ?? []).slice(1).join('.')}: ${x.msg}`))
        : [String(d ?? res.statusText)]
    } catch { /* not JSON */ }
    throw new ApiError(res.status, details)
  }
  return (res.status === 204 ? undefined : res.json()) as Promise<T>
}

export const post = <T>(path: string, body: unknown) => api<T>(path, { method: 'POST', body: JSON.stringify(body) })

export interface WorkflowNode { id: CaseStatus; label: string; actor: 'scheduler' | 'agents' | 'maker' | 'checker' | 'done' }
export interface WorkflowEdge { id: string; source: CaseStatus; target: CaseStatus; rules: string[]; description: string }
export interface Workflow { nodes: WorkflowNode[]; edges: WorkflowEdge[] }

export interface UploadBody { item: string; type: string; date: string; fy_end?: string; expiry?: string; text: string }
export interface MakerSubmitBody {
  uploads: UploadBody[]
  field_edits: Record<string, { value: unknown; note: string }>
  dispositions: Record<string, string>
}
