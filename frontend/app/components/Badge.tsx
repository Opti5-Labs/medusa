const TONES = {
  gray: "badge",
  green: "badge badge-green",
  red: "badge badge-red",
  amber: "badge badge-amber",
  blue: "badge badge-blue",
  violet: "badge badge-violet",
} as const;

export type Tone = keyof typeof TONES;

export default function Badge({ tone = "gray", children }: { tone?: Tone; children: React.ReactNode }) {
  return <span className={TONES[tone]}>{children}</span>;
}
