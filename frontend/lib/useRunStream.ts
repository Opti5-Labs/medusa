"use client";

import { useEffect } from "react";
import type { LogEvent } from "./api";

interface Handlers<T> {
  onReset: () => void;
  onLog: (event: LogEvent) => void;
  onDone: (payload: T) => void;
  onError: (message: string) => void;
  /** Ask streams only: incremental answer text. */
  onToken?: (text: string) => void;
}

/**
 * Follow a Medusa SSE stream until its `done` event.
 *
 * The server replays the whole run on every connection, so a reconnect
 * clears the log first (onReset) instead of duplicating it. The EventSource
 * is closed on `done` and on unmount.
 */
export function useRunStream<T>(open: (() => EventSource) | null, handlers: Handlers<T>) {
  useEffect(() => {
    if (!open) return;
    const source = open();
    let finished = false;

    source.onopen = () => handlers.onReset();
    source.addEventListener("log", (e) => {
      handlers.onLog(JSON.parse((e as MessageEvent).data) as LogEvent);
    });
    source.addEventListener("token", (e) => {
      const { text } = JSON.parse((e as MessageEvent).data) as { text: string };
      handlers.onToken?.(text);
    });
    source.addEventListener("done", (e) => {
      finished = true;
      source.close();
      handlers.onDone(JSON.parse((e as MessageEvent).data) as T);
    });
    source.onerror = () => {
      if (finished) return;
      if (source.readyState === EventSource.CLOSED) {
        handlers.onError("Lost the connection to the server. Please try again.");
      }
      // Otherwise the browser is reconnecting; onopen will reset the log.
    };
    return () => source.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reconnect only when the stream changes
  }, [open]);
}
