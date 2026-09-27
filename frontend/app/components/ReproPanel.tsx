"use client";

import { memo } from "react";
import type { InvestigatorReport, LogEvent, Mode, ReproAttempt } from "../../lib/api";
import AutoOpenDetails from "./AutoOpenDetails";
import Badge, { type Tone } from "./Badge";
import CodeBlock from "./CodeBlock";
import Icon from "./Icon";
import LogView from "./LogView";

const STATUS: Record<ReproAttempt["status"], { label: string; tone: Tone }> = {
  running: { label: "Running…", tone: "blue" },
  reproduced: { label: "Reproduced", tone: "red" },
  not_reproducible: { label: "Not reproducible", tone: "green" },
  plausible: { label: "Plausible (not executed)", tone: "amber" },
  error: { label: "Error", tone: "red" },
};

/** Who investigated, as plain words for the meta line under the heading. */
const INVESTIGATORS: Record<string, string> = {
  bob_replay: "Recorded Bob session",
  bob: "Bob (live)",
  granite: "Granite (live)",
  bob_and_granite: "Bob + Granite, independently",
  unavailable: "Investigators unavailable",
};

const REPORT_STATUS: Record<InvestigatorReport["status"], { label: string; tone: Tone }> = {
  ok: { label: "Diagnosed", tone: "green" },
  unavailable: { label: "Unavailable", tone: "amber" },
  error: { label: "Failed", tone: "red" },
  limit: { label: "Stopped at limit", tone: "amber" },
};

function InvestigatorCard({ report }: { report: InvestigatorReport }) {
  const bob = report.investigator === "bob";
  const status = REPORT_STATUS[report.status];
  return (
    <div className="inset-card">
      <div className="investigator-head">
        <span className={`tile tile-sm ${bob ? "" : "cyan"}`}><Icon name={bob ? "bob" : "granite"} /></span>
        <span className="inset-card-title">{bob ? "IBM Bob" : "Granite"}</span>
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

/**
 * The reproduce step. Heading, one status, and a quiet meta line (where it
 * ran, who investigated, how long); then the finding; then the evidence. The
 * run log opens while it runs and stays open until the reader folds it.
 */
// Memoized so the debug stream that follows doesn't re-render it.
export default memo(function ReproPanel({ mode, attempt, log, error }: Props) {
  const status = attempt?.status ?? "running";
  const running = status === "running" && !error;
  const finished = attempt && attempt.status !== "running";
  const elapsed = finished ? elapsedSeconds(log) : null;
  const meta = [
    mode === "sandboxed" ? "Isolated sandbox" : "Analysis only, not executed",
    attempt?.investigator_source ? INVESTIGATORS[attempt.investigator_source] : null,
    elapsed !== null ? `${elapsed}s` : null,
  ].filter(Boolean);

  return (
    <section className="card step-card" aria-labelledby="repro-heading">
      <header className="step-head">
        <span className="tile"><Icon name="play" /></span>
        <div className="step-head-text">
          <div className="step-title-row">
            <h3 id="repro-heading" className="headline">Reproduce</h3>
            <Badge tone={STATUS[status].tone}>{STATUS[status].label}</Badge>
          </div>
          <p className="step-meta">{meta.join(" · ")}</p>
        </div>
      </header>

      {finished && attempt.root_cause && <p className="panel-summary">{firstSentence(attempt.root_cause)}</p>}

      {error && (
        <p role="alert" className="alert">
          {error}
        </p>
      )}

      {/* Open while the run streams; it doesn't fold itself away when the run ends, so the
          page never shrinks under the reader (collapse it by hand). */}
      <AutoOpenDetails openWhen={running || status === "error" || !attempt?.root_cause}>
        <summary>
          <Icon name="chevron" />
          Run log{log.length > 0 && <span className="disclosure-count">{log.length}</span>}
        </summary>
        <LogView events={log} busy={running} />
      </AutoOpenDetails>

      {finished && attempt.reproducer_test && (
        <div className="stack">
          <p className="field-hint">This test failed on the original code in the sandbox.</p>
          <CodeBlock code={attempt.reproducer_test} title="Reproducer test" />
        </div>
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
});
