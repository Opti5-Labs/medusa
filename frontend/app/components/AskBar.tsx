"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  getAskStatus,
  openAskStream,
  postAsk,
  type AskAnswer,
  type AskStatus,
  type Grounding,
  type Issue,
  type ScanResult,
} from "../../lib/api";
import { useRunStream } from "../../lib/useRunStream";
import AnswerText from "./AnswerText";
import Badge, { type Tone } from "./Badge";

const PLACEHOLDER =
  "Ask anything about this repo... e.g. What are the issues? Why does it happen? How do I fix it?";
const LOST_CONNECTION = "Lost the connection to the server. Please try again.";
const NEAR_BOTTOM_PX = 48;
const PRIORITY_RANK ={ High: 0, Medium: 1, Low: 2 } as const;

const GROUNDING: Record<Grounding, { tone: Tone; label: string }> = {
  scan_data: { tone: "gray", label: "From scan data" },
  sandbox_verified: { tone: "green", label: "Includes sandbox-verified results" },
  reasoning: { tone: "amber", label: "Reasoning only: code was read, not run" },
};

// ── Conversation state ───────────────────────────────────────────────────────

interface Message {
  id: string;
  question: string;
  text: string;
  status: "pending" | "streaming" | "done" | "error";
  answer?: AskAnswer;
  note?: string;
  error?: string;
}

type Action =
  | { type: "add"; message: Message }
  | { type: "reset"; id: string }
  | { type: "token"; id: string; text: string }
  | { type: "note"; id: string; note: string }
  | { type: "logError"; id: string; error: string }
  | { type: "done"; id: string; answer: AskAnswer }
  | { type: "fail"; id: string; error: string }
  | { type: "clear" };

function patch(messages: Message[], id: string, change: (m: Message) => Message): Message[] {
  return messages.map((m) => (m.id === id ? change(m) : m));
}

function reducer(messages: Message[], action: Action): Message[] {
  switch (action.type) {
    case "add":
      return [...messages, action.message];
    case "reset":
      return patch(messages, action.id, (m) => ({ ...m, text: "", note: undefined, error: undefined, status: "pending" }));
    case "token":
      return patch(messages, action.id, (m) => ({ ...m, text: m.text + action.text, status: "streaming" }));
    case "note":
      return patch(messages, action.id, (m) => ({ ...m, note: action.note }));
    case "logError":
      return patch(messages, action.id, (m) => ({ ...m, error: action.error }));
    case "done":
      return patch(messages, action.id, (m) =>
        action.answer.error
          ? { ...m, text: "", answer: action.answer, error: action.answer.error, status: "error" }
          : { ...m, text: action.answer.answer, answer: action.answer, error: undefined, status: "done" }
      );
    case "fail":
      return patch(messages, action.id, (m) => ({ ...m, error: action.error, status: "error" }));
    case "clear":
      return [];
  }
}

const inFlight = (m: Message) => m.status === "pending" || m.status === "streaming";

// ── Outer: decides whether the bar shows at all ──────────────────────────────

function readScan(scanId: string): ScanResult | null {
  try {
    const raw = sessionStorage.getItem(`scan:${scanId}`);
    return raw ? (JSON.parse(raw) as ScanResult) : null;
  } catch {
    return null;
  }
}

export default function AskBar() {
  const scanId = useSearchParams().get("scan");
  const [stored, setStored] = useState<{ id: string; scan: ScanResult | null } | null>(null);
  const [status, setStatus] = useState<AskStatus | null>(null);

  useEffect(() => {
    if (!scanId) return;
    setStored({ id: scanId, scan: readScan(scanId) });
  }, [scanId]);

  useEffect(() => {
    let live = true;
    getAskStatus()
      .then((s) => live && setStatus(s))
      .catch(() => live && setStatus({ enabled: true, granite_available: true, bob_available: true, bob_reason: null }));
    return () => {
      live = false;
    };
  }, []);

  const scan = scanId && stored?.id === scanId ? stored.scan : null;
  if (!scanId || !scan || !status?.enabled) return null;
  return <AskConversation key={scanId} scanId={scanId} scan={scan} />;
}

// ── Inner: the conversation, reset by a new scan ─────────────────────────────

function topIssue(issues: Issue[]): Issue | undefined {
  return [...issues].sort((a, b) => PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority])[0];
}

