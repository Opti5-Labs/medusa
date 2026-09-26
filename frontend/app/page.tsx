"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import DemoButton from "./components/DemoButton";
import Icon, { type IconName } from "./components/Icon";
import { readRecentScans, type RecentScan } from "../lib/recentScans";

const SOURCE_TILE = {
  github: { icon: "github", tone: "" },
  zip: { icon: "folder", tone: "cyan" },
  demo: { icon: "cube", tone: "purple" },
} as const;

const HEADLINE = [["Clarity", "for", "your", "code."], ["Confidence", "in", "every", "fix."]];

// Real public repositories (the same examples the GitHub scan page offers).
const EXAMPLE_REPOS = ["pallets/itsdangerous", "pallets/markupsafe", "owner/repo"];
const STATIC_PLACEHOLDER = "github.com/owner/repo";

/**
 * Types example repositories into the placeholder while the field is idle.
 * Writes to the element directly so the page does not re-render per letter.
 */
function useTypedPlaceholder(input: RefObject<HTMLInputElement | null>, paused: boolean) {
  useEffect(() => {
    const setText = (text: string) => { if (input.current) input.current.placeholder = text; };
    if (paused || matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setText(STATIC_PLACEHOLDER);
      return;
    }
    let example = 0;
    let chars = 0;
    let deleting = false;
    let timer = 0;
    const tick = () => {
      const full = EXAMPLE_REPOS[example];
      if (!deleting && chars === full.length) {
        deleting = true;
        timer = window.setTimeout(tick, 2400);
        return;
      }
      if (deleting && chars === 0) {
        deleting = false;
        example = (example + 1) % EXAMPLE_REPOS.length;
      }
      chars += deleting ? -1 : 1;
      setText("github.com/" + full.slice(0, chars));
      timer = window.setTimeout(tick, deleting ? 30 : 70);
    };
    timer = window.setTimeout(tick, 1600);
    return () => window.clearTimeout(timer);
  }, [input, paused]);
}

export default function Home() {
  const router = useRouter();
  const [repository, setRepository] = useState("");
  const [recent, setRecent] = useState<RecentScan[]>([]);
  const [greeting, setGreeting] = useState("Welcome");
  const [focused, setFocused] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useTypedPlaceholder(input, focused || repository.length > 0);
  const latest = recent[0];

  useEffect(() => {
    const hour = new Date().getHours();
    setGreeting(hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening");
    const refresh = () => setRecent(readRecentScans());
    refresh();
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, []);

  return (
    <>
      <div className="hero-stage">
        <section className="dashboard-hero" aria-labelledby="welcome-heading">
          <div className="hero-content">
            <p className="eyebrow">{greeting}</p>
            <h1 id="welcome-heading">
              {HEADLINE.map((line, row) => (
                <span key={row} className="line">
                  {line.map((word, col) => (
                    <span key={word}>
                      <span className="word" style={{ "--i": row * 4 + col } as CSSProperties}>{word}</span>{col < line.length - 1 ? " " : ""}
                    </span>
                  ))}
                  {row === 0 ? " " : ""}
                </span>
              ))}
            </h1>
            <p className="hero-description">
              Medusa scans a repository for issues. IBM Bob and Granite then diagnose each one
              independently and propose fixes. On the OptiLearn demo, every fix is tested in a sandbox.
            </p>
            <form className="repository-launcher glass" onSubmit={(event) => {
              event.preventDefault();
              const value = repository.trim();
              router.push(value ? `/scan/github?repo=${encodeURIComponent(value)}` : "/scan/github");
            }}>
              <Icon name="search" />
              <label className="sr-only" htmlFor="dashboard-repository">GitHub repository URL</label>
              <input id="dashboard-repository" value={repository} onChange={(event) => setRepository(event.target.value)} ref={input} placeholder={STATIC_PLACEHOLDER} onFocus={() => setFocused(true)} onBlur={() => setFocused(false)} autoComplete="off" spellCheck={false} />
              <button className="launcher-submit" type="submit" aria-label="Scan this repository" disabled={!repository.trim()}><Icon name="arrow" /></button>
            </form>
            <p className="launcher-caption"><Icon name="shield" /> Public repositories only. Code is read as text and never run.</p>
          </div>
          <DemoButton />
        </section>

        <section className="quick-actions" aria-label="Start a scan">
          <ActionCard href="/scan/github" title="Link a repository" description="Scan public GitHub code" icon="github" tone="" />
          <ActionCard href="/scan/upload" title="Upload a zip" description="Scan an archive of your code" icon="upload" tone="cyan" />
          {latest && <ActionCard href={`/issues?scan=${encodeURIComponent(latest.id)}`} title="Continue" description={latest.name} icon="history" tone="magenta" />}
        </section>
      </div>

      <div className="dashboard-lower">
        <section className="panel glass" id="recent-scans" aria-labelledby="recent-heading">
          <div className="panel-heading">
            <div><h2 id="recent-heading">Recent scans</h2><p>Pick up where you left off.</p></div>
            {recent.length > 0 && <Link href="/recent" className="text-link">See all</Link>}
          </div>
          {recent.length ? (
            <div className="recent-list">
              {recent.map((scan) => (
                <Link key={scan.id} href={`/issues?scan=${encodeURIComponent(scan.id)}`} className="recent-row">
                  <span className={`tile ${SOURCE_TILE[scan.source].tone}`}><Icon name={SOURCE_TILE[scan.source].icon} /></span>
                  <span className="recent-name"><strong>{scan.name}</strong><small>{scan.issues} {scan.issues === 1 ? "issue" : "issues"} · {scan.files} files analysed</small></span>
                  <span className="recent-time">{new Date(scan.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                  <Icon name="chevron" />
                </Link>
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <span className="tile gray"><Icon name="folder" /></span>
              <h3>No scans yet</h3>
              <p>Scans you run in this tab appear here for 30 minutes.</p>
            </div>
          )}
        </section>

        <section className="panel glass" aria-labelledby="intelligence-heading">
          <div className="panel-heading">
            <div><h2 id="intelligence-heading">Two investigators</h2><p>Neither sees the other&apos;s diagnosis.</p></div>
          </div>
          <div className="engine-row"><span className="tile"><Icon name="code" /></span><div><strong>IBM Bob</strong><small>Reads the code, diagnoses, proposes fixes</small></div></div>
          <div className="engine-row"><span className="tile purple"><Icon name="spark" /></span><div><strong>Granite</strong><small>Scans for issues and diagnoses on its own</small></div></div>
          <div className="engine-note"><Icon name="check" /><span><strong>Models investigate. Tests decide.</strong>Sandbox testing is available on the OptiLearn demo.</span></div>
        </section>
      </div>
    </>
  );
}

function ActionCard({ href, title, description, icon, tone }: { href: string; title: string; description: string; icon: IconName; tone: string }) {
  return (
    <Link href={href} className="action-card glass pressable" data-holo="glare">
      <span className={`tile ${tone}`}><Icon name={icon} /></span>
      <span className="action-copy"><strong>{title}</strong><small>{description}</small></span>
      <span className="action-arrow"><Icon name="arrow" /></span>
    </Link>
  );
}
