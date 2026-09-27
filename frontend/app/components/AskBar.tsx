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
import Lenis from "lenis";
import { useRunStream } from "../../lib/useRunStream";
import AnswerText from "./AnswerText";
import Badge, { type Tone } from "./Badge";
import Icon from "./Icon";
import ThinkingOrb from "./ThinkingOrb";

const PLACEHOLDER = "Ask about this repo: what is wrong, why, and how to fix it";
const ISSUE_PLACEHOLDER = "Ask about this issue: why it happens, how to fix it";
// Phones: the bar grows with its text (placeholder included), so keep it one line.
const PLACEHOLDER_SHORT = "Ask about this repo";
const ISSUE_PLACEHOLDER_SHORT = "Ask about this issue";
const LOST_CONNECTION = "Lost the connection to the server. Please try again.";
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
      // Newest first: a new question goes to the top of the sheet.
      return [action.message, ...messages];
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
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const query = matchMedia("(max-width: 739px)");
    const read = () => setCompact(query.matches);
    read();
    query.addEventListener("change", read);
    return () => query.removeEventListener("change", read);
  }, []);
  const [expired, setExpired] = useState<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const latest = useRef<string | null>(null);
  const seq = useRef(0);
  const panel = useRef<HTMLDivElement>(null);
  const panelScroll = useRef<Lenis | null>(null);

  const scoped = routeId ? scan.issues.find((i) => i.id === routeId) : undefined;
  // On an issue page, questions are about that issue; elsewhere, about the whole repo.
  const scopedId = scoped?.id;
  const busy = messages.some(inFlight);
  const suggested = topIssue(scan.issues);

  // Stream the in-flight answer. The opener is memoised so it does not reconnect on every render.
  // The stream depends only on `active`, never on panelOpen, so hiding the panel keeps it open.
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

  // The answer list glides like the page does (its own inertial scroller).
  const hasMessages = messages.length > 0;
  useEffect(() => {
    const el = panel.current;
    if (!hasMessages || !el?.firstElementChild || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const lenis = new Lenis({ wrapper: el, content: el.firstElementChild, eventsTarget: el, autoRaf: true, lerp: 0.07, wheelMultiplier: 0.9 });
    panelScroll.current = lenis;
    return () => {
      lenis.destroy();
      panelScroll.current = null;
    };
  }, [hasMessages]);

  // Newest question sits at the top: glide there when one is asked or the sheet reopens.
  const panelVisible = panelOpen && messages.length > 0;
  useLayoutEffect(() => {
    if (!panelVisible || !panel.current) return;
    if (panelScroll.current) panelScroll.current.scrollTo(0);
    else panel.current.scrollTop = 0;
  }, [messages.length, panelVisible]);

  // Grow the textarea with its content (1 to 5 rows).
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value, compact]);

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
    // Repo-wide by nature, so never scoped: they get the instant answer from scan data.
    { label: "What are the issues?" },
    { label: "Give me an overview of this repository" },
  ];
  if (suggested) {
    suggestions.push(
      { label: `Why does "${cut(suggested.title)}" happen?`, issueId: suggested.id },
      { label: `How would I fix "${cut(suggested.title)}"?`, issueId: suggested.id }
    );
  }

  return (
    <section aria-label="Ask about this repository" className="ask">
      <div className="ask-inner">
        <form
          className="ask-bar"
          onSubmit={(e) => {
            e.preventDefault();
            send(value, scopedId);
          }}
        >
          <ThinkingOrb thinking={busy} />
          <label htmlFor="ask-input" className="sr-only">
            Ask about this repository
          </label>
          <textarea
            id="ask-input"
            ref={input}
            rows={1}
            maxLength={1000}
            value={value}
            placeholder={
              compact
                ? scopedId ? ISSUE_PLACEHOLDER_SHORT : PLACEHOLDER_SHORT
                : scopedId ? ISSUE_PLACEHOLDER : PLACEHOLDER
            }
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send(value, scopedId);
              }
            }}
            className="ask-input"
          />
          {messages.length > 0 && (
            <button
              type="button"
              className={`ask-toggle ${panelOpen ? "is-open" : ""}`}
              onClick={() => setPanelOpen((open) => !open)}
              aria-expanded={panelOpen}
              aria-label={panelOpen ? "Hide answers" : "Show answers"}
            >
              {messages.length}
              <span className="ask-toggle-label">{messages.length === 1 ? " answer" : " answers"}</span>
              <Icon name="chevron" />
            </button>
          )}
          {!value && <kbd className="ask-kbd" aria-hidden="true">/</kbd>}
          <button type="submit" className="launcher-submit" disabled={busy || !value.trim()} aria-busy={busy} aria-label={busy ? "Answering" : "Ask"}>
            {busy ? <span className="holo-spinner" /> : <Icon name="arrow" />}
          </button>
        </form>

        {messages.length === 0 && (
          <div className="ask-suggestions">
            {suggestions.map((s) => (
              <button key={s.label} type="button" className="ask-chip" onClick={() => send(s.label, s.issueId)} title={s.label}>
                <Icon name="spark" />
                <span>{s.label}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div role="status" className="sr-only">
        {announce}
      </div>

      {messages.length > 0 && (
        <div className={`ask-sheet-wrap ${panelOpen ? "is-open" : ""}`} inert={!panelOpen} aria-hidden={!panelOpen}>
          <div className="ask-sheet">
            <header className="ask-sheet-head">
              <span>Answers</span>
              <span className="ask-count">{messages.length}</span>
              <div className="ask-head-actions">
                <button type="button" className="ask-text-btn" onClick={() => setPanelOpen(false)} aria-expanded={true}>
                  <Icon name="chevron" className="rotate-[-90deg]" />
                  Hide
                </button>
                <button
                  type="button"
                  className="ask-text-btn"
                  onClick={() => dispatch({ type: "clear" })}
                  disabled={busy}
                  title={busy ? "Wait for the current answer to finish" : undefined}
                >
                  <Icon name="trash" />
                  Clear
                </button>
              </div>
            </header>
            <div ref={panel} className="ask-sheet-body">
              <div>
                {messages.map((m) => (
                  <MessageView key={m.id} message={m} expired={expired === m.id} />
                ))}
              </div>
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
  const thinking = inFlight(m);
  return (
    <article className="ask-msg">
      <h3 className="ask-q">{m.question}</h3>
      <div className="ask-a">
        <ThinkingOrb thinking={thinking} size="md" />
        <div className="ask-a-body">
          {m.status === "pending" && <p className="ask-thinking">Thinking…</p>}
          {thinking && m.note && <p className="ask-note">{m.note}</p>}
          {answer?.notice && <p className="ask-notice">{answer.notice}</p>}

          {m.status === "error" ? (
            <p role="alert" className="ask-error">
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
          {m.status === "streaming" && m.error && <p className="ask-error">{m.error}</p>}

          {m.status === "done" && answer && (
            <div className="ask-meta">
              <Badge tone={GROUNDING[answer.grounding].tone}>{GROUNDING[answer.grounding].label}</Badge>
              {answer.answered_by !== "scan" && (
                <span>
                  {answer.answered_by === "bob" ? "Answered by IBM Bob" : "Answered by Granite"}
                  {answer.cost !== null && ` · ${answer.cost} Bobcoins`}
                </span>
              )}
              {answer.files_read.length > 0 && (
                <details className="ask-files">
                  <summary>Read {answer.files_read.length} {answer.files_read.length === 1 ? "file" : "files"}</summary>
                  <ul>
                    {answer.files_read.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
