import type { CSSProperties } from "react";

// The emblem has eight-fold symmetry, so it is cut into eight wedges, one per
// arm, each centred on an arm. At rest the wedges line up into the full logo;
// while thinking each arm spirals in from the centre, one after another, as
// the whole mark turns. Wedges overlap by a degree so no seam shows.
const ARMS = 8;
const HALF = 360 / ARMS / 2 + 1;

function wedge(index: number): string {
  const point = (deg: number) => {
    const rad = (deg * Math.PI) / 180;
    return `${(50 + 80 * Math.sin(rad)).toFixed(2)}% ${(50 - 80 * Math.cos(rad)).toFixed(2)}%`;
  };
  const mid = index * (360 / ARMS);
  return `polygon(50% 50%, ${point(mid - HALF)}, ${point(mid)}, ${point(mid + HALF)})`;
}

const WEDGES = Array.from({ length: ARMS }, (_, i) => wedge(i));

/** The Medusa emblem, assembling itself while `thinking`. Decorative only. */
export default function ThinkingOrb({ thinking = false, size = "sm" }: { thinking?: boolean; size?: "sm" | "md" }) {
  return (
    <span className={`orb orb-${size} ${thinking ? "is-thinking" : ""}`} aria-hidden="true">
      <span className="orb-spin">
        {WEDGES.map((clip, i) => (
          <img
            key={i}
            className="orb-arm"
            src="/images/medusa-mark.png"
            alt=""
            draggable={false}
            style={{ clipPath: clip, "--i": i } as CSSProperties}
          />
        ))}
      </span>
    </span>
  );
}
