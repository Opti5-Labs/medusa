"use client";

import type { FixAttempt, LogEvent, Mode } from "../../lib/api";
import Badge, { type Tone } from "./Badge";
import LogView from "./LogView";

const STATUS: Record<FixAttempt["sandbox_status"], { label: string; tone: Tone }> = {
  running: { label: "Running…", tone: "blue" },
  passed: { label: "Passed", tone: "green" },
  failed: { label: "Failed", tone: "red" },
  not_applicable: { label: "Not tested", tone: "amber" },
};

function DiffView({ patch }: { patch: string }) {
  return (
    <pre className="max-h-64 overflow-auto rounded-md bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 p-2 text-xs leading-relaxed">
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
      className={`rounded-lg border p-3 space-y-2 min-w-0 ${
        recommended ? "border-green-500 ring-1 ring-green-500" : "border-gray-200 dark:border-gray-800"
      }`}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-sm font-semibold">{c.candidate_id}</span>
          <Badge tone={STATUS[c.sandbox_status].tone}>{STATUS[c.sandbox_status].label}</Badge>
          {c.origin === "prepared" && <Badge>Prepared candidate</Badge>}
          {c.origin === "granite" && <Badge tone="blue">Granite</Badge>}
          {c.attempts > 1 && <Badge tone="violet">Revised after failing tests</Badge>}
          {recommended && <Badge tone="green">Recommended</Badge>}
        </div>
        {canHide && (
          <button
            onClick={onHide}
            className="text-xs text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            aria-label={`Hide ${c.candidate_id}`}
            title="Hide this candidate"
          >
            ✕
          </button>
        )}
      </header>

      <p className="text-sm text-gray-700 dark:text-gray-300">{c.approach}</p>

      {(r || s) && (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
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
        <p className="text-xs text-red-600 dark:text-red-400 break-all">{c.error}</p>
      )}

      {mode === "sandboxed" && <LogView events={log} showSource={false} maxHeight="max-h-40" emptyText="Queued…" />}

      {c.patch && (
        <details open={mode === "reasoning"}>
          <summary className="cursor-pointer text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200">
            {mode === "reasoning" ? "Proposed patch (not applied or tested)" : "Patch"}
          </summary>
          <div className="mt-1">
            <DiffView patch={c.patch} />
          </div>
        </details>
      )}

      {finished && downloadUrl && (
        <a
          href={downloadUrl}
          className="inline-block text-xs px-3 py-1.5 rounded-md bg-gray-900 text-white dark:bg-white dark:text-gray-900 font-medium hover:opacity-90"
        >
          Download fixed code (.zip)
        </a>
      )}
    </article>
  );
}
