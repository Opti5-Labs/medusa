import Link from "next/link";
import DemoButton from "./components/DemoButton";

export default function Home() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-10">
      <div className="text-center space-y-3">
        <h1 className="text-4xl font-bold tracking-tight">Medusa</h1>
        <p className="text-gray-500 dark:text-gray-400 text-lg max-w-md">
          Find the bug, prove it, fix it, and show your work.
        </p>
      </div>

      <div className="flex flex-col sm:flex-row gap-4 items-center">
        {/* Run the demo — client component, calls POST /api/scan */}
        <DemoButton />

        {/* Link a GitHub repository */}
        <Link
          href="/scan/github"
          className="px-6 py-3 rounded-lg border border-gray-300 dark:border-gray-700 font-medium text-center hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
        >
          Link a GitHub repository
        </Link>

        {/* Upload a zip */}
        <Link
          href="/scan/upload"
          className="px-6 py-3 rounded-lg border border-gray-300 dark:border-gray-700 font-medium text-center hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
        >
          Upload a zip
        </Link>
      </div>
    </div>
  );
}
