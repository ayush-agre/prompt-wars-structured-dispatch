# Final source templates
Generated from the verified workspace sources. These blocks are complete and contain no placeholders.
## `backend/pyproject.toml`

```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "structured-dispatch-api"
version = "1.0.0"
description = "An authenticated, asynchronous Gemini structured-dispatch service."
readme = "README.md"
requires-python = ">=3.11,<3.12"
dependencies = [
    "anyio>=4.8,<5",
    "fastapi>=0.115,<1",
    "google-api-core>=2.24,<3",
    "google-auth>=2.38,<3",
    "google-generativeai==0.8.6",
    "limits>=5.8,<6",
    "pydantic>=2.10,<3",
    "pydantic-settings>=2.7,<3",
    "redis>=5.2.1,<6",
    "slowapi==0.1.9",
    "starlette>=0.47,<2",
    "uvicorn[standard]>=0.34,<1",
]

[dependency-groups]
dev = [
    "bandit>=1.8,<2",
    "httpx>=0.28,<1",
    "mypy>=1.15,<2",
    "pip-audit>=2.9,<3",
    "pytest>=8.3,<10",
    "pytest-asyncio>=0.25,<2",
    "pytest-cov>=6,<8",
    "ruff>=0.11,<1",
    "types-redis>=4.6,<5",
]

[tool.hatch.build.targets.wheel]
packages = ["app"]
exclude = [
    "/.venv",
    "/venv",
    "/*venv*",
    "/dist",
    "/.mypy_cache",
    "/.pytest_cache",
    "/.ruff_cache",
    "/.coverage",
    "/htmlcov",
]

[tool.hatch.build.targets.sdist]
exclude = [
    "/.venv",
    "/venv",
    "/*venv*",
    "/dist",
    "/.mypy_cache",
    "/.pytest_cache",
    "/.ruff_cache",
    "/.coverage",
    "/htmlcov",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
filterwarnings = [
    "error",
    # Intentional legacy dependency retained as an explicit stack constraint.
    "ignore::FutureWarning",
]

[tool.coverage.run]
branch = true
source = ["app"]

[tool.coverage.report]
show_missing = true
skip_covered = true

[tool.ruff]
target-version = "py311"
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ANN", "D", "ASYNC", "S"]
ignore = ["D203", "D213"]

[tool.ruff.lint.per-file-ignores]
"tests/*.py" = ["S101", "S105", "S106", "S311"]
# These test doubles must match the vendor's timeout keyword, not expose our own API.
"tests/test_services.py" = ["ASYNC109"]

[tool.mypy]
python_version = "3.11"
strict = true
explicit_package_bases = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = ["slowapi", "slowapi.*", "limits", "limits.*"]
ignore_missing_imports = true

[tool.bandit]
exclude_dirs = ["tests", ".venv"]
```

## `frontend/src/components/IntakeDashboard.tsx`

```tsx
"use client";

import {
  Component,
  createRef,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
} from "react";

/* -------------------------------------------------------------------------- */
/* API contract                                                               */
/* -------------------------------------------------------------------------- */

export type SeverityLevel = "low" | "medium" | "high" | "critical";

export interface StructuredDispatch {
  title: string;
  severity_level: SeverityLevel;
  category: string;
  key_entities: string[];
  confidence_score: number;
  action_items: string[];
}

export interface APIResponse<T = StructuredDispatch> {
  status: "success";
  data: T;
  latency_ms: number;
  cached: boolean;
}

export interface IntakeResponse {
  /** Read from the X-Request-ID response header, not invented by the client. */
  requestId: string;
  body: APIResponse<StructuredDispatch>;
}

export interface IntakeDashboardProps {
  /**
   * POST target for the ingest request. It must be a same-origin authenticated
   * proxy (the default is /api/ingest), or another server-side integration,
   * that adds X-API-Key server-side. This browser component never stores or
   * sends an API key.
   */
  endpoint?: string;
  /**
   * Optional presentation adapter. The engine never passes its endpoint,
   * request controller, or any secret-bearing value to a skin.
   */
  skin?: IntakeDashboardSkin;
}

export type IntakeInputMode = "file" | "text";

export interface IntakeErrorState {
  readonly message: string;
  readonly field: IntakeInputMode | null;
}

export interface IntakeDashboardSelectedFile {
  readonly name: string;
  readonly size: number;
}

export interface IntakeDashboardResponseSnapshot {
  readonly requestId: string;
  readonly body: {
    readonly status: "success";
    readonly data: {
      readonly title: string;
      readonly severity_level: SeverityLevel;
      readonly category: string;
      readonly key_entities: readonly string[];
      readonly confidence_score: number;
      readonly action_items: readonly string[];
    };
    readonly latency_ms: number;
    readonly cached: boolean;
  };
}

/**
 * Immutable engine-owned identifiers, refs, state, and actions exposed to a
 * visual skin. A skin may render them but must not own workflow state or
 * transport behavior.
 */
export interface IntakeDashboardIds {
  readonly title: string;
  readonly sourceTitle: string;
  readonly sourceHelp: string;
  readonly tabsHelp: string;
  readonly fileTab: string;
  readonly textTab: string;
  readonly filePanel: string;
  readonly textPanel: string;
  readonly fileInput: string;
  readonly textInput: string;
  readonly fileHelp: string;
  readonly selectedFile: string;
  readonly textHelp: string;
  readonly textCount: string;
  readonly errorMessage: string;
  readonly actionsHelp: string;
  readonly resultsTitle: string;
  readonly resultsHelp: string;
  readonly jsonHelp: string;
}

export interface IntakeDashboardRefs {
  readonly fileInput: Ref<HTMLInputElement>;
  readonly textInput: Ref<HTMLTextAreaElement>;
  readonly fileTab: Ref<HTMLButtonElement>;
  readonly textTab: Ref<HTMLButtonElement>;
  readonly submit: Ref<HTMLButtonElement>;
  readonly error: Ref<HTMLElement>;
}

export interface IntakeDashboardLiveRegions {
  readonly announcement: string;
  readonly error: IntakeErrorState | null;
}

export interface IntakeDashboardEngineState {
  readonly mode: IntakeInputMode;
  /** Safe display metadata only; the raw File remains private to the engine. */
  readonly file: IntakeDashboardSelectedFile | null;
  readonly text: string;
  readonly pending: boolean;
  readonly dragging: boolean;
  readonly response: IntakeDashboardResponseSnapshot | null;
  readonly textCharacterTotal: number;
  readonly maxTextInputUnits: number;
  /** Live-region content is produced by the engine, not by a skin. */
  readonly liveRegions: IntakeDashboardLiveRegions;
}

export interface IntakeDashboardEngineActions {
  readonly selectMode: (nextMode: IntakeInputMode) => void;
  readonly handleTabKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
  readonly selectFiles: (files: File[]) => void;
  readonly handleDragEnter: (event: DragEvent<HTMLElement>) => void;
  readonly handleDragLeave: (event: DragEvent<HTMLElement>) => void;
  readonly handleDragOver: (event: DragEvent<HTMLElement>) => void;
  readonly handleDrop: (event: DragEvent<HTMLElement>) => void;
  readonly cancelRequest: () => void;
  readonly handleFormKeyDown: (event: KeyboardEvent<HTMLFormElement>) => void;
  readonly clearIntake: () => void;
  readonly handleTextChange: (nextText: string) => void;
  readonly handleSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void>;
}

export interface IntakeDashboardSkinProps {
  readonly id: string;
  readonly ids: IntakeDashboardIds;
  readonly refs: IntakeDashboardRefs;
  readonly state: IntakeDashboardEngineState;
  readonly actions: IntakeDashboardEngineActions;
}

export interface IntakeDashboardSkin {
  readonly render: (props: IntakeDashboardSkinProps) => ReactNode;
}

/* -------------------------------------------------------------------------- */
/* Constants                                                                  */
/* -------------------------------------------------------------------------- */

const MIN_TEXT_CHARACTERS = 5;
const MAX_TEXT_CHARACTERS = 15_000;
/* maxLength counts UTF-16 code units, while the API counts Unicode characters. */
const MAX_TEXT_INPUT_UNITS = MAX_TEXT_CHARACTERS * 2;
/* The backend's lower configured request-body bound is 196,608 bytes. */
const MAX_REQUEST_BODY_BYTES = 196_608;
const MAX_FILE_BYTES = MAX_REQUEST_BODY_BYTES;
const REQUEST_TIMEOUT_MS = 60_000;

const SEVERITY_LEVELS: readonly SeverityLevel[] = [
  "low",
  "medium",
  "high",
  "critical",
];

/* ========================================================================== */
/* INTAKE_ENGINE_SUPPORT_START                                               */
/* Immutable engine support: runtime validation and transport helpers.        */
/* ========================================================================== */

/**
 * Text uses dark 950/700 shades on white or very light backgrounds.
 * Outline supplements box-shadow rings in forced-colors environments.
 */
const FOCUS_STYLES =
  "focus-visible:outline focus-visible:outline-2 " +
  "focus-visible:outline-offset-2 focus-visible:outline-slate-950 " +
  "focus-visible:ring-2 focus-visible:ring-slate-950 " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-white";

const ACCESSIBILITY_CSS = `
@media (forced-colors: active) {
  .intake-dashboard {
    forced-color-adjust: auto;
  }
}

