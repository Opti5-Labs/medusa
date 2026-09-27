import type { Tok } from "../../lib/highlight";

/** One highlighted line; an empty line keeps its height. */
export default function Tokens({ line }: { line: Tok[] }) {
  if (line.length === 0 || line.every((t) => t.text === "")) return <>{" "}</>;
  return (
    <>
      {line.map((t, i) =>
        t.cls ? (
          <span key={i} className={t.cls}>
            {t.text}
          </span>
        ) : (
          t.text
        ),
      )}
    </>
  );
}
