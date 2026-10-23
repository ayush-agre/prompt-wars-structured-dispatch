"""Focused validation checks for public HTTP models and environment settings."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.config import Settings
from app.models import (
    AnalyseRequest,
    APIResponse,
    ErrorDetails,
    HealthState,
    IngestRequest,
    StructuredDispatch,
)


def _dispatch_data() -> dict[str, object]:
    """Return a fresh valid dispatch payload for boundary checks."""
    return {
        "title": "Alarm at station",
        "severity_level": "high",
        "category": "incident",
        "key_entities": ["Station 4"],
        "confidence_score": 0.9,
        "action_items": ["Contact responders"],
        "reasoning_summary": "Alarm indicates incident requiring responders.",
        "domain_tags": ["operations"],
    }


def _required_environment() -> dict[str, str]:
    """Return synthetic required settings without consulting the user's environment."""
    return {
        "DISPATCH_GEMINI_API_KEY": "test-gemini-key",
        "DISPATCH_INGEST_API_KEY": "t" * 32,
        "DISPATCH_GEMINI_MODEL": "gemini-2.5-flash",
        "DISPATCH_PRODUCTION_ORIGIN": "https://dispatch.example.com",
    }


def _settings(extra_environment: dict[str, str] | None = None) -> Settings:
    """Construct settings from an isolated synthetic environment."""
    environment = _required_environment()
    environment.update(extra_environment or {})
    with patch.dict(os.environ, environment, clear=True):
        return Settings()


