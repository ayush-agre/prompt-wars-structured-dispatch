"""Strict, immutable HTTP request and response schemas for the dispatch API."""

from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictStr,
    field_validator,
)

#: Supported analysis domains for the multi-param /api/analyse endpoint.
AnalysisDomain = Literal["operations", "security", "finance", "health", "general"]

#: Urgency hint supplied by the caller to bias Gemini's severity assessment.
UrgencyHint = Literal["routine", "urgent", "critical"]


def _require_nonblank(value: str) -> str:
    """Reject whitespace-only text while returning valid text unchanged."""
    if not value.strip():
        raise ValueError("Text must contain at least one non-whitespace character.")
    return value


_TitleText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=200),
    AfterValidator(_require_nonblank),
]
_CategoryText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=80),
    AfterValidator(_require_nonblank),
]
_EntityText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=200),
    AfterValidator(_require_nonblank),
]
_ActionText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=500),
    AfterValidator(_require_nonblank),
]
_DomainTagText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=50),
    AfterValidator(_require_nonblank),
]
_LanguageHintText = Annotated[
    StrictStr,
    Field(min_length=2, max_length=10, pattern=r"^[a-z]{2,3}(-[A-Z]{2,4})?$"),
]
_ReasoningSummaryText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=500),
    AfterValidator(_require_nonblank),
]
_T = TypeVar("_T")


class _StrictModel(BaseModel):
    """Apply shared strict validation and error-display rules to HTTP models."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )


class IngestRequest(_StrictModel):
    """Accept bounded raw dispatch text without normalizing its contents."""

    text: StrictStr = Field(
        min_length=5,
        max_length=15000,
        description=(
            "Raw dispatch text containing 5 to 15000 characters and at least one "
            "non-whitespace character; accepted text is preserved exactly."
        ),
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        """Reject blank requests without stripping any accepted raw content."""
        return _require_nonblank(value)


class AnalyseRequest(_StrictModel):
    """Accept bounded dispatch text with optional domain and urgency context."""

    text: StrictStr = Field(
        min_length=5,
        max_length=15000,
        description=(
            "Raw dispatch text containing 5 to 15000 characters and at least one "
            "non-whitespace character; accepted text is preserved exactly."
        ),
    )
    domain: AnalysisDomain = Field(
        default="general",
        description=(
            "Optional analysis domain used to specialize Gemini's extraction: "
            "operations, security, finance, health, or general."
        ),
    )
    urgency_hint: UrgencyHint = Field(
        default="routine",
        description=(
            "Optional caller-supplied urgency hint to bias severity assessment: "
            "routine, urgent, or critical."
        ),
    )
    language_hint: _LanguageHintText | None = Field(
        default=None,
        description=(
            "Optional BCP-47 language tag (e.g. 'en', 'fr', 'zh-CN') indicating "
            "the source language of the dispatch text."
        ),
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        """Reject blank requests without stripping any accepted raw content."""
        return _require_nonblank(value)


class StructuredDispatch(_StrictModel):
    """Represent the bounded structured result of a dispatch generation."""

    title: _TitleText = Field(
        description="Nonblank dispatch title containing at most 200 characters."
    )
    severity_level: Literal["low", "medium", "high", "critical"] = Field(
        min_length=3,
        max_length=8,
        description="Dispatch severity: low, medium, high, or critical.",
    )
    category: _CategoryText = Field(
        description="Nonblank dispatch category containing at most 80 characters."
    )
    key_entities: list[_EntityText] = Field(
        max_length=50,
        description=(
            "At most 50 key entities, each a nonblank string containing at most "
            "200 characters; an empty list is permitted."
        ),
    )
    confidence_score: StrictFloat = Field(
        ge=0.0,
        le=1.0,
        description="Finite numeric confidence score from 0.0 to 1.0 inclusive.",
    )
    action_items: list[_ActionText] = Field(
        max_length=30,
        description=(
            "At most 30 action items, each a nonblank string containing at most "
            "500 characters; an empty list is permitted."
        ),
    )
    reasoning_summary: _ReasoningSummaryText = Field(
        description=(
            "Concise nonblank summary of the model's reasoning process, "
            "containing at most 500 characters."
        ),
    )
    domain_tags: list[_DomainTagText] = Field(
        max_length=10,
        description=(
            "At most 10 domain classification tags, each a nonblank string "
            "containing at most 50 characters; an empty list is permitted."
        ),
    )


class APIResponse(_StrictModel, Generic[_T]):
    """Wrap a typed API payload with outcome, timing, and cache metadata."""

    status: Literal["success", "error"] = Field(
        min_length=5,
        max_length=7,
        description="Whether the API operation succeeded or returned an error.",
    )
    data: _T = Field(description="Typed success payload or sanitized error details.")
    latency_ms: StrictFloat = Field(
        ge=0.0,
        description=(
            "Finite, nonnegative time spent handling the request in milliseconds."
        ),
    )
    cached: StrictBool = Field(
        description="Whether the response was served from a previously cached result."
    )


class ErrorDetails(_StrictModel):
    """Describe a sanitized API error without embedding rejected request values."""

    code: StrictStr = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
        description="Stable machine-readable API error code.",
    )
    message: StrictStr = Field(
        min_length=1,
        max_length=500,
        description="Safe human-readable error message without raw validation input.",
    )
    request_id: StrictStr = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Request identifier for correlating the error with server logs.",
    )


class HealthState(_StrictModel):
    """Report a running API process and its nonnegative uptime."""

    state: Literal["running"] = Field(
        min_length=7,
        max_length=7,
        description="Current API process state: running.",
    )
    uptime_seconds: StrictFloat = Field(
        ge=0.0,
        description="Finite, nonnegative process uptime measured in seconds.",
    )
