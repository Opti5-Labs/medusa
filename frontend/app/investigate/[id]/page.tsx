// Placeholder – live investigator view for a single issue.
// Person 3 wires this to:
//   POST /api/issues/{id}/repro  → starts a ReproAttempt
//   GET  /api/repro/{attempt_id}/events  → SSE stream of LogEvent, closed on "done"
//   POST /api/issues/{id}/debug  → starts a DebugSession
//   GET  /api/debug/{session_id}/events  → SSE stream, closed on "done"

interface Props {
  params: { id: string };
}

export default function InvestigatePage({ params }: Props) {
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold">
        Investigating issue <code className="font-mono">{params.id}</code>
      </h2>
      <p className="text-gray-500 dark:text-gray-400">
        Live investigator view placeholder — render ReproPanel and DebugPanels
        here, driven by SSE streams from the backend.
      </p>
    </div>
  );
}
