// Placeholder – zip upload entry point.
// Person 3 replaces this with a file input that POSTs multipart to
// POST /api/scan/upload then redirects to /issues.

export default function ScanUploadPage() {
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold">Upload a zip</h2>
      <p className="text-gray-500 dark:text-gray-400">
        Zip upload form placeholder — multipart POST to{" "}
        <code className="font-mono text-sm">/api/scan/upload</code>.
      </p>
    </div>
  );
}
