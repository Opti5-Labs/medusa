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
  getDebugPatchUrl,
  openDebugStream,
  openReproStream,
  postDebug,
  postRepro,
} from "../../../lib/api";
import { useRunStream } from "../../../lib/useRunStream";
import Badge from "../../components/Badge";
import Icon from "../../components/Icon";
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
  // Per-issue, not per-scan: the demo mixes one sandboxed issue with several
  // reasoning-mode ones, so repo_source alone isn't enough to tell.
  const mode: Mode = issue?.mode ?? "reasoning";

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
    return <div className="empty-page">Loading…</div>;
  }
  if (!scan || !issue) {
    return (
      <div className="empty-page">
        <p>This issue is no longer available. Scans are kept for 30 minutes.</p>
        <Link href="/" className="btn btn-secondary">
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
    <div className="page">
      <header className="page-header">
        <Link href={`/issues?scan=${scan.scan_id}`} className="back-link"><Icon name="chevron" />Issues</Link>
        <div className="badges">
          <Badge tone={PRIORITY_TONE[issue.priority]}>{issue.priority}</Badge>
          <Badge>{issue.source === "github_issue" ? "GitHub Issue" : "Code scan"}</Badge>
          {issue.category && <Badge tone="blue">{issue.category}</Badge>}
          <Badge tone={mode === "sandboxed" ? "green" : "amber"}>{mode === "sandboxed" ? "Sandboxed" : "Analysis only"}</Badge>
        </div>
        <h2 className="title-1">{issue.title}</h2>
        <p className="page-lede">{issue.description}</p>
        {issue.file && (
          <p className="code-ref">
            {issue.file}
            {issue.line ? `:${issue.line}` : ""}
            {issue.function ? `, ${issue.function}()` : ""}
          </p>
        )}
      </header>

      {mode === "reasoning" && (
        <div className="notice">
          <p>
            <strong>Analysis only.</strong> This repository&apos;s code is read as text and never executed. Results
            are model reasoning, not reproductions, and patches are not verified.
          </p>
        </div>
      )}

      <section className="run-bar" aria-label="Run">
        <div className="actions">
          <button onClick={() => run("both")} disabled={running} className="btn btn-primary">
            Reproduce and Debug
          </button>
          <button onClick={() => run("repro")} disabled={running} className="btn btn-secondary">
            Reproduce
          </button>
          <button onClick={() => run("debug")} disabled={running} className="btn btn-secondary">
            Debug
          </button>
        </div>
        {mode === "sandboxed" ? (
          <label className="run-option">
            Candidates
            <select value={candidates} onChange={(e) => setCandidates(Number(e.target.value))} disabled={running}>
              {[2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="run-option">2 candidate patches for general repositories</span>
        )}
      </section>

      {startError && (
        <p role="alert" className="alert">
          {startError}
        </p>
      )}

      {state.repro.attempt && (
        <ReproPanel mode={state.repro.attempt.mode} attempt={state.repro.attempt} log={state.repro.log} error={state.repro.error} />
      )}

      {session && (
        <section className="section" aria-labelledby="debug-heading">
          <div className="section-head">
            <h3 id="debug-heading" className="headline">{session.mode === "sandboxed" ? "Debug race" : "Proposed fixes"}</h3>
            <div className="badges">
              <Badge tone={debugRunning ? "blue" : "gray"}>{debugRunning ? "Running…" : "Finished"}</Badge>
              {session.mode === "sandboxed" && reproduced && <Badge tone="green">Bug gate open</Badge>}
              {state.hidden.length > 0 && (
                <button onClick={() => dispatch({ type: "show-all" })} className="text-link">
                  Show {state.hidden.length} hidden
                </button>
              )}
            </div>
          </div>

          <LogView events={state.debug.log} maxHeight="max-h-40" emptyText="Starting…" />
          {state.debug.error && (
            <p role="alert" className="alert">
              {state.debug.error}
            </p>
          )}

          <div className="candidate-grid">
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
                patchUrl={
                  c.patch && (session.mode === "reasoning" || c.sandbox_status === "passed")
                    ? getDebugPatchUrl(session.session_id, c.candidate_id)
                    : null
                }
                canHide={visible.length > 2}
                onHide={() => dispatch({ type: "hide", candidateId: c.candidate_id })}
              />
            ))}
          </div>

          {state.debug.done && (
            <div className={`card recommendation ${state.debug.recommendation?.verified ? "is-verified" : ""}`}>
              {state.debug.recommendation ? (
                <>
                  <p className="headline">
                    Recommendation: {state.debug.recommendation.candidate_id}
                    <span className="recommendation-status">
                      {state.debug.recommendation.verified ? "Verified in sandbox" : "Not verified"}
                    </span>
                  </p>
                  <p className="issue-description">{state.debug.recommendation.reason}</p>
                  {session.mode === "sandboxed" && (
                    <p className="field-hint">You can download any other passing candidate from its panel.</p>
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
    <Suspense fallback={<div className="empty-page">Loading…</div>}>
      <Investigate />
    </Suspense>
  );
}
