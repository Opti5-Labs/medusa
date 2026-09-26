"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { postScan, ScanResult } from "../../lib/api";

export default function DemoButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDemo() {
    setLoading(true);
    setError(null);
    try {
      const result: ScanResult = await postScan({ source: "demo" });
      sessionStorage.setItem(`scan:${result.scan_id}`, JSON.stringify(result));
      router.push(`/issues?scan=${result.scan_id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "The demo could not start. Please try again.");
      setLoading(false);
    }
  }

  return (
    <div className="space-y-2">
      <button
        onClick={handleDemo}
        disabled={loading}
        aria-busy={loading}
        className="w-full sm:w-auto rounded-lg bg-verdigris-600 hover:bg-verdigris-700 text-white px-5 py-3 text-left transition-colors disabled:opacity-60 disabled:cursor-wait"
      >
        <span className="block font-medium">{loading ? "Opening the demo…" : "Run the OptiLearn demo"}</span>
        <span className="block text-xs text-verdigris-100">Reproduce and fix a real bug, verified in a sandbox</span>
      </button>
      {error && (
        <p role="alert" className="text-sm text-fail dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}
