"use client";

import type { ReactNode } from "react";

/**
 * Minimal, safe markdown for Ask answers. Everything is emitted as React text
 * nodes (never HTML), and it is one cheap line-based pass because it re-renders
 * on every streamed token. An unterminated code fence renders as code to the end.
 */

const CHIP =
  "rounded px-1.5 text-xs font-mono bg-verdigris-50 text-verdigris-700 dark:bg-verdigris-900/40 dark:text-verdigris-300";

// `code` | **bold** | [path], [path:line], [path:line-line] (a bare [word] is not a citation)
const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[(?=[^\]\s]*[./:])[\w./@\\-]+(?::\d+(?:-\d+)?)?\](?!\())/g;

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // clipboard unavailable (insecure context or denied): nothing to do
  }
}

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const [token, code, bold] = m;
    if (code) {
      out.push(
        <code key={key++} className="rounded px-1 font-mono text-[0.85em] bg-gray-100 dark:bg-gray-800">
          {token.slice(1, -1)}
        </code>
      );
    } else if (bold) {
      out.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else {
      const cite = token.slice(1, -1);
      out.push(
        <button key={key++} type="button" title="Click to copy" onClick={() => copy(cite)} className={CHIP}>
          {cite}
        </button>
      );
    }
    last = at + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { kind: "code"; text: string }
  | { kind: "heading"; text: string }
  | { kind: "ul"; items: Item[] }
  | { kind: "ol"; items: Item[]; start: number }
  | { kind: "p"; text: string };

/** A list item: its first line, plus any indented continuation lines beneath it. */
interface Item {
  text: string;
  more: string[];
}

const FENCE = /^\s*```/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;
const BULLET = /^\s*[-*]\s+(.*)$/;
const NUMBERED = /^\s*(\d+)\.\s+(.*)$/;
const CONTINUATION = /^ {2,}\S|^\t+\S/;

const isList = (b: Block | undefined): boolean => !!b && (b.kind === "ul" || b.kind === "ol");

function parse(source: string): Block[] {
  const blocks: Block[] = [];
  let code: string[] | null = null;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) blocks.push({ kind: "p", text: para.join("\n") });
    para = [];
  };
  const pushItem = (kind: "ul" | "ol", text: string, start = 1) => {
    const prev = blocks[blocks.length - 1];
    const item: Item = { text, more: [] };
    if (prev && prev.kind === kind) prev.items.push(item);
    else if (kind === "ol") blocks.push({ kind, items: [item], start });
    else blocks.push({ kind, items: [item] });
  };

  for (const line of source.split("\n")) {
    if (code) {
      if (FENCE.test(line)) {
        blocks.push({ kind: "code", text: code.join("\n") });
        code = null;
      } else code.push(line);
      continue;
    }
    let m: RegExpMatchArray | null;
    if (FENCE.test(line)) {
      flushPara();
      code = [];
    } else if ((m = line.match(HEADING))) {
      flushPara();
      blocks.push({ kind: "heading", text: m[1] });
    } else if ((m = line.match(BULLET))) {
      flushPara();
      pushItem("ul", m[1]);
    } else if ((m = line.match(NUMBERED))) {
      flushPara();
      pushItem("ol", m[2], Number(m[1]));
    } else if (CONTINUATION.test(line) && isList(blocks[blocks.length - 1])) {
      // an indented line directly under a list item belongs to that item
      const list = blocks[blocks.length - 1] as Extract<Block, { items: Item[] }>;
      list.items[list.items.length - 1].more.push(line.trim());
    } else if (line.trim() === "") {
      flushPara();
      // a blank line ends a list: the next item starts a fresh one
      const prev = blocks[blocks.length - 1];
      if (prev && (prev.kind === "ul" || prev.kind === "ol")) blocks.push({ kind: "p", text: "" });
    } else {
      para.push(line);
    }
  }
  if (code) blocks.push({ kind: "code", text: code.join("\n") });
  flushPara();
  return blocks.filter((b) => !(b.kind === "p" && b.text === ""));
}

function ItemView({ item }: { item: Item }) {
  return (
    <li>
      {inline(item.text)}
      {item.more.map((line, k) => (
        <span key={k} className="block">
          {inline(line)}
        </span>
      ))}
    </li>
  );
}

export default function AnswerText({ text }: { text: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed text-gray-800 dark:text-gray-200 break-words">
      {parse(text).map((b, i) => {
        switch (b.kind) {
          case "code":
            return (
              <pre
                key={i}
                className="rounded-md bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 p-3 font-mono text-xs leading-relaxed overflow-x-auto"
              >
                <code>{b.text}</code>
              </pre>
            );
          case "heading":
            return (
              <p key={i} className="font-semibold text-gray-900 dark:text-gray-100">
                {inline(b.text)}
              </p>
            );
          case "ul":
            return (
              <ul key={i} className="list-disc pl-5 space-y-1">
                {b.items.map((it, j) => (
                  <ItemView key={j} item={it} />
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={i} start={b.start} className="list-decimal pl-5 space-y-1">
                {b.items.map((it, j) => (
                  <ItemView key={j} item={it} />
                ))}
              </ol>
            );
          default:
            return (
              <p key={i} className="whitespace-pre-wrap">
                {inline(b.text)}
              </p>
            );
        }
      })}
    </div>
  );
}
