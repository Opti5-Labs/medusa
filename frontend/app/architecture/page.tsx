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
import { useScanGone } from "../../lib/useScanGone";
import AssessingStatus from "../components/AssessingStatus";
import Badge, { type Tone } from "../components/Badge";
import Icon from "../components/Icon";
import LogView from "../components/LogView";
import MermaidView from "../components/MermaidView";
import { ExpiredScan, LoadingState } from "../components/EmptyState";
import MoreText from "../components/MoreText";

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

const SOURCE_LABEL: Record<string, string> = {
  demo: "Demo (OptiLearn)",
  github: "GitHub repository",
  zip: "Uploaded zip",
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
      className="btn btn-secondary btn-sm"
    >
      <Icon name={copied ? "check" : "code"} aria-hidden="true" />
      {copied ? "Copied" : label}
    </button>
  );
}

function EvidenceList({ evidence }: { evidence: EvidenceRef[] }) {
  if (evidence.length === 0) return null;
  return (
    <ul className="evidence-list">
      {evidence.map((ev, i) => (
        <li key={i} className="code-ref">
          {ev.verified || !ev.url ? (
            <span>{ev.path}</span>
          ) : (
            <a href={ev.url} target="_blank" rel="noopener noreferrer" className="external-link">
              {ev.path} (upstream)
              <Icon name="arrow" aria-hidden="true" />
            </a>
          )}
          {ev.line && <span>{`:${ev.line}`}</span>}
          {ev.note && <span> — {ev.note}</span>}
        </li>
      ))}
    </ul>
  );
}

function ComponentCard({ c }: { c: ArchitectureComponent }) {
  return (
    <article className="card component-card">
      <div className="component-head">
        <h4>{c.label}</h4>
        <span className="component-confidence" title="Confidence">{Math.round(c.confidence * 100)}%</span>
      </div>
      <div className="badges">
        <Badge>{c.type.replace(/_/g, " ")}</Badge>
        {c.assisted_by === "model" && <Badge tone="amber">AI-named</Badge>}
        <span className="component-files">
          {c.file_count} file{c.file_count === 1 ? "" : "s"}
        </span>
      </div>
      {c.description && <p className="component-description">{c.description}</p>}
      {c.evidence.length > 0 && (
        <details className="disclosure">
          <summary><Icon name="chevron" aria-hidden="true" />Evidence</summary>
          <EvidenceList evidence={c.evidence} />
        </details>
      )}
    </article>
  );
}

function RelationshipRow({ r, labelFor }: { r: ArchitectureRelationship; labelFor: (id: string) => string }) {
  return (
    <li>
      <div className="relation-line">
        <strong>{labelFor(r.source)}</strong>
        <span className="relation-type">
          <Icon name="arrow" aria-hidden="true" />
          {r.type.replace(/_/g, " ")}
        </span>
        <strong>{labelFor(r.target)}</strong>
        {r.assisted_by === "model" && <Badge tone="amber">AI</Badge>}
      </div>
      {r.explanation && <p>{r.explanation}</p>}
    </li>
  );
}

// ── page ───────────────────────────────────────────────────────────────────────

