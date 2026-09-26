"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, Suspense } from "react";
import { ArchitectureReport, ScanResult, Issue } from "../../lib/api";
import { useArchitecture } from "../../lib/useArchitecture";
import Badge, { type Tone } from "../components/Badge";
import Icon from "../components/Icon";
import MermaidView from "../components/MermaidView";

const PRIORITY_TONE: Record<string, Tone> = { High: "red", Medium: "amber", Low: "green" };

const SOURCE_LABELS: Record<string, string> = {
  scan: "Code scan",
  github_issue: "GitHub Issue",
};

const URL_RE = /(https?:\/\/[^\s]+)/g;

/** Render a notice's plain text with any URLs turned into real links. */
function linkify(text: string) {
  // .split() on a capturing regex returns the URLs as their own array
  // elements, always starting with the scheme — checked with a plain
  // startsWith rather than re-testing URL_RE, since a `g`-flagged regex's
  // .test() is stateful (tracks lastIndex) and gives wrong results when
  // reused across calls like this.
  return text.split(URL_RE).map((part, i) =>
    part.startsWith("http://") || part.startsWith("https://") ? (
      <a
        key={i}
        href={part}
        target="_blank"
        rel="noopener noreferrer"
      >
        {part}
      </a>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

function IssueRow({ issue, scanId }: { issue: Issue; scanId: string }) {
  return (
    <article data-reveal className="card issue-card">
      <div className="badges">
        <Badge tone={PRIORITY_TONE[issue.priority] ?? "gray"}>{issue.priority}</Badge>
        <Badge>{SOURCE_LABELS[issue.source] ?? issue.source}</Badge>
        {issue.found_by && <Badge tone="violet">Found by {issue.found_by === "bob" ? "IBM Bob" : "Granite"}</Badge>}
        {issue.category && <Badge tone="blue">{issue.category}</Badge>}
        <Badge tone={issue.mode === "sandboxed" ? "green" : "amber"}>
          {issue.mode === "sandboxed" ? "Sandboxed" : "Analysis only"}
        </Badge>
      </div>

      <div className="issue-body">
        <h3 className="headline">{issue.title}</h3>
        <p className="issue-description">{issue.description}</p>
      </div>

      {(issue.file || issue.github_url) && (
        <div className="issue-meta">
          {issue.file && (
            <span className="code-ref">
              {issue.file}
              {issue.line ? `:${issue.line}` : ""}
              {issue.function ? `, ${issue.function}()` : ""}
            </span>
          )}
          {issue.github_url && (
            <a href={issue.github_url} target="_blank" rel="noopener noreferrer" className="text-link">
              View on GitHub
            </a>
          )}
        </div>
      )}

      <div className="actions issue-actions">
        {[
          ["both", "Reproduce and Debug"],
          ["repro", "Reproduce"],
          ["debug", "Debug"],
        ].map(([action, label]) => (
          <Link
            key={action}
            href={`/investigate/${issue.id}?scan=${scanId}&action=${action}`}
            className={`btn btn-sm ${action === "both" ? "btn-primary" : "btn-secondary"}`}
          >
            {label}
          </Link>
        ))}
      </div>
    </article>
  );
}

/** Compact, textual stand-in for when the diagram itself fails to render. */
function ComponentFallbackList({ report }: { report: ArchitectureReport }) {
  if (report.components.length === 0) return null;
  return (
    <ul className="panel-list">
      {report.components.map((c) => (
        <li key={c.id}>{c.label}</li>
      ))}
    </ul>
  );
}

/**
 * Auto-starts (or reuses) the architecture run for this scan and shows a
 * rendered diagram once it is ready. Never implies the repository code was
 * executed: the source/status badges and caption say plainly whether this
 * is the curated OptiLearn reference or statically inferred.
 */
function DerivedArchitecture({ scanId }: { scanId: string }) {
  const { report, error, running } = useArchitecture(scanId);

  return (
    <section className="section" aria-labelledby="architecture-heading">
      <div className="section-head">
        <h3 id="architecture-heading" className="headline">Project architecture</h3>
        <Link href={`/architecture?scan=${scanId}`} className="btn btn-secondary btn-sm">
          View full architecture
        </Link>
      </div>

      {error && (
        <p role="alert" className="alert">
          {error}
        </p>
      )}

      {running && !error && (
        <div className="card architecture-loading">
          <span className="holo-spinner" aria-hidden="true" />
          Generating the architecture diagram…
        </div>
      )}

      {report && report.status !== "running" && (
        <div className="card architecture-card">
          <div className="badges">
            <Badge tone={report.source === "curated" ? "violet" : "blue"}>
              {report.source === "curated" ? "Curated" : "Statically inferred"}
            </Badge>
            {report.status !== "complete" && (
              <Badge tone="amber">{report.status === "partial" ? "Partial" : "Unavailable"}</Badge>
            )}
          </div>

          {report.mermaid ? (
            <div className="architecture-diagram">
              <MermaidView
                source={report.mermaid}
                id={report.architecture_id}
                fallback={<ComponentFallbackList report={report} />}
              />
            </div>
          ) : (
            <p className="panel-text">
              {report.status === "unavailable"
                ? "This repository is too large for a diagram. See the full architecture report for details."
                : "No diagram was generated for this repository."}
            </p>
          )}

          <p className="field-hint">
            {report.source === "curated"
              ? "A curated, hand-authored reference diagram for the OptiLearn demo."
              : "Inferred from repository files by static analysis. Nothing here was executed."}
          </p>
        </div>
      )}
    </section>
  );
}

function IssuesContent() {
  const router = useRouter();
  const params = useSearchParams();
  const scanId = params.get("scan");

  const [result, setResult] = useState<ScanResult | null>(null);
  const [expired, setExpired] = useState(false);
  const [repoName, setRepoName] = useState<string | null>(null);

  useEffect(() => {
    if (!scanId) {
      setExpired(true);
      return;
    }
    const raw = sessionStorage.getItem(`scan:${scanId}`);
    if (!raw) {
      setExpired(true);
      return;
    }
    try {
      setResult(JSON.parse(raw) as ScanResult);
    } catch {
      setExpired(true);
    }
    // Set by the scan entry points (see rememberScan). Absent for older links,
    // in which case the title falls back to the source label.
    setRepoName(sessionStorage.getItem(`scan:${scanId}:name`));
  }, [scanId]);

  if (expired) {
    return (
      <div className="empty-page">
        <p>This scan has expired or could not be found. Scans are kept for 30 minutes.</p>
        <button onClick={() => router.push("/")} className="btn btn-secondary">
          Start a new scan
        </button>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="empty-page">Loading…</div>
    );
  }

  const sourceLabel: Record<string, string> = {
    demo: "Demo (OptiLearn)",
    github: "GitHub repository",
    zip: "Uploaded zip",
  };

  const count = result.issues.length;

  return (
    <div className="page">
      <header className="page-header">
        <Link href="/" className="back-link"><Icon name="chevron" />Overview</Link>
        <div className="page-header-row">
          <h2 className="title-1 repo-title">{repoName ?? sourceLabel[result.repo_source] ?? result.repo_source}</h2>
          <button onClick={() => router.push("/")} className="btn btn-secondary">
            New scan
          </button>
        </div>
        <dl className="facts">
          <div>
            <dt>Source</dt>
            <dd>{sourceLabel[result.repo_source] ?? result.repo_source}</dd>
          </div>
          <div>
            <dt>Language</dt>
            <dd>{result.language}</dd>
          </div>
          <div>
            <dt>Files analysed</dt>
            <dd>
              {result.files_scanned.length} of {result.files_total}
            </dd>
          </div>
        </dl>
      </header>

      {result.warnings.length > 0 && (
        <div className="notice">
          <p className="notice-title">Notices</p>
          <ul>
            {result.warnings.map((w, i) => (
              <li key={i}>{linkify(w)}</li>
            ))}
          </ul>
        </div>
      )}

      <DerivedArchitecture scanId={result.scan_id} />

      <section className="section" aria-labelledby="issue-count">
        <div className="section-head">
          <h3 id="issue-count" className="headline">
            {count === 0 ? "No issues found" : `${count} ${count === 1 ? "issue" : "issues"} found`}
          </h3>
        </div>
        {count === 0 ? (
          <p className="page-lede">No issues were detected in this scan.</p>
        ) : (
          <div className="stack">
            {result.issues.map((issue) => (
              <IssueRow key={issue.id} issue={issue} scanId={result.scan_id} />
            ))}
          </div>
        )}
      </section>

      {result.files_scanned.length > 0 && (
        <details className="disclosure">
          <summary>
            <Icon name="chevron" />
            Files analysed ({result.files_scanned.length})
          </summary>
          <ul className="file-list">
            {result.files_scanned.map((f) => (
              <li key={f} className="code-ref">
                {f}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export default function IssuesPage() {
  return (
    <Suspense
      fallback={
        <div className="empty-page">Loading…</div>
      }
    >
      <IssuesContent />
    </Suspense>
  );
}
