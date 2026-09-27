"use client";

import type { CSSProperties } from "react";
import Snippet from "./Snippet";

/** Plain source shown as a code snippet: numbered lines, no wrapping, copy button. */
export default function CodeBlock({ code, title, label }: { code: string; title: string; label?: string }) {
  const lines = code.replace(/\n$/, "").split("\n");
  const digits = String(lines.length).length;
  return (
    <Snippet title={title} copyText={code} label={label ?? title} style={{ "--digits": `${digits}ch` } as CSSProperties}>
      <div className="diff-rows">
        {lines.map((line, i) => (
          <div key={i} className="diff-row diff-ctx">
            <span className="diff-gutter code-gutter" aria-hidden="true"><span>{i + 1}</span></span>
            <code>{line || " "}</code>
          </div>
        ))}
      </div>
    </Snippet>
  );
}
