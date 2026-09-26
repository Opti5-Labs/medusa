"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { postScan, ScanResult } from "../../lib/api";
import AssessingStatus from "./AssessingStatus";
import Icon from "./Icon";
import { rememberScan } from "../../lib/recentScans";

// What the demo scan really does: read the bundled OptiLearn snapshot, take
// its prepared issue list, and map each issue to sandboxed or reasoning mode.
const DEMO_STEPS = [
  "Loading the bundled OptiLearn snapshot",
  "Selecting the source files in scope",
  "Reading the prepared issue list",
  "Matching issues to the sandbox harness",
];
const STEP_MS = 900;

export default function DemoButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDemo() {
    setLoading(true);
    setError(null);
    try {
      const result: ScanResult = await postScan({ source: "demo" });
      rememberScan(result, "OptiLearn demo");
      router.push(`/issues?scan=${result.scan_id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "The demo could not start. Please try again.");
      setLoading(false);
    }
  }

  return (
    <aside className="demo-card glass holo-rim" aria-labelledby="demo-heading">
      <div className="demo-card-head">
        <span className="tile purple"><Icon name="play" /></span>
        <div><h2 id="demo-heading">Try the OptiLearn demo</h2><p>A real bug, start to finish</p></div>
      </div>
      <ol className="demo-steps">
        <li>The Whisper bug is reproduced in an isolated sandbox.</li>
        <li>Bob and Granite diagnose it and propose fixes.</li>
        <li>Every fix runs the tests. The one that passes is recommended.</li>
      </ol>
      <button onClick={handleDemo} disabled={loading} aria-busy={loading} className="btn btn-primary btn-lg">
        {loading ? "Opening…" : "Run the demo"}
      </button>
      {loading ? (
        <div className="demo-progress">
          <AssessingStatus steps={DEMO_STEPS} stepMs={STEP_MS} />
        </div>
      ) : (
        <p className="demo-footnote">The issue list is prepared in advance. Only the Whisper issue runs in the sandbox; the others are analysed as text.</p>
      )}
      {error && <p role="alert" className="demo-error">{error}</p>}
    </aside>
  );
}
