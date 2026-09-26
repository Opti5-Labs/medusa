"use client";

import { useEffect, useRef } from "react";
import type { LogEvent } from "../../lib/api";

const LEVEL_STYLES: Record<LogEvent["level"], string> = {
  info: "text-gray-700 dark:text-gray-300",
  warn: "text-amber-700 dark:text-amber-400",
  error: "text-red-700 dark:text-red-400 font-medium",
  result: "text-gray-900 dark:text-gray-100 font-semibold",
};

const SOURCE_LABELS: Record<string, string> = {
  "investigator:runtime": "runtime",
  "investigator:repository": "repository",
  "investigator:skeptic": "skeptic",
};

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source.replace(/^candidate:/, "");
}

interface Props {
  events: LogEvent[];
  showSource?: boolean;
  emptyText?: string;
  maxHeight?: string;
}

/** Scrolling live log. Sticks to the bottom unless the user has scrolled up. */
export default function LogView({ events, showSource = true, emptyText = "Waiting…", maxHeight = "max-h-80" }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  // Re-pin after reflows (e.g. the panel grid changing width) while stuck to the bottom.
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (stick.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={box}
      onWheel={() => {
        // Only the user's own scrolling unpins the log.
        requestAnimationFrame(() => {
          const el = box.current;
          if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        });
      }}
      role="log"
      aria-live="polite"
      className={`${maxHeight} log-view`}
    >
      <div>
      {events.length === 0 ? (
        <p className="text-gray-400 dark:text-gray-600">{emptyText}</p>
      ) : (
        events.map((e, i) => (
          <div key={i} className={`whitespace-pre-wrap break-words ${LEVEL_STYLES[e.level]}`}>
            {showSource && (
              <span className="text-gray-400 dark:text-gray-500 select-none">[{sourceLabel(e.source)}] </span>
            )}
            {e.message}
          </div>
        ))
      )}
      </div>
    </div>
  );
}
