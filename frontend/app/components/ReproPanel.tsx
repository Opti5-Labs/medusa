"use client";

import type { LogEvent, Mode, ReproAttempt } from "../../lib/api";
import Badge, { type Tone } from "./Badge";
import LogView from "./LogView";

const STATUS: Record<ReproAttempt["status"], { label: string; tone: Tone }> = {
  running: { label: "Running…", tone: "blue" },
  reproduced: { label: "Reproduced", tone: "red" },
  not_reproducible: { label: "Not reproducible", tone: "green" },
  plausible: { label: "Plausible (not executed)", tone: "amber" },
  error: { label: "Error", tone: "red" },
};

const INVESTIGATORS: Record<string, { label: string; tone: Tone }> = {
  bob_replay: { label: "Recorded Bob session", tone: "violet" },
  granite: { label: "Investigators: Granite (live)", tone: "blue" },
  unavailable: { label: "Investigators unavailable", tone: "amber" },
};

interface Props {
  mode: Mode;
  attempt: ReproAttempt | null;
  log: LogEvent[];
  error: string | null;
}

export default function ReproPanel({ mode, attempt, log, error }: Props) {
  const status = attempt?.status ?? "running";
  const investigators = attempt?.investigator_source ? INVESTIGATORS[attempt.investigator_source] : null;
  const finished = attempt && attempt.status !== "running";

  return (
    <section className="rounded-lg border border-gray-200 dark:border-gray-800 p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold mr-1">Reproduce</h3>
        <Badge tone={STATUS[status].tone}>{STATUS[status].label}</Badge>
        {investigators && <Badge tone={investigators.tone}>{investigators.label}</Badge>}
        <Badge tone={mode === "sandboxed" ? "green" : "amber"}>
          {mode === "sandboxed" ? "Runs in isolated sandbox" : "Analysis only · not executed"}
        </Badge>
      </div>

      <LogView events={log} />

      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      {finished && attempt.root_cause && (
        <div className="rounded-md bg-gray-50 dark:bg-gray-900 p-3 text-sm space-y-1">
          <p className="font-medium">
            {attempt.status === "not_reproducible"
              ? "Result"
              : attempt.investigator_source === "unavailable"
                ? "Reported cause (not independently diagnosed)"
                : mode === "sandboxed"
                  ? "Root cause"
                  : "Likely root cause"}
          </p>
          <p className="text-gray-700 dark:text-gray-300">{attempt.root_cause}</p>
          {attempt.confidence !== null && (
            <p className="text-xs text-gray-500">
              Confidence {Math.round(attempt.confidence * 100)}% — model estimate from reading the code; nothing was run.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
