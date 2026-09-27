"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Outfit } from "next/font/google";
import ThinkingOrb from "./components/ThinkingOrb";

const display = Outfit({ subsets: ["latin"], weight: "600", display: "swap" });

/**
 * 404, in Medusa's terms: whatever she looks at turns to stone. The number is
 * carved from the app's chrome artwork drained to grey, a crack draws across it
 * once, and the emblem above it is petrified too.
 */
export default function NotFound() {
  const pathname = usePathname();
  return (
    <div className="not-found">
      <div className="not-found-emblem" aria-hidden="true"><ThinkingOrb size="md" /></div>
      <div className={`not-found-stone ${display.className}`} aria-hidden="true">
        <span>404</span>
        <svg viewBox="0 0 300 120" preserveAspectRatio="none" className="not-found-crack">
          <path d="M18 38 L64 52 L92 44 L121 70 L150 58 L176 81 L205 66 L238 88 L282 74" />
          <path d="M121 70 L128 96 M205 66 L214 40" />
        </svg>
      </div>
      <h2 className="title-1">This page turned to stone.</h2>
      <p>
        Medusa looked at <code>{pathname}</code> and nothing moved. The link may be incomplete, or the page was
        never here.
      </p>
      <div className="actions">
        <Link href="/" className="btn btn-primary btn-lg">Back to Overview</Link>
        <Link href="/recent" className="btn btn-secondary btn-lg">Recent scans</Link>
      </div>
    </div>
  );
}