@media (prefers-reduced-motion: reduce) {
  .intake-dashboard,
  .intake-dashboard * {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
`;

class PublicIntakeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PublicIntakeError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value)
  );
}

function invalidResponse(): never {
  throw new PublicIntakeError(
    "The server returned a response that does not match the intake contract.",
  );
}

function characterCount(value: string): number {
  /* Array.from counts Unicode code points like Python's len used by FastAPI. */
  return Array.from(value).length;
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value);

  return (
    actual.length === expected.length &&
    expected.every((key) =>
      Object.prototype.hasOwnProperty.call(value, key),
    )
  );
}

function readBoundedString(
  value: unknown,
  minLength: number,
  maxLength: number,
): string {
  if (
    typeof value !== "string" ||
    value.trim().length === 0 ||
    characterCount(value) < minLength ||
    characterCount(value) > maxLength
  ) {
    return invalidResponse();
  }

  return value;
}

function readStringArray(
  value: unknown,
  maxItems: number,
  itemMaxLength: number,
): string[] {
  if (!Array.isArray(value) || value.length > maxItems) {
    return invalidResponse();
  }

  return value.map((item: unknown) =>
    readBoundedString(item, 1, itemMaxLength),
  );
}

function readSeverity(value: unknown): SeverityLevel {
  for (const severity of SEVERITY_LEVELS) {
    if (value === severity) {
      return severity;
    }
  }

  return invalidResponse();
}

function readNonnegativeNumber(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return invalidResponse();
  }

  return value;
}

function readRequestId(response: Response): string {
  const requestId = response.headers.get("X-Request-ID");

  if (
    requestId === null ||
    requestId.trim().length === 0 ||
    characterCount(requestId) > 200
  ) {
    throw new PublicIntakeError(
      "The server did not return a valid X-Request-ID header.",
    );
  }

  return requestId;
}

/**
 * Validate untrusted JSON at runtime rather than asserting its TypeScript type.
 * Exact-key checks also reject fields that are not part of APIResponse.
 */
function parseIntakeResponse(
  value: unknown,
): APIResponse<StructuredDispatch> {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["status", "data", "latency_ms", "cached"]) ||
    value.status !== "success"
  ) {
    return invalidResponse();
  }

  const data = value.data;

  if (
    !isRecord(data) ||
    !hasExactKeys(data, [
      "title",
      "severity_level",
      "category",
      "key_entities",
      "confidence_score",
      "action_items",
    ])
  ) {
    return invalidResponse();
  }

  const confidence = data.confidence_score;

  if (
    typeof confidence !== "number" ||
    !Number.isFinite(confidence) ||
    confidence < 0 ||
    confidence > 1
  ) {
    return invalidResponse();
  }

  return {
    status: "success",
    data: {
      title: readBoundedString(data.title, 1, 200),
      severity_level: readSeverity(data.severity_level),
      category: readBoundedString(data.category, 1, 80),
      key_entities: readStringArray(data.key_entities, 50, 200),
      confidence_score: confidence,
      action_items: readStringArray(data.action_items, 30, 500),
    },
    latency_ms: readNonnegativeNumber(value.latency_ms),
    cached:
      typeof value.cached === "boolean"
        ? value.cached
        : invalidResponse(),
  };
}

function createDashboardResponseSnapshot(
  response: IntakeResponse,
): IntakeDashboardResponseSnapshot {
  return {
    requestId: response.requestId,
    body: {
      status: response.body.status,
      data: {
        title: response.body.data.title,
        severity_level: response.body.data.severity_level,
        category: response.body.data.category,
        key_entities: response.body.data.key_entities,
        confidence_score: response.body.data.confidence_score,
        action_items: response.body.data.action_items,
      },
      latency_ms: response.body.latency_ms,
      cached: response.body.cached,
    },
  };
}

function validateText(text: string): string | null {
  const count = characterCount(text);

  if (text.trim().length === 0) {
    return "Enter at least one non-whitespace character.";
  }

  if (count < MIN_TEXT_CHARACTERS) {
    return "Enter at least 5 characters of intake text.";
  }

  if (count > MAX_TEXT_CHARACTERS) {
    return "Reduce the intake text to 15,000 characters or fewer.";
  }

  return null;
}

function validateFile(file: File): string | null {
  if (!/\.(txt|csv|json)$/i.test(file.name)) {
    return "Choose a TXT, CSV, or JSON file. PDF files are not supported.";
  }

  if (file.size === 0) {
    return "The selected file is empty. Choose a file containing intake information.";
  }

  if (file.size > MAX_FILE_BYTES) {
    return "The selected file exceeds the 192 KiB practical limit. Choose a smaller file.";
  }

  return null;
}

/**
 * Read only the textual file formats allowed by validateFile. No PDF or other
 * binary parser is implied: the server receives the resulting text string.
 */
function readFileAsText(
  file: File,
  signal: AbortSignal,
): Promise<string> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Reading cancelled.", "AbortError"));
      return;
    }

    const reader = new FileReader();

    const abortReading = () => {
      if (reader.readyState === FileReader.LOADING) {
        reader.abort();
      }
    };

    const cleanup = () => {
      signal.removeEventListener("abort", abortReading);
    };

    reader.onload = () => {
      cleanup();

      if (typeof reader.result !== "string") {
        reject(
          new PublicIntakeError(
            "The selected file could not be read as text. Choose it again.",
          ),
        );
        return;
      }

      resolve(reader.result);
    };

    reader.onerror = () => {
      cleanup();
      reject(
        new PublicIntakeError(
          "The selected file could not be read. Check access to the file and try again.",
        ),
      );
    };

    reader.onabort = () => {
      cleanup();
      reject(new DOMException("Reading cancelled.", "AbortError"));
    };

    signal.addEventListener("abort", abortReading, { once: true });
    reader.readAsText(file);
  });
}

function createRequestBody(text: string): string {
  const body = JSON.stringify({ text });
  const bodyBytes = new TextEncoder().encode(body).byteLength;

  if (bodyBytes > MAX_REQUEST_BODY_BYTES) {
    throw new PublicIntakeError(
      "The encoded request is too large for the intake service. Use less text.",
    );
  }

  return body;
}

function httpErrorMessage(status: number): string {
  switch (status) {
    case 400:
    case 422:
      return "The server could not process this intake. Check the source information before submitting again.";
    case 401:
    case 403:
      return "You are not authorized to submit this intake. Check your sign-in and permissions.";
    case 408:
      return "The server timed out while reading the submission. Try again with a smaller intake.";
    case 413:
      return "The server rejected the submission because it is too large.";
    case 415:
      return "The intake service requires an uncompressed JSON request.";
    case 429:
      return "Too many submissions were received. Wait before submitting again.";
    default:
      return "The server could not complete the request. Check the submission status before trying again.";
  }
}

function humanize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function formatConfidence(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/* ========================================================================== */
/* INTAKE_ENGINE_SUPPORT_END                                                 */
/* ========================================================================== */

/* ========================================================================== */
/* INTAKE_VISUAL_SKIN_START                                                  */
/* Mutable presentation boundary: layout, branding, dropzones, metrics, and  */
/* result panels are replaceable without changing the engine contract.        */
/* ========================================================================== */

/* -------------------------------------------------------------------------- */
/* Explicitly labeled button                                                  */
/* -------------------------------------------------------------------------- */

interface LabeledButtonProps
  extends Omit<
    ButtonHTMLAttributes<HTMLButtonElement>,
    "id" | "aria-label" | "aria-describedby"
  > {
  id: string;
  label: string;
  describedBy: string;
  buttonRef?: Ref<HTMLButtonElement>;
}

function LabeledButton({
  id,
  label,
  describedBy,
  buttonRef,
  type = "button",
  className = "",
  children,
  ...props
}: LabeledButtonProps) {
  return (
    <button
      {...props}
      ref={buttonRef}
      id={id}
      type={type}
      aria-label={label}
      aria-describedby={describedBy}
      className={`${BUTTON_STYLES} ${className}`}
    >
      {children ?? label}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Keyboard-operable JSON preview                                             */
/* -------------------------------------------------------------------------- */

interface JsonNodeProps {
  name: string;
  value: unknown;
  helpId: string;
  depth?: number;
}

function JsonNode({
  name,
  value,
  helpId,
  depth = 0,
}: JsonNodeProps) {
  const id = useId();
  const [expanded, setExpanded] = useState(depth < 2);
  const toggleRef = useRef<HTMLButtonElement>(null);

  const isArray = Array.isArray(value);
  const isContainer = isArray || isRecord(value);

  const entries: Array<[string, unknown]> = isArray
    ? value.map(
        (item: unknown, index: number): [string, unknown] => [
          String(index),
          item,
        ],
      )
    : isRecord(value)
      ? Object.entries(value)
      : [];

  if (!isContainer || entries.length === 0) {
    return (
      <li className="min-w-0">
        <code className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
          {JSON.stringify(name)}: {JSON.stringify(value) ?? "null"}
        </code>
      </li>
    );
  }

  const label =
    `${expanded ? "Collapse" : "Expand"} ${name} ` +
    `(${entries.length} ${isArray ? "items" : "properties"})`;

  function toggleExpanded() {
    setExpanded((current) => !current);
  }

  function handleDisclosureKeyDown(
    event: KeyboardEvent<HTMLButtonElement>,
  ) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggleExpanded();
    } else if (event.key === "Escape") {
      event.preventDefault();
      setExpanded(false);
      toggleRef.current?.focus();
    }
  }

  return (
    <li className="min-w-0 space-y-2">
      <LabeledButton
        id={`${id}-toggle`}
        label={label}
        describedBy={helpId}
        buttonRef={toggleRef}
        aria-expanded={expanded}
        aria-controls={`${id}-children`}
        aria-keyshortcuts="Enter Space Escape"
        onClick={toggleExpanded}
        onKeyDown={handleDisclosureKeyDown}
        className="max-w-full bg-white text-left text-slate-950 hover:bg-slate-100"
      >
        <span aria-hidden="true" className="mr-2">
          {expanded ? "−" : "+"}
        </span>
        <span className="break-words [overflow-wrap:anywhere]">
          {label}
        </span>
      </LabeledButton>

      <ul
        id={`${id}-children`}
        hidden={!expanded}
        className="space-y-3 border-l-2 border-slate-700 pl-3 sm:pl-5"
      >
        {entries.map(([key, child]) => (
          <JsonNode
            key={key}
            name={key}
            value={child}
            helpId={helpId}
            depth={depth + 1}
          />
        ))}
      </ul>
    </li>
  );
}

const SEVERITY_STYLES: Record<SeverityLevel, string> = {
  low: "border-blue-950 bg-blue-50 text-blue-950",
  medium: "border-slate-950 bg-slate-100 text-slate-950",
  high: "border-amber-950 bg-amber-50 text-amber-950",
  critical: "border-red-950 bg-red-50 text-red-950",
};

const BUTTON_STYLES =
  "min-h-12 min-w-12 rounded-md border-2 border-slate-950 " +
  "px-4 py-2 text-base font-semibold leading-relaxed " +
  "aria-disabled:cursor-not-allowed disabled:cursor-not-allowed " +
  "disabled:opacity-60 " +
  FOCUS_STYLES;

const INPUT_STYLES =
  "w-full rounded-md border-2 border-slate-700 bg-white " +
  "p-3 text-base leading-relaxed text-slate-950 " +
  "placeholder:text-slate-700 disabled:cursor-not-allowed " +
  "disabled:bg-slate-100 disabled:text-slate-700 " +
  FOCUS_STYLES;

const DEFAULT_INTAKE_DASHBOARD_SKIN: IntakeDashboardSkin = {
  render: ({ id, ids, refs, state, actions }: IntakeDashboardSkinProps) => {
    const {
      mode,
      file,
      text,
      pending,
      dragging,
      response,
      textCharacterTotal,
      maxTextInputUnits,
      liveRegions,
    } = state;
    const { announcement, error } = liveRegions;
    const {
      title: titleId,
      sourceTitle: sourceTitleId,
      sourceHelp: sourceHelpId,
      tabsHelp: tabsHelpId,
      fileTab: fileTabId,
      textTab: textTabId,
      filePanel: filePanelId,
      textPanel: textPanelId,
      fileInput: fileInputId,
      textInput: textInputId,
      fileHelp: fileHelpId,
      selectedFile: selectedFileId,
      textHelp: textHelpId,
      textCount: textCountId,
      errorMessage: errorMessageId,
      actionsHelp: actionsHelpId,
      resultsTitle: resultsTitleId,
      resultsHelp: resultsHelpId,
      jsonHelp: jsonHelpId,
    } = ids;
    const {
      fileInput: fileInputRef,
      textInput: textInputRef,
      fileTab: fileTabRef,
      textTab: textTabRef,
      submit: submitRef,
      error: errorRef,
    } = refs;
    const {
      selectMode,
      handleTabKeyDown,
      selectFiles,
      handleDragEnter,
      handleDragLeave,
      handleDragOver,
      handleDrop,
      cancelRequest,
      handleFormKeyDown,
      clearIntake,
      handleTextChange,
      handleSubmit,
    } = actions;

    return (
      <main
        aria-labelledby={titleId}
        role="main"
        className="intake-dashboard mx-auto max-w-7xl space-y-8 bg-white p-4 text-base leading-relaxed text-slate-950 [color-scheme:light] sm:p-6 lg:p-8"
      >
        <style>{ACCESSIBILITY_CSS}</style>

        <header role="banner" className="space-y-3">
          <h1
            id={titleId}
            tabIndex={-1}
            className={`text-3xl font-bold tracking-tight sm:text-4xl ${FOCUS_STYLES}`}
          >
            Structured dispatch intake
          </h1>

          <p className="max-w-prose text-slate-700">
            Upload one textual source file or paste raw dispatch information
            to extract a structured, reviewable intake.
          </p>

          {/* The engine renders the screen-reader live region outside the skin. */}
          <p
            aria-hidden="true"
            className="min-h-8 max-w-prose font-semibold"
          >
            {announcement}
          </p>
        </header>

        <section
          aria-label="Intake workspace"
          className="grid min-w-0 gap-8 xl:grid-cols-2"
        >
          <section
            aria-labelledby={sourceTitleId}
            role="region"
            className="min-w-0 space-y-5"
          >
            <header className="space-y-2">
              <h2 id={sourceTitleId} className="text-2xl font-bold">
                Intake source
              </h2>

              <p id={sourceHelpId} className="max-w-prose text-slate-700">
                Only the selected source type is submitted. The request body
                is always exactly {"{text: string}"}. Source information is
                not saved in browser storage. Configure the endpoint as a
                same-origin authenticated proxy that adds X-API-Key on the
                server; this browser never sends an API key.
              </p>
            </header>

            <form
              noValidate
              aria-labelledby={sourceTitleId}
              aria-describedby={sourceHelpId}
              aria-busy={pending}
              onKeyDown={handleFormKeyDown}
              onSubmit={handleSubmit}
              className="space-y-5"
            >
              <p id={tabsHelpId} className="max-w-prose text-slate-700">
                Use Left or Right Arrow to switch tabs, Home for file upload,
                and End for raw text. Press Tab to reach the selected source
                input. Source selection is locked during processing.
              </p>

              <nav aria-label="Intake input type navigation">
                <section
                  role="tablist"
                  aria-label="Intake input type"
                  aria-describedby={tabsHelpId}
                  aria-orientation="horizontal"
                  aria-keyshortcuts="ArrowLeft ArrowRight Home End Enter Space Escape"
                  aria-busy={pending}
                  onKeyDown={handleTabKeyDown}
                  className="flex flex-wrap gap-3"
                >
                  <button
                    ref={fileTabRef}
                    id={fileTabId}
                    type="button"
                    role="tab"
                    aria-label="File upload"
                    aria-describedby={tabsHelpId}
                    aria-selected={mode === "file"}
                    aria-controls={filePanelId}
                    disabled={pending}
                    tabIndex={mode === "file" ? 0 : -1}
                    onClick={() => selectMode("file")}
                    className={`${BUTTON_STYLES} ${
                      mode === "file"
                        ? "bg-slate-950 text-white"
                        : "bg-white text-slate-950 hover:bg-slate-100"
                    }`}
                  >
                    File upload
                  </button>

                  <button
                    ref={textTabRef}
                    id={textTabId}
                    type="button"
                    role="tab"
                    aria-label="Raw text"
                    aria-describedby={tabsHelpId}
                    aria-selected={mode === "text"}
                    aria-controls={textPanelId}
                    disabled={pending}
                    tabIndex={mode === "text" ? 0 : -1}
                    onClick={() => selectMode("text")}
                    className={`${BUTTON_STYLES} ${
                      mode === "text"
                        ? "bg-slate-950 text-white"
                        : "bg-white text-slate-950 hover:bg-slate-100"
                    }`}
                  >
                    Raw text
                  </button>
                </section>
              </nav>

              <section
                id={filePanelId}
                role="tabpanel"
                aria-labelledby={fileTabId}
                aria-describedby={fileHelpId}
                hidden={mode !== "file"}
                onDragEnter={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                className={`space-y-4 rounded-lg border-2 border-dashed p-5 ${
                  dragging
                    ? "border-slate-950 bg-slate-100"
                    : "border-slate-700 bg-white"
                }`}
              >
                <h3 className="text-xl font-bold">
                  Upload a text file (sent as text)
                </h3>

                <p id={fileHelpId} className="max-w-prose text-slate-700">
                  Drop one file here or use the file chooser. Supported
                  textual formats: TXT, CSV, and JSON. PDF files are not
                  supported because no server-side PDF parser is part of
                  this flow. The file is read locally and sent as the same
                  JSON {"{text: string}"} payload as Raw text; no multipart
                  upload is sent. Maximum file size: 192 KiB; the extracted
                  text must contain 5–15,000 characters.
                </p>

                <label htmlFor={fileInputId} className="block font-semibold">
                  Choose one source text file
                </label>

                <input
                  ref={fileInputRef}
                  id={fileInputId}
                  name="sourceFile"
                  type="file"
                  accept=".txt,.csv,.json"
                  disabled={pending}
                  aria-label="Choose one source text file"
                  aria-describedby={[
                    fileHelpId,
                    selectedFileId,
                    error?.field === "file" ? errorMessageId : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  aria-invalid={error?.field === "file"}
                  onChange={(event) =>
                    selectFiles(Array.from(event.currentTarget.files ?? []))
                  }
                  className={`${INPUT_STYLES} min-h-12 file:mr-4 file:min-h-12 file:rounded-md file:border-0 file:bg-slate-950 file:px-4 file:py-3 file:text-base file:font-semibold file:text-white`}
                />

                <p
                  id={selectedFileId}
                  className="break-words font-semibold [overflow-wrap:anywhere]"
                >
                  {file
                    ? `Selected source: ${file.name} (${(
                        file.size / 1024
                      ).toFixed(1)} KiB).`
                    : "No source text file selected."}
                </p>
              </section>

              <section
                id={textPanelId}
                role="tabpanel"
                aria-labelledby={textTabId}
                aria-describedby={textHelpId}
                hidden={mode !== "text"}
                className="space-y-4 rounded-lg border-2 border-slate-700 bg-white p-5"
              >
                <h3 className="text-xl font-bold">
                  Paste dispatch information
                </h3>

                <p id={textHelpId} className="max-w-prose text-slate-700">
                  Include the incident, location, urgency, and any requested
                  actions. Enter 5–15,000 characters. This field is required
                  when the Raw text tab is selected.
                </p>

                <label htmlFor={textInputId} className="block font-semibold">
                  Raw dispatch text
                </label>

                <textarea
                  ref={textInputRef}
                  id={textInputId}
                  name="rawText"
                  rows={10}
                  maxLength={maxTextInputUnits}
                  required={mode === "text"}
                  disabled={pending}
                  value={text}
                  aria-label="Raw dispatch text"
                  aria-describedby={[
                    textHelpId,
                    textCountId,
                    error?.field === "text" ? errorMessageId : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  aria-invalid={error?.field === "text"}
                  onChange={(event) => handleTextChange(event.currentTarget.value)}
                  className={`${INPUT_STYLES} min-h-64 resize-y`}
                />

                <p id={textCountId} className="text-slate-700">
                  {textCharacterTotal.toLocaleString("en-US")} of 15,000
                  characters used.
                </p>
              </section>

              {/* Persistently mounted so engine-produced errors are announced. */}
              <section
                ref={errorRef}
                aria-label="Intake errors"
                tabIndex={-1}
                className={
                  error
                    ? `space-y-2 rounded-lg border-2 border-red-950 bg-red-50 p-5 text-red-950 ${FOCUS_STYLES}`
                    : ""
                }
              >
                {error && (
                  <h3
                    id={`${id}-visual-error-title`}
                    className="text-xl font-bold"
                  >
                    Submission not completed
                  </h3>
                )}

                <p id={errorMessageId}>{error?.message ?? ""}</p>
              </section>

              <p id={actionsHelpId} className="max-w-prose text-slate-700">
                Submit sends the selected source as {"{text: string}"} to the
                intake service. Clear removes local inputs and results. Cancel
                stops waiting but cannot undo work already started by the
                server.
              </p>

              <footer aria-label="Intake actions" className="flex flex-wrap gap-3">
                <LabeledButton
                  id={`${id}-submit`}
                  buttonRef={submitRef}
                  type="submit"
                  label={pending ? "Processing intake" : "Submit intake"}
                  describedBy={actionsHelpId}
                  disabled={pending}
                  aria-busy={pending}
                  className="bg-slate-950 text-white hover:bg-slate-800"
                />

                {pending && (
                  <LabeledButton
                    id={`${id}-cancel`}
                    label="Cancel request"
                    describedBy={actionsHelpId}
                    onClick={cancelRequest}
                    className="bg-white text-slate-950 hover:bg-slate-100"
                  />
                )}

                <LabeledButton
                  id={`${id}-clear`}
                  label="Clear intake"
                  describedBy={actionsHelpId}
                  disabled={pending}
                  onClick={clearIntake}
                  className="bg-white text-slate-950 hover:bg-slate-100"
                />
              </footer>
            </form>
          </section>

          <section
            aria-labelledby={resultsTitleId}
            aria-describedby={resultsHelpId}
            role="region"
            aria-busy={pending}
            className="min-w-0 space-y-5"
          >
            <header className="space-y-2">
              <h2 id={resultsTitleId} className="text-2xl font-bold">
                Structured intake
              </h2>

              <p id={resultsHelpId} className="max-w-prose text-slate-700">
                The latest completed response appears here. Editing an input
                does not automatically submit it. Review severity, entities,
                confidence, and action items before acting on the result.
              </p>
            </header>

            {!response ? (
              <article
                aria-label="Structured intake preview status"
                className="rounded-lg border-2 border-slate-700 bg-slate-100 p-5"
              >
                <p className="font-semibold">
                  {pending
                    ? "Processing intake. The structured response will appear here when ready."
                    : "No structured intake available yet."}
                </p>
              </article>
            ) : (
              <article
                aria-labelledby={`${id}-dispatch-title`}
                className="min-w-0 space-y-6 rounded-lg border-2 border-slate-700 bg-white p-5"
              >
                <header className="space-y-4">
                  <h3
                    id={`${id}-dispatch-title`}
                    className="break-words text-xl font-bold [overflow-wrap:anywhere]"
                  >
                    {response.body.data.title}
                  </h3>

                  <div className="flex flex-wrap gap-3">
                    <span
                      className={`rounded-md border-2 px-3 py-2 font-semibold ${
                        SEVERITY_STYLES[response.body.data.severity_level]
                      }`}
                    >
                      <span aria-hidden="true">◆ </span>
                      Severity: {humanize(response.body.data.severity_level)}
                    </span>

                    <span className="rounded-md border-2 border-slate-950 bg-white px-3 py-2 font-semibold text-slate-950">
                      Confidence: {formatConfidence(response.body.data.confidence_score)}
                    </span>

                    <span className="rounded-md border-2 border-slate-700 bg-slate-100 px-3 py-2 font-semibold text-slate-950">
                      Category: {response.body.data.category}
                    </span>
                  </div>
                </header>

                <section
                  aria-labelledby={`${id}-entities-title`}
                  className="space-y-3"
                >
                  <h4 id={`${id}-entities-title`} className="text-lg font-bold">
                    Key entities
                  </h4>

                  {response.body.data.key_entities.length > 0 ? (
                    <ul className="list-disc space-y-2 pl-6">
                      {response.body.data.key_entities.map((entity, index) => (
                        <li
                          key={`${index}-${entity}`}
                          className="break-words [overflow-wrap:anywhere]"
                        >
                          {entity}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>No key entities were returned.</p>
                  )}
                </section>

                <section
                  aria-labelledby={`${id}-actions-title`}
                  className="space-y-3"
                >
                  <h4 id={`${id}-actions-title`} className="text-lg font-bold">
                    Action items
                  </h4>

                  {response.body.data.action_items.length > 0 ? (
                    <ol className="list-decimal space-y-2 pl-6">
                      {response.body.data.action_items.map((action, index) => (
                        <li
                          key={`${index}-${action}`}
                          className="break-words [overflow-wrap:anywhere]"
                        >
                          {action}
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p>No action items were returned.</p>
                  )}
                </section>

                <section
                  aria-labelledby={`${id}-json-title`}
                  className="min-w-0 space-y-3 border-t-2 border-slate-700 pt-5"
                >
                  <h4 id={`${id}-json-title`} className="text-lg font-bold">
                    JSON response preview
                  </h4>

                  <p id={jsonHelpId} className="max-w-prose text-slate-700">
                    Use Tab to reach an expand or collapse button. Press Enter
                    or Space to toggle its properties. Values are read-only.
                  </p>

                  {/*
                   * This is a nested list with disclosure buttons, not an
                   * ARIA tree widget. It intentionally uses ordinary tab
                   * navigation instead of claiming unsupported tree behavior.
                   */}
                  <ul
                    aria-label="Structured API response JSON"
                    className="min-w-0 space-y-3"
                  >
                    <JsonNode
                      key={response.requestId}
                      name="response"
                      value={response.body}
                      helpId={jsonHelpId}
                    />
                  </ul>
                </section>

                <footer
                  aria-label="Response metadata"
                  className="space-y-2 border-t-2 border-slate-700 pt-5 text-slate-700"
                >
                  <p className="break-words [overflow-wrap:anywhere]">
                    <span className="font-bold">Request ID:</span>{" "}
                    <code>{response.requestId}</code>
                  </p>

                  <p>
                    <span className="font-bold">API latency:</span>{" "}
                    {response.body.latency_ms.toLocaleString("en-US")} ms
                  </p>

                  <p>
                    <span className="font-bold">Cached:</span>{" "}
                    {response.body.cached ? "Yes" : "No"}
                  </p>
                </footer>
              </article>
            )}
          </section>
        </section>
      </main>
    );
  },
};

/* ========================================================================== */
/* INTAKE_VISUAL_SKIN_END                                                    */
/* ========================================================================== */

/* -------------------------------------------------------------------------- */
/* Dashboard                                                                  */
/* -------------------------------------------------------------------------- */

/* ========================================================================== */
/* INTAKE_ENGINE_CORE_START                                                  */
/* Immutable engine/core: state, keyboard listeners, live-region data, refs,  */
/* transport, validation entry points, and same-origin security boundaries.   */
/* ========================================================================== */

function EngineLiveRegions({
  liveRegions,
}: Pick<IntakeDashboardEngineState, "liveRegions">) {
  const { announcement, error } = liveRegions;

  return (
    <>
      <p
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      >
        {announcement}
      </p>

      <section
        role="alert"
        aria-label="Intake errors"
        aria-live="assertive"
        aria-atomic="true"
        className="sr-only"
      >
        {error?.message ?? ""}
      </section>
    </>
  );
}

interface DashboardContentProps extends IntakeDashboardProps {
  id: string;
}

function DashboardContent({
  id,
  endpoint = "/api/ingest",
  skin = DEFAULT_INTAKE_DASHBOARD_SKIN,
}: DashboardContentProps) {
  const [mode, setMode] = useState<IntakeInputMode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<IntakeErrorState | null>(null);
  const [response, setResponse] = useState<IntakeResponse | null>(
    null,
  );
  const [announcement, setAnnouncement] = useState(
    "Ready. Choose a source and submit an intake.",
  );

  const fileInputRef = useRef<HTMLInputElement>(null);
  const textInputRef = useRef<HTMLTextAreaElement>(null);
  const fileTabRef = useRef<HTMLButtonElement>(null);
  const textTabRef = useRef<HTMLButtonElement>(null);
  const submitRef = useRef<HTMLButtonElement>(null);
  const errorRef = useRef<HTMLElement>(null);

  const controllerRef = useRef<AbortController | null>(null);
  const dragDepthRef = useRef(0);
  const focusSubmitAfterCancelRef = useRef(false);

  const ids: IntakeDashboardIds = {
    title: `${id}-title`,
    sourceTitle: `${id}-source-title`,
    sourceHelp: `${id}-source-help`,
    tabsHelp: `${id}-tabs-help`,
    fileTab: `${id}-file-tab`,
    textTab: `${id}-text-tab`,
    filePanel: `${id}-file-panel`,
    textPanel: `${id}-text-panel`,
    fileInput: `${id}-file-input`,
    textInput: `${id}-text-input`,
    fileHelp: `${id}-file-help`,
    selectedFile: `${id}-selected-file`,
    textHelp: `${id}-text-help`,
    textCount: `${id}-text-count`,
    errorMessage: `${id}-error-message`,
    actionsHelp: `${id}-actions-help`,
    resultsTitle: `${id}-results-title`,
    resultsHelp: `${id}-results-help`,
    jsonHelp: `${id}-json-help`,
  };

  useEffect(() => {
    return () => {
      const controller = controllerRef.current;
      controllerRef.current = null;
      controller?.abort();
    };
  }, []);

  useEffect(() => {
    if (!error) {
      return;
    }

    if (error.field === "file") {
      fileInputRef.current?.focus();
    } else if (error.field === "text") {
      textInputRef.current?.focus();
    } else {
      errorRef.current?.focus();
    }
  }, [error]);

  useEffect(() => {
    /* Focus only after the native disabled state has been removed. */
    if (!pending && focusSubmitAfterCancelRef.current) {
      focusSubmitAfterCancelRef.current = false;
      submitRef.current?.focus();
    }
  }, [pending]);

  function selectMode(nextMode: IntakeInputMode) {
    if (pending || controllerRef.current) {
      return;
    }

    setMode(nextMode);
    setError(null);
  }

  function handleTabKeyDown(event: KeyboardEvent<HTMLElement>) {
    const supportedKeys = [
      "ArrowLeft",
      "ArrowRight",
      "Home",
      "End",
      "Enter",
      " ",
      "Escape",
    ];

    if (!supportedKeys.includes(event.key)) {
      return;
    }

    event.preventDefault();

    if (event.key === "Escape") {
      event.preventDefault();
      if (pending || controllerRef.current) {
        cancelRequest();
      } else if (mode === "file") {
        fileTabRef.current?.focus();
      } else {
        textTabRef.current?.focus();
      }
      return;
    }

    if (pending || controllerRef.current) {
      return;
    }

    const focusedTabMode: IntakeInputMode =
      event.target === textTabRef.current ? "text" : "file";
    const nextMode: IntakeInputMode =
      event.key === "Home"
        ? "file"
        : event.key === "End"
          ? "text"
          : event.key === "Enter" || event.key === " "
            ? focusedTabMode
            : mode === "file"
              ? "text"
              : "file";

    selectMode(nextMode);

    if (nextMode === "file") {
      fileTabRef.current?.focus();
    } else {
      textTabRef.current?.focus();
    }
  }

  function selectFiles(files: File[]) {
    if (pending || controllerRef.current) {
      return;
    }

    const candidate = files[0];

    if (files.length !== 1 || !candidate) {
      setFile(null);
      setAnnouncement("");

      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }

      setError({
        field: "file",
        message: "Choose or drop exactly one supported text file.",
      });
      return;
    }

    const validationMessage = validateFile(candidate);

    if (validationMessage) {
      setFile(null);
      setAnnouncement("");

      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }

      setError({
        field: "file",
        message: validationMessage,
      });
      return;
    }

    setFile(candidate);
    setError(null);
    setAnnouncement(
      `Selected ${candidate.name}. Ready to submit as text.`,
    );
  }

  function handleDragEnter(event: DragEvent<HTMLElement>) {
    event.preventDefault();

    if (pending || controllerRef.current) {
      return;
    }

    dragDepthRef.current += 1;
    setDragging(true);
  }

  function handleDragLeave(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    dragDepthRef.current = Math.max(
      0,
      dragDepthRef.current - 1,
    );

    if (dragDepthRef.current === 0) {
      setDragging(false);
    }
  }

  function handleDragOver(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    event.dataTransfer.dropEffect = pending ? "none" : "copy";
  }

  function handleDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragging(false);

    if (pending || controllerRef.current) {
      return;
    }

    const droppedFiles = event.dataTransfer.files;

    /*
     * Keep the native input's displayed selection synchronized where supported.
     * The React File state remains the authoritative submission source.
     */
    if (fileInputRef.current && droppedFiles.length === 1) {
      try {
        fileInputRef.current.files = droppedFiles;
      } catch {
        fileInputRef.current.value = "";
      }
    }

    selectFiles(Array.from(droppedFiles));
  }

  function cancelRequest() {
    const controller = controllerRef.current;

    if (!controller) {
      return;
    }

    controllerRef.current = null;
    controller.abort();

    focusSubmitAfterCancelRef.current = true;
    setPending(false);
    setError(null);
    setAnnouncement(
      "Stopped waiting for the response. If the request was sent, server processing may continue.",
    );

  }

  function handleFormKeyDown(event: KeyboardEvent<HTMLFormElement>) {
    if (event.key === "Escape" && (pending || controllerRef.current)) {
      event.preventDefault();
      cancelRequest();
    }
  }

  function clearIntake() {
    if (pending || controllerRef.current) {
      return;
    }

    setFile(null);
    setText("");
    setError(null);
    setResponse(null);
    setDragging(false);
    dragDepthRef.current = 0;

    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }

    setAnnouncement("Intake cleared. Ready for a new submission.");

    if (mode === "file") {
      fileInputRef.current?.focus();
    } else {
      textInputRef.current?.focus();
    }
  }

  function handleTextChange(nextText: string) {
    const boundedText =
      characterCount(nextText) > MAX_TEXT_CHARACTERS
        ? Array.from(nextText).slice(0, MAX_TEXT_CHARACTERS).join("")
        : nextText;

    setText(boundedText);

    if (error?.field === "text") {
      setError(null);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    // Ref guard also prevents duplicate submissions within the same render.
    if (pending || controllerRef.current) {
      return;
    }

    if (mode === "file" && !file) {
      setAnnouncement("");
      setError({
        field: "file",
        message: "Choose or drop a source text file before submitting.",
      });
      return;
    }

    if (mode === "text") {
      const validationMessage = validateText(text);

      if (validationMessage) {
        setAnnouncement("");
        setError({
          field: "text",
          message: validationMessage,
        });
        return;
      }
    }

    const controller = new AbortController();
    controllerRef.current = controller;

    setPending(true);
    setError(null);
    setResponse(null);
    setDragging(false);

    let timedOut = false;
    let errorField: IntakeInputMode | null = mode === "file" ? "file" : null;

    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);

    try {
      let intakeText = text;

      if (mode === "file") {
        const selectedFile = file;

        if (!selectedFile) {
          throw new PublicIntakeError(
            "Choose a source text file before submitting.",
          );
        }

        const validationMessage = validateFile(selectedFile);

        if (validationMessage) {
          throw new PublicIntakeError(validationMessage);
        }

        setAnnouncement("Reading the selected text file.");
        intakeText = await readFileAsText(selectedFile, controller.signal);
      }

      if (controllerRef.current !== controller) {
        return;
      }

      const textValidationMessage = validateText(intakeText);

      if (textValidationMessage) {
        throw new PublicIntakeError(textValidationMessage);
      }

      const body = createRequestBody(intakeText);
      errorField = null;

      setAnnouncement(
        "Processing intake. You can cancel while waiting for the response.",
      );

      const httpResponse = await fetch(endpoint, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        /* The same-origin proxy, not this browser, supplies X-API-Key. */
        body,
      });

      if (!httpResponse.ok) {
        throw new PublicIntakeError(
          httpErrorMessage(httpResponse.status),
        );
      }

      let rawResponse: unknown;

      try {
        rawResponse = await httpResponse.json();
      } catch {
        throw new PublicIntakeError(
          "The server returned invalid JSON instead of a structured intake.",
        );
      }

      const parsedResponse = parseIntakeResponse(rawResponse);
      const requestId = readRequestId(httpResponse);

      // Ignore a result from an aborted, unmounted, or superseded request.
      if (controllerRef.current !== controller) {
        return;
      }

      setResponse({ requestId, body: parsedResponse });
      setAnnouncement(
        `Intake ready: ${parsedResponse.data.title}. ` +
          `Severity: ${humanize(parsedResponse.data.severity_level)}. ` +
          `${parsedResponse.data.key_entities.length} key entities, ` +
          `${formatConfidence(parsedResponse.data.confidence_score)} confidence, ` +
          `${parsedResponse.data.action_items.length} action items. ` +
          "The structured response is available below.",
      );
    } catch (caught: unknown) {
      if (controllerRef.current !== controller) {
        return;
      }

      setAnnouncement("");
      setError({
        field: timedOut ? null : errorField,
        message: timedOut
          ? "The response timed out after 60 seconds. The server may still be processing the submission. Check its status before submitting again."
          : caught instanceof PublicIntakeError
            ? caught.message
            : "The request could not be completed. Check your connection and submission status before trying again.",
      });
    } finally {
      window.clearTimeout(timeoutId);

      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setPending(false);
      }
    }
  }

  const textCharacterTotal = characterCount(text);
  const refs: IntakeDashboardRefs = {
    fileInput: fileInputRef,
    textInput: textInputRef,
    fileTab: fileTabRef,
    textTab: textTabRef,
    submit: submitRef,
    error: errorRef,
  };
  const state: IntakeDashboardEngineState = {
    mode,
    file: file
      ? {
          name: file.name,
          size: file.size,
        }
      : null,
    text,
    pending,
    dragging,
    response: response
      ? createDashboardResponseSnapshot(response)
      : null,
    textCharacterTotal,
    maxTextInputUnits: MAX_TEXT_INPUT_UNITS,
    liveRegions: {
      announcement,
      error,
    },
  };
  const actions: IntakeDashboardEngineActions = {
    selectMode,
    handleTabKeyDown,
    selectFiles,
    handleDragEnter,
    handleDragLeave,
    handleDragOver,
    handleDrop,
    cancelRequest,
    handleFormKeyDown,
    clearIntake,
    handleTextChange,
    handleSubmit,
  };

  return (
    <>
      <EngineLiveRegions liveRegions={state.liveRegions} />
      {skin.render({ id, ids, refs, state, actions })}
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Render error boundary                                                      */
/* -------------------------------------------------------------------------- */

interface DashboardBoundaryProps {
  id: string;
  children: ReactNode;
}

interface DashboardBoundaryState {
  failed: boolean;
}

class DashboardErrorBoundary extends Component<
  DashboardBoundaryProps,
  DashboardBoundaryState
> {
  override state: DashboardBoundaryState = { failed: false };

  private readonly headingRef = createRef<HTMLHeadingElement>();

  static getDerivedStateFromError(): DashboardBoundaryState {
    return { failed: true };
  }

  override componentDidCatch(): void {
    // Do not log intake content or raw server responses.
    this.headingRef.current?.focus();
  }

  private resetDashboard = () => {
    this.setState({ failed: false }, () => {
      document
        .getElementById(`${this.props.id}-title`)
        ?.focus();
    });
  };

  override render() {
    if (!this.state.failed) {
      return this.props.children;
    }

    const { id } = this.props;

    return (
      <main
        aria-labelledby={`${id}-boundary-title`}
        role="main"
        className="intake-dashboard mx-auto max-w-4xl space-y-6 bg-white p-6 text-base leading-relaxed text-slate-950 [color-scheme:light]"
      >
        <style>{ACCESSIBILITY_CSS}</style>

        <header role="banner">
          <h1
            ref={this.headingRef}
            id={`${id}-boundary-title`}
            tabIndex={-1}
            className={`text-3xl font-bold ${FOCUS_STYLES}`}
          >
            Intake dashboard unavailable
          </h1>
        </header>

        <section
          role="alert"
          aria-live="assertive"
          aria-atomic="true"
          aria-labelledby={`${id}-boundary-error`}
          className="space-y-3 rounded-lg border-2 border-red-950 bg-red-50 p-5 text-red-950"
        >
          <h2
            id={`${id}-boundary-error`}
            className="text-xl font-bold"
          >
            The dashboard could not be displayed
          </h2>

          <p id={`${id}-boundary-help`}>
            Resetting clears local inputs and results. It does not
            automatically submit another request. A request already
            received by the server may still be processing.
          </p>
        </section>

        <LabeledButton
          id={`${id}-boundary-reset`}
          label="Reset dashboard"
          describedBy={`${id}-boundary-help`}
          onClick={this.resetDashboard}
          className="bg-slate-950 text-white hover:bg-slate-800"
        />
      </main>
    );
  }
}

/* ========================================================================== */
/* INTAKE_ENGINE_CORE_END                                                    */
/* ========================================================================== */

export default function IntakeDashboard(
  props: IntakeDashboardProps,
) {
  const id = useId();

  return (
    <DashboardErrorBoundary id={id}>
      <DashboardContent {...props} id={id} />
    </DashboardErrorBoundary>
  );
}
```

## `frontend/verify_contract.py`

```python
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
```

