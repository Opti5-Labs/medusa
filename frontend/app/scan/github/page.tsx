"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useRef } from "react";
import { postScan, ScanResult } from "../../../lib/api";
import AssessingStatus from "../../components/AssessingStatus";

// Same pattern the server validates — https://github.com/{owner}/{repo}
const GITHUB_URL_RE =
  /^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*(\.git|\/)?$/;

// Mirrors the server: a bare "github.com/owner/repo" is assumed to be https.
function normalizeGithubUrl(value: string): string {
  const trimmed = value.trim();
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

// The steps /api/scan really runs for a linked repository.
const SCAN_STEPS = [
  "Downloading the repository",
  "Selecting the source files to analyse",
  "Reviewing the code for issues",
  "Fetching the repository's open GitHub Issues",
];

type Stage = "idle" | "scanning" | "done";

const EXAMPLES = ["https://github.com/pallets/itsdangerous", "https://github.com/pallets/markupsafe"];

export default function ScanGitHubPage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState<string | null>(null);
  const [urlError, setUrlError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  function validateUrl(value: string): boolean {
    const normalized = normalizeGithubUrl(value).replace(/\/tree\/.*$/, "").replace(/\/$/, "");
    if (!GITHUB_URL_RE.test(normalized)) {
      setUrlError("Enter a valid public GitHub URL, e.g. github.com/owner/repo");
      return false;
    }
    setUrlError(null);
    return true;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validateUrl(url)) return;

    setStage("scanning");
    setError(null);
    abortRef.current = new AbortController();

    try {
      const result: ScanResult = await postScan(
        { source: "github", repo_url: normalizeGithubUrl(url) },
        abortRef.current.signal
      );
      sessionStorage.setItem(`scan:${result.scan_id}`, JSON.stringify(result));
      router.push(`/issues?scan=${result.scan_id}`);
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Scan failed. Please try again.");
      setStage("idle");
    }
  }

  function handleCancel() {
    abortRef.current?.abort();
    setStage("idle");
    setError(null);
  }

  const stageText: Record<Stage, string> = {
    idle: "Scan repository",
    scanning: "Scanning…",
    done: "Done",
  };

  return (
    <div className="max-w-lg space-y-6">
      <div className="space-y-1">
        <Link href="/" className="text-sm text-gray-500 hover:text-gray-900 dark:hover:text-gray-100">
          Back
        </Link>
        <h2 className="text-2xl font-semibold tracking-tight pt-2">Link a GitHub repository</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Medusa reviews a public repository&apos;s code and open GitHub Issues. The code is read
          as text and never executed, so results are analysis, not test runs. Link a folder with
          /tree/main/src to scan part of a large repository.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="space-y-1">
          <label
            htmlFor="repo-url"
            className="block text-sm font-medium text-gray-700 dark:text-gray-300"
          >
            Repository URL
          </label>
          <input
            id="repo-url"
            type="url"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              if (urlError) validateUrl(e.target.value);
            }}
            onBlur={() => url && validateUrl(url)}
            placeholder="github.com/owner/repo"
            disabled={stage === "scanning"}
            aria-describedby={urlError ? "url-error" : undefined}
            aria-invalid={!!urlError}
            className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
            autoComplete="off"
            spellCheck={false}
          />
          {urlError && (
            <p id="url-error" role="alert" className="text-xs text-red-600 dark:text-red-400">
              {urlError}
            </p>
          )}
          <p className="text-xs text-gray-500 dark:text-gray-400 pt-1">
            Try{" "}
            {EXAMPLES.map((example, i) => (
              <span key={example}>
                {i > 0 && " or "}
                <button
                  type="button"
                  onClick={() => {
                    setUrl(example);
                    setUrlError(null);
                  }}
                  disabled={stage === "scanning"}
                  className="font-mono text-verdigris-700 dark:text-verdigris-300 hover:underline"
                >
                  {example.replace("https://github.com/", "")}
                </button>
              </span>
            ))}
          </p>
        </div>

        {/* Stage text */}
        {stage === "scanning" && (
          <AssessingStatus
            steps={SCAN_STEPS}
            stepMs={2200}
            note="Code is read as text and never executed. This can take up to 90 seconds."
          />
        )}

        {/* Server error */}
        {error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        )}

        <div className="flex gap-3">
          <button
            type="submit"
            disabled={stage === "scanning" || !url.trim()}
            className="px-5 py-2 rounded-lg bg-verdigris-600 text-white hover:bg-verdigris-700 dark:bg-verdigris-600 dark:hover:bg-verdigris-700 text-sm font-medium transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {stageText[stage]}
          </button>
          {stage === "scanning" && (
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
