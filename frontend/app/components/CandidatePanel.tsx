"use client";

import type { FixAttempt, LogEvent, Mode } from "../../lib/api";
import Badge, { type Tone } from "./Badge";
import Icon from "./Icon";
import LogView from "./LogView";

const STATUS: Record<FixAttempt["sandbox_status"], { label: string; tone: Tone }> = {
  running: { label: "Running…", tone: "blue" },
  passed: { label: "Passed", tone: "green" },
  failed: { label: "Failed", tone: "red" },
  not_applicable: { label: "Not tested", tone: "amber" },
};

function DiffView({ patch }: { patch: string }) {
  return (
    <pre className="diff-view">
      {patch.split("\n").map((line, i) => {
        const tone = line.startsWith("+") && !line.startsWith("+++")
          ? "text-green-700 dark:text-green-400"
          : line.startsWith("-") && !line.startsWith("---")
            ? "text-red-700 dark:text-red-400"
            : line.startsWith("@@")
              ? "text-blue-700 dark:text-blue-400"
              : "text-gray-600 dark:text-gray-400";
        return (
          <div key={i} className={tone}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

interface Props {
  candidate: FixAttempt;
  mode: Mode;
  log: LogEvent[];
  recommended: boolean;
  finished: boolean;
  downloadUrl: string | null;
  canHide: boolean;
  onHide: () => void;
}

export default function CandidatePanel({ candidate: c, mode, log, recommended, finished, downloadUrl, canHide, onHide }: Props) {
  const r = c.test_results;
  const s = c.patch_stats;
  return (
    <article
      className={`card candidate ${recommended ? "holo-rim is-recommended" : ""}`}
    >
      <header className="candidate-head">
        <div className="badges">
          <span className="candidate-id">{c.candidate_id}</span>
          <Badge tone={STATUS[c.sandbox_status].tone}>{STATUS[c.sandbox_status].label}</Badge>
          {c.origin === "prepared" && <Badge>Prepared candidate</Badge>}
          {c.origin === "bob" && <Badge tone="violet">Bob</Badge>}
          {c.origin === "granite" && <Badge tone="blue">Granite</Badge>}
          {c.attempts > 1 && <Badge tone="violet">Revised after failing tests</Badge>}
          {recommended && <Badge tone="green">Recommended</Badge>}
        </div>
        {canHide && (
          <button
            onClick={onHide}
            className="icon-button candidate-hide"
            aria-label={`Hide ${c.candidate_id}`}
            title="Hide this candidate"
          >
            <Icon name="close" />
          </button>
        )}
      </header>

      <p className="panel-text">{c.approach}</p>

      {(r || s) && (
        <dl className="candidate-stats">
          {r && (
            <>
              <dt className="text-gray-500">Reproducer</dt>
              <dd className={r.reproducer_fixed ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400"}>
                {r.reproducer_fixed ? "fixed" : "still fails"}
              </dd>
              <dt className="text-gray-500">Checks</dt>
              <dd>
                {r.passed}/{r.total} passed
              </dd>
              <dt className="text-gray-500">Regressions</dt>
              <dd className={r.regressions.length ? "text-red-700 dark:text-red-400" : ""}>
                {r.regressions.length ? r.regressions.length : "none"}
              </dd>
            </>
          )}
          {s && (
            <>
              <dt className="text-gray-500">Patch</dt>
              <dd>
                <span className="text-green-700 dark:text-green-400">+{s.lines_added}</span>{" "}
                <span className="text-red-700 dark:text-red-400">−{s.lines_removed}</span>
              </dd>
            </>
          )}
        </dl>
      )}

      {c.error && c.sandbox_status !== "running" && (
        <p className="field-error">{c.error}</p>
      )}

      {mode === "sandboxed" && <LogView events={log} showSource={false} maxHeight="max-h-40" emptyText="Queued…" />}

      {c.patch && (
        <details className="disclosure" open={mode === "reasoning"}>
          <summary>
            <Icon name="chevron" />
            {mode === "reasoning" ? "Proposed patch (not applied or tested)" : "Patch"}
          </summary>
          <DiffView patch={c.patch} />
        </details>
      )}

      {finished && downloadUrl && (
        <a
          href={downloadUrl}
          className="btn btn-primary btn-sm"
        >
          Download fixed code (.zip)
        </a>
      )}
    </article>
  );
}
