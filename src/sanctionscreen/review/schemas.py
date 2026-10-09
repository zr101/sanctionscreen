"""Schemas for the review assistant: analyst input, tool arguments, draft case
and the run result.

Every model is extra="forbid" so a model cannot smuggle extra fields (e.g. a
"disposition") past validation. DraftCase deliberately has no disposition
field at all: its status is fixed to pending_human_review (DECISIONS.md D12).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sanctionscreen.api.schemas import EntityType


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewRequest(_Strict):
    name: str = Field(
        min_length=1,
        max_length=200,
        description="Customer name as captured. Treated as data, never as instructions.",
        examples=["Ivan Testov"],
    )
    date_of_birth: str | None = Field(
        default=None,
        max_length=40,
        description="Customer date of birth, e.g. 1965-02-15 or 1965.",
    )
    nationality: str | None = Field(default=None, max_length=80)
    entity_type: EntityType | None = None
    analyst_note: str | None = Field(
        default=None,
        max_length=500,
        description="Free-text context for the assistant; passed to the model as data.",
    )


# --- tool arguments ---------------------------------------------------------


class ScreenNameArgs(_Strict):
    name: str = Field(min_length=1, max_length=200, description="Name to screen.")
    entity_type: EntityType | None = Field(
        default=None, description="Restrict to one listed type, e.g. individual."
    )
    threshold: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Minimum score; values below the service floor are raised to it.",
    )


class GetRecordArgs(_Strict):
    record_id: str = Field(
        max_length=64,
        description="A record_id returned by screen_name in this review, e.g. OFAC:12345.",
    )


InfoField = Literal["date_of_birth", "nationality", "entity_type", "other_identifier"]


class RequestInformationArgs(_Strict):
    fields: list[InfoField] = Field(min_length=1, max_length=4)
    reason: str = Field(min_length=1, max_length=500)


EvidenceField = Literal[
    "primary_name", "names", "date_of_birth", "nationality", "entity_type", "listed_date"
]
EvidenceKind = Literal["supports", "conflicts", "unknown"]
Assessment = Literal["possible_match", "unlikely_match", "insufficient_information"]


class EvidenceItem(_Strict):
    record_id: str = Field(max_length=64)
    field: EvidenceField = Field(description="Record field this evidence is taken from.")
    value: str = Field(
        max_length=200,
        description="The value as it appears in the record; empty if the record lacks it.",
    )
    kind: EvidenceKind = Field(
        description="supports / conflicts / unknown. For date_of_birth, nationality and "
        "entity_type this must agree with the deterministic comparison from get_record."
    )
    statement: str = Field(max_length=300)


class CandidateAssessment(_Strict):
    record_id: str = Field(max_length=64)
    assessment: Assessment
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=10)
    rationale: str = Field(max_length=600)


class DraftCaseArgs(_Strict):
    summary: str = Field(min_length=1, max_length=1500)
    candidates: list[CandidateAssessment] = Field(default_factory=list, max_length=5)
    missing_information: list[str] = Field(default_factory=list, max_length=5)
    suggested_next_steps: list[str] = Field(min_length=1, max_length=6)


class DraftCase(DraftCaseArgs):
    status: Literal["pending_human_review"] = "pending_human_review"
    screening_ids: list[str]


# --- run result -------------------------------------------------------------

TraceKind = Literal["model_call", "tool_call", "tool_error", "draft_rejected", "stop"]
ReviewStatus = Literal[
    "drafted",
    "needs_information",
    "incomplete",
    "budget_exhausted",
    "model_unavailable",
    "rate_limited",
]


class TraceEvent(_Strict):
    step: int
    kind: TraceKind
    name: str | None = None
    arguments: dict | str | None = None
    summary: str
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class Usage(_Strict):
    model_calls: int = 0
    failed_model_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    rejected_drafts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: float = 0.0


class ReviewResult(_Strict):
    case_id: str
    status: ReviewStatus
    provider: str
    model: str
    is_mock: bool = Field(description="True when the model is the offline scripted mock.")
    request: ReviewRequest
    draft: DraftCase | None = None
    information_request: RequestInformationArgs | None = None
    message: str = Field(description="Human-readable explanation of how the run ended.")
    retry_after_seconds: float | None = None
    screening_ids: list[str]
    trace: list[TraceEvent]
    usage: Usage
