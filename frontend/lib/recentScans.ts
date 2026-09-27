import { getScansStatus, type ScanResult } from "./api";

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
  // Display name read by the Issues page title (a client-side label; the contract has no name field).
  sessionStorage.setItem(`scan:${result.scan_id}:name`, name);
  const entry: RecentScan = {
    id: result.scan_id, name, source: result.repo_source,
    issues: result.issues.length, files: result.files_scanned.length, createdAt: Date.now(),
  };
  try {
    sessionStorage.setItem(INDEX_KEY, JSON.stringify([entry, ...readRecentScans().filter((item) => item.id !== entry.id)].slice(0, 5)));
  } catch { /* The scan still works when the optional history cannot be stored. */ }
}

/** Why a remembered scan is gone from the server. */
export type GoneReason = "restarted" | "expired";

function readIndex(): RecentScan[] {
  try {
    const entries = JSON.parse(sessionStorage.getItem(INDEX_KEY) ?? "[]");
    return Array.isArray(entries) ? entries : [];
  } catch { return []; }
}

/** Forget one scan in this tab: its stored result, name and history entry. */
export function forgetScan(id: string) {
  try {
    sessionStorage.removeItem(`scan:${id}`);
    sessionStorage.removeItem(`scan:${id}:name`);
    sessionStorage.setItem(INDEX_KEY, JSON.stringify(readIndex().filter((entry) => entry.id !== id)));
  } catch { /* Nothing to forget when storage is unavailable. */ }
}

/**
 * Ask the server which of these scans it still has and forget the rest here.
 * Scans live only in the server's memory, so a restart loses them before
 * their 30 minutes are up. Returns why each missing scan is gone. When the
 * server can't be reached nothing is forgotten.
 */
export async function pruneScans(ids: string[]): Promise<Record<string, GoneReason>> {
  if (ids.length === 0) return {};
  let status;
  try {
    status = await getScansStatus(ids);
  } catch {
    return {};
  }
  const created = new Map(readIndex().map((entry) => [entry.id, entry.createdAt]));
  const gone: Record<string, GoneReason> = {};
  for (const id of ids) {
    if (status.alive.includes(id)) continue;
    const at = created.get(id);
    gone[id] = at !== undefined && at < status.server_started_at * 1000 ? "restarted" : "expired";
    forgetScan(id);
  }
  return gone;
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
