/**
 * Thin fetch wrapper. All API calls go through here.
 *
 * Base URL is read from NEXT_PUBLIC_API_URL (set in .env.local).
 * Falls back to http://localhost:8000 for local development.
 *
 * Mirrors the contracts in backend/app/models/contracts.py.
 */

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  // Only set JSON content-type when the body is NOT FormData
  // (FormData needs the boundary that the browser sets automatically)
  const isFormData = init?.body instanceof FormData;
  const headers: HeadersInit = isFormData
    ? { ...init?.headers }
    : { "Content-Type": "application/json", ...init?.headers };

  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    // Read the body once; it may be JSON ({"detail": ...}) or plain text.
    const text = await res.text().catch(() => "");
    let detail = text || res.statusText;
    try {
      const json = JSON.parse(text);
      if (typeof json?.detail === "string") detail = json.detail;
      else if (Array.isArray(json?.detail)) detail = json.detail.map((d: { msg?: string }) => d.msg).join("; ");
    } catch {
      // not JSON: keep the text
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ── Types (mirror of backend/app/models/contracts.py) ────────────────────────
// Optional Python fields arrive as null, so they are typed `T | null`.

export type Priority = "Low" | "Medium" | "High";
export type Mode = "sandboxed" | "reasoning";
export type InvestigatorSource = "bob_replay" | "bob" | "granite" | "bob_and_granite" | "unavailable";
export type CandidateOrigin = "bob" | "granite" | "prepared";

export interface Issue {
  id: string;
  title: string;
  description: string;
  priority: Priority;
  source: "scan" | "github_issue";
  category: "security" | "correctness" | "performance" | "maintainability" | null;
  file: string | null;
  function: string | null;
  line: number | null;
  github_url: string | null;
  found_by: "granite" | "bob" | null; // scan issues only
  mode: Mode; // whether THIS issue's Reproduce/Debug run sandboxed or reasoning
}

export interface ScanResult {
  scan_id: string;
  repo_source: "demo" | "github" | "zip";
  language: string;
  files_scanned: string[];
  files_total: number;
  issues: Issue[];
  warnings: string[];
}

export type LogLevel = "info" | "warn" | "error" | "result";

export interface LogEvent {
  ts: number;
  source: string;
  level: LogLevel;
  message: string;
}

/** One investigator's independent result. Confidence is self-reported and never ranks patches. */
export interface InvestigatorReport {
  investigator: "bob" | "granite";
  status: "ok" | "unavailable" | "error" | "limit";
  root_cause: string | null;
  evidence: string[];
  confidence: number | null;
  proposed_fixes: number;
  error: string | null;
  cost: number | null;
  recorded: boolean;
  trigger_conditions: string | null; // demo mode only
  execution_trace: string[]; // demo mode only
}

export type ReproStatus = "running" | "reproduced" | "not_reproducible" | "plausible" | "error";

export interface ReproAttempt {
  attempt_id: string;
  issue_id: string;
  mode: Mode;
  status: ReproStatus;
  log: LogEvent[];
  root_cause: string | null;
  confidence: number | null;
  investigator_source: InvestigatorSource | null;
  investigators: InvestigatorReport[];
}

export interface TestResults {
  passed: number;
  failed: number;
  total: number;
  reproducer_fixed: boolean;
  regressions: string[];
}

export interface PatchStats {
  files_changed: number;
  lines_added: number;
  lines_removed: number;
}

export type SandboxStatus = "running" | "passed" | "failed" | "not_applicable";

export interface FixAttempt {
  candidate_id: string;
  approach: string;
  patch: string | null;
  sandbox_status: SandboxStatus;
  test_results: TestResults | null;
  patch_stats: PatchStats | null;
  origin: CandidateOrigin | null;
  attempts: number; // 2 when revised once after failing tests
  error: string | null;
  active: boolean;
}

export interface DebugSession {
  session_id: string;
  issue_id: string;
  mode: Mode;
  candidates: FixAttempt[];
}

export interface Recommendation {
  candidate_id: string;
  reason: string;
  verified: boolean;
}

/** Payload of the final `done` event on /api/debug/{session_id}/events. */
export interface DebugDone {
  session: DebugSession;
  recommendation: Recommendation | null;
}

export type Grounding = "scan_data" | "sandbox_verified" | "reasoning";

export interface AskCitation {
  file: string;
  line: number | null;
}

export interface AskStart {
  ask_id: string;
}

export interface AskStatus {
  enabled: boolean;
  granite_available: boolean;
  bob_available: boolean;
  bob_reason: string | null;
}

/** scan_data only when answered_by is "scan"; sandbox_verified only for a sandbox-reproduced demo issue. */
export interface AskAnswer {
  ask_id: string;
  question: string;
  answer: string; // markdown; empty when error is set
  grounding: Grounding;
  answered_by: "scan" | "granite" | "bob";
  citations: AskCitation[];
  files_read: string[];
  issue_id: string | null;
  cost: number | null; // Bobcoins, Bob answers only
  notice: string | null;
  error: string | null;
}

// ── API helpers ───────────────────────────────────────────────────────────────

/** GET /api/health */
export const getHealth = () =>
  request<{ ok: boolean }>("/api/health");

/** POST /api/scan  — demo or GitHub URL */
export const postScan = (
  body: { source: "demo" | "github"; repo_url?: string },
  signal?: AbortSignal
) => request<ScanResult>("/api/scan", { method: "POST", body: JSON.stringify(body), signal });

/** POST /api/scan/upload  — multipart zip */
export const postScanUpload = (file: File, signal?: AbortSignal) => {
  const form = new FormData();
  form.append("file", file);
  return request<ScanResult>("/api/scan/upload", {
    method: "POST",
    body: form,
    signal,
  });
};

/** POST /api/issues/{issue_id}/repro */
export const postRepro = (issueId: string) =>
  request<ReproAttempt>(`/api/issues/${issueId}/repro`, { method: "POST" });

/**
 * GET /api/repro/{attempt_id}/events — SSE stream.
 * Returns an EventSource; caller is responsible for closing it on unmount.
 */
export const openReproStream = (attemptId: string): EventSource =>
  new EventSource(`${BASE}/api/repro/${attemptId}/events`);

/** POST /api/issues/{issue_id}/debug — omit candidates for the server default. */
export const postDebug = (issueId: string, candidates?: number) =>
  request<DebugSession>(`/api/issues/${issueId}/debug`, {
    method: "POST",
    body: JSON.stringify(candidates ? { candidates } : {}),
  });

/**
 * GET /api/debug/{session_id}/events — SSE stream.
 * Returns an EventSource; caller is responsible for closing it on unmount.
 */
export const openDebugStream = (sessionId: string): EventSource =>
  new EventSource(`${BASE}/api/debug/${sessionId}/events`);

/** GET /api/ask/status */
export const getAskStatus = () => request<AskStatus>("/api/ask/status");

/** POST /api/scan/{scan_id}/ask — omit issue_id to ask about the whole repo. */
export const postAsk = (scanId: string, body: { question: string; issue_id?: string }) =>
  request<AskStart>(`/api/scan/${encodeURIComponent(scanId)}/ask`, {
    method: "POST",
    body: JSON.stringify(body),
  });

/**
 * GET /api/ask/{ask_id}/events — SSE stream (log, token, then done with an AskAnswer).
 * Returns an EventSource; caller is responsible for closing it on unmount.
 */
export const openAskStream = (askId: string): EventSource =>
  new EventSource(`${BASE}/api/ask/${encodeURIComponent(askId)}/events`);

/** GET /api/debug/{session_id}/download?candidate_id= */
export const getDebugDownloadUrl = (
  sessionId: string,
  candidateId: string
): string =>
  `${BASE}/api/debug/${sessionId}/download?candidate_id=${encodeURIComponent(candidateId)}`;

// ── Architecture (mirror of the same section in backend/app/models/contracts.py) ─

export type ArchitectureStatus = "running" | "complete" | "partial" | "unavailable" | "error";
export type ArchitectureSource = "curated" | "static" | "static_and_model";
export type Assisted = "deterministic" | "model";

export type ComponentType =
  | "frontend" | "backend" | "api_layer" | "service" | "library" | "data_store"
  | "external_service" | "worker" | "cli" | "config" | "infrastructure"
  | "tests" | "docs" | "unknown";

export type RelationshipType =
  | "imports" | "calls" | "serves" | "persists_to" | "publishes_to"
  | "configures" | "deploys" | "depends_on";

export interface EvidenceRef {
  path: string;
  line: number | null;
  end_line: number | null;
  note: string | null;
  verified: boolean;
  url: string | null;
}

export interface ArchitectureComponent {
  id: string;
  label: string;
  type: ComponentType;
  description: string;
  paths: string[];
  evidence: EvidenceRef[];
  confidence: number;
  assisted_by: Assisted;
  file_count: number;
  rank: number;
}

export interface ArchitectureRelationship {
  source: string;
  target: string;
  type: RelationshipType;
  explanation: string;
  evidence: EvidenceRef[];
  confidence: number;
  assisted_by: Assisted;
}

export interface TechnologyStack {
  languages: string[];
  frameworks: string[];
  build_systems: string[];
  package_managers: string[];
  test_frameworks: string[];
  unsupported_languages: string[];
}

export interface Entrypoint {
  path: string;
  kind: "http_server" | "cli" | "worker" | "web_app" | "script" | "container" | "unknown";
  detail: string;
  evidence: EvidenceRef[];
}

export interface DeploymentArtifact {
  kind: "dockerfile" | "compose" | "ci_workflow" | "iac" | "systemd" | "webserver" | "other";
  path: string;
  detail: string;
  services: string[];
}

export interface ExternalService {
  name: string;
  detail: string;
  evidence: EvidenceRef[];
  confidence: number;
  assisted_by: Assisted;
}

export interface DataStore {
  name: string;
  kind: "relational" | "document" | "key_value" | "vector" | "object_store" | "file" | "unknown";
  detail: string;
  evidence: EvidenceRef[];
  confidence: number;
  assisted_by: Assisted;
}

export interface ArchitectureCoverage {
  files_discovered: number;
  source_files_discovered: number;
  source_files_supported: number;
  files_considered: number;
  files_parsed: number;
  files_skipped: number;
  source_bytes: number;
  source_lines: number;
  parse_rate: number;
  parse_failures: number;
  tier: 1 | 2 | 3;
  limit_exceeded: string | null;
  skipped_reasons: Record<string, number>;
  languages_parsed: Record<string, number>;
  languages_not_parsed: Record<string, number>;
}

export interface ArchitectureReport {
  architecture_id: string;
  scan_id: string;
  status: ArchitectureStatus;
  source: ArchitectureSource;
  repo_source: "demo" | "github" | "zip";
  summary: string;
  technology_stack: TechnologyStack;
  entrypoints: Entrypoint[];
  components: ArchitectureComponent[];
  relationships: ArchitectureRelationship[];
  external_services: ExternalService[];
  data_stores: DataStore[];
  deployment: DeploymentArtifact[];
  mermaid: string;
  detail_mermaid: string | null;
  coverage: ArchitectureCoverage | null;
  warnings: string[];
  limitations: string[];
  narrowing_suggestions: string[];
  files_considered: string[];
  files_parsed: string[];
  files_skipped: string[];
  curated_version: string | null;
  log: LogEvent[];
  generated_at: number;
}

/** POST /api/scans/{scan_id}/architecture — idempotent per scan. */
export const postArchitecture = (scanId: string) =>
  request<ArchitectureReport>(`/api/scans/${scanId}/architecture`, { method: "POST" });

/** GET /api/architecture/{id} — the final report (reconnect / no-SSE fallback). */
export const getArchitecture = (id: string) =>
  request<ArchitectureReport>(`/api/architecture/${id}`);

/**
 * GET /api/architecture/{id}/events — SSE stream.
 * Returns an EventSource; caller is responsible for closing it on unmount.
 */
export const openArchitectureStream = (id: string): EventSource =>
  new EventSource(`${BASE}/api/architecture/${id}/events`);

/** GET /api/architecture/{id}/download?format= */
export const getArchitectureDownloadUrl = (id: string, format: "mermaid" | "json"): string =>
  `${BASE}/api/architecture/${id}/download?format=${format}`;
