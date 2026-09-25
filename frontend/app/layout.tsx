import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Medusa",
  description: "Find the bug, prove it, fix it, and show your work.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-white text-gray-900 dark:bg-gray-950 dark:text-gray-100 antialiased">
        <header className="border-b border-gray-200 dark:border-gray-800">
          <div className="mx-auto max-w-5xl px-6 py-4 flex items-center gap-3">
            <span className="text-lg font-semibold tracking-tight">Medusa</span>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              IBM Bob 2.0 Hackathon
            </span>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