class HTTPModelTests(unittest.TestCase):
    """Check immutable, bounded, noncoercing public schemas."""

    def test_ingest_preserves_content_and_is_frozen(self) -> None:
        """Accepted text retains its whitespace and cannot be reassigned."""
        text = "  Alarm\n at station  "
        request = IngestRequest(text=text)
        self.assertEqual(request.text, text)
        self.assertEqual(IngestRequest(text="a" * 15000).text, "a" * 15000)
        with self.assertRaises(ValidationError):
            request.__setattr__("text", "Changed message")

    def test_ingest_rejects_blank_oversize_and_nonstring_content(self) -> None:
        """Blank, out-of-bounds, coerced, and unknown request values fail."""
        for value in ("abcd", " " * 5, "\t\n\r  ", "a" * 15001, b"alarm", 12345, None):
            with (
                self.subTest(value_type=type(value).__name__),
                self.assertRaises(ValidationError),
            ):
                IngestRequest.model_validate({"text": value})
        with self.assertRaises(ValidationError):
            IngestRequest.model_validate({"text": "alarm", "unexpected": True})

    def test_dispatch_rejects_invalid_fields_and_collection_items(self) -> None:
        """Dispatch constraints apply to both outer collections and each item."""
        invalid_fields: list[tuple[str, object]] = [
            ("title", " " * 5),
            ("title", "t" * 201),
            ("title", 123),
            ("category", "\t"),
            ("category", "c" * 81),
            ("severity_level", "urgent"),
            ("key_entities", ["e"] * 51),
            ("key_entities", ["e" * 201]),
            ("key_entities", [" "]),
            ("key_entities", [123]),
            ("key_entities", ("entity",)),
            ("confidence_score", "0.9"),
            ("confidence_score", True),
            ("confidence_score", -0.01),
            ("confidence_score", 1.01),
            ("confidence_score", float("nan")),
            ("confidence_score", float("inf")),
            ("action_items", ["a"] * 31),
            ("action_items", ["a" * 501]),
            ("action_items", ["\n"]),
            ("action_items", [False]),
            ("action_items", ("action",)),
        ]
        for field, value in invalid_fields:
            with self.subTest(field=field, value_type=type(value).__name__):
                payload = _dispatch_data()
                payload[field] = value
                with self.assertRaises(ValidationError):
                    StructuredDispatch.model_validate(payload)

    def test_dispatch_accepts_inclusive_limits_and_empty_collections(self) -> None:
        """Valid boundary values and empty generated lists remain usable."""
        result = StructuredDispatch(
            title="t" * 200,
            severity_level="critical",
            category="c" * 80,
            key_entities=["e" * 200] * 50,
            confidence_score=1.0,
            action_items=["a" * 500] * 30,
            reasoning_summary="r" * 500,
            domain_tags=["d" * 50] * 10,
        )
        self.assertEqual(len(result.key_entities), 50)
        self.assertEqual(len(result.action_items), 30)
        self.assertEqual(len(result.domain_tags), 10)
        payload = _dispatch_data()
        payload.update(
            key_entities=[], action_items=[], domain_tags=[], confidence_score=0.0
        )
        dispatch = StructuredDispatch.model_validate(payload)
        self.assertEqual(dispatch.action_items, [])
        self.assertEqual(dispatch.domain_tags, [])

    def test_response_is_typed_and_metadata_is_strict(self) -> None:
        """Generic payloads, finite timings, and boolean cache flags are validated."""
        data = HealthState(state="running", uptime_seconds=0.0)
        response = APIResponse[HealthState](
            status="success", data=data, latency_ms=0.0, cached=False
        )
        self.assertEqual(response.data, data)
        payload = response.model_dump()
        invalid_fields: list[tuple[str, object]] = [
            ("status", "ok"),
            ("data", {"state": "stopped", "uptime_seconds": 0.0}),
            ("latency_ms", -1.0),
            ("latency_ms", "1.0"),
            ("latency_ms", float("inf")),
            ("cached", 1),
            ("cached", "false"),
        ]
        for field, value in invalid_fields:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                APIResponse[HealthState].model_validate({**payload, field: value})
        for uptime in (-1.0, float("nan"), "1.0", True):
            with self.assertRaises(ValidationError):
                HealthState.model_validate(
                    {"state": "running", "uptime_seconds": uptime}
                )

    def test_error_details_and_error_display_do_not_coerce_or_echo_input(self) -> None:
        """Keep error fields strict and omit input from displayed validation errors."""
        error = ErrorDetails(
            code="invalid_request", message="Invalid input", request_id="r1"
        )
        self.assertEqual(
            APIResponse[ErrorDetails](
                status="error", data=error, latency_ms=0.0, cached=False
            ).data,
            error,
        )
        for field in ("code", "message", "request_id"):
            with self.assertRaises(ValidationError):
                ErrorDetails.model_validate({**error.model_dump(), field: 123})
        rejected = "private-input-marker" * 1000
        with self.assertRaises(ValidationError) as caught:
            IngestRequest(text=rejected)
        self.assertNotIn("private-input-marker", str(caught.exception))


