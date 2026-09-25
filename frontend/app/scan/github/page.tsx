// Placeholder pages for the two scan entry points.
// Person 3 replaces these with real forms that call POST /api/scan and
// POST /api/scan/upload respectively, then redirect to /issues.

export default function ScanGitHubPage() {
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">Link a GitHub repository</h2>
      <p className="text-gray-500 dark:text-gray-400">
        GitHub URL input form placeholder — POST{" "}
        <code className="font-mono text-sm">
          {"/api/scan"}
        </code>{" "}
        with{" "}
        <code className="font-mono text-sm">
          {'{"source":"github","repo_url":"..."}'}
        </code>
        .
      </p>
    </div>
  );
}
