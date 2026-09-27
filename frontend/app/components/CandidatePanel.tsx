"use client";

import { memo, useState } from "react";
import { downloadFile, type FixAttempt, type LogEvent, type Mode } from "../../lib/api";
import AutoOpenDetails from "./AutoOpenDetails";
import Badge, { type Tone } from "./Badge";
import DiffView from "./DiffView";
import HoloRing from "./HoloRing";
import Icon from "./Icon";
import LogView from "./LogView";

const STATUS: Record<FixAttempt["sandbox_status"], { label: string; tone: Tone }> = {
  running: { label: "Running…", tone: "blue" },
  passed: { label: "Passed", tone: "green" },
  failed: { label: "Failed", tone: "red" },
  not_applicable: { label: "Not tested", tone: "amber" },
};

const ORIGIN: Record<string, string> = {
  prepared: "Prepared candidate",
  bob: "From Bob",
  granite: "From Granite",
};

interface Props {
  candidate: FixAttempt;
  mode: Mode;
  log: LogEvent[];
  recommended: boolean;
  finished: boolean;
  downloadUrl: string | null;
  patchUrl: string | null;
  canHide: boolean;
  onHide: (candidateId: string) => void;
}

/**
 * One candidate fix. The outcome (status, tests, patch size) comes first; the
 * raw test output and the patch sit in disclosures below it. The test output
 * is open while the candidate runs, folds away once it passes, and stays
 * open when it fails so the failure is in view.
 */
// Memoized: while a run streams, only the panel whose log or result changed re-renders.
export default memo(function CandidatePanel({ candidate: c, mode, log, recommended, finished, downloadUrl, patchUrl, canHide, onHide }: Props) {
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const download = (url: string, name: string) => {
    setDownloadError(null);
    downloadFile(url, name).catch((err: unknown) =>
      setDownloadError(err instanceof Error ? `Download failed: ${err.message}` : "Download failed."),
    );
  };
  const r = c.test_results;
  const s = c.patch_stats;
  const running = c.sandbox_status === "running";
  return (
    <article
      id={`candidate-${c.candidate_id}`}
      className={`card candidate ${recommended ? "holo-rim is-recommended" : ""} ${running ? "is-running" : ""}`}
    >
      {recommended && <HoloRing />}
      <header className="candidate-head">
        <div className="candidate-title">
          <span className="candidate-id">{c.candidate_id}</span>
          <Badge tone={STATUS[c.sandbox_status].tone}>{STATUS[c.sandbox_status].label}</Badge>
          {recommended && <Badge tone="green">Recommended</Badge>}
        </div>
        {canHide && (
          <button
            onClick={() => onHide(c.candidate_id)}
            className="icon-button candidate-hide"
            aria-label={`Hide ${c.candidate_id}`}
            title="Hide this candidate"
          >
            <Icon name="close" />
          </button>
        )}
      </header>

      <div className="candidate-body">
        <p className="candidate-approach">{c.approach}</p>
        <p className="candidate-origin">
          {c.origin ? ORIGIN[c.origin] ?? c.origin : "Candidate fix"}
          {c.attempts > 1 && " · revised after failing tests"}
        </p>
      </div>

      {(r || s) && (
        <dl className="candidate-stats">
          {r && (
            <>
              <div>
                <dt>Reproducer</dt>
                <dd className={r.reproducer_fixed ? "is-good" : "is-bad"}>{r.reproducer_fixed ? "Fixed" : "Still fails"}</dd>
              </div>
              <div>
                <dt>Checks</dt>
                <dd className={r.passed === r.total ? "is-good" : ""}>{r.passed}/{r.total}</dd>
              </div>
              <div>
                <dt>Regressions</dt>
                <dd className={r.regressions.length ? "is-bad" : ""}>{r.regressions.length || "None"}</dd>
              </div>
            </>
          )}
          {s && (
            <div>
              <dt>Patch</dt>
              <dd className="candidate-patch-size">
                <span className="is-good">+{s.lines_added}</span> <span className="is-bad">−{s.lines_removed}</span>
              </dd>
            </div>
          )}
        </dl>
      )}

      {c.error && !running && <p className="field-error">{c.error}</p>}

      {mode === "sandboxed" && (
        <AutoOpenDetails openWhen={running || c.sandbox_status === "failed"}>
          <summary>
            <Icon name="chevron" />
            Test output{log.length > 0 && <span className="disclosure-count">{log.length}</span>}
          </summary>
          <LogView events={log} showSource={false} maxHeight="max-h-40" emptyText="Queued…" numbered busy={running} />
        </AutoOpenDetails>
      )}

      {c.patch && (
        <AutoOpenDetails openWhen={mode === "reasoning" || recommended}>
          <summary>
            <Icon name="chevron" />
            {mode === "reasoning" ? "Proposed patch (not applied or tested)" : "Patch"}
          </summary>
          <DiffView patch={c.patch} />
        </AutoOpenDetails>
      )}

      {finished && (downloadUrl || patchUrl) && (
        <div className="actions candidate-actions">
          {downloadUrl && (
            <button
              type="button"
              onClick={() => download(downloadUrl, `fix-${c.candidate_id}.zip`)}
              className={`btn btn-sm ${recommended ? "btn-primary" : "btn-secondary"}`}
            >
              <Icon name="upload" className="icon-download" aria-hidden="true" />
              Fixed code (.zip)
            </button>
          )}
          {patchUrl && (
            <button
              type="button"
              onClick={() => download(patchUrl, `medusa-${c.candidate_id}.patch`)}
              title="A unified diff you can apply from the repository root with git apply"
              className="btn btn-secondary btn-sm"
            >
              <Icon name="upload" className="icon-download" aria-hidden="true" />
              {mode === "sandboxed" ? ".patch" : ".patch (untested)"}
            </button>
          )}
        </div>
      )}
      {downloadError && (
        <p role="alert" className="alert">
          {downloadError}
        </p>
      )}
    </article>
  );
});
