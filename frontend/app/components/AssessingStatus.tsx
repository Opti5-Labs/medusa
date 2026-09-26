"use client";

import { useEffect, useState } from "react";

interface Props {
  steps: string[];
  /** How long each step is shown before the next one starts. */
  stepMs?: number;
  /** A short line under the list, e.g. what is and is not being run. */
  note?: string;
}

/**
 * Staged progress for a scan in flight. Every step names work the server
 * really does; the last one keeps spinning until the caller navigates away.
 */
export default function AssessingStatus({ steps, stepMs = 900, note }: Props) {
  const [current, setCurrent] = useState(0);

  useEffect(() => {
    if (current >= steps.length - 1) return;
    const timer = setTimeout(() => setCurrent((c) => c + 1), stepMs);
    return () => clearTimeout(timer);
  }, [current, steps.length, stepMs]);

  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-lg border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-4 space-y-2"
    >
      {steps.map((step, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={step} className="flex items-center gap-2.5 text-sm">
            <span className="w-4 shrink-0 text-center leading-none" aria-hidden>
              {done ? (
                <span className="inline-block h-3 w-3 rounded-full bg-green-600 dark:bg-green-400 align-middle" />
              ) : active ? (
                <span className="inline-block h-3 w-3 rounded-full border-2 border-verdigris-600 border-t-transparent animate-spin align-middle" />
              ) : (
                <span className="inline-block h-2 w-2 rounded-full bg-gray-300 dark:bg-gray-700 align-middle" />
              )}
            </span>
            <span
              className={
                done
                  ? "text-gray-500 dark:text-gray-500"
                  : active
                    ? "text-gray-900 dark:text-gray-100 font-medium"
                    : "text-gray-400 dark:text-gray-600"
              }
            >
              {step}
            </span>
          </div>
        );
      })}
      {note && <p className="text-xs text-gray-500 dark:text-gray-400 pt-1">{note}</p>}
    </div>
  );
}
