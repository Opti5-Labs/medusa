"use client";

import { useMemo, type CSSProperties } from "react";
import { highlightLines, languageForPath } from "../../lib/highlight";
import Snippet from "./Snippet";
import Tokens from "./Tokens";

/**
 * Source shown as a code snippet: numbered, syntax-highlighted lines, no
 * wrapping, copy button. *language* is a Prism language; else guessed from the title.
 */
export default function CodeBlock({
  code,
  title,
  label,
  language,
}: {
  code: string;
  title: string;
  label?: string;
  language?: string;
}) {
  const lines = useMemo(
    () => highlightLines(code.replace(/\n$/, ""), language ?? languageForPath(title)),
    [code, title, language],
  );
  const digits = String(lines.length).length;
  return (
    <Snippet title={title} copyText={code} label={label ?? title} style={{ "--digits": `${digits}ch` } as CSSProperties}>
      <div className="diff-rows">
        {lines.map((line, i) => (
          <div key={i} className="diff-row diff-ctx">
            <span className="diff-gutter code-gutter" aria-hidden="true"><span>{i + 1}</span></span>
            <code>
              <Tokens line={line} />
            </code>
          </div>
        ))}
      </div>
    </Snippet>
  );
}
