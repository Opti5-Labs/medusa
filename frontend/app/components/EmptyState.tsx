import Link from "next/link";
import type { ReactNode } from "react";
import Icon, { type IconName } from "./Icon";

interface Props {
  icon: IconName;
  title: string;
  children: ReactNode;
  /** Actions, most useful first; the first is the primary button. */
  actions?: { href: string; label: string }[];
}

/**
 * One way to say "there is nothing here": a tile, a title, one plain sentence
 * about why, and where to go next. Every dead end in the app uses it.
 */
export default function EmptyState({ icon, title, children, actions = [] }: Props) {
  return (
    <div className="empty-state-page">
      <span className="tile gray"><Icon name={icon} /></span>
      <h2 className="title-2">{title}</h2>
      <p>{children}</p>
      {actions.length > 0 && (
        <div className="actions">
          {actions.map((a, i) => (
            <Link key={a.href} href={a.href} className={`btn ${i === 0 ? "btn-primary" : "btn-secondary"}`}>
              {a.label}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * A scan or issue link that no longer resolves: scans live 30 minutes, and
 * only in the server's memory, so a restart loses them sooner.
 */
export function ExpiredScan({ what = "scan", reason }: { what?: "scan" | "issue"; reason?: "restarted" | "expired" }) {
  if (reason === "restarted") {
    return (
      <EmptyState
        icon="history"
        title="The server restarted since this scan"
        actions={[
          { href: "/", label: "Start a new scan" },
          { href: "/recent", label: "Recent scans" },
        ]}
      >
        Medusa keeps scans and their results in memory only, and the server restarted after this scan ran, so its
        results are gone. Run the scan again to pick up where you left off.
      </EmptyState>
    );
  }
  return (
    <EmptyState
      icon="history"
      title={what === "issue" ? "This issue is no longer here" : "This scan has expired"}
      actions={[
        { href: "/", label: "Start a new scan" },
        { href: "/recent", label: "Recent scans" },
      ]}
    >
      Scans and their results are deleted 30 minutes after they run, so this link has nothing left to open. Run the
      scan again to pick up where you left off.
    </EmptyState>
  );
}

/** Waiting for session data on the client; a spinner rather than bare text. */
export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading-state" role="status">
      <span className="holo-spinner" aria-hidden="true" />
      {label}
    </div>
  );
}