class ConfigurationTests(unittest.TestCase):
    """Check environment parsing, required values, and endpoint validation."""

    def test_defaults_cors_and_secret_repr(self) -> None:
        """Default limits and fresh CORS lists are available without secret reprs."""
        settings = _settings()
        self.assertEqual(
            settings.redis_url.get_secret_value(), "redis://localhost:6379/0"
        )
        self.assertEqual(settings.upstream_timeout_seconds, 30.0)
        self.assertEqual(settings.cache_timeout_seconds, 1.0)
        self.assertEqual(settings.max_concurrent_generations, 8)
        self.assertEqual(settings.store_max_entries, 1000)
        self.assertEqual(settings.max_request_bytes, 196608)
        self.assertEqual(settings.body_timeout_seconds, 10.0)
        self.assertEqual(settings.log_level, "INFO")
        self.assertIsNone(settings.cloud_project_id)
        self.assertEqual(
            settings.cors_origins,
            [
                "https://dispatch.example.com",
                "http://localhost:3000",
                "http://127.0.0.1:3000",
            ],
        )
        settings.cors_origins.append("https://not-persisted.example.com")
        self.assertEqual(len(settings.cors_origins), 3)
        for secret in (
            settings.gemini_api_key,
            settings.ingest_api_key,
            settings.redis_url,
        ):
            self.assertNotIn(secret.get_secret_value(), repr(settings))

    def test_numeric_environment_parsing_and_ignored_extra(self) -> None:
        """Environment strings parse as numbers and accept their inclusive maxima."""
        settings = _settings(
            {
                "DISPATCH_UPSTREAM_TIMEOUT_SECONDS": "120",
                "DISPATCH_CACHE_TIMEOUT_SECONDS": "5",
                "DISPATCH_MAX_CONCURRENT_GENERATIONS": "128",
                "DISPATCH_STORE_MAX_ENTRIES": "10000",
                "DISPATCH_MAX_REQUEST_BYTES": "262144",
                "DISPATCH_BODY_TIMEOUT_SECONDS": "30",
                "DISPATCH_LOG_LEVEL": "DEBUG",
                "DISPATCH_UNKNOWN_SETTING": "ignored",
            }
        )
        self.assertIs(type(settings.upstream_timeout_seconds), float)
        self.assertEqual(settings.upstream_timeout_seconds, 120.0)
        self.assertEqual(settings.cache_timeout_seconds, 5.0)
        self.assertIs(type(settings.max_concurrent_generations), int)
        self.assertEqual(settings.max_concurrent_generations, 128)
        self.assertEqual(settings.store_max_entries, 10000)
        self.assertEqual(settings.max_request_bytes, 262144)
        self.assertEqual(settings.body_timeout_seconds, 30.0)
        self.assertEqual(settings.log_level, "DEBUG")

    def test_required_environment_values_have_no_fallbacks(self) -> None:
        """Both credentials, the model ID, and the production origin are required."""
        for missing in _required_environment():
            with self.subTest(missing=missing):
                environment = _required_environment()
                del environment[missing]
                with (
                    patch.dict(os.environ, environment, clear=True),
                    self.assertRaises(ValidationError),
                ):
                    Settings()

    def test_numeric_environment_bounds_and_finiteness(self) -> None:
        """Nonpositive, excessive, nonfinite, and malformed limits are rejected."""
        invalid_settings = {
            "UPSTREAM_TIMEOUT_SECONDS": ("0", "120.1", "inf", "nan", "bad"),
            "CACHE_TIMEOUT_SECONDS": ("0", "5.1", "inf", "nan"),
            "BODY_TIMEOUT_SECONDS": ("0", "30.1", "inf", "nan"),
            "MAX_CONCURRENT_GENERATIONS": ("0", "129", "1.5", "true"),
            "STORE_MAX_ENTRIES": ("0", "10001"),
            "MAX_REQUEST_BYTES": ("95999", "262145"),
            "LOG_LEVEL": ("TRACE", "info"),
        }
        for name, values in invalid_settings.items():
            for value in values:
                with (
                    self.subTest(name=name, value=value),
                    self.assertRaises(ValidationError),
                ):
                    _settings({f"DISPATCH_{name}": value})

    def test_credentials_and_model_names(self) -> None:
        """Ingest credentials are ASCII header-safe and models cannot be paths."""
        for value in ("a" * 31, " " * 32, "a" * 31 + "é", "a" * 31 + "\n"):
            with self.assertRaises(ValidationError):
                _settings({"DISPATCH_INGEST_API_KEY": value})
        for value in ("", " \t "):
            with self.assertRaises(ValidationError):
                _settings({"DISPATCH_GEMINI_API_KEY": value})
        for value in (
            "",
            "../model",
            "models/gemini",
            "foo\\bar",
            "https://model",
            "a b",
        ):
            with self.subTest(model=value), self.assertRaises(ValidationError):
                _settings({"DISPATCH_GEMINI_MODEL": value})

    def test_production_origin_accepts_https_origins_with_optional_ports(self) -> None:
        """HTTPS DNS, IPv4, and IPv6 origins may include a valid explicit port."""
        for value in (
            "https://example.com",
            "https://example.com:8443",
            "https://example.com.",
            "https://127.0.0.1:443",
            "https://[::1]:8443",
        ):
            with self.subTest(origin=value):
                self.assertEqual(
                    _settings({"DISPATCH_PRODUCTION_ORIGIN": value}).production_origin,
                    value,
                )

    def test_production_origin_rejects_nonorigins(self) -> None:
        """Reject paths, credentials, queries, wildcards, and malformed authorities."""
        for value in (
            "http://example.com",
            "https://example.com/",
            "https://example.com/path",
            "https://*.example.com",
            "https://user:password@example.com",
            "https://example.com?",
            "https://example.com#",
            "https://example.com?x=1",
            "https://example.com#fragment",
            "https://example.com:0",
            "https://example.com:65536",
            "https://example.com:",
            "https://example.com:abc",
            "https://example.com\\evil",
            " https://example.com",
            "https://999.999.999.999",
            "https://example-.com",
            "https://bad..example",
            "https://example.com\n",
            "https://[::1]ignored",
            "https://",
        ):
            with self.subTest(origin=value), self.assertRaises(ValidationError):
                _settings({"DISPATCH_PRODUCTION_ORIGIN": value})

    def test_redis_url_allows_only_network_redis_and_integer_databases(self) -> None:
        """Redis URLs support credentials and TLS but reject query or path ambiguity."""
        for value in (
            "redis://localhost:6379/0",
            "rediss://user:password@cache.example.com:6380/12",
            "redis://[::1]:6379/2",
        ):
            with self.subTest(valid_redis=value):
                redis_url = _settings({"DISPATCH_REDIS_URL": value}).redis_url
                self.assertEqual(redis_url.get_secret_value(), value)
        for value in (
            "https://localhost/0",
            "unix:///tmp/redis.sock",
            "redis://localhost/-1",
            "redis://localhost/not-a-db",
            "redis://localhost/1/2",
            "redis://localhost",
            "redis://localhost/0?",
            "redis://localhost/0#",
            "redis://localhost/0?x=1",
            "redis://localhost:65536/0",
            "redis://localhost:/0",
            "redis:///0",
        ):
            with self.subTest(invalid_redis=value), self.assertRaises(ValidationError):
                _settings({"DISPATCH_REDIS_URL": value})

    def test_cloud_project_id_is_optional_and_validated(self) -> None:
        """Supplied project IDs follow Google Cloud's length and character rules."""
        settings = _settings({"DISPATCH_CLOUD_PROJECT_ID": "dispatch-prod-123"})
        self.assertEqual(settings.cloud_project_id, "dispatch-prod-123")
        for value in ("", "short", "Uppercase-id", "1project", "project-", "a" * 31):
            with self.subTest(project=value), self.assertRaises(ValidationError):
                _settings({"DISPATCH_CLOUD_PROJECT_ID": value})

    def test_supabase_url_is_optional_and_requires_https(self) -> None:
        """Supplied Supabase URLs must use HTTPS; omitting the setting is valid."""
        settings = _settings({"DISPATCH_SUPABASE_URL": "https://proj.supabase.co"})
        self.assertEqual(settings.supabase_url, "https://proj.supabase.co")
        self.assertIsNone(_settings().supabase_url)
        for value in ("http://proj.supabase.co", "redis://proj.supabase.co"):
            with self.subTest(url=value), self.assertRaises(ValidationError):
                _settings({"DISPATCH_SUPABASE_URL": value})

    def test_supabase_key_is_optional_and_rejects_blank(self) -> None:
        """Supplied Supabase keys must be non-blank; omitting is valid."""
        settings = _settings({"DISPATCH_SUPABASE_KEY": "valid-supabase-key-value"})
        self.assertIsNotNone(settings.supabase_key)
        self.assertIsNone(_settings().supabase_key)
        for value in ("", "   ", "\t\n"):
            with self.subTest(key=repr(value)), self.assertRaises(ValidationError):
                _settings({"DISPATCH_SUPABASE_KEY": value})


