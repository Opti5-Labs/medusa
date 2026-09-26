"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import {
  ArchitectureComponent,
  ArchitectureRelationship,
  ArchitectureReport,
  EvidenceRef,
  ScanResult,
  getArchitectureDownloadUrl,
} from "../../lib/api";
import { useArchitecture } from "../../lib/useArchitecture";
import AssessingStatus from "../components/AssessingStatus";
import Badge, { type Tone } from "../components/Badge";
import LogView from "../components/LogView";
import MermaidView from "../components/MermaidView";

const SOURCE_BADGE: Record<ArchitectureReport["source"], { label: string; tone: Tone }> = {
  curated: { label: "Curated", tone: "violet" },
  static: { label: "Statically inferred", tone: "blue" },
  static_and_model: { label: "Statically inferred + AI-assisted", tone: "blue" },
};

const STATUS_BADGE: Record<ArchitectureReport["status"], { label: string; tone: Tone }> = {
  running: { label: "Analysing…", tone: "blue" },
  complete: { label: "Complete", tone: "green" },
  partial: { label: "Partial", tone: "amber" },
  unavailable: { label: "Unavailable", tone: "amber" },
  error: { label: "Error", tone: "red" },
};

const ASSESSING_STEPS = [
  "Reading the repository tree",
  "Detecting manifests and frameworks",
  "Parsing source files",
  "Building the component graph",
  "Generating the diagram",
];

// ── small shared bits ──────────────────────────────────────────────────────────

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard
          ?.writeText(text)
          .then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          })
          .catch(() => {});
      }}
      className="text-xs px-2 py-1 rounded-md border border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900"
    >
      {copied ? "Copied" : label}
    </button>
  );
}

function EvidenceList({ evidence }: { evidence: EvidenceRef[] }) {
  if (evidence.length === 0) return null;
  return (
    <ul className="space-y-0.5">
      {evidence.map((ev, i) => (
        <li key={i} className="font-mono text-xs text-gray-500 dark:text-gray-500 break-all">
          {ev.verified ? (
            <span>{ev.path}</span>
          ) : (
            <a
              href={ev.url ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 dark:text-blue-400 hover:underline"
            >
              {ev.path} (upstream)
            </a>
          )}
          {ev.line && <span>{`:${ev.line}`}</span>}
          {ev.note && <span className="text-gray-400"> — {ev.note}</span>}
        </li>
      ))}
    </ul>
  );
}

function ComponentCard({ c }: { c: ArchitectureComponent }) {
  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-3 space-y-1.5 min-w-0">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-semibold text-sm">{c.label}</span>
        <Badge>{c.type.replace(/_/g, " ")}</Badge>
        {c.assisted_by === "model" && <Badge tone="amber">AI-named</Badge>}
        <span className="text-xs text-gray-400">{Math.round(c.confidence * 100)}% confidence</span>
      </div>
      {c.description && (
        <p className="text-sm text-gray-600 dark:text-gray-400">{c.description}</p>
      )}
      <p className="text-xs text-gray-400">
        {c.file_count} file{c.file_count === 1 ? "" : "s"}
      </p>
      {c.evidence.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-gray-500 hover:text-gray-800 dark:hover:text-gray-200">
            Evidence
          </summary>
          <div className="mt-1">
            <EvidenceList evidence={c.evidence} />
          </div>
        </details>
      )}
    </div>
  );
}

function RelationshipRow({ r, labelFor }: { r: ArchitectureRelationship; labelFor: (id: string) => string }) {
  return (
    <li className="text-sm border-b border-gray-100 dark:border-gray-900 pb-2 last:border-0">
      <span className="font-medium">{labelFor(r.source)}</span>{" "}
      <span className="text-gray-400">--{r.type}--&gt;</span>{" "}
      <span className="font-medium">{labelFor(r.target)}</span>
      {r.assisted_by === "model" && <Badge tone="amber">AI</Badge>}
      {r.explanation && <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{r.explanation}</p>}
    </li>
  );
}

// ── page ───────────────────────────────────────────────────────────────────────

