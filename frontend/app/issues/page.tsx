// Placeholder – issue list page.
// Person 3 wires this to GET /api/scan results and renders <IssueRow> components.

export default function IssuesPage() {
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold">Issues</h2>
      <p className="text-gray-500 dark:text-gray-400">
        Issue list placeholder — wire to{" "}
        <code className="font-mono text-sm">POST /api/scan</code> and render one
        row per issue with priority, description, source, and Reproduce / Debug
        actions.
      </p>
    </div>
  );
}
