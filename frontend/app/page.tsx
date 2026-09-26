import Link from "next/link";
import DemoButton from "./components/DemoButton";

const STEPS = [
  {
    title: "Scan",
    body: "Granite reviews the code and GitHub Issues are pulled in. If Granite is unavailable, IBM Bob reviews the code instead.",
  },
  {
    title: "Reproduce",
    body: "On the demo, a failing test runs against the original code in a locked-down container. Bob and Granite then diagnose the failure independently.",
  },
  {
    title: "Debug race",
    body: "When a sandbox harness exists, two to six candidate fixes from Bob, Granite and prepared strategies each run in their own sandbox, in parallel.",
  },
  {
    title: "Verify and recommend",
    body: "Plain code, not a model, checks each fix: reproducer fixed, no regressions, smallest change. The winner can be downloaded.",
  },
];

const ROLES = [
  {
    name: "IBM Bob",
    body: "Reads the relevant files, traces the failure, proposes fixes. Read-only, capped at 0.25 Bobcoins and 6 turns per run.",
  },
  {
    name: "Granite on watsonx.ai",
    body: "Scans code in chunks and runs a second, independent investigation: runtime, repository and skeptic, then a synthesis.",
  },
  {
    name: "The sandbox",
    body: "The only place code runs: no network, no credentials, read-only, 90 seconds, then deleted. Its results decide what works.",
  },
];

export default function Home() {
  return (
    <div className="space-y-20 sm:space-y-24">
      {/* Hero */}
      <section className="grid gap-10 lg:grid-cols-[1fr_1.05fr] lg:items-center">
        <div className="space-y-6">
          <h1 className="text-4xl sm:text-5xl font-semibold tracking-tight leading-[1.05] text-balance">
            Find the bug, prove it, fix it.
          </h1>
          <p className="text-lg text-gray-600 dark:text-gray-400 max-w-md leading-relaxed">
            Medusa reproduces a defect in an isolated sandbox, races candidate fixes against the
            tests, and recommends the one that actually passes.
          </p>

          <div className="space-y-3 pt-2">
            <DemoButton />
            <div className="flex flex-col sm:flex-row gap-3">
              <EntryLink href="/scan/github" title="Link a GitHub repository" note="Public repos, up to 50 MB" />
              <EntryLink href="/scan/upload" title="Upload a zip" note="Up to 20 MB" />
            </div>
            <p className="text-xs text-gray-500 dark:text-gray-400 max-w-md">
              The demo includes one sandboxed fix race and five real historical issues analysed as text.
              Linked repositories and zips are marked as not executed.
            </p>
          </div>
        </div>

        <Transcript />
      </section>

      {/* How a run works: a real sequence */}
      <section className="space-y-8">
        <h2 className="text-2xl font-semibold tracking-tight">How a run works</h2>
        <ol className="grid gap-x-8 gap-y-8 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((step, i) => (
            <li key={step.title} className="space-y-2 border-t-2 border-gray-900 dark:border-gray-100 pt-4">
              <p className="font-mono text-sm text-verdigris-700 dark:text-verdigris-300">{i + 1}</p>
              <h3 className="font-semibold">{step.title}</h3>
              <p className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">{step.body}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* Who does what */}
      <section className="grid gap-8 lg:grid-cols-[1fr_2fr]">
        <div className="space-y-3">
          <h2 className="text-2xl font-semibold tracking-tight">Models investigate. Tests decide.</h2>
          <p className="text-gray-600 dark:text-gray-400 leading-relaxed">
            Bob and Granite never see each other&apos;s diagnosis, and their confidence is shown but
            never used to pick a fix.
          </p>
        </div>
        <dl className="divide-y divide-gray-200 dark:divide-gray-800 border-y border-gray-200 dark:border-gray-800">
          {ROLES.map((role) => (
            <div key={role.name} className="grid gap-1 sm:grid-cols-[11rem_1fr] sm:gap-6 py-4">
              <dt className="font-medium">{role.name}</dt>
              <dd className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">{role.body}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}

function EntryLink({ href, title, note }: { href: string; title: string; note: string }) {
  return (
    <Link
      href={href}
      className="flex-1 rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-4 py-3 hover:border-gray-900 dark:hover:border-gray-300 transition-colors"
    >
      <span className="block font-medium">{title}</span>
      <span className="block text-xs text-gray-500 dark:text-gray-400">{note}</span>
    </Link>
  );
}

/** The demo bug's real reproducer output: the moment Medusa exists for. */
function Transcript() {
  return (
    <figure className="rounded-xl bg-gray-900 dark:bg-gray-900 text-gray-100 shadow-[0_1px_0_0_rgba(255,255,255,0.06)_inset] ring-1 ring-gray-800 overflow-hidden">
      <div className="px-4 py-2.5 border-b border-gray-800 text-xs text-gray-400">OptiLearn sandbox, network off</div>
      <pre className="px-4 py-4 font-mono text-[12.5px] leading-6 overflow-x-auto">
        <code>
          <span className="text-gray-400">$ run reproducer against original code{"\n"}</span>
          <span className="text-gray-300">PASS  test_existing_local_folder_is_used{"\n"}</span>
          <span className="text-gray-300">PASS  test_custom_hub_id_is_not_overridden{"\n"}</span>
          <span className="verdict-line text-red-400" style={{ animationDelay: "250ms" }}>
            FAIL  test_missing_local_whisper_path_resolves_to_valid_model{"\n"}
          </span>
          <span className="text-gray-500">      returned &apos;./models/whisper/openai-whisper-tiny&apos;{"\n"}</span>
          <span className="text-gray-500">      not a folder, not a valid Hub id{"\n\n"}</span>
          <span className="text-gray-400">$ run candidate c1 (+4 −0){"\n"}</span>
          <span className="verdict-line text-emerald-400" style={{ animationDelay: "900ms" }}>
            PASSED: reproducer fixed, 7/7 checks pass, no regressions
          </span>
        </code>
      </pre>
      <figcaption className="px-4 py-2.5 border-t border-gray-800 text-xs text-gray-400">
        A real bug in OptiLearn&apos;s speech-to-text fallback, reproduced and fixed in the demo.
      </figcaption>
    </figure>
  );
}