function ArchitectureContent() {
  const params = useSearchParams();
  const scanId = params.get("scan");

  const [scan, setScan] = useState<ScanResult | null | undefined>(undefined);
  const [repoName, setRepoName] = useState<string | null>(null);

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
    // Set by the scan entry points (see rememberScan); absent for older links.
    setRepoName(sessionStorage.getItem(`scan:${scanId}:name`));
  }, [scanId]);

  const { report, log, error } = useArchitecture(scanId);
  const gone = useScanGone(scanId);

  if (!scanId || scan === null || gone) {
    return (
      <ExpiredScan reason={gone ?? undefined} />
    );
  }

  const running = !report || report.status === "running";

  return (
    <div className="page">
      <header className="page-header">
        <Link href={`/issues?scan=${scanId}`} className="back-link"><Icon name="chevron" />Scan results</Link>
        <h2 className="title-1">Architecture</h2>
        {scan && (
          <dl className="facts">
            <div>
              <dt>Repository</dt>
              <dd className="repo-title">{repoName ?? SOURCE_LABEL[scan.repo_source] ?? scan.repo_source}</dd>
            </div>
            <div>
              <dt>Language</dt>
              <dd>{scan.language}</dd>
            </div>
            {report && report.status !== "running" && (
              <div>
                <dt>Components</dt>
                <dd>{report.components.length}</dd>
              </div>
            )}
          </dl>
        )}
        {report && (
          <div className="provenance">
            <div className="badges">
              <Badge tone={SOURCE_BADGE[report.source].tone}>{SOURCE_BADGE[report.source].label}</Badge>
              <Badge tone={STATUS_BADGE[report.status].tone}>{STATUS_BADGE[report.status].label}</Badge>
            </div>
            <p>
              {report.source === "curated"
                ? "A hand-authored reference for the OptiLearn demo (optilearn-architecture.md)."
                : "Inferred from repository files by static analysis. Nothing here was executed."}
            </p>
          </div>
        )}
      </header>

      {error && <p role="alert" className="alert">{error}</p>}

      {running && !error && (
        <div className="stack">
          <AssessingStatus
            steps={ASSESSING_STEPS}
            note="General repositories are read as text only — nothing is executed."
          />
          <LogView events={log} showSource emptyText="Starting…" />
        </div>
      )}

      {report && report.status !== "running" && <ReportView report={report} />}
    </div>
  );
}

