"use client";

import { useEffect, useRef } from "react";

/**
 * The turning holographic rim of a .holo-rim card. Even a compositor animation costs the
 * main thread a little every frame, so the light only turns while the card is on screen
 * (and, in CSS, pauses while the page scrolls).
 */
export default function HoloRing() {
  const ring = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const el = ring.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => el.classList.toggle("is-visible", entry.isIntersecting));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return <span ref={ring} className="holo-ring" aria-hidden="true" />;
}
