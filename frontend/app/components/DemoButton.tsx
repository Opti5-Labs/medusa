"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { postScan, ScanResult } from "../../lib/api";
import AssessingStatus from "./AssessingStatus";
import HoloRing from "./HoloRing";
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
const STEP_MS = 1250;

export default function DemoButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDemo() {
    setLoading(true);
    setError(null);
    try {
      // The demo scan answers almost instantly, so hold the steps on screen long
      // enough to follow them (each is real work the server does; see above).
      const [result] = await Promise.all([
        postScan({ source: "demo" }) as Promise<ScanResult>,
        new Promise((resolve) => setTimeout(resolve, DEMO_STEPS.length * STEP_MS)),
      ]);
      rememberScan(result, "OptiLearn demo");
      router.push(`/issues?scan=${result.scan_id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "The demo could not start. Please try again.");
      setLoading(false);
    }
  }

  return (
    <aside className="demo-card glass holo-rim" aria-labelledby="demo-heading">
      <HoloRing />
      <div className="demo-card-head">
        <span className="tile purple"><Icon name="play" /></span>
        <div><h2 id="demo-heading">Try the OptiLearn demo</h2><p>A real bug, start to finish</p></div>
      </div>
      <ol className="demo-steps">
        {/* Each step is two short lines, so all three are the same height (and evenly
            spaced) at every card width. */}
        <li><span>Whisper bug reproduced</span> <span>in an isolated sandbox.</span></li>
        <li><span>Bob and Granite diagnose</span> <span>it and propose fixes.</span></li>
        <li><span>Every fix is tested;</span> <span>the one that passes wins.</span></li>
      </ol>
      <button onClick={handleDemo} disabled={loading} aria-busy={loading} className="btn btn-primary btn-lg">
        {loading ? "Opening…" : "Run the demo"}
      </button>
      {loading ? (
        <div className="demo-progress">
          <AssessingStatus steps={DEMO_STEPS} stepMs={STEP_MS} />
        </div>
      ) : (
        <p className="demo-footnote">Issues are prepared in advance. Only the Whisper issue runs in the sandbox; the rest are read as text.</p>
      )}
      {error && <p role="alert" className="demo-error">{error}</p>}
    </aside>
  );
}
