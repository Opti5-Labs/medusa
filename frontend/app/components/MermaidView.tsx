"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  /** Server-generated, validated Mermaid source (see backend/app/architecture/mermaid.py). */
  source: string;
  /** Stable id for this diagram instance — must be unique per rendered diagram on the page. */
  id: string;
  className?: string;
  /** Shown instead of the diagram if rendering fails (e.g. an unsupported browser). */
  fallback?: React.ReactNode;
}

/**
 * Renders Mermaid source to an inline SVG, client-side only.
 *
 * `dangerouslySetInnerHTML` below is safe here specifically because the
 * source is never repository- or model-authored: it is generated and
 * allowlist-validated on the server (mermaid.py `generate()` + `validate()`)
 * before it ever reaches this component, and rendering itself runs with
 * `securityLevel: "strict"` and `htmlLabels: false`, so Mermaid never emits
 * foreign-object HTML, click handlers, or hrefs into the output SVG.
 */
export default function MermaidView({ source, id, className, fallback }: Props) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [themeTick, setThemeTick] = useState(0);
  const [ready, setReady] = useState(false);
  const mounted = useRef(false);
  const host = useRef<HTMLDivElement>(null);

  // Follow the selected app theme, including changes from another browser tab.
  useEffect(() => {
    let dark = document.documentElement.classList.contains("dark");
    const observer = new MutationObserver(() => {
      const next = document.documentElement.classList.contains("dark");
      if (next === dark) return;
      dark = next;
      setThemeTick((t) => t + 1);
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Layout (Mermaid + ELK) blocks the main thread for up to a second, and the
  // smooth scroller runs on the main thread, so rendering mid-scroll stutters.
  // Wait until the diagram is near the viewport (never, while inside a closed
  // <details>, which has no box) and scrolling has paused.
  useEffect(() => {
    const el = host.current;
    if (!el || ready) return;
    let near = false;
    let quietTimer = 0;
    const tryStart = () => {
      window.clearTimeout(quietTimer);
      quietTimer = window.setTimeout(() => { if (near) setReady(true); }, 400);
    };
    const observer = new IntersectionObserver(([entry]) => {
      near = entry.isIntersecting;
      if (near) tryStart();
    }, { rootMargin: "50% 0px" });
    observer.observe(el);
    document.addEventListener("scroll", tryStart, { capture: true, passive: true });
    return () => {
      observer.disconnect();
      document.removeEventListener("scroll", tryStart, { capture: true });
      window.clearTimeout(quietTimer);
    };
  }, [ready]);

  useEffect(() => {
    if (!source) {
      setSvg(null);
      setFailed(false);
      return;
    }
    if (!ready) return;
    let cancelled = false;
    setFailed(false);

    (async () => {
      try {
        const { default: mermaid } = await import("mermaid");
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          htmlLabels: false,
          theme: document.documentElement.classList.contains("dark") ? "dark" : "default",
          flowchart: { htmlLabels: false, useMaxWidth: true },
        });
        await mermaid.parse(source);
        const { svg: rendered } = await mermaid.render(`mmd-${id}`, source);
        if (!cancelled && mounted.current) setSvg(rendered);
      } catch {
        if (!cancelled) {
          setFailed(true);
          setSvg(null);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- themeTick intentionally forces a re-render
  }, [source, id, themeTick, ready]);

  if (!source) return null;

  if (failed) {
    return (
      <div ref={host} className={className}>
        <p role="alert" className="field-error">
          The diagram could not be rendered. The information below is the same.
        </p>
        {fallback}
      </div>
    );
  }

  if (!svg) {
    return (
      <div ref={host} className={className}>
        <p className="field-hint">{ready ? "Rendering diagram…" : "Diagram loads when you reach it."}</p>
      </div>
    );
  }

  return (
    <div
      ref={host}
      className={`mermaid-view ${className ?? ""}`}
      // eslint-disable-next-line @typescript-eslint/naming-convention -- React API
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
