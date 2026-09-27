"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Icon from "./Icon";

interface Props {
  /** Server-generated, validated Mermaid source (see backend/app/architecture/mermaid.py). */
  source: string;
  /** Stable id for this diagram instance — must be unique per rendered diagram on the page. */
  id: string;
  className?: string;
  /** Shown instead of the diagram if rendering fails (e.g. an unsupported browser). */
  fallback?: React.ReactNode;
}

const MIN_SCALE = 0.15;
const MAX_SCALE = 4;
const ZOOM_STEP = 1.35;
const PAN_STEP = 90;

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
  const viewport = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const view = useRef({ scale: 1, x: 0, y: 0 });
  const drag = useRef<{ pointerX: number; pointerY: number; startX: number; startY: number } | null>(null);

  const applyTransform = useCallback(() => {
    const el = stage.current;
    if (!el) return;
    const { scale, x, y } = view.current;
    el.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
  }, []);

  const zoomAtCenter = useCallback(
    (factor: number) => {
      const vp = viewport.current;
      if (!vp) return;
      const cx = vp.clientWidth / 2;
      const cy = vp.clientHeight / 2;
      const { scale, x, y } = view.current;
      const nextScale = Math.min(Math.max(scale * factor, MIN_SCALE), MAX_SCALE);
      const ratio = nextScale / scale;
      view.current = {
        scale: nextScale,
        x: cx - (cx - x) * ratio,
        y: cy - (cy - y) * ratio,
      };
      applyTransform();
    },
    [applyTransform]
  );

  const panBy = useCallback(
    (dx: number, dy: number) => {
      view.current = { ...view.current, x: view.current.x + dx, y: view.current.y + dy };
      applyTransform();
    },
    [applyTransform]
  );

  // Fits the diagram to the viewport's width (its full height pans into view
  // with the directional pad or a drag), the same layout GitHub's own
  // Mermaid viewer uses instead of shrinking everything to fit one box.
  const fitToView = useCallback(() => {
    const vp = viewport.current;
    const st = stage.current;
    const svgEl = st?.querySelector("svg");
    if (!vp || !st || !svgEl) return;
    st.style.transform = "translate(0px, 0px) scale(1)";
    const svgRect = svgEl.getBoundingClientRect();
    const vw = vp.clientWidth;
    if (svgRect.width === 0 || vw === 0) return;
    const scale = Math.min(Math.max(vw / svgRect.width, MIN_SCALE), MAX_SCALE);
    view.current = { scale, x: (vw - svgRect.width * scale) / 2, y: 12 };
    applyTransform();
  }, [applyTransform]);

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

  // Reset pan/zoom and fit the freshly rendered SVG into the viewport.
  useEffect(() => {
    if (!svg) return;
    view.current = { scale: 1, x: 0, y: 0 };
    const raf = requestAnimationFrame(fitToView);
    return () => cancelAnimationFrame(raf);
  }, [svg, fitToView]);

  // Dragging is the only way to pan besides the directional pad — there is no
  // scroll-wheel zoom, so hovering the diagram scrolls the page normally.
  function onPointerDown(e: React.PointerEvent) {
    if (e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = {
      pointerX: e.clientX,
      pointerY: e.clientY,
      startX: view.current.x,
      startY: view.current.y,
    };
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!drag.current) return;
    view.current.x = drag.current.startX + (e.clientX - drag.current.pointerX);
    view.current.y = drag.current.startY + (e.clientY - drag.current.pointerY);
    applyTransform();
  }

  function onPointerUp(e: React.PointerEvent) {
    drag.current = null;
    (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
  }

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
    <div ref={host} className={`mermaid-view ${className ?? ""}`}>
      <div
        ref={viewport}
        className="diagram-viewport"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <div
          ref={stage}
          className="diagram-stage"
          // eslint-disable-next-line @typescript-eslint/naming-convention -- React API
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
      <div className="diagram-controls">
        <div className="diagram-pad-panel">
          <button type="button" className="pad-btn pad-up" onClick={() => panBy(0, PAN_STEP)} aria-label="Pan up">
            <Icon name="chevron" style={{ transform: "rotate(-90deg)" }} />
          </button>
          <button type="button" className="pad-btn pad-left" onClick={() => panBy(PAN_STEP, 0)} aria-label="Pan left">
            <Icon name="chevron" style={{ transform: "rotate(180deg)" }} />
          </button>
          <button type="button" className="pad-btn pad-center" onClick={fitToView} aria-label="Recenter diagram">
            <Icon name="frame" />
          </button>
          <button type="button" className="pad-btn pad-right" onClick={() => panBy(-PAN_STEP, 0)} aria-label="Pan right">
            <Icon name="chevron" />
          </button>
          <button type="button" className="pad-btn pad-down" onClick={() => panBy(0, -PAN_STEP)} aria-label="Pan down">
            <Icon name="chevron" style={{ transform: "rotate(90deg)" }} />
          </button>
        </div>
        <div className="diagram-zoom-panel">
          <button type="button" className="pad-btn" onClick={() => zoomAtCenter(ZOOM_STEP)} aria-label="Zoom in">
            <Icon name="zoomIn" />
          </button>
          <button type="button" className="pad-btn" onClick={() => zoomAtCenter(1 / ZOOM_STEP)} aria-label="Zoom out">
            <Icon name="zoomOut" />
          </button>
        </div>
      </div>
    </div>
  );
}
