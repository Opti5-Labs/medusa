"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useRef, useCallback } from "react";
import { postScanUpload, ScanResult } from "../../../lib/api";
import AssessingStatus from "../../components/AssessingStatus";
import Icon from "../../components/Icon";
import { rememberScan } from "../../../lib/recentScans";

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
      rememberScan(result, file.name.replace(/\.zip$/i, ""));
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
    <div className="page-narrow page">
      <header className="page-header">
        <Link href="/" className="back-link"><Icon name="chevron" />Overview</Link>
        <h2 className="title-1">Upload a zip</h2>
        <p className="page-lede">
          Up to {MAX_ZIP_MB} MB and 2,000 files. Scanning reads the code as text. When you
          reproduce an issue in a Python project with tests, its tests run in an isolated sandbox; otherwise
          results are analysis, not test runs.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="form" noValidate>
        <div className="field">
          <input
            ref={inputRef}
            id="zip-file"
            type="file"
            accept=".zip"
            className="sr-only"
            tabIndex={-1}
            onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
            disabled={stage === "uploading"}
          />
          <div
            role="button"
            tabIndex={stage === "uploading" ? -1 : 0}
            aria-label={file ? `Selected ${file.name}. Choose a different zip file` : "Choose a zip file"}
            aria-describedby={fileError ? "file-error" : undefined}
            onClick={() => inputRef.current?.click()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                inputRef.current?.click();
              }
            }}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            className={`dropzone ${dragOver ? "is-over" : ""} ${file ? "has-file" : ""}`}
          >
            <span className={`tile ${file ? "" : "cyan"}`}><Icon name={file ? "folder" : "upload"} /></span>
            {file ? (
              <>
                <span className="dropzone-title">{file.name}</span>
                <span className="dropzone-hint">{(file.size / (1024 * 1024)).toFixed(2)} MB · <span className="copy-long">Click to choose a different file</span><span className="copy-short">Tap to change</span></span>
              </>
            ) : (
              <>
                <span className="dropzone-title"><span className="copy-long">Drop a .zip here, or click to choose</span><span className="copy-short">Tap to choose a .zip</span></span>
                <span className="dropzone-hint">Up to {MAX_ZIP_MB} MB</span>
              </>
            )}
          </div>
          {fileError && (
            <p id="file-error" role="alert" className="field-error">
              {fileError}
            </p>
          )}
        </div>

        {stage === "uploading" && (
          <AssessingStatus
            steps={SCAN_STEPS}
            stepMs={2200}
            note="Scanning reads the code as text. This can take up to 90 seconds."
          />
        )}

        {error && (
          <p role="alert" className="alert">
            {error}
          </p>
        )}

        <div className="actions">
          <button type="submit" disabled={!file || !!fileError || stage === "uploading"} className="btn btn-primary btn-lg">
            {stage === "uploading" ? "Scanning…" : "Scan zip"}
          </button>
          {stage === "uploading" && (
            <button type="button" onClick={handleCancel} className="btn btn-secondary btn-lg">
              Cancel
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
