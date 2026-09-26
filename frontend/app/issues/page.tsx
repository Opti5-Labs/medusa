"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, Suspense } from "react";
import { ArchitectureReport, ScanResult, Issue } from "../../lib/api";
import { useArchitecture } from "../../lib/useArchitecture";
import Badge from "../components/Badge";
import MermaidView from "../components/MermaidView";

const PRIORITY_COLORS: Record<string, string> = {
  High: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
  Medium: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
  Low: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
};

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
        className="text-blue-600 dark:text-blue-400 hover:underline break-all"
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
    <div className="border border-gray-200 dark:border-gray-800 rounded-lg p-4 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`text-xs font-semibold px-2 py-0.5 rounded-full ${PRIORITY_COLORS[issue.priority] ?? ""}`}
        >
          {issue.priority}
        </span>
        <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
          {SOURCE_LABELS[issue.source] ?? issue.source}
        </span>
        {issue.found_by && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300">
            Found by {issue.found_by === "bob" ? "IBM Bob" : "Granite"}
          </span>
        )}
        {issue.category && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300">
            {issue.category}
          </span>
        )}
        <span
          className={`text-xs px-2 py-0.5 rounded-full ${
            issue.mode === "sandboxed"
              ? "bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300"
              : "bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300"
          }`}
        >
          {issue.mode === "sandboxed" ? "Sandboxed" : "Analysis only"}
        </span>
      </div>

      <h3 className="font-semibold text-gray-900 dark:text-gray-100">{issue.title}</h3>
      <p className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">
        {issue.description}
      </p>

      <div className="flex flex-wrap gap-4 text-xs text-gray-500 dark:text-gray-500">
        {issue.file && (
          <span>
            <code className="font-mono">{issue.file}{issue.line ? `:${issue.line}` : ""}</code>
            {issue.function && (
              <>, <code className="font-mono">{issue.function}()</code></>
            )}
          </span>
        )}
        {issue.github_url && (
          <a
            href={issue.github_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 dark:text-blue-400 hover:underline"
          >
            View on GitHub
          </a>
        )}
      </div>

      <div className="flex flex-wrap gap-2 pt-1">
        {[
          ["repro", "Reproduce"],
          ["debug", "Debug"],
          ["both", "Reproduce and Debug"],
        ].map(([action, label]) => (
          <Link
            key={action}
            href={`/investigate/${issue.id}?scan=${scanId}&action=${action}`}
            className={`text-xs px-3 py-1 rounded border transition-colors ${
              action === "both"
                ? "border-verdigris-600 bg-verdigris-600 text-white hover:bg-verdigris-700 hover:border-verdigris-700"
                : "border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900"
            }`}
          >
            {label}
          </Link>
        ))}
      </div>
    </div>
  );
}

/** Compact, textual stand-in for when the diagram itself fails to render. */
function ComponentFallbackList({ report }: { report: ArchitectureReport }) {
  if (report.components.length === 0) return null;
  return (
    <ul className="text-sm list-disc list-inside space-y-0.5">
      {report.components.map((c) => (
        <li key={c.id}>{c.label}</li>
      ))}
    </ul>
  );
}

/**
 * Auto-starts (or reuses) the architecture run for this scan and shows a
 * rendered diagram once it's ready. Never implies the repository code was
 * executed — the source/status badges and caption say plainly whether this
 * is the curated OptiLearn reference or statically inferred.
 */
