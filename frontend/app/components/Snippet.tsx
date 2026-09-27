"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import Icon from "./Icon";

interface Props {
  /** Shown in the header, e.g. a file path. Truncated from the left. */
  title: string;
  /** Extra header content before the copy button, e.g. +/− counts. */
  extra?: ReactNode;
  /** The text the Copy button puts on the clipboard. */
  copyText: string;
  /** Accessible name for the scrollable body. */
  label: string;
  style?: CSSProperties;
  children: ReactNode;
}

/**
 * Frame for code shown like an editor pane: header (icon, title, copy), a
 * body that scrolls both ways inside its own box, and a slim horizontal thumb
 * floating over the bottom of the code. Chrome's own horizontal bar is hidden
 * because it reserves a dark strip under the last line.
 */
export default function Snippet({ title, extra, copyText, label, style, children }: Props) {
  const [copied, setCopied] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const thumb = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const el = body.current;
    const bar = track.current;
    const knob = thumb.current;
    if (!el || !bar || !knob) return;
    const layout = () => {
      const range = el.scrollWidth - el.clientWidth;
      bar.hidden = range <= 1;
      if (range <= 1) return;
      const size = Math.max(32, (el.clientWidth / el.scrollWidth) * bar.clientWidth);
      knob.style.width = `${size}px`;
      knob.style.transform = `translateX(${(el.scrollLeft / range) * (bar.clientWidth - size)}px)`;
    };
    let drag: { x: number; left: number } | null = null;
    const down = (e: PointerEvent) => {
      drag = { x: e.clientX, left: el.scrollLeft };
      knob.setPointerCapture(e.pointerId);
      bar.classList.add("is-dragging");
      e.preventDefault();
    };
    const move = (e: PointerEvent) => {
      if (!drag) return;
      const travel = Math.max(bar.clientWidth - knob.offsetWidth, 1);
      el.scrollLeft = drag.left + ((e.clientX - drag.x) / travel) * (el.scrollWidth - el.clientWidth);
    };
    const up = () => {
      drag = null;
      bar.classList.remove("is-dragging");
    };
    const resize = new ResizeObserver(layout);
    resize.observe(el);
    if (el.firstElementChild) resize.observe(el.firstElementChild);
    el.addEventListener("scroll", layout, { passive: true });
    knob.addEventListener("pointerdown", down);
    knob.addEventListener("pointermove", move);
    knob.addEventListener("pointerup", up);
    knob.addEventListener("pointercancel", up);
    layout();
    return () => {
      resize.disconnect();
      el.removeEventListener("scroll", layout);
      knob.removeEventListener("pointerdown", down);
      knob.removeEventListener("pointermove", move);
      knob.removeEventListener("pointerup", up);
      knob.removeEventListener("pointercancel", up);
    };
  }, []);

  return (
    <figure className="code-snippet" style={style}>
      <figcaption className="code-snippet-head">
        <Icon name="code" aria-hidden="true" />
        <span className="code-snippet-title" title={title}>{title}</span>
        {extra}
        <button
          type="button"
          className="code-snippet-copy"
          onClick={() => {
            navigator.clipboard?.writeText(copyText).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }).catch(() => {});
          }}
        >
          <Icon name={copied ? "check" : "layers"} aria-hidden="true" />
          {copied ? "Copied" : "Copy"}
        </button>
      </figcaption>
      <div ref={body} className="code-snippet-body" tabIndex={0} aria-label={label}>
        {children}
      </div>
      <div ref={track} className="snippet-hbar" aria-hidden="true" hidden><span ref={thumb} /></div>
    </figure>
  );
}
