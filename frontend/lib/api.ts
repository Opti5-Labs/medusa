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

/** GET /api/debug/{session_id}/download?candidate_id= */
export const getDebugDownloadUrl = (
  sessionId: string,
  candidateId: string
): string =>
  `${BASE}/api/debug/${sessionId}/download?candidate_id=${encodeURIComponent(candidateId)}`;