class AnalyseRequestTests(unittest.TestCase):
    """Check multi-param analyse request validation."""

    def test_accepts_text_only_with_defaults(self) -> None:
        """All optional fields default safely without requiring caller input."""
        req = AnalyseRequest(text="Pump failure at station 7 needs response.")
        self.assertEqual(req.domain, "general")
        self.assertEqual(req.urgency_hint, "routine")
        self.assertIsNone(req.language_hint)

    def test_accepts_full_contextual_payload(self) -> None:
        """All optional fields are accepted when supplied with valid values."""
        req = AnalyseRequest(
            text="Server down in DC-West; critical outage.",
            domain="operations",
            urgency_hint="critical",
            language_hint="en",
        )
        self.assertEqual(req.domain, "operations")
        self.assertEqual(req.urgency_hint, "critical")
        self.assertEqual(req.language_hint, "en")

    def test_rejects_invalid_domain_and_urgency(self) -> None:
        """Unknown literal values for domain and urgency_hint are rejected."""
        base = {"text": "Valid intake text here for testing purposes."}
        for field, value in (
            ("domain", "logistics"),
            ("domain", "OPERATIONS"),
            ("urgency_hint", "high"),
            ("urgency_hint", 1),
        ):
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(ValidationError),
            ):
                AnalyseRequest.model_validate({**base, field: value})

    def test_rejects_invalid_language_hint_patterns(self) -> None:
        """Language hints not matching BCP-47 pattern or bounds are rejected."""
        base = {"text": "Valid intake text here for testing purposes."}
        for lang in ("x", "EN", "en-gb", "eng-US-extra", "1234", "toolong123"):
            with self.subTest(lang=lang), self.assertRaises(ValidationError):
                AnalyseRequest.model_validate({**base, "language_hint": lang})

    def test_accepts_valid_language_hints(self) -> None:
        """BCP-47 two-letter, three-letter, and region-subtag forms are accepted."""
        base = {"text": "Valid intake text here for testing purposes."}
        for lang in ("en", "fr", "zho", "zh-CN", "pt-BR"):
            with self.subTest(lang=lang):
                req = AnalyseRequest.model_validate({**base, "language_hint": lang})
                self.assertEqual(req.language_hint, lang)

    def test_rejects_blank_and_short_text(self) -> None:
        """Text validation mirrors IngestRequest: blank and short texts fail."""
        for text in ("abcd", "  \t  ", ""):
            with self.subTest(text=repr(text)), self.assertRaises(ValidationError):
                AnalyseRequest.model_validate({"text": text})

    def test_is_frozen_and_rejects_unknown_fields(self) -> None:
        """AnalyseRequest is immutable and forbids extra keys."""
        req = AnalyseRequest(text="Valid intake text here for testing purposes.")
        with self.assertRaises(ValidationError):
            req.__setattr__("text", "Changed")
        with self.assertRaises(ValidationError):
            AnalyseRequest.model_validate(
                {"text": "Valid intake text here for testing purposes.", "extra": True}
            )


