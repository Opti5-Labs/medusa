"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, Suspense } from "react";
import { ScanResult, Issue } from "../../lib/api";
import Badge, { type Tone } from "../components/Badge";
import Icon from "../components/Icon";

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

function IssuesContent() {
  const router = useRouter();
  const params = useSearchParams();
  const scanId = params.get("scan");

  const [result, setResult] = useState<ScanResult | null>(null);
  const [expired, setExpired] = useState(false);

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
          <h2 className="title-1">Issues</h2>
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
