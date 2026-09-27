"use client";

import Link from "next/link";
import { useEffect, useState, type CSSProperties } from "react";
import { expiresAt, readRecentScans } from "../../lib/recentScans";
import Icon from "./Icon";

function clock(ms: number): string {
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/**
 * "Nothing is kept", shown instead of told: an iridescent ring around a
 * shield drains in real time toward the next deletion, beside how many scans
 * are stored and when the next one goes. With several scans it always tracks
 * the one deleted soonest, since that is what happens next; when it goes, the
 * ring picks up the following one. Empty and dotted when nothing is stored.
 * Uses the real expiry of the scans in this tab and links to Recent scans,
 * where every stored scan and its timer are listed. No scan names here: they
 * can be long, and the list on Recent scans has them.
 */
export default function RetentionTimer() {
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  // Read on the client only; the first server render shows the empty state.
  const lefts =
    now === null
      ? []
      : readRecentScans()
          .map((scan) => ({ left: expiresAt(scan) - now, total: expiresAt(scan) - scan.createdAt }))
          .filter((s) => s.left > 0)
          .sort((a, b) => a.left - b.left);
  const count = lefts.length;
  const next = lefts[0];
  const last = lefts[count - 1];
  const active = count > 0;

  const summary = !active
    ? "Nothing is kept: every scan and its results are deleted from the server 30 minutes after it runs."
    : count === 1
      ? `Nothing is kept: your scan and its results are deleted from the server in ${clock(next.left)}, 30 minutes after it ran.`
      : `Nothing is kept: ${count} scans are stored. The next is deleted in ${clock(next.left)}, the last in ${clock(last.left)}. Open Recent scans to see each one.`;

  return (
    <Link
      href="/recent"
      className={`retention ${active ? "is-active" : ""} ${active && next.left < 5 * 60000 ? "is-ending" : ""}`}
      style={{ "--p": active ? next.left / next.total : 0 } as CSSProperties}
      title={summary}
      aria-label={summary}
    >
      <span className="retention-ring" aria-hidden="true"><Icon name="shield" /></span>
      <span className="retention-text" aria-hidden="true">
        <strong>Nothing is kept</strong>
        {active ? (
          <>
            <span>{count === 1 ? "1 scan stored" : `${count} scans stored`}</span>
            <span className="retention-when">
              {count === 1 ? "Deleted in " : "Next deleted in "}
              <time>{clock(next.left)}</time>
            </span>
          </>
        ) : (
          <span>Deleted after 30 min</span>
        )}
      </span>
    </Link>
  );
}