class StructuredDispatchNewFieldTests(unittest.TestCase):
    """Check the reasoning_summary and domain_tags fields added for hackathon scope."""

    def test_rejects_blank_reasoning_summary(self) -> None:
        """reasoning_summary must be nonblank and within 500 characters."""
        for value in (" " * 5, "", "r" * 501, 42):
            payload = _dispatch_data()
            payload["reasoning_summary"] = value
            with (
                self.subTest(value_type=type(value).__name__),
                self.assertRaises(ValidationError),
            ):
                StructuredDispatch.model_validate(payload)

    def test_rejects_invalid_domain_tags(self) -> None:
        """domain_tags items must be nonblank, max 50 chars; list max 10 items."""
        for tags in (
            ["d"] * 11,
            ["d" * 51],
            [" "],
            [123],
            ("tag",),
        ):
            payload = _dispatch_data()
            payload["domain_tags"] = tags
            with self.subTest(tags=tags), self.assertRaises(ValidationError):
                StructuredDispatch.model_validate(payload)

    def test_accepts_empty_domain_tags(self) -> None:
        """An empty domain_tags list is valid."""
        payload = _dispatch_data()
        payload["domain_tags"] = []
        dispatch = StructuredDispatch.model_validate(payload)
        self.assertEqual(dispatch.domain_tags, [])
