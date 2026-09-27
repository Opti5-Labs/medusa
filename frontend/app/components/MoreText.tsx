"use client";

import { useState, type ElementType } from "react";

interface Props {
  text: string;
  className?: string;
  /** The element to render the text in (defaults to a paragraph). */
  as?: ElementType;
}

/**
 * Long text that, on phones only, shows its first four lines with a "More"
 * button to expand, like the App Store. Wider screens always show it in full;
 * the button only appears when the text is long enough to be clamped.
 */
export default function MoreText({ text, className = "", as: Tag = "p" }: Props) {
  const [open, setOpen] = useState(false);
  const long = text.length > 220;
  return (
    <div className={`more-text ${open ? "is-open" : ""} ${long ? "is-long" : ""}`}>
      <Tag className={className}>{text}</Tag>
      {long && (
        <button type="button" className="more-text-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? "Less" : "More"}
        </button>
      )}
    </div>
  );
}
