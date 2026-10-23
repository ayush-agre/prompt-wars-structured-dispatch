"""Environment-backed dispatch settings with explicit validation and secret handling."""

from __future__ import annotations

import re
from ipaddress import ip_address
from typing import Literal
from urllib.parse import SplitResult, urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _validate_hostname(hostname: str) -> None:
    """Require an IP address or a valid DNS hostname without URL metacharacters."""
    if not hostname or "%" in hostname or "*" in hostname:
        raise ValueError("URL must contain a valid hostname.")
    try:
        ip_address(hostname)
    except ValueError:
        if ":" in hostname or re.fullmatch(r"[0-9.]+", hostname):
            raise ValueError("URL must contain a valid IP address.") from None
    else:
        return

    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("URL must contain a valid hostname.") from None
    if ascii_hostname.endswith("."):
        ascii_hostname = ascii_hostname[:-1]
    labels = ascii_hostname.split(".")
    if len(ascii_hostname) > 253 or any(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) is None
        for label in labels
    ):
        raise ValueError("URL must contain a valid hostname.")


def _parse_service_url(value: str) -> SplitResult:
    """Parse a network URL while rejecting malformed authority and unsafe delimiters."""
    if (
        not value
        or any(
            character.isspace() or ord(character) < 33 or ord(character) == 127
            for character in value
        )
        or any(character in value for character in ("\\", "?", "#"))
    ):
        raise ValueError(
            "URL cannot contain whitespace, backslashes, queries, or fragments."
        )
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("URL must have a valid hostname and optional port.") from None
    if hostname is None:
        raise ValueError("URL must contain a hostname.")
    _validate_hostname(hostname)

    authority = parsed.netloc.rsplit("@", maxsplit=1)[-1]
    if authority.startswith("["):
        closing_bracket = authority.find("]")
        port_suffix = authority[closing_bracket + 1 :]
    else:
        _, separator, raw_port = authority.partition(":")
        port_suffix = f":{raw_port}" if separator else ""
    if port_suffix and re.fullmatch(r":[0-9]+", port_suffix) is None:
        raise ValueError("URL port must be a decimal integer.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("URL port must be between 1 and 65535.")
    return parsed


class Settings(BaseSettings):
    """Read typed DISPATCH_ settings without loading any dotenv file at import time."""

    model_config = SettingsConfigDict(
        env_prefix="DISPATCH_",
        env_file=None,
        extra="ignore",
        validate_default=True,
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )

    gemini_api_key: SecretStr = Field(
        min_length=1,
        repr=False,
        description="Required secret API key used to authenticate with Gemini.",
    )
    ingest_api_key: SecretStr = Field(
        min_length=32,
        max_length=512,
        repr=False,
        description=(
            "Required ingest key containing at least 32 printable ASCII bytes "
            "without whitespace."
        ),
    )
    gemini_model: str = Field(
        description="Required simple Gemini model identifier without path separators."
    )
    production_origin: str = Field(
        description=(
            "Required HTTPS origin with an optional port and no path, credentials, "
            "or trailing slash."
        )
    )
    redis_url: SecretStr = Field(
        default=SecretStr("redis://localhost:6379/0"),
        repr=False,
        description=(
            "Secret redis or rediss URL ending in a nonnegative database index "
            "and without a query or fragment."
        ),
    )
    upstream_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=120.0,
        description=(
            "Finite upstream generation timeout in seconds, greater than zero "
            "and at most 120."
        ),
    )
    cache_timeout_seconds: float = Field(
        default=1.0,
        gt=0.0,
        le=5.0,
        description=(
            "Finite cache operation timeout in seconds, greater than zero "
            "and at most five."
        ),
    )
    max_concurrent_generations: int = Field(
        default=8,
        ge=1,
        le=128,
        description="Maximum concurrent generation operations, from 1 to 128.",
    )
    store_max_entries: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Maximum entries in the bounded local store, from 1 to 10000.",
    )
    max_request_bytes: int = Field(
        default=196608,
        ge=196608,
        le=262144,
        description="Maximum request body size in bytes, from 196608 to 262144.",
    )
    body_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=30.0,
        description=(
            "Finite request body read timeout in seconds, greater than zero "
            "and at most 30."
        ),
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Application logging threshold: DEBUG, INFO, WARNING, or ERROR.",
    )
    cloud_project_id: str | None = Field(
        default=None,
        description=(
            "Optional Google Cloud project ID, consisting of 6 to 30 lowercase "
            "letters, digits, or hyphens."
        ),
    )
    supabase_url: str | None = Field(
        default=None,
        description=(
            "Optional Supabase project HTTPS URL for row-level-security-protected "
            "vector storage; omit to disable Supabase integration."
        ),
    )
    supabase_key: SecretStr | None = Field(
        default=None,
        repr=False,
        description=(
            "Optional Supabase service-role or anon key used only when "
            "supabase_url is also supplied."
        ),
    )

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def validate_gemini_api_key(cls, value: object) -> object:
        """Reject blank raw credentials before Pydantic wraps them as secrets."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("Gemini API key must not be blank.")
        return value

    @field_validator("ingest_api_key", mode="before")
    @classmethod
    def validate_ingest_api_key(cls, value: object) -> object:
        """Require header-safe ASCII bytes before Pydantic wraps the secret."""
        if isinstance(value, str) and (
            len(value) < 32 or any(not "!" <= character <= "~" for character in value)
        ):
            raise ValueError(
                "Ingest API key must contain at least 32 printable ASCII bytes "
                "without whitespace."
            )
        return value

    @field_validator("gemini_model")
    @classmethod
    def validate_gemini_model(cls, value: str) -> str:
        """Require a simple model name rather than a URL or resource path."""
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None:
            raise ValueError(
                "Gemini model must start with a letter or digit and contain only "
                "letters, digits, dots, underscores, or hyphens."
            )
        return value

    @field_validator("production_origin")
    @classmethod
    def validate_production_origin(cls, value: str) -> str:
        """Require a concrete HTTPS origin while preserving its configured spelling."""
        parsed = _parse_service_url(value)
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or "*" in value
        ):
            raise ValueError(
                "Production origin must use HTTPS without credentials, a path, "
                "a trailing slash, or wildcards."
            )
        return value

    @field_validator("redis_url", mode="before")
    @classmethod
    def validate_redis_url(cls, value: object) -> object:
        """Validate raw Redis URLs before Pydantic wraps them as secrets."""
        if isinstance(value, str):
            parsed = _parse_service_url(value)
            if parsed.scheme not in {"redis", "rediss"}:
                raise ValueError("Redis URL scheme must be redis or rediss.")
            if re.fullmatch(r"/[0-9]+", parsed.path) is None:
                raise ValueError(
                    "Redis URL path must be a nonnegative integer database index."
                )
        return value

    @field_validator("cloud_project_id")
    @classmethod
    def validate_cloud_project_id(cls, value: str | None) -> str | None:
        """Validate supplied Google Cloud project IDs without inventing a default."""
        if (
            value is not None
            and re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", value) is None
        ):
            raise ValueError(
                "Cloud project ID must contain 6 to 30 lowercase letters, digits, or "
                "hyphens, start with a letter, and end with a letter or digit."
            )
        return value

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, value: str | None) -> str | None:
        """Require an HTTPS Supabase URL when supplied, accepting the empty default."""
        if value is not None:
            parsed = _parse_service_url(value)
            if parsed.scheme != "https":
                raise ValueError("Supabase URL must use HTTPS.")
        return value

    @field_validator("supabase_key", mode="before")
    @classmethod
    def validate_supabase_key(cls, value: object) -> object:
        """Reject blank Supabase keys before Pydantic wraps them as secrets."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("Supabase key must not be blank.")
        return value

    @property
    def cors_origins(self) -> list[str]:
        """Return the production origin and both supported local frontend origins."""
        return [
            self.production_origin,
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
