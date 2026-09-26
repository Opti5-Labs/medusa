"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  type DebugDone,
  type DebugSession,
  type Issue,
  type LogEvent,
  type Mode,
  type Recommendation,
  type ReproAttempt,
  type ScanResult,
  getDebugDownloadUrl,
  openDebugStream,
  openReproStream,
  postDebug,
  postRepro,
} from "../../../lib/api";
import { useRunStream } from "../../../lib/useRunStream";
import Badge from "../../components/Badge";
import CandidatePanel from "../../components/CandidatePanel";
import LogView from "../../components/LogView";
import ReproPanel from "../../components/ReproPanel";

// ── State ────────────────────────────────────────────────────────────────────

interface State {
  repro: { attempt: ReproAttempt | null; log: LogEvent[]; error: string | null };
  debug: {
    session: DebugSession | null;
    log: LogEvent[]; // gate, granite, recommendation and other session-level events
    candidateLogs: Record<string, LogEvent[]>;
    recommendation: Recommendation | null;
    done: boolean;
    error: string | null;
  };
  hidden: string[];
}

type Action =
  | { type: "repro/start"; attempt: ReproAttempt }
  | { type: "repro/reset" }
  | { type: "repro/log"; event: LogEvent }
  | { type: "repro/done"; attempt: ReproAttempt }
  | { type: "repro/error"; message: string }
  | { type: "debug/start"; session: DebugSession }
  | { type: "debug/reset" }
  | { type: "debug/log"; event: LogEvent }
  | { type: "debug/done"; done: DebugDone }
  | { type: "debug/error"; message: string }
  | { type: "hide"; candidateId: string }
  | { type: "show-all" };

const emptyDebug: State["debug"] = {
  session: null,
  log: [],
  candidateLogs: {},
  recommendation: null,
  done: false,
  error: null,
};

const initialState: State = {
  repro: { attempt: null, log: [], error: null },
  debug: emptyDebug,
  hidden: [],
};

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "repro/start":
      return { ...state, repro: { attempt: action.attempt, log: [], error: null } };
    case "repro/reset":
      return { ...state, repro: { ...state.repro, log: [] } };
    case "repro/log":
      return { ...state, repro: { ...state.repro, log: [...state.repro.log, action.event] } };
    case "repro/done":
      return { ...state, repro: { ...state.repro, attempt: action.attempt } };
    case "repro/error":
      return { ...state, repro: { ...state.repro, error: action.message } };
    case "debug/start":
      return { ...state, debug: { ...emptyDebug, session: action.session }, hidden: [] };
    case "debug/reset":
      return { ...state, debug: { ...state.debug, log: [], candidateLogs: {} } };
    case "debug/log": {
      const { event } = action;
      if (event.source.startsWith("candidate:")) {
        const cid = event.source.slice("candidate:".length);
        const logs = state.debug.candidateLogs;
        return {
          ...state,
          debug: { ...state.debug, candidateLogs: { ...logs, [cid]: [...(logs[cid] ?? []), event] } },
        };
      }
      return { ...state, debug: { ...state.debug, log: [...state.debug.log, event] } };
    }
    case "debug/done":
      return {
        ...state,
        debug: {
          ...state.debug,
          session: action.done.session,
          recommendation: action.done.recommendation,
          done: true,
        },
      };
    case "debug/error":
      return { ...state, debug: { ...state.debug, error: action.message } };
    case "hide":
      return { ...state, hidden: [...state.hidden, action.candidateId] };
    case "show-all":
      return { ...state, hidden: [] };
  }
}

// ── Page ─────────────────────────────────────────────────────────────────────

const PRIORITY_TONE = { High: "red", Medium: "amber", Low: "green" } as const;

function loadScan(scanId: string | null): ScanResult | null {
  if (!scanId) return null;
  try {
    const raw = sessionStorage.getItem(`scan:${scanId}`);
    return raw ? (JSON.parse(raw) as ScanResult) : null;
  } catch {
    return null;
  }
}

