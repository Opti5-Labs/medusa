"use client";

import { memo, useMemo, type CSSProperties } from "react";
import Snippet from "./Snippet";

type Row =
  | { kind: "file"; path: string }
  | { kind: "hunk"; text: string }
  | { kind: "add" | "del" | "ctx"; oldNo: number | null; newNo: number | null; text: string };

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/** Unified diff → rows with old/new line numbers, like an editor's diff gutter. */
function parse(patch: string): { rows: Row[]; added: number; removed: number } {
  const rows: Row[] = [];
  let oldNo = 0;
  let newNo = 0;
  let added = 0;
  let removed = 0;
  for (const line of patch.replace(/\n$/, "").split("\n")) {
    if (line.startsWith("diff --git") || line.startsWith("index ") || line.startsWith("--- ")) continue;
    if (line.startsWith("+++ ")) {
      rows.push({ kind: "file", path: line.slice(4).replace(/^b\//, "") });
      continue;
    }
    const hunk = HUNK.exec(line);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      rows.push({ kind: "hunk", text: line });
    } else if (line.startsWith("+")) {
      rows.push({ kind: "add", oldNo: null, newNo: newNo++, text: line.slice(1) });
      added++;
    } else if (line.startsWith("-")) {
      rows.push({ kind: "del", oldNo: oldNo++, newNo: null, text: line.slice(1) });
      removed++;
    } else if (!line.startsWith("\\")) {
      rows.push({ kind: "ctx", oldNo: oldNo++, newNo: newNo++, text: line.slice(1) });
    }
  }
  return { rows, added, removed };
}

/**
 * A patch shown as a code snippet: file header, old/new line-number gutters
 * that stay put while the code scrolls sideways, tinted added/removed rows,
 * no wrapping, and a copy button.
 */
export default memo(function DiffView({ patch }: { patch: string }) {
  const { rows, added, removed } = useMemo(() => parse(patch), [patch]);
  const files = rows.filter((r) => r.kind === "file");
  const title = files.length === 1 && files[0].kind === "file" ? files[0].path : `${files.length} files`;
  // Size the number columns to the longest line number instead of a fixed width.
  const digits = String(Math.max(1, ...rows.map((r) => ("newNo" in r ? Math.max(r.oldNo ?? 0, r.newNo ?? 0) : 0)))).length;

  return (
    <Snippet
      title={title}
      copyText={patch}
      label="Patch"
      style={{ "--digits": `${digits}ch` } as CSSProperties}
      extra={
        <span className="code-snippet-stats">
          <span className="is-add">+{added}</span> <span className="is-del">−{removed}</span>
        </span>
      }
    >
      <div className="diff-rows">
        {rows.map((r, i) => {
          if (r.kind === "file") {
            return files.length > 1 ? <div key={i} className="diff-row diff-file">{r.path}</div> : null;
          }
          if (r.kind === "hunk") return <div key={i} className="diff-row diff-hunk"><span className="diff-gutter" /><code>{r.text}</code></div>;
          return (
            <div key={i} className={`diff-row diff-${r.kind}`}>
              <span className="diff-gutter" aria-hidden="true">
                <span>{r.oldNo ?? ""}</span>
                <span>{r.newNo ?? ""}</span>
                <span className="diff-sign">{r.kind === "add" ? "+" : r.kind === "del" ? "−" : ""}</span>
              </span>
              <code>{r.text || " "}</code>
            </div>
          );
        })}
      </div>
    </Snippet>
  );
});
