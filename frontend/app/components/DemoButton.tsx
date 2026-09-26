"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { postScan, ScanResult } from "../../lib/api";
import AssessingStatus from "./AssessingStatus";

// What the demo scan really does: read the bundled OptiLearn snapshot, take
// its prepared issue list, and map each issue to sandboxed or reasoning mode.
const DEMO_STEPS = [
  "Loading the bundled OptiLearn snapshot",
  "Selecting the source files in scope",
  "Reading the prepared issue list",
  "Matching issues to the sandbox harness",
];
const STEP_MS = 900;
// The demo's issues are prepared in advance, so the request itself returns
// almost instantly. Hold the steps long enough to be readable instead of
// flashing past; the work described has already finished by then.
const MIN_VISIBLE_MS = DEMO_STEPS.length * STEP_MS;

export default function DemoButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDemo() {
    setLoading(true);
    setError(null);
    const started = Date.now();
    try {
      const result: ScanResult = await postScan({ source: "demo" });
      sessionStorage.setItem(`scan:${result.scan_id}`, JSON.stringify(result));
      sessionStorage.setItem(`scan:${result.scan_id}:name`, "OptiLearn Demo");
      const remaining = MIN_VISIBLE_MS - (Date.now() - started);
      if (remaining > 0) await new Promise((r) => setTimeout(r, remaining));
      router.push(`/issues?scan=${result.scan_id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "The demo could not start. Please try again.");
      setLoading(false);
    }
  }

  return (
    <div className="space-y-3">
      <button
        onClick={handleDemo}
        disabled={loading}
        aria-busy={loading}
        className="w-full sm:w-auto rounded-lg bg-verdigris-600 hover:bg-verdigris-700 text-white px-5 py-3 text-left transition-colors disabled:opacity-60 disabled:cursor-wait"
      >
        <span className="block font-medium">{loading ? "Opening the demo…" : "Run the OptiLearn demo"}</span>
        <span className="block text-xs text-verdigris-100">One sandboxed bug plus five real historical issues</span>
      </button>
      {loading && (
        <AssessingStatus
          steps={DEMO_STEPS}
          stepMs={STEP_MS}
          note="The demo's issue list is prepared in advance. The Whisper issue runs in the sandbox; the other issues are analysed as text."
        />
      )}
      {error && (
        <p role="alert" className="text-sm text-fail dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}
