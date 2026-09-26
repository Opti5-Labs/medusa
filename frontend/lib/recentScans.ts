import type { ScanResult } from "./api";

export interface RecentScan {
  id: string;
  name: string;
  source: ScanResult["repo_source"];
  issues: number;
  files: number;
  createdAt: number;
}
const INDEX_KEY = "medusa:recent-scans";
const MAX_AGE = 30 * 60 * 1000;

export function readRecentScans(): RecentScan[] {
  try {
    const entries = JSON.parse(sessionStorage.getItem(INDEX_KEY) ?? "[]");
    if (!Array.isArray(entries)) return [];
    return entries.filter((entry: RecentScan) =>
      typeof entry.id === "string" && typeof entry.name === "string" &&
      Date.now() - entry.createdAt < MAX_AGE && sessionStorage.getItem(`scan:${entry.id}`)
    ).slice(0, 5);
  } catch { return []; }
}

export function rememberScan(result: ScanResult, name: string) {
  sessionStorage.setItem(`scan:${result.scan_id}`, JSON.stringify(result));
  const entry: RecentScan = {
    id: result.scan_id, name, source: result.repo_source,
    issues: result.issues.length, files: result.files_scanned.length, createdAt: Date.now(),
  };
  try {
    sessionStorage.setItem(INDEX_KEY, JSON.stringify([entry, ...readRecentScans().filter((item) => item.id !== entry.id)].slice(0, 5)));
  } catch { /* The scan still works when the optional history cannot be stored. */ }
}

/** When a scan's session ends (the backend deletes runs after 30 minutes). */
export function expiresAt(scan: RecentScan): number {
  return scan.createdAt + MAX_AGE;
}

/** Forget every recent scan in this tab, including the stored results. */
export function clearRecentScans() {
  try {
    for (const scan of readRecentScans()) sessionStorage.removeItem(`scan:${scan.id}`);
    sessionStorage.removeItem(INDEX_KEY);
  } catch { /* Nothing to clear when storage is unavailable. */ }
}
