import type { Metadata, Viewport } from "next";
import AppShell from "./components/AppShell";
import "./globals.css";

export const metadata: Metadata = {
  title: "Medusa",
  description: "Scan a codebase for issues, investigate them with IBM Bob and Granite, and on the OptiLearn demo, test fixes in a sandbox.",
};

export const viewport: Viewport = {
  themeColor: "#0b0b0c",
  colorScheme: "dark",
  // Lets the phone tab bar extend under the home indicator (env(safe-area-inset-bottom)).
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body><AppShell>{children}</AppShell></body>
    </html>
  );
}
