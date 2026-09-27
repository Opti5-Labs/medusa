"use client";

import { useEffect, useRef, type RefObject } from "react";

// Spring-driven presentation for a modal <dialog>, after iOS sheets:
// - "slide" (phones, and touch tablets as form sheets): rises from the bottom edge,
//   follows the finger 1:1 when dragged down, rubber-bands when pulled up, and on
//   release projects the flick to decide between dismissing and settling back.
// - "zoom" (mouse and trackpad): scales up from the control that opened it and fades.
// - "fade" (Reduce Motion): a cross-fade only.
// Every animation starts from the sheet's current value and velocity, so it can be
// caught and reversed at any moment.

type Mode = "slide" | "zoom" | "fade";
type Spring = { response: number; damping: number };

const OPEN: Spring = { response: 0.42, damping: 1 };
const SETTLE: Spring = { response: 0.36, damping: 0.86 }; // after a drag, so a little give
const DISMISS: Spring = { response: 0.3, damping: 1 };
const ZOOM_CLOSE: Spring = { response: 0.24, damping: 1 };

// Apple's momentum projection (Designing Fluid Interfaces, WWDC 2018).
function project(velocity: number, rate = 0.998) {
  return ((velocity / 1000) * rate) / (1 - rate);
}

function rubberband(overshoot: number, dimension: number, constant = 0.55) {
  return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));
}

function currentMode(): Mode {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return "fade";
  return matchMedia("(max-width: 739px), (pointer: coarse)").matches ? "slide" : "zoom";
}

