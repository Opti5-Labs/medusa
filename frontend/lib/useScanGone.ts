"use client";

import { useEffect, useState } from "react";
import { pruneScans, type GoneReason } from "./recentScans";

/**
 * Whether the server has lost this scan (restart or expiry). Checked on mount
 * and whenever the tab regains focus; a lost scan is forgotten in this tab.
 */
export function useScanGone(scanId: string | null): GoneReason | null {
  const [gone, setGone] = useState<GoneReason | null>(null);
  useEffect(() => {
    if (!scanId) return;
    let active = true;
    const check = () => {
      void pruneScans([scanId]).then((result) => {
        if (active && result[scanId]) setGone(result[scanId]);
      });
    };
    check();
    window.addEventListener("focus", check);
    return () => {
      active = false;
      window.removeEventListener("focus", check);
    };
  }, [scanId]);
  return gone;
}