function DerivedArchitecture({ scanId }: { scanId: string }) {
  const { report, error, running } = useArchitecture(scanId);

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-lg font-semibold tracking-tight">Project architecture</h3>
        <Link
          href={`/architecture?scan=${scanId}`}
          className="text-sm px-3 py-1.5 rounded-lg border border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
        >
          View project architecture
        </Link>
      </div>

      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      )}

      {running && !error && (
        <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-6 flex items-center gap-3 text-sm text-gray-500 dark:text-gray-400">
          <span
            className="inline-block h-4 w-4 rounded-full border-2 border-verdigris-600 border-t-transparent animate-spin"
            aria-hidden
          />
          Generating the architecture diagram…
        </div>
      )}

      {report && report.status !== "running" && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={report.source === "curated" ? "violet" : "blue"}>
              {report.source === "curated" ? "Curated" : "Statically inferred"}
            </Badge>
            {report.status !== "complete" && (
              <Badge tone="amber">{report.status === "partial" ? "Partial" : "Unavailable"}</Badge>
            )}
          </div>

          {report.mermaid ? (
            <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-3 overflow-x-auto bg-white dark:bg-gray-950">
              <MermaidView
                source={report.mermaid}
                id={report.architecture_id}
                fallback={<ComponentFallbackList report={report} />}
              />
            </div>
          ) : (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {report.status === "unavailable"
                ? "This repository is too large for a diagram. See the full architecture report for details."
                : "No diagram was generated for this repository."}
            </p>
          )}

          <p className="text-xs text-gray-500 dark:text-gray-400">
            {report.source === "curated"
              ? "A curated, hand-authored reference diagram for the OptiLearn demo."
              : "Inferred from repository files by static analysis. Nothing here was executed."}
          </p>
        </>
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
    // Set by the scan/github, scan/upload and demo entry points. Absent for
    // links created before this existed, or hit directly — falls back to
    // the repo_source label below.
    setRepoName(sessionStorage.getItem(`scan:${scanId}:name`));
  }, [scanId]);

  if (expired) {
    return (
      <div className="space-y-4 text-center py-16">
        <p className="text-gray-500 dark:text-gray-400">
          Session expired or no scan data found.
        </p>
        <button
          onClick={() => router.push("/")}
          className="px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-700 text-sm hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
        >
          Start again
        </button>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="py-16 text-center text-gray-500 dark:text-gray-400">
        Loading…
      </div>
    );
  }

  const sourceLabel: Record<string, string> = {
    demo: "Demo (OptiLearn)",
    github: "GitHub repository",
    zip: "Uploaded zip",
  };

  return (
    <div className="space-y-8">
      <Link href="/" className="inline-block text-sm text-gray-500 hover:text-gray-900 dark:hover:text-gray-100">
        Back to home
      </Link>

      {/* Repository name, at the very top */}
      <div className="space-y-3">
        <h2 className="text-2xl font-semibold tracking-tight break-all">
          {repoName ?? sourceLabel[result.repo_source] ?? result.repo_source}
        </h2>
        <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
          <div>
            <dt className="text-gray-500 dark:text-gray-400">Source</dt>
            <dd className="font-medium">{sourceLabel[result.repo_source] ?? result.repo_source}</dd>
          </div>
          <div>
            <dt className="text-gray-500 dark:text-gray-400">Language</dt>
            <dd className="font-medium">{result.language}</dd>
          </div>
          <div>
            <dt className="text-gray-500 dark:text-gray-400">Files analysed</dt>
            <dd className="font-medium">
              {result.files_scanned.length} of {result.files_total}
            </dd>
          </div>
        </dl>
      </div>

      {/* Notices */}
      {result.warnings.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/10 p-4 space-y-1">
          <p className="text-sm font-semibold text-amber-900 dark:text-amber-300">Notices</p>
          <ul className="list-disc list-inside space-y-1">
            {result.warnings.map((w, i) => (
              <li key={i} className="text-sm text-amber-800 dark:text-amber-400">
                {linkify(w)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Project architecture */}
      <DerivedArchitecture scanId={result.scan_id} />

      {/* Issue count */}
      <div className="flex items-center justify-between">
        <h3 className="font-medium text-gray-700 dark:text-gray-300">
          {result.issues.length === 0
            ? "No issues found"
            : `${result.issues.length} issue${result.issues.length !== 1 ? "s" : ""} found`}
        </h3>
        <button
          onClick={() => router.push("/")}
          className="text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
        >
          New scan
        </button>
      </div>

      {/* Issue list */}
      {result.issues.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No issues were detected in this scan.
        </p>
      ) : (
        <div className="space-y-4">
          {result.issues.map((issue) => (
            <IssueRow key={issue.id} issue={issue} scanId={result.scan_id} />
          ))}
        </div>
      )}

      {/* Files analysed */}
      {result.files_scanned.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300">
            Files selected ({result.files_scanned.length})
          </summary>
          <ul className="mt-2 space-y-0.5 pl-4">
            {result.files_scanned.map((f) => (
              <li key={f} className="font-mono text-xs text-gray-600 dark:text-gray-500">
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
        <div className="py-16 text-center text-gray-500 dark:text-gray-400">Loading…</div>
      }
    >
      <IssuesContent />
    </Suspense>
  );
}
