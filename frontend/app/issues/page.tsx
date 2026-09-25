"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, Suspense } from "react";
import { ScanResult, Issue } from "../../lib/api";

const PRIORITY_COLORS: Record<string, string> = {
  High: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
  Medium: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
  Low: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
};

const SOURCE_LABELS: Record<string, string> = {
  scan: "Code scan",
  github_issue: "GitHub Issue",
};

function IssueRow({ issue }: { issue: Issue }) {
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
        {issue.category && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300">
            {issue.category}
          </span>
        )}
      </div>

      <h3 className="font-semibold text-gray-900 dark:text-gray-100">{issue.title}</h3>
      <p className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">
        {issue.description}
      </p>

      <div className="flex flex-wrap gap-4 text-xs text-gray-500 dark:text-gray-500">
        {issue.file && (
          <span>
            📄 <code className="font-mono">{issue.file}</code>
            {issue.function && (
              <> · <code className="font-mono">{issue.function}()</code></>
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
            View on GitHub →
          </a>
        )}
      </div>

      <div className="flex gap-2 pt-1">
        <button
          disabled
          className="text-xs px-3 py-1 rounded border border-gray-300 dark:border-gray-700 text-gray-400 dark:text-gray-600 cursor-not-allowed"
          title="Reproduce — coming soon"
        >
          Reproduce
        </button>
        <button
          disabled
          className="text-xs px-3 py-1 rounded border border-gray-300 dark:border-gray-700 text-gray-400 dark:text-gray-600 cursor-not-allowed"
          title="Debug — coming soon"
        >
          Debug
        </button>
      </div>
    </div>
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
      {/* Header */}
      <div className="space-y-1">
        <h2 className="text-2xl font-semibold">Issues</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Source: <span className="font-medium">{sourceLabel[result.repo_source] ?? result.repo_source}</span>
          {" · "}Language: <span className="font-medium">{result.language}</span>
          {" · "}
          <span className="font-medium">{result.files_scanned.length}</span> of{" "}
          <span className="font-medium">{result.files_total}</span> files selected
        </p>
      </div>

      {/* Warnings */}
      {result.warnings.length > 0 && (
        <div className="rounded-lg border border-yellow-200 bg-yellow-50 dark:border-yellow-800 dark:bg-yellow-900/10 p-4 space-y-1">
          <p className="text-sm font-semibold text-yellow-800 dark:text-yellow-300">Notices</p>
          <ul className="list-disc list-inside space-y-1">
            {result.warnings.map((w, i) => (
              <li key={i} className="text-sm text-yellow-700 dark:text-yellow-400">
                {w}
              </li>
            ))}
          </ul>
        </div>
      )}

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
          ← New scan
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
            <IssueRow key={issue.id} issue={issue} />
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
