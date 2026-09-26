"use client";

import type { InvestigatorReport, LogEvent, Mode, ReproAttempt } from "../../lib/api";
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
  bob: { label: "Investigator: Bob (live)", tone: "violet" },
  granite: { label: "Investigator: Granite (live)", tone: "blue" },
  bob_and_granite: { label: "Investigators: Bob + Granite (independent)", tone: "violet" },
  unavailable: { label: "Investigators unavailable", tone: "amber" },
};

const REPORT_STATUS: Record<InvestigatorReport["status"], { label: string; tone: Tone }> = {
  ok: { label: "Diagnosed", tone: "green" },
  unavailable: { label: "Unavailable", tone: "amber" },
  error: { label: "Failed", tone: "red" },
  limit: { label: "Stopped at limit", tone: "amber" },
};

function InvestigatorCard({ report }: { report: InvestigatorReport }) {
  const name = report.investigator === "bob" ? "IBM Bob" : "Granite";
  const status = REPORT_STATUS[report.status];
  return (
    <div className="inset-card">
      <div className="badges">
        <span className="inset-card-title">{name}</span>
        <Badge tone={status.tone}>{status.label}</Badge>
        {report.recorded && <Badge tone="violet">Recorded session</Badge>}
      </div>
      {report.root_cause && <p className="panel-text">{report.root_cause}</p>}
      {report.error && <p className="field-error">{report.error}</p>}
      {report.trigger_conditions && (
        <p className="panel-small">
          <span className="panel-small-label">Triggers when:</span> {report.trigger_conditions}
        </p>
      )}
      {report.execution_trace.length > 0 && (
        <ol className="panel-list panel-list-numbered">
          {report.execution_trace.map((step, i) => (
            <li key={i} className="code-ref">{step}</li>
          ))}
        </ol>
      )}
      {report.evidence.length > 0 && (
        <ul className="panel-list">
          {report.evidence.map((item, i) => (
            <li key={i} className="code-ref">{item}</li>
          ))}
        </ul>
      )}
      <p className="panel-meta">
        {report.confidence !== null && <span>Self-reported confidence {Math.round(report.confidence * 100)}% (not used to pick a fix)</span>}
        {report.proposed_fixes > 0 && <span>{report.proposed_fixes} proposed fix(es)</span>}
        {report.cost !== null && <span>{report.cost} Bobcoins</span>}
      </p>
    </div>
  );
}

function firstSentence(text: string, maxLen = 160): string {
  // A terminator only counts at a real sentence boundary (followed by
  // whitespace or end of string) — otherwise "the value in .env.example"
  // or "_resolve_hf_asr_model()" would cut the summary off mid-word.
  const m = text.match(/^[^.!?]*[.!?](?=\s|$)/);
  const sentence = (m ? m[0] : text).trim();
  return sentence.length > maxLen ? sentence.slice(0, maxLen - 1).trimEnd() + "…" : sentence;
}

function elapsedSeconds(log: LogEvent[]): number | null {
  if (log.length < 2) return null;
  return Math.round((log[log.length - 1].ts - log[0].ts) * 10) / 10;
}

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
  const elapsed = finished ? elapsedSeconds(log) : null;

  return (
    <section className="card panel-card">
      <div className="badges">
        <h3 className="headline panel-heading-title">Reproduce</h3>
        <Badge tone={STATUS[status].tone}>{STATUS[status].label}</Badge>
        {elapsed !== null && <span className="panel-meta">{elapsed}s</span>}
        {investigators && <Badge tone={investigators.tone}>{investigators.label}</Badge>}
        <Badge tone={mode === "sandboxed" ? "green" : "amber"}>
          {mode === "sandboxed" ? "Runs in isolated sandbox" : "Analysis only, not executed"}
        </Badge>
      </div>

      {finished && attempt.root_cause && (
        <p className="panel-summary">
          {firstSentence(attempt.root_cause)}
        </p>
      )}

      <LogView events={log} />

      {error && (
        <p role="alert" className="alert">
          {error}
        </p>
      )}

      {finished && attempt.reproducer_test && (
        <details className="rounded-md border border-gray-200 dark:border-gray-800 p-3 text-sm" open>
          <summary className="cursor-pointer font-medium">
            Reproducer test (failed on the original code in the sandbox)
          </summary>
          <pre className="mt-2 max-h-72 overflow-auto rounded bg-gray-50 dark:bg-gray-900 p-2 font-mono text-xs leading-relaxed">
            {attempt.reproducer_test}
          </pre>
        </details>
      )}

      {finished && attempt.investigators.length > 0 && (
        <div className="inset-grid">
          {attempt.investigators.map((r) => (
            <InvestigatorCard key={r.investigator} report={r} />
          ))}
        </div>
      )}

      {finished && attempt.root_cause && attempt.investigators.every((r) => r.status !== "ok") && (
        <div className="inset-card">
          <p className="inset-card-title">
            {attempt.status === "not_reproducible"
              ? "Result"
              : attempt.investigator_source === "unavailable"
                ? "Reported cause (not independently diagnosed)"
                : mode === "sandboxed"
                  ? "Root cause"
                  : "Likely root cause"}
          </p>
          <p className="panel-text">{attempt.root_cause}</p>
          {attempt.confidence !== null && (
            <p className="panel-meta">
              Confidence {Math.round(attempt.confidence * 100)}% — model estimate from reading the code; nothing was run.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
