"use client";

import { useEffect, useMemo, useReducer, useState } from "react";
import {
  ArchitectureReport,
  LogEvent,
  openArchitectureStream,
  postArchitecture,
} from "./api";
import { useRunStream } from "./useRunStream";

interface State {
  report: ArchitectureReport | null;
  log: LogEvent[];
  error: string | null;
}

type Action =
  | { type: "reset" }
  | { type: "log"; event: LogEvent }
  | { type: "done"; report: ArchitectureReport }
  | { type: "error"; message: string };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "reset":
      return { report: null, log: [], error: null };
    case "log":
      return { ...state, log: [...state.log, action.event] };
    case "done":
      return { ...state, report: action.report, error: null };
    case "error":
      return { ...state, error: action.message };
    default:
      return state;
  }
}

/**
 * Starts (or reuses, per the server's per-scan dedup) an architecture run for
 * *scanId* and follows it over SSE, same pattern as repro/debug. Shared
 * between the compact summary on the issues page and the full /architecture
 * page so both stay in sync with exactly one implementation.
 */
export function useArchitecture(scanId: string | null) {
  const [architectureId, setArchitectureId] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [state, dispatch] = useReducer(reducer, { report: null, log: [], error: null });

  useEffect(() => {
    if (!scanId) return;
    let cancelled = false;
    setArchitectureId(null);
    setStartError(null);
    postArchitecture(scanId)
      .then((report) => {
        if (cancelled) return;
        setArchitectureId(report.architecture_id);
        if (report.status !== "running") {
          dispatch({ type: "done", report });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setStartError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [scanId]);

  const openStream = useMemo(
    () => (architectureId ? () => openArchitectureStream(architectureId) : null),
    [architectureId]
  );

  useRunStream<ArchitectureReport>(openStream, {
    onReset: () => dispatch({ type: "reset" }),
    onLog: (event) => dispatch({ type: "log", event }),
    onDone: (report) => dispatch({ type: "done", report }),
    onError: (message) => dispatch({ type: "error", message }),
  });

  const { report, log } = state;
  return {
    report,
    log,
    error: startError ?? state.error,
    running: !report || report.status === "running",
  };
}
