"use client";

import Link from "next/link";
import { Outfit } from "next/font/google";
import { usePathname } from "next/navigation";
import Lenis from "lenis";
import { Suspense, useEffect, useRef, useState, type CSSProperties, type MouseEvent } from "react";
import { flushSync } from "react-dom";
import { useSheet } from "../../lib/useSheet";
import AskBar from "./AskBar";
import Icon, { type IconName } from "./Icon";
import RetentionTimer from "./RetentionTimer";
import ThinkingOrb from "./ThinkingOrb";

// Geometric display face for the footer's oversized wordmark only; self-hosted by next/font.
const display = Outfit({ subsets: ["latin"], weight: "600", display: "swap" });

const navigation:{ href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Overview", icon: "home" },
  { href: "/scan/github", label: "GitHub repo", icon: "github" },
  { href: "/scan/upload", label: "Upload zip", icon: "upload" },
  { href: "/recent", label: "Recent scans", icon: "history" },
];

function pageTitle(pathname: string): string {
  if (pathname === "/") return "Overview";
  if (pathname.startsWith("/scan/github")) return "GitHub repo";
  if (pathname.startsWith("/scan/upload")) return "Upload zip";
  if (pathname.startsWith("/investigate")) return "Investigation";
  if (pathname.startsWith("/recent")) return "Recent scans";
  if (pathname.startsWith("/architecture")) return "Architecture";
  if (pathname.startsWith("/issues")) return "Scan results";
  return "Not found";
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [solid, setSolid] = useState(false);
  const [light, setLight] = useState(false);
  const [dialogContent, setDialogContent] = useState<"guide" | "appearance">("guide");
  const dialog = useRef<HTMLDialogElement>(null);
  const sheet = useSheet(dialog);
  const scroller = useRef<HTMLDivElement>(null);
  const smooth = useRef<Lenis | null>(null);
  const scrollTrack = useRef<HTMLDivElement>(null);
  const giant = useRef<HTMLDivElement>(null);
  const home = pathname === "/";

  useEffect(() => {
    try { setSolid(localStorage.getItem("medusa:solid-surfaces") === "true"); } catch { /* Storage can be disabled. */ }
  }, []);

  useEffect(() => {
    const sync = () => {
      const enabled = document.documentElement.classList.contains("light");
      setLight(enabled);
      document.querySelector('meta[name="theme-color"]')?.setAttribute("content", enabled ? "#fafafb" : "#0b0b0c");
    };
    sync();
    const onStorage = (event: StorageEvent) => {
      if (event.key !== "medusa:theme" && event.key !== null) return;
      const enabled = event.newValue === "light";
      document.documentElement.classList.toggle("light", enabled);
      document.documentElement.classList.toggle("dark", !enabled);
      sync();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // Inertial scrolling: the wheel sets a target and the content glides to it.
  // From 740px the window frame stays put and only its content column scrolls;
  // on phones the page itself scrolls. Touch stays native; off for reduced motion.
  useEffect(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const framed = matchMedia("(min-width: 740px)");
    const start = () => {
      smooth.current?.destroy();
      const el = scroller.current;
      const inFrame = framed.matches && el?.firstElementChild ? { wrapper: el, content: el.firstElementChild, eventsTarget: el } : {};
      smooth.current = new Lenis({ ...inFrame, autoRaf: true, lerp: 0.13, wheelMultiplier: 1, anchors: true, allowNestedScroll: true, stopInertiaOnNavigate: true });
    };
    start();
    framed.addEventListener("change", start);
    return () => {
      framed.removeEventListener("change", start);
      smooth.current?.destroy();
      smooth.current = null;
    };
  }, []);

  // The footer emblem is hidden until the lockup scrolls into view, then its arms
  // spiral in; scrolling away unwinds them again. The gap between the two
  // thresholds keeps it from flickering at the boundary. The class is set directly,
  // not through state, so revealing it mid-scroll doesn't re-render the whole shell.
  useEffect(() => {
    const el = giant.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.intersectionRatio >= 0.4) el.classList.add("is-in");
      else if (entry.intersectionRatio < 0.1) el.classList.remove("is-in");
    }, { threshold: [0, 0.1, 0.4] });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // A new page starts at the top of the content column (anchor links excepted).
  useEffect(() => {
    if (window.location.hash) return;
    if (smooth.current) smooth.current.scrollTo(0, { immediate: true });
    else scroller.current?.scrollTo(0, 0);
  }, [pathname]);

  // Scrollbars show while an element scrolls and fade out shortly after (see globals.css).
  useEffect(() => {
    const timers = new Map<Element, number>();
    const onScroll = (event: Event) => {
      const el = event.target === document ? document.documentElement : event.target;
      if (!(el instanceof Element)) return;
      if (!timers.has(el)) el.classList.add("is-scrolling");
      window.clearTimeout(timers.get(el));
      timers.set(el, window.setTimeout(() => {
        el.classList.remove("is-scrolling");
        timers.delete(el);
      }, 900));
    };
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    return () => {
      document.removeEventListener("scroll", onScroll, { capture: true });
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, []);

  // The content column's scrollbar floats over the content (Chrome's own always
  // takes width, which left a strip by the window corner and made the page
  // narrower whenever it scrolled). The thumb follows the scroll position and
  // can be dragged; it fades like the other scrollbars.
  useEffect(() => {
    const el = scroller.current;
    const track = scrollTrack.current;
    const thumb = track?.firstElementChild as HTMLElement | null;
    if (!el || !track || !thumb) return;
    let frame = 0;
    const layout = () => {
      frame = 0;
      const range = el.scrollHeight - el.clientHeight;
      track.hidden = range <= 1;
      if (range <= 1) return;
      const trackHeight = track.clientHeight;
      const size = Math.max(40, (el.clientHeight / el.scrollHeight) * trackHeight);
      thumb.style.height = `${size}px`;
      thumb.style.transform = `translateY(${(el.scrollTop / range) * (trackHeight - size)}px)`;
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(layout); };
    const resize = new ResizeObserver(schedule);
    resize.observe(el);
    if (el.firstElementChild) resize.observe(el.firstElementChild);
    el.addEventListener("scroll", schedule, { passive: true });

    let drag: { y: number; top: number } | null = null;
    const down = (event: PointerEvent) => {
      drag = { y: event.clientY, top: el.scrollTop };
      thumb.setPointerCapture(event.pointerId);
      track.classList.add("is-dragging");
      event.preventDefault();
    };
    const move = (event: PointerEvent) => {
      if (!drag) return;
      const range = el.scrollHeight - el.clientHeight;
      const travel = track.clientHeight - thumb.offsetHeight;
      const top = drag.top + ((event.clientY - drag.y) / Math.max(travel, 1)) * range;
      if (smooth.current) smooth.current.scrollTo(top, { immediate: true });
      else el.scrollTop = top;
    };
    const up = () => { drag = null; track.classList.remove("is-dragging"); };
    thumb.addEventListener("pointerdown", down);
    thumb.addEventListener("pointermove", move);
    thumb.addEventListener("pointerup", up);
    thumb.addEventListener("pointercancel", up);
    layout();
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      el.removeEventListener("scroll", schedule);
      thumb.removeEventListener("pointerdown", down);
      thumb.removeEventListener("pointermove", move);
      thumb.removeEventListener("pointerup", up);
      thumb.removeEventListener("pointercancel", up);
    };
  }, []);

  // Holographic cards: [data-holo] elements get a pointer-tracked glare. The
  // card itself never moves. Mouse only, and off when the user prefers
  // reduced motion.
  useEffect(() => {
    if (!matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let target: HTMLElement | null = null;
    let point: { x: number; y: number } | null = null;
    let frame = 0;

    const release = (el: HTMLElement | null) => {
      if (!el) return;
      el.classList.remove("holo-active");
    };
    const apply = () => {
      frame = 0;
      if (!target || !point) return;
      const box = target.getBoundingClientRect();
      target.style.setProperty("--hx", `${Math.round(point.x - box.left)}px`);
      target.style.setProperty("--hy", `${Math.round(point.y - box.top)}px`);
    };
    const move = (event: PointerEvent) => {
      const el = (event.target as Element | null)?.closest<HTMLElement>("[data-holo]") ?? null;
      if (el !== target) {
        release(target);
        target = el;
        target?.classList.add("holo-active");
      }
      point = { x: event.clientX, y: event.clientY };
      if (target && !frame) frame = requestAnimationFrame(apply);
    };
    const leave = () => { release(target); target = null; };

    document.addEventListener("pointermove", move, { passive: true });
    document.documentElement.addEventListener("pointerleave", leave);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("pointermove", move);
      document.documentElement.removeEventListener("pointerleave", leave);
      release(target);
    };
  }, []);

  // The system accessibility settings, shown read-only in Appearance.
  const [system, setSystem] = useState({ motion: false, transparency: false, contrast: false });
  useEffect(() => {
    const queries = {
      motion: matchMedia("(prefers-reduced-motion: reduce)"),
      transparency: matchMedia("(prefers-reduced-transparency: reduce)"),
      contrast: matchMedia("(prefers-contrast: more)"),
    };
    const read = () => setSystem({ motion: queries.motion.matches, transparency: queries.transparency.matches, contrast: queries.contrast.matches });
    read();
    Object.values(queries).forEach((q) => q.addEventListener("change", read));
    return () => Object.values(queries).forEach((q) => q.removeEventListener("change", read));
  }, []);

  function openDialog(content: "guide" | "appearance", event: MouseEvent<HTMLElement>) {
    // Render the content first, so the sheet measures its real height before it moves.
    flushSync(() => setDialogContent(content));
    sheet.open(event.currentTarget);
  }

  function toggleSurface() {
    setSolid((previous) => {
      const next = !previous;
      try { localStorage.setItem("medusa:solid-surfaces", String(next)); } catch { /* Keep the preference for this visit. */ }
      return next;
    });
  }

  function toggleTheme() {
    const enabled = !light;
    document.documentElement.classList.toggle("light", enabled);
    document.documentElement.classList.toggle("dark", !enabled);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", enabled ? "#fafafb" : "#0b0b0c");
    setLight(enabled);
    try { localStorage.setItem("medusa:theme", enabled ? "light" : "dark"); } catch { /* Keep the preference for this visit. */ }
  }

  return (
    <div className={`workspace ${home ? "" : "route"} ${solid ? "solid-surfaces" : ""}`}>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside className="sidebar" aria-label="Workspace navigation">
        <Link href="/" className="brand" aria-label="Medusa home">
          <img src="/images/medusa-mark.png" width="36" height="36" alt="" />
          <span className="wordmark" aria-hidden="true"><strong>MEDUSA</strong><small>INTELLIGENCE IN HARMONY</small></span>
        </Link>
        <div className="sidebar-group">
          <div className="sidebar-section">Workspace</div>
          {/* --tab drives the phone tab bar's sliding selection pill (-1: no tab is current). */}
          <nav aria-label="Main navigation" style={{ "--tab": navigation.findIndex((n) => n.href === pathname) } as CSSProperties}>
            {navigation.map(({ href, label, icon }) => (
              <Link key={label} href={href} title={label} className={`nav-link ${pathname === href ? "active" : ""}`} aria-current={pathname === href ? "page" : undefined}>
                <Icon name={icon} /><span>{label}</span>
              </Link>
            ))}
          </nav>
        </div>
        <div className="sidebar-bottom">
          <RetentionTimer />
          <button className="nav-link" onClick={(e) => openDialog("appearance", e)} title="Appearance"><Icon name="appearance" /><span>Appearance</span></button>
          {/* Phones: the two global actions share one glass capsule in the top bar (iOS toolbar grouping). */}
          <div className="bar-group">
            <button type="button" aria-label="How Medusa works" onClick={(e) => openDialog("guide", e)}><Icon name="info" /></button>
            <button type="button" aria-label="Appearance" onClick={(e) => openDialog("appearance", e)}><Icon name="appearance" /></button>
          </div>
        </div>
      </aside>

      <div className="workspace-main" ref={scroller}>
        <div className="workspace-scroll">
          <div className="workspace-art" aria-hidden="true" />
          <header className="workspace-header">
            <div className="breadcrumb"><span>Workspace</span><Icon name="chevron" /><strong>{pageTitle(pathname)}</strong></div>
            <button className="icon-button" title="How Medusa works" aria-label="How Medusa works" onClick={(e) => openDialog("guide", e)}><Icon name="info" /></button>
          </header>
          <div className="ask-container"><Suspense fallback={null}><AskBar /></Suspense></div>
          <main id="main-content" tabIndex={-1} className={home ? "dashboard-main" : "route-main"}>{children}</main>
          <footer className="workspace-footer">
            <div className="footer-inner">
              <div className="footer-top">
                <p>Built for the IBM Bob 2.0 Hackathon <span aria-hidden="true">·</span> lablab.ai, Sep 2026</p>
                <p className="footer-credits">
                  Powered by
                  <span><Icon name="bob" />IBM Bob</span>
                  <span aria-hidden="true">&amp;</span>
                  <span><Icon name="granite" />Granite on watsonx.ai</span>
                </p>
              </div>
              <div ref={giant} className={`footer-giant ${display.className}`} aria-hidden="true">
                <ThinkingOrb />
                <span className="footer-giant-text">medusa</span>
              </div>
            </div>
          </footer>
        </div>
      </div>
      <div ref={scrollTrack} className="scroll-track" aria-hidden="true" hidden><span className="scroll-thumb" /></div>

      {/* Opening, closing, dragging and dismissing (Escape, outside click) live in useSheet. */}
      <dialog ref={dialog} className="workspace-dialog" aria-labelledby="dialog-title">
        {dialogContent === "guide" ? (
          <>
            {/* A "What's New" style welcome sheet: title, three features with tinted symbols,
                and one button. Like Apple's, it has no close button: Continue is the way out. */}
            <div className="guide-hero" data-sheet-handle>
              <h2 id="dialog-title">How Medusa works</h2>
              <p>From a repository to a tested fix, with nothing left behind.</p>
            </div>
            <ol className="guide-features">
              <li style={{ "--i": 0 } as CSSProperties}>
                <span className="guide-symbol is-cyan"><Icon name="search" /></span>
                <div><strong>Scan</strong><p>Link a public repository or upload a zip. Medusa lists the issues; ask anything about the code.</p></div>
              </li>
              <li style={{ "--i": 1 } as CSSProperties}>
                <span className="guide-symbol is-purple"><Icon name="spark" /></span>
                <div><strong>Investigate</strong><p>IBM Bob and Granite each diagnose an issue independently and propose fixes.</p></div>
              </li>
              <li style={{ "--i": 2 } as CSSProperties}>
                <span className="guide-symbol is-green"><Icon name="shield" /></span>
                <div><strong>Verify</strong><p>On the OptiLearn demo, every fix runs the tests in a sandbox; the one that passes is recommended.</p></div>
              </li>
              <li style={{ "--i": 3 } as CSSProperties}>
                <span className="guide-symbol is-amber"><Icon name="history" /></span>
                <div><strong>Forget</strong><p>Nothing is kept. Uploads, scans and their results are deleted from our servers 30 minutes after they run.</p></div>
              </li>
            </ol>
            <button className="btn btn-primary btn-lg guide-done" onClick={sheet.close}>Continue</button>
          </>
        ) : (
          <>
            {/* A settings sheet: grouped sections with a header and footer, dismissed from the toolbar. */}
            <div className="sheet-bar" data-sheet-handle>
              <button className="icon-button sheet-close" aria-label="Close" onClick={sheet.close}><Icon name="close" /></button>
              <h2 id="dialog-title" className="sheet-title">Appearance</h2>
            </div>
            <div className="settings-group">
              <div className="settings-row">
                <span className="tile tile-sm"><Icon name="appearance" /></span>
                <div className="settings-row-text"><strong id="light-label">Light mode</strong></div>
                <button type="button" role="switch" aria-checked={light} aria-labelledby="light-label" aria-describedby="light-note" className={`toggle ${light ? "on" : ""}`} onClick={toggleTheme}><span /></button>
              </div>
            </div>
            <p id="light-note" className="settings-footer theme-note">Pearl white, frosted glass, and soft iridescent accents.</p>
            <div className="settings-group">
              <div className="settings-row">
                <span className="tile tile-sm"><Icon name="layers" /></span>
                <div className="settings-row-text"><strong id="solid-label">Solid surfaces</strong></div>
                {/* With the device's Reduce Transparency on, surfaces are already solid: the
                    switch shows that and is locked, as iOS shows a setting managed elsewhere. */}
                <button role="switch" aria-checked={solid || system.transparency} aria-labelledby="solid-label" aria-describedby="solid-note" disabled={system.transparency} className={`toggle ${solid || system.transparency ? "on" : ""}`} onClick={toggleSurface}><span /></button>
              </div>
            </div>
            <p id="solid-note" className="settings-footer">{system.transparency ? "On while Reduce Transparency is on in your device settings." : "Opaque surfaces instead of glass, for stronger contrast."}</p>
            <p className="settings-caption">Follows your system</p>
            <div className="settings-group">
              {([
                ["motion", "Reduce motion", system.motion],
                ["layers", "Reduce transparency", system.transparency],
                ["contrast", "Increase contrast", system.contrast],
              ] as const).map(([icon, label, on]) => (
                <div key={label} className="settings-row">
                  <span className="tile tile-sm gray"><Icon name={icon} /></span>
                  <div className="settings-row-text"><strong>{label}</strong></div>
                  <span className={`settings-value ${on ? "is-on" : ""}`}>{on ? "On" : "Off"}</span>
                </div>
              ))}
            </div>
            <p className="settings-footer">Change these in your device settings; Medusa updates straight away.</p>
          </>
        )}
      </dialog>
    </div>
  );
}
