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
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-center gap-2">
      <button
        onClick={handleDemo}
        disabled={loading}
        aria-busy={loading}
        className="px-6 py-3 rounded-lg bg-gray-900 text-white dark:bg-white dark:text-gray-900 font-medium text-center hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading ? "Loading demo…" : "Run the demo"}
      </button>
      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400 max-w-xs text-center">
          {error}
        </p>
      )}
    </div>
  );
}
