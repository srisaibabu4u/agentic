"""Request bodies. They mirror the event payloads the master and agents already understand."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class Upload(BaseModel):
    doc_id: str | None = Field(None, pattern=r"^DOC-\d{2,}$", description="Assigned automatically when omitted")
    item: str = Field(description="Checklist item, e.g. audited_fs, ubo_corporate_register, sow_declaration")
    type: str = Field(min_length=1, max_length=128, description="Document type, e.g. 'Audited FS FY2025'")
    date: date
    fy_end: date | None = None
    expiry: date | None = None
    text: str = Field(min_length=1, max_length=1_000_000, description="Document text, with [pN] page markers")
    source: str = "Maker upload"


class FieldEdit(BaseModel):
    value: Any
    note: str = Field(min_length=1, description="Evidence for the confirmed value")


class MakerSubmit(BaseModel):
    uploads: list[Upload] = []
    field_edits: dict[str, FieldEdit] = {}
    dispositions: dict[str, str] = Field({}, description="Open item ref -> disposition text, e.g. screening.hits")


class ReturnedQuestion(BaseModel):
    q_id: str
    comment: str = Field(min_length=1)


class CheckerReturn(BaseModel):
    returned: list[ReturnedQuestion] = Field(min_length=1)


class CheckerApprove(BaseModel):
    final_rating: Literal["LOW", "MEDIUM", "HIGH"]
    comment: str | None = None


class Accepted(BaseModel):
    case_ref: str
    event_seq: int
    status: Literal["accepted"] = "accepted"
    detail: str = "Queued for the master; the case updates when the worker processes it."
