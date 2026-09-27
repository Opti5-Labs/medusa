"use client";

import { useLayoutEffect, useRef, type ReactNode } from "react";

/**
 * A disclosure that opens itself when `openWhen` becomes true (a run starts streaming, a
 * candidate fails) but never closes itself: once content is on screen it stays until the
 * reader collapses it, so a phase finishing never pulls the page out from under them.
 */
export default function AutoOpenDetails({ openWhen, className = "disclosure", children }: { openWhen: boolean; className?: string; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useLayoutEffect(() => {
    if (openWhen && ref.current) ref.current.open = true;
  }, [openWhen]);
  return <details ref={ref} className={className}>{children}</details>;
}
