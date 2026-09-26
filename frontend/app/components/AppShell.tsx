"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import Lenis from "lenis";
import { Suspense, useEffect, useRef, useState } from "react";
import AskBar from "./AskBar";
import Icon, { type IconName } from "./Icon";

const navigation: { href: string; label: string; icon: IconName }[] = [
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
  return "Scan results";
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [solid, setSolid] = useState(false);
  const [dialogContent, setDialogContent] = useState<"guide" | "appearance">("guide");
  const dialog = useRef<HTMLDialogElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const smooth = useRef<Lenis | null>(null);
  const home = pathname === "/";

  useEffect(() => {
    try { setSolid(localStorage.getItem("medusa:solid-surfaces") === "true"); } catch { /* Storage can be disabled. */ }
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
      smooth.current = new Lenis({ ...inFrame, autoRaf: true, lerp: 0.07, wheelMultiplier: 0.9, anchors: true, allowNestedScroll: true, stopInertiaOnNavigate: true });
    };
    start();
    framed.addEventListener("change", start);
    return () => {
      framed.removeEventListener("change", start);
      smooth.current?.destroy();
      smooth.current = null;
    };
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
      el.classList.add("is-scrolling");
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

  function openDialog(content: "guide" | "appearance") {
    setDialogContent(content);
    dialog.current?.showModal();
  }

  function toggleSurface() {
    setSolid((previous) => {
      const next = !previous;
      try { localStorage.setItem("medusa:solid-surfaces", String(next)); } catch { /* Keep the preference for this visit. */ }
      return next;
    });
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
          <nav aria-label="Main navigation">
            {navigation.map(({ href, label, icon }) => (
              <Link key={label} href={href} title={label} className={`nav-link ${pathname === href ? "active" : ""}`} aria-current={pathname === href ? "page" : undefined}>
                <Icon name={icon} /><span>{label}</span>
              </Link>
            ))}
          </nav>
        </div>
        <div className="sidebar-bottom">
          <div className="sidebar-note"><Icon name="shield" /><div><strong>Nothing is kept</strong><p>Scans are deleted after 30 minutes.</p></div></div>
          <button className="nav-link" onClick={() => openDialog("appearance")} title="Appearance"><Icon name="settings" /><span>Appearance</span></button>
        </div>
      </aside>

      <div className="workspace-main" ref={scroller}>
        <div className="workspace-scroll">
          <div className="workspace-art" aria-hidden="true" />
          <header className="workspace-header">
            <div className="breadcrumb"><span>Workspace</span><Icon name="chevron" /><strong>{pageTitle(pathname)}</strong></div>
            <button className="icon-button" title="How Medusa works" aria-label="How Medusa works" onClick={() => openDialog("guide")}><Icon name="info" /></button>
          </header>
          <div className="ask-container"><Suspense fallback={null}><AskBar /></Suspense></div>
          <main id="main-content" tabIndex={-1} className={home ? "dashboard-main" : "route-main"}>{children}</main>
          <footer className="workspace-footer"><span>MEDUSA</span><span>Powered by IBM Bob and Granite on watsonx.ai</span></footer>
        </div>
      </div>

      <dialog ref={dialog} className="workspace-dialog" onClick={(event) => { if (event.target === event.currentTarget) dialog.current?.close(); }} aria-labelledby="dialog-title">
        <div className="dialog-heading">
          <span className={`tile ${dialogContent === "guide" ? "" : "gray"}`}><Icon name={dialogContent === "guide" ? "spark" : "settings"} /></span>
          <button className="icon-button" aria-label="Close" onClick={() => dialog.current?.close()}><Icon name="close" /></button>
        </div>
        <h2 id="dialog-title">{dialogContent === "guide" ? "How Medusa works" : "Appearance"}</h2>
        {dialogContent === "guide" ? (
          <>
            <p>Link a public repository or upload a zip. Medusa reads the code, lists issues, and lets you investigate each one with IBM Bob and Granite.</p>
            <ol className="guide-steps">
              <li><div><strong>Scan</strong><p>Find issues and ask questions grounded in the code.</p></div></li>
              <li><div><strong>Investigate</strong><p>Bob and Granite diagnose independently and each propose fixes.</p></div></li>
              <li><div><strong>Verify</strong><p>On the OptiLearn demo, fixes race through tests in a sandbox. Other repositories are read as text, so their fixes are not tested.</p></div></li>
            </ol>
          </>
        ) : (
          <>
            <p>Medusa uses translucent surfaces over a dark workspace.</p>
            <div className="appearance-row">
              <div><strong>Reduce transparency</strong><p>Use solid surfaces for stronger contrast.</p></div>
              <button role="switch" aria-checked={solid} aria-label="Reduce transparency" className={`toggle ${solid ? "on" : ""}`} onClick={toggleSurface}><span /></button>
            </div>
            <p className="dialog-footnote">Motion, transparency and contrast also follow your system settings.</p>
          </>
        )}
      </dialog>
    </div>
  );
}
