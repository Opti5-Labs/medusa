"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useRef, useCallback } from "react";
import { postScanUpload, ScanResult } from "../../../lib/api";
import AssessingStatus from "../../components/AssessingStatus";

// The steps /api/scan/upload really runs for an uploaded archive.
const SCAN_STEPS = [
  "Uploading and extracting the archive",
  "Selecting the source files to analyse",
  "Reviewing the code for issues",
];

// NEXT_PUBLIC_MAX_ZIP_MB lets local devs raise the client-side check to match
// a raised server limit (set in backend/.env). Defaults to 20 to match the
// public server default. Never hard-code a non-default value here.
const MAX_ZIP_MB = Number(process.env.NEXT_PUBLIC_MAX_ZIP_MB ?? "20");
const MAX_ZIP_SIZE_BYTES = MAX_ZIP_MB * 1024 * 1024;

type Stage = "idle" | "uploading" | "done";

export default function ScanUploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  function validateFile(f: File): boolean {
    if (!f.name.toLowerCase().endsWith(".zip")) {
      setFileError("Only .zip files are accepted.");
      return false;
    }
    if (f.size > MAX_ZIP_SIZE_BYTES) {
      setFileError(`File is ${(f.size / (1024 * 1024)).toFixed(1)} MB; maximum is ${MAX_ZIP_MB} MB.`);
      return false;
    }
    if (f.size === 0) {
      setFileError("File is empty.");
      return false;
    }
    setFileError(null);
    return true;
  }

  function handleFileChange(f: File | null) {
    if (!f) return;
    setFile(f);
    validateFile(f);
    setError(null);
  }

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragOver(false);
      const dropped = e.dataTransfer.files[0];
      if (dropped) handleFileChange(dropped);
    },
    []
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !validateFile(file)) return;

    setStage("uploading");
    setError(null);
    abortRef.current = new AbortController();

    try {
      const result: ScanResult = await postScanUpload(file, abortRef.current.signal);
      sessionStorage.setItem(`scan:${result.scan_id}`, JSON.stringify(result));
      router.push(`/issues?scan=${result.scan_id}`);
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Upload failed. Please try again.");
      setStage("idle");
    }
  }

  function handleCancel() {
    abortRef.current?.abort();
    setStage("idle");
    setError(null);
  }

  return (
    <div className="max-w-lg space-y-6">
      <div className="space-y-1">
        <Link href="/" className="text-sm text-gray-500 hover:text-gray-900 dark:hover:text-gray-100">
          Back
        </Link>
        <h2 className="text-2xl font-semibold tracking-tight pt-2">Upload a zip</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Upload a zip of your codebase, up to {MAX_ZIP_MB} MB and 2,000 files. The code is read as text and never executed, so results are analysis, not test runs.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        {/* Drop zone */}
        <div
          role="region"
          aria-label="File drop zone"
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
            dragOver
              ? "border-blue-400 bg-blue-50 dark:bg-blue-900/10"
              : "border-gray-300 dark:border-gray-700 hover:border-gray-400 dark:hover:border-gray-600"
          }`}
        >
          <input
            ref={inputRef}
            id="zip-file"
            type="file"
            accept=".zip"
            className="sr-only"
            aria-label="Choose zip file"
            onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
            disabled={stage === "uploading"}
          />
          {file ? (
            <div className="space-y-1">
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{file.name}</p>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {(file.size / (1024 * 1024)).toFixed(2)} MB
              </p>
            </div>
          ) : (
            <div className="space-y-1">
              <p className="text-sm text-gray-500 dark:text-gray-400">
                Drag and drop a <code className="font-mono">.zip</code> file here, or{" "}
                <span className="text-blue-600 dark:text-blue-400">browse</span>
              </p>
              <p className="text-xs text-gray-400 dark:text-gray-600">Maximum {MAX_ZIP_MB} MB</p>
            </div>
          )}
        </div>

        {fileError && (
          <p role="alert" className="text-xs text-red-600 dark:text-red-400">
            {fileError}
          </p>
        )}

        {stage === "uploading" && (
          <AssessingStatus
            steps={SCAN_STEPS}
            stepMs={2200}
            note="Code is read as text and never executed. This can take up to 90 seconds."
          />
        )}

        {error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        )}

        <div className="flex gap-3">
          <button
            type="submit"
            disabled={!file || !!fileError || stage === "uploading"}
            className="px-5 py-2 rounded-lg bg-verdigris-600 text-white hover:bg-verdigris-700 dark:bg-verdigris-600 dark:hover:bg-verdigris-700 text-sm font-medium transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {stage === "uploading" ? "Scanning…" : "Scan zip"}
          </button>
          {stage === "uploading" && (
            <button
              type="button"
              onClick={handleCancel}
              className="px-5 py-2 rounded-lg border border-gray-300 dark:border-gray-700 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
            >
              Cancel
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