function ReportView({ report }: { report: ArchitectureReport }) {
  const componentLabel = useMemo(() => {
    const byId = new Map(report.components.map((c) => [c.id, c.label]));
    return (id: string) => byId.get(id) ?? id;
  }, [report.components]);

  const stack = report.technology_stack;
  // The same name can come from two detectors (Vite as a framework and a build system).
  const stackItems = [
    ...new Set([
      ...stack.languages,
      ...stack.frameworks,
      ...stack.build_systems,
      ...stack.package_managers,
      ...stack.test_frameworks,
    ]),
  ];
  const hasSurroundings =
    report.external_services.length > 0 || report.data_stores.length > 0 || report.deployment.length > 0;

  return (
    <>
      {report.summary && <MoreText text={report.summary} className="page-lede arch-summary" />}

      {(report.status === "partial" || report.status === "unavailable") && (
        <div className="notice">
          <p className="notice-title">
            {report.status === "partial" ? "Partial result" : "Architecture unavailable"}
          </p>
          {report.warnings.map((w, i) => <p key={i}>{w}</p>)}
          {report.narrowing_suggestions.length > 0 && (
            <ul>
              {report.narrowing_suggestions.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
          )}
        </div>
      )}

      {report.detail_mermaid || report.mermaid ? (
        <section className="section" aria-labelledby="diagram-heading">
          <div className="section-head">
            <h3 id="diagram-heading" className="headline">Diagram</h3>
            <div className="actions">
              <CopyButton text={report.detail_mermaid ?? report.mermaid} label="Copy source" />
              <a href={getArchitectureDownloadUrl(report.architecture_id, "mermaid")} className="btn btn-secondary btn-sm">
                <Icon name="upload" className="icon-download" aria-hidden="true" />.mmd
              </a>
              <a href={getArchitectureDownloadUrl(report.architecture_id, "json")} className="btn btn-secondary btn-sm">
                <Icon name="upload" className="icon-download" aria-hidden="true" />JSON
              </a>
            </div>
          </div>
          <div className="architecture-diagram">
            <MermaidView
              source={report.detail_mermaid ?? report.mermaid}
              id={`${report.architecture_id}-detail`}
              fallback={<p className="field-hint">See the components below for the same information.</p>}
            />
          </div>
          {report.detail_mermaid && report.mermaid && (
            <details className="disclosure">
              <summary><Icon name="chevron" aria-hidden="true" />Simple overview diagram</summary>
              <div className="stack detail-diagram">
                <div className="actions">
                  <CopyButton text={report.mermaid} label="Copy source" />
                </div>
                <div className="architecture-diagram">
                  <MermaidView
                    source={report.mermaid}
                    id={`${report.architecture_id}-overview`}
                    fallback={<pre className="source-block">{report.mermaid}</pre>}
                  />
                </div>
              </div>
            </details>
          )}
        </section>
      ) : (
        report.status === "complete" && (
          <p className="field-hint">No diagram for this repository; the components below describe it.</p>
        )
      )}

      {stackItems.length > 0 && (
        <section className="section" aria-labelledby="stack-heading">
          <h3 id="stack-heading" className="headline">Technology stack</h3>
          <div className="badges">
            {stackItems.map((item) => <Badge key={item}>{item}</Badge>)}
          </div>
          {stack.unsupported_languages.length > 0 && (
            <p className="field-hint">Not statically parsed: {stack.unsupported_languages.join(", ")}</p>
          )}
        </section>
      )}

      {report.entrypoints.length > 0 && (
        <section className="section" aria-labelledby="entry-heading">
          <h3 id="entry-heading" className="headline">Entrypoints</h3>
          <ul className="grouped-list">
            {report.entrypoints.map((e, i) => (
              <li key={i}>
                <div className="row-title">
                  <code className="code-ref">{e.path}</code>
                  <Badge>{e.kind.replace(/_/g, " ")}</Badge>
                </div>
                {e.detail && <p>{e.detail}</p>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {report.components.length > 0 && (
        <section className="section" aria-labelledby="components-heading">
          <div className="section-head">
            <h3 id="components-heading" className="headline">Components</h3>
            <span className="field-hint">Percentages are confidence</span>
          </div>
          <div className="component-grid">
            {report.components.map((c) => <ComponentCard key={c.id} c={c} />)}
          </div>
        </section>
      )}

      {report.relationships.length > 0 && (
        <section className="section" aria-labelledby="relations-heading">
          <h3 id="relations-heading" className="headline">Relationships</h3>
          <ul className="grouped-list relation-list">
            {report.relationships.map((r, i) => <RelationshipRow key={i} r={r} labelFor={componentLabel} />)}
          </ul>
        </section>
      )}

      {hasSurroundings && (
        <section className="section" aria-labelledby="around-heading">
          <h3 id="around-heading" className="headline">Around the code</h3>
          <div className="surroundings">
            {report.data_stores.length > 0 && (
              <div className="card">
                <h4>Data stores</h4>
                <ul>{report.data_stores.map((d, i) => <li key={i}>{d.name}</li>)}</ul>
              </div>
            )}
            {report.external_services.length > 0 && (
              <div className="card">
                <h4>External services</h4>
                <ul>{report.external_services.map((e, i) => <li key={i}>{e.name}</li>)}</ul>
              </div>
            )}
            {report.deployment.length > 0 && (
              <div className="card">
                <h4>Deployment</h4>
                <ul>
                  {report.deployment.map((d, i) => (
                    <li key={i}>
                      <code className="code-ref">{d.path}</code>
                      {d.services.length > 0 && <span> — {d.services.join(", ")}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>
      )}

      {report.limitations.length > 0 && (
        <section className="section" aria-labelledby="limits-heading">
          <h3 id="limits-heading" className="headline">Limitations</h3>
          <ul className="grouped-list">
            {report.limitations.map((l, i) => <li key={i}><p>{l}</p></li>)}
          </ul>
        </section>
      )}

      {report.coverage && (
        <details className="disclosure">
          <summary><Icon name="chevron" aria-hidden="true" />Coverage details</summary>
          <dl className="facts coverage-facts">
            <div><dt>Files discovered</dt><dd>{report.coverage.files_discovered}</dd></div>
            <div><dt>Source files recognised</dt><dd>{report.coverage.source_files_discovered}</dd></div>
            <div><dt>Parsed</dt><dd>{report.coverage.files_parsed} of {report.coverage.source_files_supported}</dd></div>
            <div><dt>Tier</dt><dd>{report.coverage.tier}</dd></div>
            {report.coverage.limit_exceeded && (
              <div><dt>Limit exceeded</dt><dd className="code-ref">{report.coverage.limit_exceeded}</dd></div>
            )}
          </dl>
        </details>
      )}
    </>
  );
}

export default function ArchitecturePage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <ArchitectureContent />
    </Suspense>
  );
}
