"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Icon from "../components/Icon";
import { clearRecentScans, expiresAt, readRecentScans, type RecentScan } from "../../lib/recentScans";
import EmptyState, { LoadingState } from "../components/EmptyState";

const SOURCE = {
  github: { icon: "github", tone: "", label: "GitHub repository" },
  zip: { icon: "folder", tone: "cyan", label: "Uploaded zip" },
  demo: { icon: "cube", tone: "purple", label: "Demo" },
} as const;

function minutesLeft(scan: RecentScan, now: number): number {
  return Math.max(0, Math.ceil((expiresAt(scan) - now) / 60_000));
}

export default function RecentScansPage() {
  const [scans, setScans] = useState<RecentScan[] | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Re-read every half minute so expired scans drop off and the countdown stays true.
  useEffect(() => {
    const refresh = () => {
      setScans(readRecentScans());
      setNow(Date.now());
    };
    refresh();
    const timer = window.setInterval(refresh, 30_000);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, []);

  if (scans === null) return <LoadingState />;

  return (
    <div className="page">
      <header className="page-header">
        <div className="page-header-row">
          <h2 className="title-1">Recent scans</h2>
          {scans.length > 0 && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                clearRecentScans();
                setScans([]);
              }}
            >
              Clear history
            </button>
          )}
        </div>
        <p className="page-lede">
          Scans from this browser tab. Each is deleted, here and on the server, 30 minutes after it runs.
        </p>
      </header>

      {scans.length === 0 ? (
        <EmptyState
          icon="history"
          title="No recent scans"
          actions={[
            { href: "/scan/github", label: "Link a repository" },
            { href: "/scan/upload", label: "Upload a zip" },
          ]}
        >
          Link a repository, upload a zip, or run the demo. Your scans appear here for 30 minutes.
        </EmptyState>
      ) : (
        <section className="section" aria-labelledby="recent-count">
          <div className="section-head">
            <h3 id="recent-count" className="headline">
              {scans.length} {scans.length === 1 ? "scan" : "scans"}
            </h3>
          </div>
          <div className="card recent-card">
            <div className="recent-list">
              {scans.map((scan) => {
                const source = SOURCE[scan.source];
                const left = minutesLeft(scan, now);
                return (
                  <Link key={scan.id} href={`/issues?scan=${encodeURIComponent(scan.id)}`} className="recent-row">
                    <span className={`tile ${source.tone}`}><Icon name={source.icon} /></span>
                    <span className="recent-name">
                      <strong>{scan.name}</strong>
                      <small>
                        {source.label} · {scan.issues} {scan.issues === 1 ? "issue" : "issues"} · {scan.files} files analysed
                      </small>
                      {/* Phones: the expiry moves here and the right-hand column is hidden. */}
                      <small className={`recent-expiry-inline ${left <= 5 ? "is-soon" : ""}`}>
                        {left <= 1 ? "Expires in a minute" : `Expires in ${left} min`}
                      </small>
                    </span>
                    <span className="recent-when">
                      <span className="recent-time">
                        {new Date(scan.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </span>
                      <span className={`recent-expiry ${left <= 5 ? "is-soon" : ""}`}>
                        {left <= 1 ? "Expires in a minute" : `Expires in ${left} min`}
                      </span>
                    </span>
                    <Icon name="chevron" />
                  </Link>
                );
              })}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
