const TONES = {
  gray: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  green: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  red: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
  blue: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  violet: "bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300",
} as const;

export type Tone = keyof typeof TONES;

export default function Badge({ tone = "gray", children }: { tone?: Tone; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-full ${TONES[tone]}`}>
      {children}
    </span>
  );
}
