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
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ── Types (mirror of backend/app/models/contracts.py) ────────────────────────

export type Priority = "Low" | "Medium" | "High";
export type Mode = "sandboxed" | "reasoning";

export interface Issue {
  id: string;
  title: string;
  description: string;
  priority: Priority;
  source: "scan" | "github_issue";
  category?: "security" | "correctness" | "performance" | "maintainability";
  file?: string;
  function?: string;
  github_url?: string;
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

export interface LogEvent {
  ts: number;
  source: string;
  level: "info" | "warn" | "error" | "result";
  message: string;
}

export interface ReproAttempt {
  attempt_id: string;
  issue_id: string;
  mode: Mode;
  status: "running" | "reproduced" | "not_reproducible" | "plausible" | "error";
  log: LogEvent[];
  root_cause?: string;
  confidence?: number;
}

export interface FixAttempt {
  candidate_id: string;
  approach: string;
  patch?: string;
  sandbox_status: "running" | "passed" | "failed" | "not_applicable";
  test_results?: { passed: number; failed: number; total: number };
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
}

// ── API helpers ───────────────────────────────────────────────────────────────

/** GET /api/health */
export const getHealth = () =>
  request<{ ok: boolean }>("/api/health");

/** POST /api/scan  — demo or GitHub URL */
export const postScan = (body: {
  source: "demo" | "github";
  repo_url?: string;
}) => request<ScanResult>("/api/scan", { method: "POST", body: JSON.stringify(body) });

/** POST /api/scan/upload  — multipart zip */
export const postScanUpload = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<ScanResult>("/api/scan/upload", {
    method: "POST",
    headers: {},          // let browser set Content-Type with boundary
    body: form,
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

/** POST /api/issues/{issue_id}/debug */
export const postDebug = (issueId: string, candidates: number) =>
  request<DebugSession>(`/api/issues/${issueId}/debug`, {
    method: "POST",
    body: JSON.stringify({ candidates }),
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
