"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import { useHealth, useMetaPeriods } from "@/hooks/use-finintel-queries";
import {
  resolveThemePreference,
  THEME_STORAGE_KEY,
  type ResolvedTheme,
  type ThemePreference,
} from "@/lib/echarts-theme";
import type { PeriodType, PeriodView, RatioName, StatementScope } from "@/types/api";

export interface SourceLookupTarget {
  ticker: string;
  ratioName: RatioName;
  statementScope: StatementScope;
  periodType: PeriodType;
  periodEndDate?: string | null;
}

interface AppContextValue {
  statementScope: StatementScope;
  setStatementScope: (scope: StatementScope) => void;
  periodType: PeriodType;
  setPeriodType: (pt: PeriodType) => void;
  periodEndDate: string | null;
  setPeriodEndDate: (dateStr: string | null) => void;
  periodView: PeriodView;
  snapshotId: string | null;
  publishedAt: string | null;
  availableDates: string[];
  sectorsLevel2: { icb_code_level2: string; icb_name_level2: string; issuer_count: number }[];
  sourceTarget: SourceLookupTarget | null;
  openSourceDrawer: (target: SourceLookupTarget) => void;
  closeSourceDrawer: () => void;
  commandOpen: boolean;
  setCommandOpen: (open: boolean) => void;
  themePreference: ThemePreference;
  resolvedTheme: ResolvedTheme;
  setThemePreference: (next: ThemePreference) => void;
}

const AppContext = createContext<AppContextValue | null>(null);

const themeListeners = new Set<() => void>();
let cachedThemePref: ThemePreference | null = null;

function isValidThemePreference(val: string | null): val is ThemePreference {
  return val === "light" || val === "dark" || val === "system";
}

function subscribeTheme(callback: () => void): () => void {
  themeListeners.add(callback);
  if (typeof window === "undefined") {
    return () => themeListeners.delete(callback);
  }
  const onStorage = (ev: StorageEvent) => {
    if (ev.key === THEME_STORAGE_KEY) {
      cachedThemePref = isValidThemePreference(ev.newValue) ? ev.newValue : "light";
      callback();
    }
  };
  const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
  const onMediaChange = () => callback();

  window.addEventListener("storage", onStorage);
  mq?.addEventListener("change", onMediaChange);
  return () => {
    themeListeners.delete(callback);
    window.removeEventListener("storage", onStorage);
    mq?.removeEventListener("change", onMediaChange);
  };
}

function getThemePreferenceSnapshot(): ThemePreference {
  if (typeof window === "undefined") return "light";
  if (cachedThemePref) return cachedThemePref;
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    cachedThemePref = isValidThemePreference(raw) ? raw : "light";
  } catch {
    cachedThemePref = "light";
  }
  return cachedThemePref;
}

function getThemePreferenceServerSnapshot(): ThemePreference {
  return "light";
}

function getSystemDarkSnapshot(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function getSystemDarkServerSnapshot(): boolean {
  return false;
}

function applyResolvedThemeToDocument(resolved: ResolvedTheme): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (resolved === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
  root.setAttribute("data-theme", resolved);
  root.style.colorScheme = resolved;
}

function persistThemePreference(next: ThemePreference): void {
  cachedThemePref = next;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Ignore storage quota / privacy mode errors
    }
  }
  const systemDark = getSystemDarkSnapshot();
  applyResolvedThemeToDocument(resolveThemePreference(next, systemDark));
  for (const fn of themeListeners) fn();
}

function AppStateInner({ children }: { children: React.ReactNode }) {
  const [statementScope, setStatementScopeState] =
    useState<StatementScope>("CONSOLIDATED");
  const [periodType, setPeriodTypeState] = useState<PeriodType>("QUARTER");
  const [selectedPeriodEndDate, setSelectedPeriodEndDate] = useState<string | null>(null);
  const [sourceTarget, setSourceTarget] = useState<SourceLookupTarget | null>(null);
  const [commandOpen, setCommandOpen] = useState(false);

  const themePreference = useSyncExternalStore(
    subscribeTheme,
    getThemePreferenceSnapshot,
    getThemePreferenceServerSnapshot,
  );
  const systemDark = useSyncExternalStore(
    subscribeTheme,
    getSystemDarkSnapshot,
    getSystemDarkServerSnapshot,
  );
  const resolvedTheme = resolveThemePreference(themePreference, systemDark);

  useEffect(() => {
    applyResolvedThemeToDocument(resolvedTheme);
  }, [resolvedTheme]);

  const setThemePreference = useCallback((next: ThemePreference) => {
    persistThemePreference(next);
  }, []);

  const health = useHealth();
  const snapshotId = health.data?.active_snapshot_id ?? null;
  const metaPeriods = useMetaPeriods(snapshotId);

  const availableDates = useMemo(() => {
    const scopeMap = metaPeriods.data?.available_periods?.[statementScope];
    return scopeMap?.[periodType] ?? [];
  }, [metaPeriods.data, periodType, statementScope]);

  const effectivePeriodEndDate = useMemo(() => {
    if (selectedPeriodEndDate && availableDates.includes(selectedPeriodEndDate)) {
      return selectedPeriodEndDate;
    }
    return availableDates[0] ?? null;
  }, [availableDates, selectedPeriodEndDate]);

  const setStatementScope = useCallback((next: StatementScope) => {
    setStatementScopeState(next);
    setSelectedPeriodEndDate(null);
  }, []);

  const setPeriodType = useCallback((next: PeriodType) => {
    setPeriodTypeState(next);
    setSelectedPeriodEndDate(null);
  }, []);

  const openSourceDrawer = useCallback((target: SourceLookupTarget) => {
    setSourceTarget(target);
  }, []);

  const closeSourceDrawer = useCallback(() => {
    setSourceTarget(null);
  }, []);

  const periodView: PeriodView = periodType === "ANNUAL" ? "Annual" : "TTM";

  const value = useMemo<AppContextValue>(
    () => ({
      statementScope,
      setStatementScope,
      periodType,
      setPeriodType,
      periodEndDate: effectivePeriodEndDate,
      setPeriodEndDate: setSelectedPeriodEndDate,
      periodView,
      snapshotId,
      publishedAt: metaPeriods.data?.published_at ?? null,
      availableDates,
      sectorsLevel2: metaPeriods.data?.sectors_level2 ?? [],
      sourceTarget,
      openSourceDrawer,
      closeSourceDrawer,
      commandOpen,
      setCommandOpen,
      themePreference,
      resolvedTheme,
      setThemePreference,
    }),
    [
      availableDates,
      closeSourceDrawer,
      commandOpen,
      effectivePeriodEndDate,
      metaPeriods.data,
      openSourceDrawer,
      periodType,
      periodView,
      resolvedTheme,
      setPeriodType,
      setStatementScope,
      setThemePreference,
      snapshotId,
      sourceTarget,
      statementScope,
      themePreference,
    ],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppState(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) {
    throw new Error("useAppState must be used within AppProviders");
  }
  return ctx;
}

export function AppProviders({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AppStateInner>{children}</AppStateInner>
    </QueryClientProvider>
  );
}
