"""Verify the frontend intake contract and offline accessibility invariants."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import cast

ROOT = Path(__file__).parent
COMPONENT = ROOT / "src" / "components" / "IntakeDashboard.tsx"
FIXTURE = ROOT / "contract_fixture.json"
JsonObject = dict[str, object]


def _json_object(value: object, label: str) -> JsonObject:
    """Narrow decoded JSON to an object with string keys."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AssertionError(f"{label} must be a JSON object with string keys.")
    return cast(JsonObject, value)


def _exact_keys(value: object, expected: set[str], label: str) -> JsonObject:
    """Require a JSON object to contain exactly the documented fields."""
    object_value = _json_object(value, label)
    actual = set(object_value)

    if actual != expected:
        raise AssertionError(
            f"{label} keys must be exactly {sorted(expected)!r}; got {sorted(actual)!r}"
        )

    return object_value


def _assert_verifier_ast_is_strict() -> None:
    """Reject an untyped verifier implementation before checking the TSX contract."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=str(__file__))

    forbidden_type_name = "A" + "ny"
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "typing"
            and any(alias.name == forbidden_type_name for alias in node.names)
        ):
            raise AssertionError(
                "The contract verifier must not import an untyped escape hatch."
            )
        if isinstance(node, ast.Name) and node.id == forbidden_type_name:
            raise AssertionError(
                "The contract verifier must not reference an untyped escape hatch."
            )


def main() -> None:
    """Check the fixture and transport/accessibility source invariants."""
    _assert_verifier_ast_is_strict()
    source = COMPONENT.read_text(encoding="utf-8")
    fixture = _json_object(
        json.loads(FIXTURE.read_text(encoding="utf-8")),
        "Fixture",
    )

    if fixture.get("endpoint") != "/api/ingest":
        raise AssertionError("The fixture endpoint must match FastAPI /api/ingest.")

    request = _exact_keys(fixture.get("request"), {"text"}, "Request")
    text = request.get("text")
    if not isinstance(text, str) or not 5 <= len(text) <= 15000 or not text.strip():
        raise AssertionError("Fixture text must satisfy IngestRequest's 5-15000 bound.")

    response = _exact_keys(
        fixture.get("response"),
        {"status", "data", "latency_ms", "cached"},
        "Response",
    )
    if response.get("status") != "success":
        raise AssertionError("The fixture must represent a successful API response.")

    data = _exact_keys(
        response.get("data"),
        {
            "title",
            "severity_level",
            "category",
            "key_entities",
            "confidence_score",
            "action_items",
            "reasoning_summary",
            "domain_tags",
        },
        "StructuredDispatch",
    )
    title = data.get("title")
    if not isinstance(title, str) or not 1 <= len(title) <= 200 or not title.strip():
        raise AssertionError(
            "StructuredDispatch.title must be a bounded nonblank string."
        )
    if data.get("severity_level") not in {"low", "medium", "high", "critical"}:
        raise AssertionError("StructuredDispatch.severity_level is invalid.")
    category = data.get("category")
    if (
        not isinstance(category, str)
        or not 1 <= len(category) <= 80
        or not category.strip()
    ):
        raise AssertionError(
            "StructuredDispatch.category must be a bounded nonblank string."
        )
    reasoning = data.get("reasoning_summary")
    if (
        not isinstance(reasoning, str)
        or not 1 <= len(reasoning) <= 500
        or not reasoning.strip()
    ):
        raise AssertionError(
            "StructuredDispatch.reasoning_summary must be a bounded nonblank string."
        )
    domain_tags = data.get("domain_tags")
    if (
        not isinstance(domain_tags, list)
        or len(domain_tags) > 10
        or any(
            not isinstance(item, str)
            or not 1 <= len(item) <= 50
            or not item.strip()
            for item in domain_tags
        )
    ):
        raise AssertionError("StructuredDispatch.domain_tags is invalid.")
    for field_name, max_items, max_length in (
        ("key_entities", 50, 200),
        ("action_items", 30, 500),
    ):
        values = data.get(field_name)
        if (
            not isinstance(values, list)
            or len(values) > max_items
            or any(
                not isinstance(item, str)
                or not 1 <= len(item) <= max_length
                or not item.strip()
                for item in values
            )
        ):
            raise AssertionError(f"StructuredDispatch.{field_name} is invalid.")
    confidence = data.get("confidence_score")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or confidence != confidence
        or confidence in (float("inf"), float("-inf"))
        or not 0 <= confidence <= 1
    ):
        raise AssertionError(
            "StructuredDispatch.confidence_score must be finite and between 0 and 1."
        )
    latency = response.get("latency_ms")
    if (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or latency != latency
        or latency in (float("inf"), float("-inf"))
        or latency < 0
    ):
        raise AssertionError("Response.latency_ms must be a finite nonnegative number.")
    if not isinstance(response.get("cached"), bool):
        raise AssertionError("Response.cached must be a boolean.")

    required_source_fragments = (
        "INTAKE_ENGINE_SUPPORT_START",
        "INTAKE_ENGINE_SUPPORT_END",
        "INTAKE_ENGINE_CORE_START",
        "INTAKE_ENGINE_CORE_END",
        "INTAKE_VISUAL_SKIN_START",
        "INTAKE_VISUAL_SKIN_END",
        "export interface IntakeDashboardSkin",
        "export interface IntakeDashboardSkinProps",
        "export interface IntakeDashboardEngineState",
        "export interface IntakeDashboardEngineActions",
        "readonly render:",
        "skin?: IntakeDashboardSkin",
        "EngineLiveRegions",
        "DashboardErrorBoundary",
        "FOCUS_STYLES",
        "ACCESSIBILITY_CSS",
        "const DEFAULT_INTAKE_DASHBOARD_SKIN: IntakeDashboardSkin",
        "skin.render({ id, ids, refs, state, actions })",
        'endpoint = "/api/ingest"',
        "JSON.stringify({ text })",
        '"Content-Type": "application/json"',
        'credentials: "same-origin"',
        'cache: "no-store"',
        "signal: controller.signal",
        'response.headers.get("X-Request-ID")',
        "APIResponse<StructuredDispatch>",
        "readFileAsText(selectedFile, controller.signal)",
        "handleDragOver",
        'role="main"',
        'role="banner"',
        "<nav aria-label=",
        'role="region"',
        'role="tablist"',
        'role="tab"',
        'role="tabpanel"',
        "aria-labelledby=",
        "aria-describedby=",
        "aria-label=",
        'role="status"',
        'aria-live="polite"',
        'role="alert"',
        'aria-live="assertive"',
        'aria-atomic="true"',
        "aria-selected=",
        "aria-controls=",
        'aria-orientation="horizontal"',
        "aria-keyshortcuts=",
        "aria-busy=",
        "aria-invalid=",
        "tabIndex=",
        "hidden={mode !==",
        "noValidate",
        'aria-hidden="true"',
        '"ArrowLeft"',
        '"ArrowRight"',
        '"Home"',
        '"End"',
        '"Enter"',
        '" "',
        '"Escape"',
        "focusSubmitAfterCancelRef",
        ".focus()",
        "@media (forced-colors: active)",
        "@media (prefers-reduced-motion: reduce)",
    )
    for fragment in required_source_fragments:
        if fragment not in source:
            raise AssertionError(
                f"Missing frontend accessibility/contract invariant: {fragment}"
            )

    marker_pairs = (
        ("INTAKE_ENGINE_SUPPORT_START", "INTAKE_ENGINE_SUPPORT_END"),
        ("INTAKE_VISUAL_SKIN_START", "INTAKE_VISUAL_SKIN_END"),
        ("INTAKE_ENGINE_CORE_START", "INTAKE_ENGINE_CORE_END"),
    )
    marker_positions: dict[str, int] = {}
    for start, end in marker_pairs:
        if source.count(start) != 1 or source.count(end) != 1:
            raise AssertionError(
                f"Modular boundary markers must appear exactly once: {start}/{end}"
            )
        start_position = source.index(start)
        end_position = source.index(end)
        if start_position >= end_position:
            raise AssertionError(
                f"Modular boundary markers are out of order: {start}/{end}"
            )
        marker_positions[start] = start_position
        marker_positions[end] = end_position

    ordered_markers = (
        marker_positions["INTAKE_ENGINE_SUPPORT_START"],
        marker_positions["INTAKE_ENGINE_SUPPORT_END"],
        marker_positions["INTAKE_VISUAL_SKIN_START"],
        marker_positions["INTAKE_VISUAL_SKIN_END"],
        marker_positions["INTAKE_ENGINE_CORE_START"],
        marker_positions["INTAKE_ENGINE_CORE_END"],
    )
    if tuple(sorted(ordered_markers)) != ordered_markers:
        raise AssertionError(
            "Engine support, visual skin, and engine core boundaries overlap."
        )

    support_source = source[
        marker_positions["INTAKE_ENGINE_SUPPORT_START"] : marker_positions[
            "INTAKE_ENGINE_SUPPORT_END"
        ]
    ]
    visual_source = source[
        marker_positions["INTAKE_VISUAL_SKIN_START"] : marker_positions[
            "INTAKE_VISUAL_SKIN_END"
        ]
    ]
    engine_source = source[
        marker_positions["INTAKE_ENGINE_CORE_START"] : marker_positions[
            "INTAKE_ENGINE_CORE_END"
        ]
    ]

    for fragment in (
        "validateText",
        "validateFile",
        "parseIntakeResponse",
        "createRequestBody",
        "readFileAsText",
        "FOCUS_STYLES",
        "ACCESSIBILITY_CSS",
        "@media (forced-colors: active)",
        "@media (prefers-reduced-motion: reduce)",
    ):
        if fragment not in support_source:
            raise AssertionError(
                "Engine support boundary is missing validation/transport "
                f"helper: {fragment}"
            )

    for fragment in (
        "useState<",
        "useRef<",
        "handleTabKeyDown",
        "handleFormKeyDown",
        "liveRegions",
        "fetch(endpoint",
        "controllerRef",
        "skin.render({ id, ids, refs, state, actions })",
        "EngineLiveRegions",
        "DashboardErrorBoundary",
        'role="status"',
        'role="alert"',
        'aria-live="polite"',
        'aria-live="assertive"',
        'aria-atomic="true"',
    ):
        if fragment not in engine_source:
            raise AssertionError(
                f"Engine core boundary is missing runtime ownership anchor: {fragment}"
            )

    for fragment in (
        "DEFAULT_INTAKE_DASHBOARD_SKIN",
        "ACCESSIBILITY_CSS",
        "onDragEnter={handleDragEnter}",
        "onDrop={handleDrop}",
        "SEVERITY_STYLES",
        "<JsonNode",
        'aria-hidden="true"',
    ):
        if fragment not in visual_source:
            raise AssertionError(
                f"Visual skin boundary is missing presentation anchor: {fragment}"
            )

    for forbidden in (
        "fetch(",
        "controllerRef",
        "AbortController",
        "credentials:",
        'role="status"',
        'role="alert"',
        "aria-live=",
        "aria-atomic=",
    ):
        if forbidden in visual_source:
            raise AssertionError(
                "Visual skin must not own engine transport state or "
                f"behavior: {forbidden}"
            )

    skin_props_match = re.search(
        r"export interface IntakeDashboardSkinProps\s*\{(?P<body>.*?)\n\}",
        source,
        flags=re.DOTALL,
    )
    if skin_props_match is None:
        raise AssertionError("IntakeDashboardSkinProps must remain explicitly typed.")
    skin_props_source = skin_props_match.group("body")
    if re.search(r"\b(?:endpoint|controllerRef|X-API-Key)\b", skin_props_source):
        raise AssertionError(
            "The visual skin props must not expose endpoint, request-"
            "controller, or API-key state."
        )
    if "readonly" not in skin_props_source:
        raise AssertionError(
            "Visual skin props must expose an immutable readonly contract."
        )

    # Strict TypeScript boundary: do not reintroduce an untyped escape hatch.
    if re.search(r"\bas\s+any\b|:\s*any\b|<\s*any\b", source):
        raise AssertionError(
            "The frontend skin/engine contract must not use TypeScript any."
        )

    # Browser code may use the server-side proxy, but it must never expose a key.
    if re.search(r"(?m)^\s*[\"']X-API-Key[\"']\s*:", source):
        raise AssertionError("The browser component must not send X-API-Key.")
    if re.search(r"\b(?:NEXT_PUBLIC|VITE|PUBLIC)[A-Z0-9_]*API[_-]?KEY\b", source):
        raise AssertionError(
            "The browser component must not reference a public API key."
        )
    if re.search(r"(?:process\.env|import\.meta\.env)[^\n]*API[_-]?KEY", source):
        raise AssertionError(
            "The browser component must not read an API key from browser "
            "environment variables."
        )

    sys.stdout.write(
        "Frontend intake contract and accessibility invariants verified "
        "(offline; no network requested).\n"
    )


if __name__ == "__main__":
    main()
