/**
 * Syntax highlighting for code snippets, line by line, so it fits numbered and
 * diff layouts. Tokens carry CSS classes (tok-keyword, tok-string, …) rather
 * than inline colours, so light and dark themes style them in CSS.
 */
import { Prism, normalizeTokens } from "prism-react-renderer";

export interface Tok {
  text: string;
  cls: string;
}

const BY_EXTENSION: Record<string, string> = {
  py: "python", pyi: "python",
  js: "jsx", jsx: "jsx", mjs: "jsx", cjs: "jsx",
  ts: "tsx", tsx: "tsx", mts: "tsx", cts: "tsx",
  json: "json", yml: "yaml", yaml: "yaml",
  go: "go", rs: "rust", md: "markdown", css: "css", sql: "sql",
  c: "c", h: "c", cpp: "cpp", cc: "cpp", hpp: "cpp",
  kt: "kotlin", swift: "swift", html: "markup", xml: "markup", svg: "markup", vue: "markup",
};

const ALIASES: Record<string, string> = {
  python: "python", py: "python", javascript: "jsx", js: "jsx", jsx: "jsx",
  typescript: "tsx", ts: "tsx", tsx: "tsx", json: "json", yaml: "yaml", yml: "yaml",
  go: "go", rust: "rust", rs: "rust", css: "css", sql: "sql", html: "markup", xml: "markup",
  c: "c", cpp: "cpp", kotlin: "kotlin", swift: "swift", markdown: "markdown", md: "markdown",
};

/** The Prism language for a file path, or null when there is none to use. */
export function languageForPath(path: string | null | undefined): string | null {
  const ext = path?.split(/[\\/]/).pop()?.split(".").pop()?.toLowerCase();
  return (ext && BY_EXTENSION[ext]) || null;
}

/** The Prism language for a Markdown fence tag such as "python" or "ts". */
export function languageForTag(tag: string | null | undefined): string | null {
  return (tag && ALIASES[tag.trim().toLowerCase()]) || null;
}

/** One entry per line of *code*; plain text when the language is unknown. */
export function highlightLines(code: string, language: string | null): Tok[][] {
  const grammar = language ? Prism.languages[language] : undefined;
  if (!grammar) return code.split("\n").map((text) => [{ text, cls: "" }]);
  return normalizeTokens(Prism.tokenize(code, grammar)).map((line) =>
    line
      .filter((t) => !t.empty)
      .map((t) => ({
        text: t.content,
        cls: t.types.filter((type) => type !== "plain").map((type) => `tok-${type}`).join(" "),
      })),
  );
}
