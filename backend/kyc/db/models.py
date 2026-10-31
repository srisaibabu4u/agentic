"""Relational data model for the KYC renewal workflow.

Four groups of tables:

* reference: users, clients, related parties, checklist, questionnaire,
  rating bands, country risk, policy rules and the dependency map;
* evidence: documents, extracted fields, screening hits, dispositions;
* workflow: cases, case events, master decisions, agent tasks
  (case_events and master_decisions are append-only: the audit trail);
* output: answers (versioned), review findings, checker returns, ratings,
  notifications.

Four-eyes control is enforced twice: guardrail G5 in the master, and in the
database by a CHECK on cases plus the triggers in the initial migration.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
                        Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

ROLES = ("maker", "checker", "admin")
CASE_STATUSES = ("NEW", "DISCOVERY", "INGESTION", "IN_DRAFT", "WITH_MAKER", "AUTO_REVIEW",
                 "WITH_CHECKER", "RETURNED", "APPROVED", "CLOSED")
AGENTS = ("A1_DISCOVERY", "A2_INGEST", "A3_DRAFT", "A4_REVIEW", "A5_NOTIFY")
TASK_STATUSES = ("PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED")
EVENT_TYPES = ("RENEWAL_DUE", "AGENT_RESULT", "MAKER_SUBMIT", "CHECKER_RETURN", "CHECKER_APPROVE")
DECISIONS = ("DISPATCH", "WAIT", "CLOSE", "ESCALATE")
SEVERITIES = ("HIGH", "MEDIUM", "LOW")
BANDS = ("LOW", "MEDIUM", "HIGH", "PROVISIONAL")


def _in(col: str, values: tuple) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ===================================================================== reference
class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(256), unique=True)
    role: Mapped[str] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(64))          # e.g. "Maker (RM)", "Team lead"
    password_hash: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = _now()


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    cif: Mapped[str] = mapped_column(String(32), unique=True)
    legal_name: Mapped[str] = mapped_column(String(256))
    short_name: Mapped[str] = mapped_column(String(128))
    uen: Mapped[str] = mapped_column(String(32), unique=True)
    segment: Mapped[str] = mapped_column(String(64))
    incorporation_country: Mapped[str] = mapped_column(String(2), default="SG")
    principal_activity: Mapped[str] = mapped_column(Text)
    ssic_code: Mapped[str | None] = mapped_column(String(8))
    incorporated_on: Mapped[date | None] = mapped_column(Date)
    prior_rating: Mapped[str | None] = mapped_column(String(16))
    prior_assessment_date: Mapped[date | None] = mapped_column(Date)
    next_review_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _now()

    parties: Mapped[list[RelatedParty]] = relationship(back_populates="client", foreign_keys="RelatedParty.client_id")


class RelatedParty(Base):
    """A director, shareholder or UBO. A UBO behind a corporate shareholder points at it via parent_id."""
    __tablename__ = "related_parties"
    __table_args__ = (
        CheckConstraint("party_type IN ('PERSON', 'ENTITY')", name="party_type"),
        CheckConstraint("ownership_pct IS NULL OR (ownership_pct >= 0 AND ownership_pct <= 100)", name="pct_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("related_parties.id", ondelete="CASCADE"))
    party_type: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(256))
    nationality: Mapped[str | None] = mapped_column(String(3))     # country code; "BVI" allowed for entities
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    id_document: Mapped[str | None] = mapped_column(String(64))
    is_director: Mapped[bool] = mapped_column(Boolean, default=False)
    is_shareholder: Mapped[bool] = mapped_column(Boolean, default=False)
    is_ubo: Mapped[bool] = mapped_column(Boolean, default=False)
    ownership_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))   # of the client, or of the parent entity
    shares_held: Mapped[int | None] = mapped_column(BigInteger)
    appointed_on: Mapped[date | None] = mapped_column(Date)
    source_of_wealth: Mapped[str | None] = mapped_column(Text)

    client: Mapped[Client] = relationship(back_populates="parties", foreign_keys=[client_id])


class Checklist(Base):
    __tablename__ = "checklists"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)      # e.g. SG_CORP_MED_v4
    description: Mapped[str | None] = mapped_column(Text)
    items: Mapped[list[ChecklistItem]] = relationship(order_by="ChecklistItem.ordinal")


class ChecklistItem(Base):
    __tablename__ = "checklist_items"
    __table_args__ = (UniqueConstraint("checklist_id", "item", name="checklist_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    checklist_id: Mapped[int] = mapped_column(ForeignKey("checklists.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    item: Mapped[str] = mapped_column(String(64))                   # e.g. registry_extract
    label: Mapped[str] = mapped_column(String(128))
    identity_critical: Mapped[bool] = mapped_column(Boolean, default=False)


class Questionnaire(Base):
    __tablename__ = "questionnaires"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)      # e.g. CRA_CORP_v7
    questions: Mapped[list[Question]] = relationship(order_by="Question.ordinal")
    bands: Mapped[list[RatingBand]] = relationship(order_by="RatingBand.max_score")


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("questionnaire_id", "q_id", name="q_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    questionnaire_id: Mapped[int] = mapped_column(ForeignKey("questionnaires.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    q_id: Mapped[str] = mapped_column(String(8))
    text: Mapped[str] = mapped_column(Text)


class RatingBand(Base):
    __tablename__ = "rating_bands"
    __table_args__ = (UniqueConstraint("questionnaire_id", "band", name="band"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    questionnaire_id: Mapped[int] = mapped_column(ForeignKey("questionnaires.id", ondelete="CASCADE"))
    band: Mapped[str] = mapped_column(String(16))
    max_score: Mapped[int] = mapped_column(Integer)


class CountryRisk(Base):
    __tablename__ = "country_risk"
    __table_args__ = (CheckConstraint("risk_level BETWEEN 1 AND 5", name="level"),)

    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[int] = mapped_column(Integer)


class IndustryRisk(Base):
    __tablename__ = "industry_risk"
    __table_args__ = (CheckConstraint("risk_level BETWEEN 1 AND 5", name="level"),)

    ssic_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    label: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[int] = mapped_column(Integer)


class PolicyRule(Base):
    __tablename__ = "policy_rules"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)  # e.g. 7.2.4
    text: Mapped[str] = mapped_column(Text)


class DocItemField(Base):
    """Dependency map, part 1: which fields a document of a checklist item yields."""
    __tablename__ = "doc_item_fields"
    __table_args__ = (UniqueConstraint("questionnaire_id", "item", "field_key", name="doc_item_field"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    questionnaire_id: Mapped[int] = mapped_column(ForeignKey("questionnaires.id", ondelete="CASCADE"))
    item: Mapped[str] = mapped_column(String(64))
    field_key: Mapped[str] = mapped_column(String(64))


class FieldQuestion(Base):
    """Dependency map, part 2: which questions use a field."""
    __tablename__ = "field_questions"
    __table_args__ = (UniqueConstraint("questionnaire_id", "field_key", "q_id", name="field_question"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    questionnaire_id: Mapped[int] = mapped_column(ForeignKey("questionnaires.id", ondelete="CASCADE"))
    field_key: Mapped[str] = mapped_column(String(64))
    q_id: Mapped[str] = mapped_column(String(8))


# ===================================================================== workflow
class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint(_in("status", CASE_STATUSES), name="status"),
        CheckConstraint("maker_id <> checker_id", name="four_eyes"),
        CheckConstraint("waiting_on IS NULL OR waiting_on IN ('maker', 'checker')", name="waiting_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    case_ref: Mapped[str] = mapped_column(String(32), unique=True)  # e.g. RA-2026-0412
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    checklist_id: Mapped[int] = mapped_column(ForeignKey("checklists.id"))
    questionnaire_id: Mapped[int] = mapped_column(ForeignKey("questionnaires.id"))
    maker_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    checker_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    team_lead_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default="NEW", server_default="NEW", index=True)
    waiting_on: Mapped[str | None] = mapped_column(String(8))
    due_date: Mapped[date] = mapped_column(Date)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manifest: Mapped[dict | None] = mapped_column(JSONB)           # Agent 1's latest document manifest
    open_items: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    retries: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")   # "agent:item" -> count
    maker_submitted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    final_rating: Mapped[str | None] = mapped_column(String(16))
    approval_comment: Mapped[str | None] = mapped_column(Text)
    next_review_date: Mapped[date | None] = mapped_column(Date)
    task_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    scenario: Mapped[str | None] = mapped_column(String(32))       # seed tag, for demos and tests
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")  # optimistic locking
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    __mapper_args__ = {"version_id_col": version}

    client: Mapped[Client] = relationship()
    maker: Mapped[User] = relationship(foreign_keys=[maker_id])
    checker: Mapped[User] = relationship(foreign_keys=[checker_id])
    team_lead: Mapped[User | None] = relationship(foreign_keys=[team_lead_id])


class CaseEvent(Base):
    """Append-only. Also the master's inbox: processed_at is NULL until the master has decided on it."""
    __tablename__ = "case_events"
    __table_args__ = (
        UniqueConstraint("case_id", "seq", name="case_seq"),
        CheckConstraint(_in("type", EVENT_TYPES), name="type"),
        Index("ix_case_events_unprocessed", "case_id", "seq", postgresql_where="processed_at IS NULL"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(24))                # agent id, maker, checker, scheduler
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # use_alter breaks the events -> tasks -> decisions -> events FK cycle at create time
    task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id", use_alter=True))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class MasterDecision(Base):
    """Append-only: the state the master read, what it matched and why."""
    __tablename__ = "master_decisions"
    __table_args__ = (CheckConstraint(_in("decision", DECISIONS), name="decision"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("case_events.id", ondelete="CASCADE"), unique=True)
    decision: Mapped[str] = mapped_column(String(16))
    prev_status: Mapped[str] = mapped_column(String(16))
    new_status: Mapped[str] = mapped_column(String(16))
    rules_matched: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    guardrails_triggered: Mapped[list[str]] = mapped_column(ARRAY(String(8)), default=list)
    rationale: Mapped[str] = mapped_column(Text)
    state_snapshot: Mapped[dict] = mapped_column(JSONB)
    config_version: Mapped[str | None] = mapped_column(String(64))  # checklist + questionnaire codes used
    shadow: Mapped[dict | None] = mapped_column(JSONB)             # LLM shadow decision, when enabled
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentTask(Base):
    """Work queue. Workers claim PENDING rows with SELECT ... FOR UPDATE SKIP LOCKED."""
    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(_in("agent", AGENTS), name="agent"),
        CheckConstraint(_in("status", TASK_STATUSES), name="status"),
        Index("ix_agent_tasks_queue", "status", "id", postgresql_where="status = 'PENDING'"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_ref: Mapped[str] = mapped_column(String(48), unique=True)  # e.g. RA-2026-0412-T03
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("master_decisions.id", ondelete="SET NULL"))
    agent: Mapped[str] = mapped_column(String(16))
    scope: Mapped[dict] = mapped_column(JSONB, default=dict)
    inputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    depends_on: Mapped[list[str]] = mapped_column(ARRAY(String(48)), default=list)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    retry: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    live: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # call Claude?
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


# ===================================================================== evidence
class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("client_id", "doc_ref", name="client_doc_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"))  # set for maker uploads
    doc_ref: Mapped[str] = mapped_column(String(16))               # e.g. DOC-03
    checklist_item: Mapped[str] = mapped_column(String(64))
    doc_type: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32))                # ACRA, DMS, WorldCheck, Maker upload
    doc_date: Mapped[date] = mapped_column(Date)
    fy_end: Mapped[date | None] = mapped_column(Date)
    expiry: Mapped[date | None] = mapped_column(Date)
    storage_path: Mapped[str] = mapped_column(String(512))         # relative to the generated/uploads root
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    text_content: Mapped[str] = mapped_column(Text)                # what the agents read
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _now()


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (
        UniqueConstraint("case_id", "field_key", name="case_field"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    field_key: Mapped[str] = mapped_column(String(64))
    value: Mapped[dict | list | str | int | None] = mapped_column(JSONB)
    raw: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    source_ref: Mapped[str | None] = mapped_column(String(64))     # as the agent wrote it, e.g. DOC-03#p1 or maker
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    source_page: Mapped[int | None] = mapped_column(Integer)
    conflict: Mapped[bool | None] = mapped_column(Boolean)
    note: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id"))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirm_note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _now()


class ScreeningHit(Base):
    __tablename__ = "screening_hits"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    party_name: Mapped[str] = mapped_column(String(256))
    list_name: Mapped[str] = mapped_column(String(64))
    record_id: Mapped[str] = mapped_column(String(64))
    listed_name: Mapped[str | None] = mapped_column(String(256))
    listed_dob: Mapped[date | None] = mapped_column(Date)
    listed_nationality: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = _now()


class Disposition(Base):
    """The maker's disposition of an open item, e.g. ref 'screening.hits' covers every hit in that field."""
    __tablename__ = "dispositions"
    __table_args__ = (CheckConstraint("outcome IN ('FALSE_POSITIVE', 'TRUE_MATCH')", name="outcome"),
                      UniqueConstraint("case_id", "ref", name="case_ref_disposition"))

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    ref: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = _now()


# ===================================================================== output
class Answer(Base):
    """Versioned: every draft or redraft adds a row; is_current marks the live one."""
    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("case_id", "q_id", "version", name="case_q_version"),
        CheckConstraint("score IS NULL OR score BETWEEN 1 AND 5", name="score"),
        Index("uq_answers_current", "case_id", "q_id", unique=True, postgresql_where="is_current"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    q_id: Mapped[str] = mapped_column(String(8))
    version: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[str] = mapped_column(String(16))                  # draft | redraft | suggest
    answer: Mapped[str] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(Integer)
    score_reason: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    flags: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    resolves: Mapped[str | None] = mapped_column(Text)
    evidence_needed: Mapped[list | None] = mapped_column(JSONB)    # suggestions only
    task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id"))
    created_at: Mapped[datetime] = _now()


class ReviewFinding(Base):
    __tablename__ = "review_findings"
    __table_args__ = (CheckConstraint(_in("severity", SEVERITIES), name="severity"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id"))
    q_id: Mapped[str] = mapped_column(String(8))
    check_code: Mapped[str] = mapped_column(String(8))             # C1..C7
    severity: Mapped[str] = mapped_column(String(8))
    excerpt: Mapped[str | None] = mapped_column(Text)
    issue: Mapped[str] = mapped_column(Text)
    policy_ref: Mapped[str | None] = mapped_column(String(16))
    evidence_available: Mapped[bool] = mapped_column(Boolean, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")  # from the latest review
    created_at: Mapped[datetime] = _now()


class CheckerReturn(Base):
    __tablename__ = "checker_returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    q_id: Mapped[str] = mapped_column(String(8))
    comment: Mapped[str] = mapped_column(Text)
    returned_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    returned_at: Mapped[datetime] = _now()
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Rating(Base):
    """Versioned like answers. A final rating needs an approver who is not the case's maker (DB trigger)."""
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("case_id", "version", name="case_version"),
        CheckConstraint(_in("band", BANDS), name="band"),
        CheckConstraint("NOT is_final OR approved_by IS NOT NULL", name="final_needs_approver"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    max_score: Mapped[int] = mapped_column(Integer)
    working: Mapped[str] = mapped_column(String(128))
    band: Mapped[str] = mapped_column(String(16))
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _now()


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id"))
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    cc_user_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    template: Mapped[str] = mapped_column(String(24))
    channel: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(String(64))
    sent_at: Mapped[datetime] = _now()
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