const cut = (s: string, n = 60) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function AskConversation({ scanId, scan }: { scanId: string; scan: ScanResult }) {
  const routeId = useParams<{ id?: string }>().id;
  const [messages, dispatch] = useReducer(reducer, [] as Message[]);
  const [active, setActive] = useState<{ msgId: string; askId: string } | null>(null);
  const [value, setValue] = useState("");
  const [panelOpen, setPanelOpen] = useState(true);
  const [wholeRepoFor, setWholeRepoFor] = useState<string | null>(null);
  const [expired, setExpired] = useState<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const latest = useRef<string | null>(null);
  const seq = useRef(0);
  const panel = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const scoped = routeId ? scan.issues.find((i) => i.id === routeId) : undefined;
  const scopedId = scoped && wholeRepoFor !== scoped.id ? scoped.id : undefined;
  const busy = messages.some(inFlight);
  const suggested = topIssue(scan.issues);

  // Stream the in-flight answer. The opener is memoised so it does not reconnect on every render.
  const open = useMemo(() => (active ? () => openAskStream(active.askId) : null), [active]);
  const id = active?.msgId ?? "";
  useRunStream<AskAnswer>(open, {
    onReset: () => dispatch({ type: "reset", id }),
    onToken: (text) => dispatch({ type: "token", id, text }),
    onLog: (e) => {
      if (e.level === "error") dispatch({ type: "logError", id, error: e.message });
      else if (e.level === "info" || e.level === "warn") dispatch({ type: "note", id, note: e.message });
    },
    onDone: (answer) => {
      dispatch({ type: "done", id, answer });
      setActive(null);
    },
    onError: () => {
      dispatch({ type: "fail", id, error: LOST_CONNECTION });
      setActive(null);
    },
  });

  function send(question: string, issueId?: string) {
    const q = question.trim();
    if (!q || busy) return;
    const msgId = `q${++seq.current}`;
    latest.current = msgId;
    stick.current = true; // a new question always pins the panel to the bottom
    dispatch({ type: "add", message: { id: msgId, question: q, text: "", status: "pending" } });
    setValue("");
    setPanelOpen(true);
    setExpired(null);
    postAsk(scanId, issueId ? { question: q, issue_id: issueId } : { question: q })
      .then((r) => {
        if (latest.current === msgId) setActive({ msgId, askId: r.ask_id });
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : "The question could not be sent. Please try again.";
        dispatch({ type: "fail", id: msgId, error: message });
        if (/expired/i.test(message)) setExpired(msgId);
      });
  }

  // Chat-style scrolling: pin to the bottom after each update, unless the user scrolled up.
  // Jumps are instant, which also honours prefers-reduced-motion and keeps scroll events honest.
  const panelVisible = panelOpen && messages.length > 0;
  useLayoutEffect(() => {
    const el = panel.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages, panelVisible]);

  // Reopening the panel starts at the bottom.
  useLayoutEffect(() => {
    if (!panelVisible) return;
    stick.current = true;
    const el = panel.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [panelVisible]);

  function onPanelScroll() {
    const el = panel.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
  }

  // Grow the textarea with its content (1 to 5 rows).
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  // "/" focuses the bar; Escape closes the answer panel.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setPanelOpen(false);
        return;
      }
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      e.preventDefault();
      input.current?.focus();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // A status region only announces when its text changes, so alternate an invisible suffix.
  const doneCount = messages.filter((m) => m.status === "done").length;
  const announce = doneCount ? `Answer ready${doneCount % 2 ? "" : "​"}` : "";

  const suggestions: { label: string; issueId?: string }[] = [
    { label: "What are the issues?", issueId: scopedId },
    { label: "Give me an overview of this repository", issueId: scopedId },
  ];
  if (suggested) {
    suggestions.push(
      { label: `Why does "${cut(suggested.title)}" happen?`, issueId: suggested.id },
      { label: `How would I fix "${cut(suggested.title)}"?`, issueId: suggested.id }
    );
  }

  return (
    <section aria-label="Ask about this repository" className="relative">
      <div className="mx-auto max-w-5xl px-4 sm:px-6 py-3 space-y-2">
        {scoped && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-gray-600 dark:text-gray-400">
            <span className="rounded-full bg-gray-100 dark:bg-gray-800 px-2 py-0.5 max-w-full break-words">
              {wholeRepoFor === scoped.id ? "Asking about: the whole repo" : `Asking about: ${scoped.title}`}
            </span>
            <button
              type="button"
              onClick={() => setWholeRepoFor(wholeRepoFor === scoped.id ? null : scoped.id)}
              className="underline underline-offset-2 hover:text-gray-900 dark:hover:text-gray-100"
            >
              {wholeRepoFor === scoped.id ? "Ask about this issue" : "Whole repo"}
            </button>
          </div>
        )}

        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            send(value, scopedId);
          }}
        >
          <label htmlFor="ask-input" className="sr-only">
            Ask about this repository
          </label>
          <textarea
            id="ask-input"
            ref={input}
            rows={1}
            maxLength={1000}
            value={value}
            placeholder={PLACEHOLDER}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send(value, scopedId);
              }
            }}
            className="flex-1 min-w-0 resize-none max-h-[7.25rem] rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 text-sm leading-5 placeholder:text-gray-400 dark:placeholder:text-gray-500"
          />
          <button
            type="submit"
            disabled={busy || !value.trim()}
            aria-busy={busy}
            className="rounded-lg bg-verdigris-600 hover:bg-verdigris-700 text-white px-4 py-2 text-sm font-medium transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {busy ? "Answering…" : "Ask"}
          </button>
        </form>

        {messages.length === 0 ? (
          <div className="flex flex-wrap gap-2">
            {suggestions.map((s) => (
              <button
                key={s.label}
                type="button"
                onClick={() => send(s.label, s.issueId)}
                className="rounded-full border border-gray-300 dark:border-gray-700 px-3 py-1 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 text-left"
              >
                {s.label}
              </button>
            ))}
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-600 dark:text-gray-400">
            <button
              type="button"
              onClick={() => setPanelOpen((o) => !o)}
              aria-expanded={panelOpen}
              className="underline underline-offset-2 hover:text-gray-900 dark:hover:text-gray-100"
            >
              {panelOpen ? "Hide answers" : "Show answers"}
            </button>
            <button
              type="button"
              onClick={() => dispatch({ type: "clear" })}
              className="underline underline-offset-2 hover:text-gray-900 dark:hover:text-gray-100"
            >
              Clear
            </button>
          </div>
        )}
      </div>

      <div role="status" className="sr-only">
        {announce}
      </div>

      {panelOpen && messages.length > 0 && (
        <div className="absolute left-0 right-0 top-full z-10 pointer-events-none">
          <div className="mx-auto max-w-5xl px-4 sm:px-6">
            {/* The scroll container is itself the opaque surface: no padding strip or rounded corner outside it. */}
            <div
              ref={panel}
              onScroll={onPanelScroll}
              className="pointer-events-auto max-h-[65vh] overflow-y-auto overscroll-contain border-x border-b border-gray-200 dark:border-gray-800 shadow-lg bg-white dark:bg-gray-900 p-4 space-y-5"
            >
              {messages.map((m) => (
                <MessageView key={m.id} message={m} expired={expired === m.id} />
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

// ── One question and its answer ──────────────────────────────────────────────

function MessageView({ message: m, expired }: { message: Message; expired: boolean }) {
  const answer = m.answer;
  return (
    <article className="space-y-2">
      <p className="text-sm font-medium text-gray-900 dark:text-gray-100 break-words">{m.question}</p>

      {m.status === "pending" && <p className="text-sm text-gray-500 dark:text-gray-400">Thinking…</p>}
      {(m.status === "pending" || m.status === "streaming") && m.note && (
        <p className="text-xs text-gray-500 dark:text-gray-400">{m.note}</p>
      )}
      {answer?.notice && <p className="text-xs text-amber-700 dark:text-amber-400">{answer.notice}</p>}

      {m.status === "error" ? (
        <p role="alert" className="text-sm text-fail dark:text-red-400">
          {m.error}
          {expired && (
            <>
              {" "}
              <Link href="/" className="underline underline-offset-2">
                Start a new scan
              </Link>
            </>
          )}
        </p>
      ) : (
        m.text && <AnswerText text={m.text} />
      )}
      {m.status === "streaming" && m.error && <p className="text-xs text-fail dark:text-red-400">{m.error}</p>}

      {m.status === "done" && answer && (
        <div className="space-y-2">
          {answer.answered_by !== "scan" && (
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {answer.answered_by === "bob" ? "Answered by IBM Bob" : "Answered by Granite"}
              {answer.cost !== null && ` · ${answer.cost} Bobcoins`}
            </p>
          )}
          <Badge tone={GROUNDING[answer.grounding].tone}>{GROUNDING[answer.grounding].label}</Badge>
          {answer.files_read.length > 0 && (
            <details className="text-xs text-gray-600 dark:text-gray-400">
              <summary className="cursor-pointer">Read {answer.files_read.length} files</summary>
              <ul className="mt-1 font-mono text-xs space-y-0.5 break-all">
                {answer.files_read.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </article>
  );
}
