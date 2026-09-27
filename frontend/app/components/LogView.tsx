"use client";

import { memo, useEffect, useRef } from "react";
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
  /** While the run is still going, end the log with a spinner row. */
  busy?: boolean;
  /** Number each line in a gutter, like a terminal pane in an editor. */
  numbered?: boolean;
}

// One line. Events are append-only and never change, so earlier lines are skipped when a new
// one arrives: a streaming log costs one line of work per event, not the whole log.
const LogLine = memo(function LogLine({ event: e, showSource }: { event: LogEvent; showSource: boolean }) {
  return (
    <div className={`whitespace-pre-wrap break-words ${LEVEL_STYLES[e.level]}`}>
      {showSource && (
        <span className="text-gray-400 dark:text-gray-500 select-none">[{sourceLabel(e.source)}] </span>
      )}
      {e.message}
    </div>
  );
});

/** Scrolling live log. Sticks to the bottom unless the user has scrolled up. */
export default memo(function LogView({ events, showSource = true, emptyText = "Waiting…", maxHeight = "max-h-80", busy = false, numbered = false }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events.length, busy]);

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
      className={`${maxHeight} log-view ${numbered ? "is-numbered" : ""}`}
    >
      <div>
      {events.length === 0 ? (
        <p className="text-gray-400 dark:text-gray-600">{emptyText}</p>
      ) : (
        events.map((e, i) => <LogLine key={i} event={e} showSource={showSource} />)
      )}
      {busy && events.length > 0 && (
        <div className="log-busy" aria-hidden="true"><span className="holo-spinner" />Working…</div>
      )}
      </div>
    </div>
  );
});