export function useSheet(ref: RefObject<HTMLDialogElement | null>) {
  const st = useRef({
    mode: "zoom" as Mode,
    open: false,
    value: 1, // slide: px below the resting position; zoom and fade: 0 open, 1 closed
    velocity: 0,
    distance: 1, // slide: px that take the sheet fully off screen
    frame: 0,
    trigger: null as Element | null,
  });

  const closed = () => (st.current.mode === "slide" ? st.current.distance : 1);

  function render() {
    const d = ref.current;
    const s = st.current;
    if (!d) return;
    let scrim: number;
    if (s.mode === "slide") {
      d.style.transform = `translate3d(0, ${s.value}px, 0)`;
      d.style.opacity = "";
      scrim = 1 - Math.min(Math.max(s.value / s.distance, 0), 1);
    } else {
      const q = Math.min(Math.max(s.value, 0), 1);
      d.style.transform = s.mode === "zoom" ? `scale(${1 - 0.06 * s.value})` : "";
      d.style.opacity = String(1 - q);
      scrim = 1 - q;
    }
    d.style.setProperty("--scrim", scrim.toFixed(3));
  }

  // Reads the resting layout: how far the sheet travels, where it zooms from, and
  // whether its content scrolls (then only its handle area starts a drag).
  function measure() {
    const d = ref.current;
    const s = st.current;
    if (!d) return;
    s.mode = currentMode();
    d.style.transform = "none";
    const rect = d.getBoundingClientRect();
    s.distance = Math.max(innerHeight - rect.top, 1);
    if (s.trigger && s.mode === "zoom") {
      const t = s.trigger.getBoundingClientRect();
      d.style.transformOrigin = `${t.left + t.width / 2 - rect.left}px ${t.top + t.height / 2 - rect.top}px`;
    } else {
      d.style.transformOrigin = "";
    }
    d.classList.toggle("is-slide", s.mode === "slide");
    d.classList.toggle("is-scrollable", d.scrollHeight > d.clientHeight + 1);
  }

  function stop() {
    cancelAnimationFrame(st.current.frame);
    st.current.frame = 0;
  }

  function animate(target: number, { response, damping }: Spring, velocity: number, done?: () => void, tolerance?: number) {
    const s = st.current;
    stop();
    s.velocity = velocity;
    const stiffness = (2 * Math.PI / response) ** 2;
    const friction = (4 * Math.PI * damping) / response;
    const eps = tolerance ?? (s.mode === "slide" ? 0.5 : 0.001);
    let last = performance.now();
    const step = (now: number) => {
      const dt = Math.min(Math.max((now - last) / 1000, 0), 1 / 30);
      last = now;
      const n = Math.max(1, Math.ceil(dt * 240));
      for (let i = 0; i < n; i++) {
        const accel = -stiffness * (s.value - target) - friction * s.velocity;
        s.velocity += (accel * dt) / n;
        s.value += (s.velocity * dt) / n;
      }
      if (Math.abs(s.value - target) < eps && Math.abs(s.velocity) < eps * 20) {
        s.value = target;
        s.velocity = 0;
        s.frame = 0;
        render();
        done?.();
        return;
      }
      render();
      s.frame = requestAnimationFrame(step);
    };
    s.frame = requestAnimationFrame(step);
  }

  function reset() {
    const d = ref.current;
    if (!d) return;
    d.style.transform = "";
    d.style.opacity = "";
    d.style.transformOrigin = "";
    d.style.removeProperty("--scrim");
  }

  function finish() {
    const d = ref.current;
    reset();
    st.current.trigger = null;
    if (d?.open) d.close(); // the browser hands focus back to the control that opened it
  }

  function open(trigger?: Element | null) {
    const d = ref.current;
    const s = st.current;
    if (!d) return;
    const midClose = s.frame !== 0 && !s.open;
    if (!d.open) {
      d.showModal();
      d.scrollTop = 0; // focusing the first control may have scrolled a tall sheet past its title
    }
    s.trigger = trigger ?? s.trigger;
    const prior = s.mode;
    measure();
    if (!midClose || prior !== s.mode) s.value = closed();
    s.open = true;
    render();
    animate(0, OPEN, midClose ? s.velocity : 0);
  }

  function close(velocity = 0) {
    const s = st.current;
    if (!s.open || !ref.current?.open) return;
    s.open = false;
    const slide = s.mode === "slide";
    // Offscreen, the last pixels of a spring's tail are invisible: finish once it's out.
    animate(closed(), slide ? DISMISS : ZOOM_CLOSE, velocity, finish, slide ? 3 : 0.02);
  }

  // Latest open/close for the listeners below, which are attached once.
  const api = useRef({ open, close });
  api.current = { open, close };

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    const s = st.current;
    let drag: { id: number; startY: number; from: number; active: boolean; caught: boolean; samples: { t: number; y: number }[] } | null = null;
    let swallowClick = false;

    const down = (e: PointerEvent) => {
      if (s.mode !== "slide" || e.button !== 0 || !d.open) return;
      const onHandle = (e.target as Element).closest("[data-sheet-handle]");
      if (!onHandle && d.classList.contains("is-scrollable")) return; // the content scrolls instead
      const caught = s.frame !== 0;
      stop(); // a moving sheet stops under the finger
      drag = { id: e.pointerId, startY: e.clientY, from: s.value, active: caught, caught, samples: [{ t: e.timeStamp, y: e.clientY }] };
      if (caught) {
        d.setPointerCapture(e.pointerId);
        d.classList.add("is-dragging");
      }
    };

    const move = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      const dy = e.clientY - drag.startY;
      if (!drag.active) {
        if (Math.abs(dy) < 8) return; // hysteresis, so taps stay taps
        drag.active = true;
        drag.startY = e.clientY; // start from here, so the sheet doesn't jump by the slop
        d.setPointerCapture(e.pointerId);
        d.classList.add("is-dragging");
        return;
      }
      const raw = drag.from + dy;
      s.value = raw < 0 ? -rubberband(-raw, d.clientHeight / 4) : raw; // stiff: the sheet barely lifts off its edge
      render();
      drag.samples.push({ t: e.timeStamp, y: e.clientY });
      while (drag.samples.length > 2 && e.timeStamp - drag.samples[0].t > 100) drag.samples.shift();
    };

    const up = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      const { active, samples } = drag;
      drag = null;
      d.classList.remove("is-dragging");
      if (!active) return; // a tap: nothing moved
      swallowClick = true;
      setTimeout(() => { swallowClick = false; }, 0);
      // Speed over the last 100 ms before release; a finger that paused has none.
      const recent = samples.filter((p) => e.timeStamp - p.t <= 100);
      const first = recent[0];
      const lastSample = recent[recent.length - 1];
      const span = recent.length > 1 ? (lastSample.t - first.t) / 1000 : 0;
      const velocity = e.type === "pointercancel" || span <= 0 ? 0 : (lastSample.y - first.y) / span;
      if (s.value + project(velocity) > s.distance / 2) {
        s.open = true; // let close() run from here, carrying the finger's speed
        api.current.close(Math.max(velocity, 0));
      } else {
        s.open = true;
        animate(0, SETTLE, velocity);
      }
    };

    const click = (e: MouseEvent) => {
      if (swallowClick) {
        e.stopPropagation();
        e.preventDefault();
        swallowClick = false;
        return;
      }
      // Only a click on the dimmed area outside the sheet closes it (a click on the
      // sheet's own padding also targets the dialog element).
      if (e.target !== d) return;
      const r = d.getBoundingClientRect();
      const outside = e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      if (outside && (e.clientX !== 0 || e.clientY !== 0)) api.current.close();
    };

    const cancel = (e: Event) => {
      e.preventDefault();
      api.current.close();
    };

    const keydown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      api.current.close();
    };

    // Closed by the browser without us (e.g. a second Escape it won't let us defer).
    const closedNatively = () => {
      if (!s.open && s.frame === 0) return;
      stop();
      s.open = false;
      reset();
    };

    const resize = () => {
      if (!s.open || s.frame !== 0 || drag) return;
      measure();
      s.value = 0;
      render();
    };

    d.addEventListener("pointerdown", down);
    d.addEventListener("pointermove", move);
    d.addEventListener("pointerup", up);
    d.addEventListener("pointercancel", up);
    d.addEventListener("click", click, true);
    d.addEventListener("cancel", cancel);
    d.addEventListener("keydown", keydown);
    d.addEventListener("close", closedNatively);
    addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(s.frame);
      d.removeEventListener("pointerdown", down);
      d.removeEventListener("pointermove", move);
      d.removeEventListener("pointerup", up);
      d.removeEventListener("pointercancel", up);
      d.removeEventListener("click", click, true);
      d.removeEventListener("cancel", cancel);
      d.removeEventListener("keydown", keydown);
      d.removeEventListener("close", closedNatively);
      removeEventListener("resize", resize);
    };
  }, [ref]);

  return { open: (trigger?: Element | null) => api.current.open(trigger), close: () => api.current.close() };
}
