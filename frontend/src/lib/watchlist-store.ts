import { z } from "zod";

const WATCHLIST_KEY = "finintel:watchlist:v1";
const DENSITY_KEY = "finintel:density:v1";
const LAST_TICKER_KEY = "finintel:last-ticker:v1";
const MAX_WATCHLIST_SIZE = 50;

export type TableDensity = "compact" | "comfortable";

const TickerSchema = z.string().regex(/^[A-Z0-9]{1,10}$/);
const WatchlistSchema = z.array(TickerSchema).max(MAX_WATCHLIST_SIZE);

const listeners = new Set<() => void>();
let cachedWatchlist: string[] = [];
let watchlistHydrated = false;
let cachedDensity: TableDensity = "compact";
let densityHydrated = false;
let cachedLastTicker: string | null = null;
let lastTickerHydrated = false;

function notify() {
  for (const fn of listeners) fn();
}

export function subscribePreferences(callback: () => void): () => void {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

export function getWatchlistSnapshot(): string[] {
  if (typeof window === "undefined") return [];
  if (watchlistHydrated) return cachedWatchlist;
  try {
    const raw = window.localStorage.getItem(WATCHLIST_KEY);
    if (raw) {
      const parsed = WatchlistSchema.safeParse(JSON.parse(raw));
      if (parsed.success) {
        cachedWatchlist = Array.from(new Set(parsed.data));
      }
    }
  } catch {
    cachedWatchlist = [];
  }
  watchlistHydrated = true;
  return cachedWatchlist;
}

const EMPTY_WATCHLIST: string[] = [];

export function getWatchlistServerSnapshot(): string[] {
  return EMPTY_WATCHLIST;
}

function saveWatchlist(next: string[]) {
  const unique = Array.from(
    new Set(
      next
        .map((t) => t.trim().toUpperCase())
        .filter((t) => /^[A-Z0-9]{1,10}$/.test(t)),
    ),
  ).slice(0, MAX_WATCHLIST_SIZE);
  cachedWatchlist = unique;
  watchlistHydrated = true;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(WATCHLIST_KEY, JSON.stringify(unique));
    } catch {
      // Ignore storage quota errors cleanly
    }
  }
  notify();
}

export function toggleWatchlistTicker(ticker: string): void {
  const clean = ticker.trim().toUpperCase();
  if (!/^[A-Z0-9]{1,10}$/.test(clean)) return;
  const current = getWatchlistSnapshot();
  if (current.includes(clean)) {
    saveWatchlist(current.filter((t) => t !== clean));
  } else {
    saveWatchlist([clean, ...current]);
  }
}

export function addWatchlistTicker(ticker: string): void {
  const clean = ticker.trim().toUpperCase();
  if (!/^[A-Z0-9]{1,10}$/.test(clean)) return;
  const current = getWatchlistSnapshot();
  if (!current.includes(clean)) {
    saveWatchlist([clean, ...current]);
  }
}

export function removeWatchlistTicker(ticker: string): void {
  const clean = ticker.trim().toUpperCase();
  saveWatchlist(getWatchlistSnapshot().filter((t) => t !== clean));
}

export function exportWatchlistJson(): string {
  return JSON.stringify(getWatchlistSnapshot(), null, 2);
}

export function importWatchlistJson(rawJson: string): { ok: true; count: number } | { ok: false; error: string } {
  try {
    const parsed = WatchlistSchema.safeParse(JSON.parse(rawJson));
    if (!parsed.success) {
      return {
        ok: false,
        error: "Định dạng danh sách mã không hợp lệ (cần mảng mã cổ phiếu viết hoa, tối đa 50 mã).",
      };
    }
    saveWatchlist(parsed.data);
    return { ok: true, count: getWatchlistSnapshot().length };
  } catch {
    return { ok: false, error: "Không thể đọc tệp JSON danh sách theo dõi." };
  }
}

export function getDensitySnapshot(): TableDensity {
  if (typeof window === "undefined") return "compact";
  if (densityHydrated) return cachedDensity;
  try {
    const raw = window.localStorage.getItem(DENSITY_KEY);
    if (raw === "comfortable" || raw === "compact") {
      cachedDensity = raw;
    }
  } catch {
    cachedDensity = "compact";
  }
  densityHydrated = true;
  return cachedDensity;
}

export function getDensityServerSnapshot(): TableDensity {
  return "compact";
}

export function setTableDensity(density: TableDensity): void {
  cachedDensity = density;
  densityHydrated = true;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(DENSITY_KEY, density);
    } catch {
      // Ignore storage errors
    }
  }
  notify();
}

export function getLastViewedTickerSnapshot(): string | null {
  if (typeof window === "undefined") return null;
  if (lastTickerHydrated) return cachedLastTicker;
  try {
    const raw = window.localStorage.getItem(LAST_TICKER_KEY);
    if (raw) {
      const clean = raw.trim().toUpperCase();
      if (TickerSchema.safeParse(clean).success) {
        cachedLastTicker = clean;
      }
    }
  } catch {
    cachedLastTicker = null;
  }
  lastTickerHydrated = true;
  return cachedLastTicker;
}

export function getLastViewedTickerServerSnapshot(): string | null {
  return null;
}

export function setLastViewedTicker(ticker: string): void {
  const clean = ticker.trim().toUpperCase();
  if (!TickerSchema.safeParse(clean).success) return;
  if (cachedLastTicker === clean && lastTickerHydrated) return;
  cachedLastTicker = clean;
  lastTickerHydrated = true;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(LAST_TICKER_KEY, clean);
    } catch {
      // Ignore storage errors
    }
  }
  notify();
}