function ArchitectureContent() {
  const params = useSearchParams();
  const scanId = params.get("scan");

  const [scan, setScan] = useState<ScanResult | null | undefined>(undefined);

  useEffect(() => {
    if (!scanId) {
      setScan(null);
      return;
    }
    const raw = sessionStorage.getItem(`scan:${scanId}`);
    if (!raw) {
      setScan(null);
      return;
    }
    try {
      setScan(JSON.parse(raw) as ScanResult);
    } catch {
      setScan(null);
    }
  }, [scanId]);

  const { report, log, error } = useArchitecture(scanId);

  if (!scanId || scan === null) {
    return (
      <div className="space-y-4 text-center py-16">
        <p className="text-gray-500 dark:text-gray-400">Session expired or no scan data found.</p>
        <Link
          href="/"
          className="inline-block px-4 py-2 rounded-lg border border-gray-300 dark:border-gray-700 text-sm hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
        >
          Start again
        </Link>
      </div>
    );
  }

  const running = !report || report.status === "running";

  return (
    <div className="space-y-8">
      <Link
        href={`/issues?scan=${scanId}`}
        className="inline-block text-sm text-gray-500 hover:text-gray-900 dark:hover:text-gray-100"
      >
        Back to issues
      </Link>

      <div className="space-y-3">
        <h2 className="text-2xl font-semibold tracking-tight">Project Architecture</h2>
        {scan && (
          <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
            <div>
              <dt className="text-gray-500 dark:text-gray-400">Source</dt>
              <dd className="font-medium">{scan.repo_source}</dd>
            </div>
            <div>
              <dt className="text-gray-500 dark:text-gray-400">Language</dt>
              <dd className="font-medium">{scan.language}</dd>
            </div>
          </dl>
        )}
        {report && (
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={SOURCE_BADGE[report.source].tone}>{SOURCE_BADGE[report.source].label}</Badge>
            <Badge tone={STATUS_BADGE[report.status].tone}>{STATUS_BADGE[report.status].label}</Badge>
          </div>
        )}
        <p className="text-xs text-gray-500 dark:text-gray-400">
          {report?.source === "curated"
            ? "A curated, hand-authored reference diagram for the OptiLearn demo — see golden/optilearn for provenance."
            : "Inferred from repository files by static analysis. Nothing here was executed."}
        </p>
      </div>

      {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      {running && (
        <div className="space-y-4">
          <AssessingStatus
            steps={ASSESSING_STEPS}
            note="General repositories are read as text only — nothing is executed."
          />
          <LogView events={log} showSource emptyText="Starting…" />
        </div>
      )}

      {report && report.status !== "running" && (
        <ReportView report={report} />
      )}
    </div>
  );
}

function ReportView({ report }: { report: ArchitectureReport }) {
  const componentLabel = useMemo(() => {
    const byId = new Map(report.components.map((c) => [c.id, c.label]));
    return (id: string) => byId.get(id) ?? id;
  }, [report.components]);

  const stack = report.technology_stack;
  const hasStack =
    stack.languages.length + stack.frameworks.length + stack.build_systems.length +
    stack.package_managers.length + stack.test_frameworks.length > 0;

  return (
    <div className="space-y-8">
      {report.summary && (
        <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">{report.summary}</p>
      )}

      {(report.status === "partial" || report.status === "unavailable") && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-900/10 p-4 space-y-2">
          <p className="text-sm font-semibold text-amber-900 dark:text-amber-300">
            {report.status === "partial" ? "Partial result" : "Architecture unavailable"}
          </p>
          {report.warnings.map((w, i) => (
            <p key={i} className="text-sm text-amber-800 dark:text-amber-400">{w}</p>
          ))}
          {report.narrowing_suggestions.length > 0 && (
            <ul className="list-disc list-inside space-y-0.5">
              {report.narrowing_suggestions.map((s, i) => (
                <li key={i} className="text-sm text-amber-800 dark:text-amber-400">{s}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {hasStack && (
        <section className="space-y-2">
          <h3 className="font-medium text-gray-700 dark:text-gray-300">Technology stack</h3>
          <div className="flex flex-wrap gap-1.5">
            {[...stack.languages, ...stack.frameworks, ...stack.build_systems, ...stack.package_managers, ...stack.test_frameworks].map(
              (item, i) => <Badge key={`${item}-${i}`}>{item}</Badge>
            )}
          </div>
          {stack.unsupported_languages.length > 0 && (
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Not statically parsed: {stack.unsupported_languages.join(", ")}
            </p>
          )}
        </section>
      )}

      {report.entrypoints.length > 0 && (
        <section className="space-y-2">
          <h3 className="font-medium text-gray-700 dark:text-gray-300">Entrypoints</h3>
          <ul className="space-y-1">
            {report.entrypoints.map((e, i) => (
              <li key={i} className="text-sm">
                <code className="font-mono text-xs">{e.path}</code>{" "}
                <span className="text-gray-500 dark:text-gray-400">({e.kind}) {e.detail}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {report.components.length > 0 && (
        <section className="space-y-2">
          <h3 className="font-medium text-gray-700 dark:text-gray-300">
            Components ({report.components.length})
          </h3>
          <div className="grid gap-3 sm:grid-cols-2">
            {report.components.map((c) => (
              <ComponentCard key={c.id} c={c} />
            ))}
          </div>
        </section>
      )}

      {report.relationships.length > 0 && (
        <section className="space-y-2">
          <h3 className="font-medium text-gray-700 dark:text-gray-300">
            Relationships ({report.relationships.length})
          </h3>
          <ul className="space-y-2">
            {report.relationships.map((r, i) => (
              <RelationshipRow key={i} r={r} labelFor={componentLabel} />
            ))}
          </ul>
        </section>
      )}

      {(report.external_services.length > 0 || report.data_stores.length > 0 || report.deployment.length > 0) && (
        <section className="grid gap-4 sm:grid-cols-3">
          {report.data_stores.length > 0 && (
            <div className="space-y-1">
              <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300">Data stores</h4>
              <ul className="text-sm space-y-0.5">
                {report.data_stores.map((d, i) => <li key={i}>{d.name}</li>)}
              </ul>
            </div>
          )}
          {report.external_services.length > 0 && (
            <div className="space-y-1">
              <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300">External services</h4>
              <ul className="text-sm space-y-0.5">
                {report.external_services.map((e, i) => <li key={i}>{e.name}</li>)}
              </ul>
            </div>
          )}
          {report.deployment.length > 0 && (
            <div className="space-y-1">
              <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300">Deployment</h4>
              <ul className="text-sm space-y-0.5">
                {report.deployment.map((d, i) => (
                  <li key={i}>
                    <code className="font-mono text-xs">{d.path}</code>
                    {d.services.length > 0 && <span className="text-gray-500"> — {d.services.join(", ")}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {report.mermaid ? (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-gray-700 dark:text-gray-300">Diagram</h3>
            <div className="flex gap-2">
              <CopyButton text={report.mermaid} label="Copy source" />
              <a
                href={getArchitectureDownloadUrl(report.architecture_id, "mermaid")}
                className="text-xs px-2 py-1 rounded-md border border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900"
              >
                Download .mmd
              </a>
              <a
                href={getArchitectureDownloadUrl(report.architecture_id, "json")}
                className="text-xs px-2 py-1 rounded-md border border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900"
              >
                Download JSON
              </a>
            </div>
          </div>
          <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-3 overflow-x-auto bg-white dark:bg-gray-950">
            <MermaidView
              source={report.mermaid}
              id={`${report.architecture_id}-overview`}
              fallback={
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  See the component list above for the same information.
                </p>
              }
            />
          </div>
          <details>
            <summary className="cursor-pointer text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200">
              View Mermaid source
            </summary>
            <pre className="mt-1 max-h-96 overflow-auto rounded-md bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 p-3 text-xs leading-relaxed font-mono whitespace-pre-wrap break-words">
              {report.mermaid}
            </pre>
          </details>
        </section>
      ) : (
        report.status === "complete" && (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            No diagram was generated for this repository; see the component list above.
          </p>
        )
      )}

      {report.detail_mermaid && (
        <details className="space-y-2">
          <summary className="cursor-pointer text-sm font-medium text-gray-700 dark:text-gray-300">
            Show full detail diagram
          </summary>
          <div className="flex justify-end">
            <CopyButton text={report.detail_mermaid} label="Copy source" />
          </div>
          <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-3 overflow-x-auto bg-white dark:bg-gray-950">
            <MermaidView
              source={report.detail_mermaid}
              id={`${report.architecture_id}-detail`}
              fallback={
                <pre className="max-h-96 overflow-auto text-xs leading-relaxed font-mono whitespace-pre-wrap break-words">
                  {report.detail_mermaid}
                </pre>
              }
            />
          </div>
        </details>
      )}

      {report.limitations.length > 0 && (
        <section className="space-y-1">
          <h3 className="font-medium text-gray-700 dark:text-gray-300">Limitations</h3>
          <ul className="list-disc list-inside space-y-0.5">
            {report.limitations.map((l, i) => (
              <li key={i} className="text-sm text-gray-500 dark:text-gray-400">{l}</li>
            ))}
          </ul>
        </section>
      )}

      {report.coverage && (
        <details className="text-sm">
          <summary className="cursor-pointer text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300">
            Coverage details
          </summary>
          <dl className="mt-2 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-xs">
            <dt className="text-gray-500">Files discovered</dt>
            <dd>{report.coverage.files_discovered}</dd>
            <dt className="text-gray-500">Source files (recognised)</dt>
            <dd>{report.coverage.source_files_discovered}</dd>
            <dt className="text-gray-500">Parsed</dt>
            <dd>{report.coverage.files_parsed} of {report.coverage.source_files_supported}</dd>
            <dt className="text-gray-500">Tier</dt>
            <dd>{report.coverage.tier}</dd>
            {report.coverage.limit_exceeded && (
              <>
                <dt className="text-gray-500">Limit exceeded</dt>
                <dd className="font-mono">{report.coverage.limit_exceeded}</dd>
              </>
            )}
          </dl>
        </details>
      )}
    </div>
  );
}

export default function ArchitecturePage() {
  return (
    <Suspense
      fallback={<div className="py-16 text-center text-gray-500 dark:text-gray-400">Loading…</div>}
    >
      <ArchitectureContent />
    </Suspense>
  );
}
