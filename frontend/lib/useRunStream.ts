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

/** How long to wait after the first event to see whether the whole run arrives at once. */
const BURST_MS = 200;
/** Events from a model (Granite or Bob, live or recorded) mean real work: never paced. */
const MODEL_SOURCE = /^(granite|bob|investigator|synthesis|recorded)/;

/**
 * Follow a Medusa SSE stream until its `done` event.
 *
 * The server replays the whole run on every connection, so a reconnect
 * clears the log first (onReset) instead of duplicating it. The EventSource
 * is closed on `done` and on unmount.
 *
 * `paceMs` / `minMs` (optional) are for runs that finish instantly, such as
 * the bundled demo: when the whole run, `done` included, arrives in one
 * burst and none of it came from Granite or Bob, its real events are played
 * back `paceMs` apart and spread over at least `minMs`, so each step can be
 * read instead of the page jumping straight to the result. Nothing is
 * invented. Anything that is still streaming after the first moment is a
 * real process (sandbox, Granite, Bob) and is shown as it arrives, unpaced.
 */
export function useRunStream<T>(open: (() => EventSource) | null, handlers: Handlers<T>, paceMs = 0, minMs = 0) {
  useEffect(() => {
    if (!open) return;
    const source = open();
    let finished = false;
    // pending: holding the first events to see if the run is an instant burst.
    // paced: it was; play it back. live: it wasn't; show everything as it comes.
    let mode: "pending" | "paced" | "live" = paceMs ? "pending" : "live";
    let held: { step: () => void; source?: string }[] = [];
    let burstTimer = 0;
    const queue: (() => void)[] = [];
    let timer = 0;
    const startedAt = performance.now();

    const gap = () => {
      const burst = Math.max(120, Math.min(paceMs, 6000 / Math.max(queue.length, 1)));
      const left = startedAt + minMs - performance.now();
      return Math.max(burst, Math.min(2500, left / Math.max(queue.length, 1)));
    };
    const drain = () => {
      timer = 0;
      const step = queue.shift();
      if (!step) return;
      step();
      if (queue.length) timer = window.setTimeout(drain, gap());
    };
    const goLive = () => {
      mode = "live";
      window.clearTimeout(burstTimer);
      const steps = held;
      held = [];
      steps.forEach((h) => h.step());
    };
    const decide = () => {
      // Called when `done` arrives while still pending.
      window.clearTimeout(burstTimer);
      if (held.some((h) => h.source && MODEL_SOURCE.test(h.source))) {
        goLive();
        return;
      }
      mode = "paced";
      queue.push(...held.map((h) => h.step));
      held = [];
      drain();
    };
    const handle = (step: () => void, eventSource?: string, isDone = false) => {
      if (mode === "live") {
        step();
        return;
      }
      if (mode === "paced") {
        queue.push(step);
        if (!timer) timer = window.setTimeout(drain, gap());
        return;
      }
      held.push({ step, source: eventSource });
      if (isDone) decide();
      else if (!burstTimer) burstTimer = window.setTimeout(goLive, BURST_MS);
    };

    source.onopen = () => {
      held = [];
      queue.length = 0;
      window.clearTimeout(timer);
      window.clearTimeout(burstTimer);
      timer = 0;
      burstTimer = 0;
      if (mode === "paced") mode = "pending";
      handlers.onReset();
    };
    source.addEventListener("log", (e) => {
      const event = JSON.parse((e as MessageEvent).data) as LogEvent;
      handle(() => handlers.onLog(event), event.source);
    });
    source.addEventListener("token", (e) => {
      const { text } = JSON.parse((e as MessageEvent).data) as { text: string };
      handlers.onToken?.(text);
    });
    source.addEventListener("done", (e) => {
      finished = true;
      source.close();
      const payload = JSON.parse((e as MessageEvent).data) as T;
      handle(() => handlers.onDone(payload), undefined, true);
    });
    source.onerror = () => {
      if (finished) return;
      if (source.readyState === EventSource.CLOSED) {
        if (mode === "pending") goLive();
        handle(() => handlers.onError("Lost the connection to the server. Please try again."));
      }
      // Otherwise the browser is reconnecting; onopen will reset the log.
    };
    return () => {
      source.close();
      window.clearTimeout(timer);
      window.clearTimeout(burstTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reconnect only when the stream changes
  }, [open]);
}
