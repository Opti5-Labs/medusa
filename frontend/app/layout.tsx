import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Medusa",
  description:
    "Find the bug, prove it, fix it. Medusa reproduces a defect in an isolated sandbox, races candidate fixes and recommends the one the tests verify.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`}>
      <body className="min-h-screen flex flex-col bg-gray-50 text-gray-900 dark:bg-gray-950 dark:text-gray-100 antialiased font-sans">
        <header className="border-b border-gray-200 dark:border-gray-800 bg-white/70 dark:bg-gray-950/70">
          <div className="mx-auto max-w-5xl px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
            <Link href="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
              <MedusaMark />
              Medusa
            </Link>
            <span className="text-xs text-gray-500 dark:text-gray-400 hidden sm:inline">
              Built for the IBM Bob 2.0 Hackathon
            </span>
          </div>
        </header>
        <main className="flex-1 w-full mx-auto max-w-5xl px-4 sm:px-6 py-10 sm:py-14">{children}</main>
        <footer className="border-t border-gray-200 dark:border-gray-800">
          <div className="mx-auto max-w-5xl px-4 sm:px-6 py-5 text-xs text-gray-500 dark:text-gray-400 flex flex-wrap gap-x-6 gap-y-1">
            <span>Code from linked repositories and zips is read as text and never executed.</span>
            <span>Scans and runs are kept for 30 minutes, then deleted.</span>
          </div>
        </footer>
      </body>
    </html>
  );
}

/** A coiled line: one serpent, drawn as a single stroke. */
function MedusaMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" className="text-verdigris-600 dark:text-verdigris-400">
      <path
        d="M12 21c-4.4 0-8-3.1-8-7.2C4 9.9 7.2 7 11 7c3 0 5.2 2.1 5.2 4.8 0 2.2-1.7 3.9-3.8 3.9-1.8 0-3.1-1.3-3.1-2.9 0-1.3 1-2.3 2.2-2.3M16.5 5.5c1.2-1.3 2.6-2 4-2"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="20.5" cy="3.5" r="1.3" fill="currentColor" />
    </svg>
  );
}
