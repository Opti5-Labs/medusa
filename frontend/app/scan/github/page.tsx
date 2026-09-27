"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useRef, useEffect } from "react";
import { postScan } from "../../../lib/api";
import AssessingStatus from "../../components/AssessingStatus";
import Icon from "../../components/Icon";
import { rememberScan } from "../../../lib/recentScans";

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
const STEP_MS = 2200;

type Stage = "idle" | "scanning" | "done";

const EXAMPLES = ["https://github.com/pallets/itsdangerous", "https://github.com/pallets/markupsafe"];

export default function ScanGitHubPage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState<string | null>(null);
  const [urlError, setUrlError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get("repo");
    if (initial) setUrl(initial);
  }, []);

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
      // A matching request can answer almost instantly (e.g. the OptiLearn demo
      // shortcut), so hold the steps on screen long enough to follow them
      // instead of jumping straight to the results — same as the demo button.
      const [result] = await Promise.all([
        postScan(
          { source: "github", repo_url: normalizeGithubUrl(url) },
          abortRef.current.signal
        ),
        new Promise((resolve) => setTimeout(resolve, SCAN_STEPS.length * STEP_MS)),
      ]);
      // "owner/repo" for display, derived from what was typed.
      const displayName = normalizeGithubUrl(url)
        .replace(/^https?:\/\//i, "")
        .replace(/^www\./i, "")
        .replace(/^github\.com\//i, "")
        .replace(/\.git$/i, "")
        .replace(/\/$/, "")
        .replace(/\/tree\/.*$/, "");
      rememberScan(result, displayName);
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
    <div className="page-narrow page">
      <header className="page-header">
        <Link href="/" className="back-link"><Icon name="chevron" />Overview</Link>
        <h2 className="title-1">Link a GitHub repository</h2>
        <p className="page-lede">
          Medusa reviews a public repository&apos;s code and open GitHub Issues. Code is read as text,
          never run, so results are analysis, not test runs.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="form" noValidate>
        <div className="field">
          <label htmlFor="repo-url" className="field-label">
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
            aria-describedby={urlError ? "url-error repo-hint" : "repo-hint"}
            aria-invalid={!!urlError}
            autoComplete="off"
            spellCheck={false}
          />
          {urlError && (
            <p id="url-error" role="alert" className="field-error">
              {urlError}
            </p>
          )}
          <p id="repo-hint" className="field-hint">
            Large repository? Link a folder, such as /tree/main/src.
          </p>
        </div>

        <div className="field">
          <span className="field-label">Or try an example</span>
          <div className="field-hint">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                className="chip-button"
                onClick={() => {
                  setUrl(example);
                  setUrlError(null);
                }}
                disabled={stage === "scanning"}
              >
                {example.replace("https://github.com/", "")}
              </button>
            ))}
          </div>
        </div>

        {stage === "scanning" && (
          <AssessingStatus
            steps={SCAN_STEPS}
            stepMs={2200}
            note="Code is read as text and never executed. This can take up to 90 seconds."
          />
        )}

        {error && (
          <p role="alert" className="alert">
            {error}
          </p>
        )}

        <div className="actions">
          <button type="submit" disabled={stage === "scanning" || !url.trim()} className="btn btn-primary btn-lg">
            {stageText[stage]}
          </button>
          {stage === "scanning" && (
            <button type="button" onClick={handleCancel} className="btn btn-secondary btn-lg">
              Cancel
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