function Investigate() {
  const { id: issueId } = useParams<{ id: string }>();
  const params = useSearchParams();
  const scanId = params.get("scan");
  const autoAction = params.get("action");

  const [scan, setScan] = useState<ScanResult | null | undefined>(undefined);
  const [state, dispatch] = useReducer(reducer, initialState);
  const [candidates, setCandidates] = useState(4);
  const [busy, setBusy] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const chainDebug = useRef(false);
  const autoStarted = useRef(false);

  useEffect(() => setScan(loadScan(scanId)), [scanId]);

  const issue: Issue | undefined = scan?.issues.find((i) => i.id === issueId);
  const mode: Mode = scan?.repo_source === "demo" ? "sandboxed" : "reasoning";

  const startDebug = useCallback(async () => {
    setStartError(null);
    try {
      const session = await postDebug(issueId, mode === "sandboxed" ? candidates : undefined);
      dispatch({ type: "debug/start", session });
    } catch (err) {
      setStartError(err instanceof Error ? err.message : "Could not start debug.");
    }
  }, [issueId, mode, candidates]);

  const startRepro = useCallback(
    async (thenDebug: boolean) => {
      setStartError(null);
      chainDebug.current = thenDebug;
      try {
        const attempt = await postRepro(issueId);
        dispatch({ type: "repro/start", attempt });
      } catch (err) {
        chainDebug.current = false;
        setStartError(err instanceof Error ? err.message : "Could not start reproduce.");
      }
    },
    [issueId],
  );

  // ── Streams ──
  const attemptId = state.repro.attempt?.attempt_id ?? null;
  const reproOpen = useMemo(() => (attemptId ? () => openReproStream(attemptId) : null), [attemptId]);
  useRunStream<ReproAttempt>(reproOpen, {
    onReset: () => dispatch({ type: "repro/reset" }),
    onLog: (event) => dispatch({ type: "repro/log", event }),
    onDone: (attempt) => {
      dispatch({ type: "repro/done", attempt });
      if (chainDebug.current) {
        chainDebug.current = false;
        if (attempt.status === "reproduced" || attempt.status === "plausible") void startDebug();
      }
    },
    onError: (message) => dispatch({ type: "repro/error", message }),
  });

  const sessionId = state.debug.session?.session_id ?? null;
  const debugOpen = useMemo(() => (sessionId ? () => openDebugStream(sessionId) : null), [sessionId]);
  useRunStream<DebugDone>(debugOpen, {
    onReset: () => dispatch({ type: "debug/reset" }),
    onLog: (event) => dispatch({ type: "debug/log", event }),
    onDone: (done) => dispatch({ type: "debug/done", done }),
    onError: (message) => dispatch({ type: "debug/error", message }),
  });

  const reproRunning = state.repro.attempt?.status === "running" && !state.repro.error;
  const debugRunning = !!state.debug.session && !state.debug.done && !state.debug.error;
  const running = busy || reproRunning || debugRunning;

  const run = useCallback(
    async (action: string) => {
      setBusy(true);
      try {
        if (action === "debug") await startDebug();
        else await startRepro(action === "both");
      } finally {
        setBusy(false);
      }
    },
    [startDebug, startRepro],
  );

  // Start the action chosen on the issue list once.
  useEffect(() => {
    if (issue && autoAction && !autoStarted.current) {
      autoStarted.current = true;
      void run(autoAction);
    }
  }, [issue, autoAction, run]);

  if (scan === undefined) {
    return <p className="py-16 text-center text-gray-500">Loading…</p>;
  }
  if (!scan || !issue) {
    return (
      <div className="py-16 text-center space-y-4">
        <p className="text-gray-500 dark:text-gray-400">This issue is no longer available. Scans expire after 30 minutes.</p>
        <Link href="/" className="inline-block px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-700 text-sm">
          Start a new scan
        </Link>
      </div>
    );
  }

  const session = state.debug.session;
  const visible = session?.candidates.filter((c) => !state.hidden.includes(c.candidate_id)) ?? [];
  const recommendedId = state.debug.recommendation?.candidate_id;
  const reproduced = state.repro.attempt?.status === "reproduced";

  return (
    <div className="space-y-6">
      <Link href={`/issues?scan=${scan.scan_id}`} className="inline-block text-sm text-gray-500 hover:text-gray-900 dark:hover:text-gray-100">
        Back to issues
      </Link>

      {/* Issue */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={PRIORITY_TONE[issue.priority]}>{issue.priority}</Badge>
          <Badge>{issue.source === "github_issue" ? "GitHub Issue" : "Code scan"}</Badge>
          {issue.category && <Badge tone="blue">{issue.category}</Badge>}
        </div>
        <h2 className="text-2xl font-semibold">{issue.title}</h2>
        <p className="text-gray-600 dark:text-gray-400">{issue.description}</p>
        {issue.file && (
          <p className="text-xs text-gray-500 font-mono">
            {issue.file}
            {issue.line ? `:${issue.line}` : ""}
            {issue.function ? `, ${issue.function}()` : ""}
          </p>
        )}
      </section>

      {mode === "reasoning" && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/10 p-3 text-sm text-amber-800 dark:text-amber-300">
          <strong>Analysis only.</strong> This repository&apos;s code is read as text and never executed. Results
          are model reasoning, not reproductions, and patches are not verified.
        </div>
      )}

      {/* Actions */}
      <section className="flex flex-wrap items-end gap-3">
        <button
          onClick={() => run("repro")}
          disabled={running}
          className="px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-700 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-900 disabled:opacity-50"
        >
          Reproduce
        </button>
        <button
          onClick={() => run("debug")}
          disabled={running}
          className="px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-700 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-900 disabled:opacity-50"
        >
          Debug
        </button>
        <button
          onClick={() => run("both")}
          disabled={running}
          className="px-4 py-2 rounded-lg bg-verdigris-600 text-white hover:bg-verdigris-700 dark:bg-verdigris-600 dark:hover:bg-verdigris-700 text-sm font-medium disabled:opacity-50"
        >
          Reproduce and Debug
        </button>
        {mode === "sandboxed" ? (
          <label className="text-sm text-gray-600 dark:text-gray-400 flex items-center gap-2">
            Candidates
            <select
              value={candidates}
              onChange={(e) => setCandidates(Number(e.target.value))}
              disabled={running}
              className="rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1.5"
            >
              {[2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="text-xs text-gray-500">2 candidate patches for general repositories</span>
        )}
      </section>

      {startError && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {startError}
        </p>
      )}

      {state.repro.attempt && (
        <ReproPanel mode={state.repro.attempt.mode} attempt={state.repro.attempt} log={state.repro.log} error={state.repro.error} />
      )}

      {/* Debug race */}
      {session && (
        <section className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold mr-1">{session.mode === "sandboxed" ? "Debug race" : "Proposed fixes"}</h3>
            <Badge tone={debugRunning ? "blue" : "gray"}>{debugRunning ? "Running…" : "Finished"}</Badge>
            {session.mode === "sandboxed" && reproduced && <Badge tone="green">Bug gate open</Badge>}
            {state.hidden.length > 0 && (
              <button onClick={() => dispatch({ type: "show-all" })} className="text-xs text-blue-600 dark:text-blue-400 hover:underline">
                Show {state.hidden.length} hidden
              </button>
            )}
          </div>

          <LogView events={state.debug.log} maxHeight="max-h-40" emptyText="Starting…" />
          {state.debug.error && (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {state.debug.error}
            </p>
          )}

          <div className={`grid gap-3 ${visible.length > 2 ? "md:grid-cols-2 xl:grid-cols-3" : "md:grid-cols-2"}`}>
            {visible.map((c) => (
              <CandidatePanel
                key={c.candidate_id}
                candidate={c}
                mode={session.mode}
                log={state.debug.candidateLogs[c.candidate_id] ?? []}
                recommended={state.debug.done && c.candidate_id === recommendedId}
                finished={state.debug.done}
                downloadUrl={
                  session.mode === "sandboxed" && c.sandbox_status === "passed"
                    ? getDebugDownloadUrl(session.session_id, c.candidate_id)
                    : null
                }
                canHide={visible.length > 2}
                onHide={() => dispatch({ type: "hide", candidateId: c.candidate_id })}
              />
            ))}
          </div>

          {state.debug.done && (
            <div
              className={`rounded-lg p-4 text-sm ${
                state.debug.recommendation?.verified
                  ? "border border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-900/10"
                  : "border border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-900"
              }`}
            >
              {state.debug.recommendation ? (
                <>
                  <p className="font-semibold">
                    Recommendation: {state.debug.recommendation.candidate_id}
                    {state.debug.recommendation.verified ? " (verified in sandbox)" : " (not verified)"}
                  </p>
                  <p className="mt-1 text-gray-700 dark:text-gray-300">{state.debug.recommendation.reason}</p>
                  {session.mode === "sandboxed" && (
                    <p className="mt-1 text-xs text-gray-500">You can download any other passing candidate from its panel.</p>
                  )}
                </>
              ) : (
                <p>No candidate is recommended for this run.</p>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

export default function InvestigatePage() {
  return (
    <Suspense fallback={<p className="py-16 text-center text-gray-500">Loading…</p>}>
      <Investigate />
    </Suspense>
  );
}
