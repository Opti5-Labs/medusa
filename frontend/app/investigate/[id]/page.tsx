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
import AutoOpenDetails from "../../components/AutoOpenDetails";
import Badge from "../../components/Badge";
import Icon from "../../components/Icon";
import CandidatePanel from "../../components/CandidatePanel";
import LogView from "../../components/LogView";
import ReproPanel from "../../components/ReproPanel";
import { ExpiredScan, LoadingState } from "../../components/EmptyState";
import { useScanGone } from "../../../lib/useScanGone";
import MoreText from "../../components/MoreText";

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

// ── Step tracker ─────────────────────────────────────────────────────────────

interface Step {
  label: string;
  state: "idle" | "running" | "done" | "failed";
}

/** Reproduce → fixes → recommendation, each showing where it is. Status, always visible. */
function RunSteps({ steps }: { steps: Step[] }) {
  return (
    <ol className="run-steps" aria-label="Progress">
      {steps.map((s) => (
        <li key={s.label} className={`run-step is-${s.state}`}>
          <span className="run-step-dot" aria-hidden="true">
            {s.state === "running" ? (
              <span className="holo-spinner" />
            ) : s.state === "done" ? (
              <Icon name="check" />
            ) : s.state === "failed" ? (
              <Icon name="close" />
            ) : null}
          </span>
          <span>
            {s.label}
            <span className="sr-only">
              {s.state === "idle" ? ", not started" : s.state === "running" ? ", running" : s.state === "done" ? ", done" : ", did not complete"}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
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
  const [debugHeld, setDebugHeld] = useState(false);
  const autoStarted = useRef(false);
  // Stable, so the memoized candidate panels skip renders for other panels' log lines.
  const hide = useCallback((candidateId: string) => dispatch({ type: "hide", candidateId }), []);

  useEffect(() => setScan(loadScan(scanId)), [scanId]);
  const gone = useScanGone(scanId);

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
  // A demo run that comes back instantly is played back step by step over a
  // few seconds. Real work (the sandbox running, Granite or Bob) is never
  // delayed: useRunStream only paces a run that arrives in one burst with no
  // model output. General repos are never paced.
  const demo = scan?.repo_source === "demo";
  const paceMs = demo ? 400 : 0;
  const attemptId = state.repro.attempt?.attempt_id ?? null;
  const reproOpen = useMemo(() => (attemptId ? () => openReproStream(attemptId) : null), [attemptId]);
  useRunStream<ReproAttempt>(reproOpen, {
    onReset: () => dispatch({ type: "repro/reset" }),
    onLog: (event) => dispatch({ type: "repro/log", event }),
    onDone: (attempt) => {
      dispatch({ type: "repro/done", attempt });
      if (chainDebug.current) {
        chainDebug.current = false;
        // A passing test of the correct behaviour is evidence against the report:
        // ask before proposing fixes for a bug that may not exist.
        if (attempt.no_evidence) setDebugHeld(true);
        else if (attempt.status === "reproduced" || attempt.status === "plausible") void startDebug();
      }
    },
    onError: (message) => dispatch({ type: "repro/error", message }),
  }, paceMs, demo ? 3500 : 0);

  const sessionId = state.debug.session?.session_id ?? null;
  const debugOpen = useMemo(() => (sessionId ? () => openDebugStream(sessionId) : null), [sessionId]);
  useRunStream<DebugDone>(debugOpen, {
    onReset: () => dispatch({ type: "debug/reset" }),
    onLog: (event) => dispatch({ type: "debug/log", event }),
    onDone: (done) => dispatch({ type: "debug/done", done }),
    onError: (message) => dispatch({ type: "debug/error", message }),
  }, paceMs, demo ? 4500 : 0);

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
    return <LoadingState />;
  }
  if (!scan || !issue || gone) {
    return (
      <ExpiredScan what="issue" reason={gone ?? undefined} />
    );
  }

  const session = state.debug.session;
  const visible = session?.candidates.filter((c) => !state.hidden.includes(c.candidate_id)) ?? [];
  const recommendedId = state.debug.recommendation?.candidate_id;
  const reproduced = state.repro.attempt?.status === "reproduced";
  // A general repo can still run: when the server allows it, reproduce upgrades
  // to a sandboxed result and the race tests real patches.
  const executed = state.repro.attempt?.mode === "sandboxed" || session?.mode === "sandboxed";

  // Where the run is, for the step tracker in the action card.
  const attempt = state.repro.attempt;
  const steps: Step[] = [
    {
      label: "Reproduce",
      state: !attempt
        ? "idle"
        : state.repro.error || attempt.status === "error"
          ? "failed"
          : attempt.status === "running"
            ? "running"
            : "done",
    },
    {
      label: (session?.mode ?? mode) === "sandboxed" ? "Race fixes" : "Propose fixes",
      state: !session ? "idle" : state.debug.error ? "failed" : state.debug.done ? "done" : "running",
    },
    {
      label: "Recommend",
      state: !state.debug.done ? "idle" : state.debug.recommendation ? "done" : "failed",
    },
  ];

  return (
    <div className="page">
      <header className="page-header">
        <Link href={`/issues?scan=${scan.scan_id}`} className="back-link"><Icon name="chevron" />Issues</Link>
        <div className="badges">
          <Badge tone={PRIORITY_TONE[issue.priority]}>{issue.priority}</Badge>
          <Badge>{issue.source === "github_issue" ? "GitHub Issue" : "Code scan"}</Badge>
          {issue.category && <Badge tone="blue">{issue.category}</Badge>}
          <Badge tone={mode === "sandboxed" || executed ? "green" : "amber"}>
            {mode === "sandboxed" || executed ? "Sandboxed" : "Analysis only"}
          </Badge>
        </div>
        <h2 className="title-1">{issue.title}</h2>
        <MoreText text={issue.description} className="page-lede" />
        {issue.file && (
          <p className="location-chip">
            <Icon name="code" aria-hidden="true" />
            {/* Break opportunities after each "/" so a long path wraps between folders, not mid-name. */}
            <span>
              {issue.file.replace(/\//g, "/\u200b")}
              {issue.line ? `:${issue.line}` : ""}
              {issue.function ? `, ${issue.function}()` : ""}
            </span>
          </p>
        )}
      </header>

      {mode === "reasoning" && !executed && (
        <div className="notice">
          <p>
            <strong>Analysis only.</strong> Unless a run below says it ran in the sandbox, the code is read as
            text: results are model reasoning, not reproductions, and patches are unverified.
          </p>
        </div>
      )}

      <section className="card run-card" aria-labelledby="run-heading">
        <div className="run-card-head">
          <div className="run-card-intro">
            <h3 id="run-heading" className="headline">Investigate this issue</h3>
            <p>
              {mode === "sandboxed"
                ? "Reproduce the bug in a sandbox, then race candidate fixes through the tests."
                : "Diagnose the bug from the code. Python projects with tests are also run in an isolated sandbox: a failing test confirms the bug, and patches are verified against it."}
            </p>
          </div>
          <RunSteps steps={steps} />
        </div>
        <div className="run-card-actions">
          <div className="actions">
            <button onClick={() => run("both")} disabled={running} className="btn btn-primary btn-lg">
              {running ? <span className="holo-spinner" aria-hidden="true" /> : <Icon name="play" aria-hidden="true" />}
              {running ? "Running…" : "Reproduce and Debug"}
            </button>
            <button onClick={() => run("repro")} disabled={running} className="btn btn-secondary btn-lg">
              Reproduce only
            </button>
            <button onClick={() => run("debug")} disabled={running} className="btn btn-secondary btn-lg">
              Debug only
            </button>
          </div>
          {mode === "sandboxed" ? (
            <div className="run-option">
              <span id="candidates-label">Candidate fixes</span>
              <div className="segmented" role="radiogroup" aria-labelledby="candidates-label">
                {[2, 3, 4, 5, 6].map((n) => (
                  <button
                    key={n}
                    type="button"
                    role="radio"
                    aria-checked={candidates === n}
                    disabled={running}
                    onClick={() => setCandidates(n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <span className="run-option">2 candidate patches</span>
          )}
        </div>
      </section>

      {startError && (
        <p role="alert" className="alert">
          {startError}
        </p>
      )}

      {state.repro.attempt && (
        <ReproPanel mode={state.repro.attempt.mode} attempt={state.repro.attempt} log={state.repro.log} error={state.repro.error} />
      )}

      {debugHeld && !session && (
        <div className="notice" role="status">
          <p>
            <strong>No evidence of this bug.</strong> A test of the correct behaviour passed on the original code, so the
            issue may not exist as described. Fixes were not proposed.
          </p>
          <div className="actions">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={running}
              onClick={() => {
                setDebugHeld(false);
                void startDebug();
              }}
            >
              Propose fixes anyway
            </button>
          </div>
        </div>
      )}

      {session && (
        <section className="card step-card" aria-labelledby="debug-heading">
          <header className="step-head">
            <span className="tile purple"><Icon name="bolt" /></span>
            <div className="step-head-text">
              <div className="step-title-row">
                <h3 id="debug-heading" className="headline">{session.mode === "sandboxed" ? "Debug race" : "Proposed fixes"}</h3>
                <Badge tone={debugRunning ? "blue" : state.debug.error ? "red" : "gray"}>
                  {debugRunning ? "Running…" : state.debug.error ? "Error" : "Finished"}
                </Badge>
              </div>
              <p className="step-meta">
                {[
                  `${session.candidates.length} candidates`,
                  session.mode === "sandboxed"
                    ? "each patch runs the tests in its own sandbox"
                    : session.candidates.some((c) => c.test_results)
                      ? "checked against existing tests only; fixes not verified"
                      : "patches are not applied or tested",
                  session.mode === "sandboxed" && reproduced ? "bug gate open" : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </div>
            {state.hidden.length > 0 && (
              <button onClick={() => dispatch({ type: "show-all" })} className="btn btn-secondary btn-sm step-head-action">
                Show {state.hidden.length} hidden
              </button>
            )}
          </header>

          {state.debug.done && (
            <div className={`recommendation ${state.debug.recommendation?.verified ? "is-verified" : ""}`}>
              {state.debug.recommendation ? (
                <>
                  <span className="recommendation-icon" aria-hidden="true">
                    <Icon name={state.debug.recommendation.verified ? "check" : "spark"} />
                  </span>
                  <div className="recommendation-text">
                    <p className="recommendation-title">
                      Use {state.debug.recommendation.candidate_id}
                      <span className="recommendation-status">
                        {state.debug.recommendation.verified ? "Verified in sandbox" : "Not verified"}
                      </span>
                    </p>
                    <p className="panel-text">{state.debug.recommendation.reason}</p>
                    {session.mode === "sandboxed" && (
                      <p className="field-hint">Other passing candidates can be downloaded from their cards.</p>
                    )}
                  </div>
                  <a href={`#candidate-${state.debug.recommendation.candidate_id}`} className="btn btn-secondary btn-sm">
                    View {state.debug.recommendation.candidate_id}
                    <Icon name="arrow" aria-hidden="true" />
                  </a>
                </>
              ) : (
                <p className="panel-text">No candidate is recommended for this run.</p>
              )}
            </div>
          )}

          {state.debug.error && (
            <p role="alert" className="alert">
              {state.debug.error}
            </p>
          )}

          <AutoOpenDetails openWhen={debugRunning}>
            <summary>
              <Icon name="chevron" />
              Session log{state.debug.log.length > 0 && <span className="disclosure-count">{state.debug.log.length}</span>}
            </summary>
            <LogView events={state.debug.log} maxHeight="max-h-40" emptyText="Starting…" busy={debugRunning} />
          </AutoOpenDetails>

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
                  mode === "sandboxed" && c.sandbox_status === "passed"
                    ? getDebugDownloadUrl(session.session_id, c.candidate_id)
                    : null
                }
                patchUrl={
                  c.patch && (session.mode === "reasoning" || c.sandbox_status === "passed")
                    ? getDebugPatchUrl(session.session_id, c.candidate_id)
                    : null
                }
                canHide={visible.length > 2}
                onHide={hide}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

export default function InvestigatePage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <Investigate />
    </Suspense>
  );
}
