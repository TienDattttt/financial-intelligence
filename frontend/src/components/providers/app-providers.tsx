"use client";

import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  shouldRetryFinintelQuery,
  useHealth,
  useMetaPeriods,
} from "@/hooks/use-finintel-queries";
import {
  subscribeObservedSnapshotId,
  type DataFreshnessItem,
} from "@/lib/api-client";
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

export interface AppContextValue {
  statementScope: StatementScope;
  setStatementScope: (scope: StatementScope) => void;
  periodType: PeriodType;
  setPeriodType: (pt: PeriodType) => void;
  periodEndDate: string | null;
  queryPeriodEndDate: string | null;
  setPeriodEndDate: (dateStr: string | null) => void;
  periodView: PeriodView;
  snapshotId: string | null;
  cacheSnapshotKey: string;
  ragAvailable: boolean;
  hasSeparateData: boolean;
  publishedAt: string | null;
  freshness: DataFreshnessItem | null;
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

  const queryClient = useQueryClient();
  const health = useHealth();
  const [observedSnapshotId, setObservedSnapshotId] = useState<string | null>(null);
  const snapshotId = observedSnapshotId ?? health.data?.active_snapshot_id ?? null;
  const activeSnapshotRef = useRef<string | null>(snapshotId);
  useEffect(() => {
    activeSnapshotRef.current = snapshotId;
  }, [snapshotId]);

  const [initialSnapshotId, setInitialSnapshotId] = useState<string | null>(null);
  if (snapshotId && initialSnapshotId === null) {
    setInitialSnapshotId(snapshotId);
  }
  const cacheSnapshotKey =
    snapshotId && initialSnapshotId && snapshotId !== initialSnapshotId
      ? snapshotId
      : "active";

  useEffect(() => {
    return subscribeObservedSnapshotId((nextSnapId) => {
      if (nextSnapId && nextSnapId !== activeSnapshotRef.current) {
        activeSnapshotRef.current = nextSnapId;
        setObservedSnapshotId(nextSnapId);
        void queryClient.invalidateQueries({ queryKey: ["health"] });
        void queryClient.invalidateQueries({ queryKey: ["meta-periods"] });
      }
    }, 150);
  }, [queryClient]);

  const metaPeriods = useMetaPeriods(cacheSnapshotKey);

  const ragAvailable = health.data?.rag_available ?? false;

  const hasSeparateData = useMemo(() => {
    if (!metaPeriods.data) return true;
    const sep = metaPeriods.data.available_periods?.SEPARATE;
    const qLen = sep?.QUARTER?.length ?? 0;
    const aLen = sep?.ANNUAL?.length ?? 0;
    return qLen > 0 || aLen > 0;
  }, [metaPeriods.data]);

  const effectiveStatementScope: StatementScope =
    !hasSeparateData && statementScope === "SEPARATE"
      ? "CONSOLIDATED"
      : statementScope;

  const availableDates = useMemo(() => {
    const scopeMap = metaPeriods.data?.available_periods?.[effectiveStatementScope];
    return scopeMap?.[periodType] ?? [];
  }, [effectiveStatementScope, metaPeriods.data, periodType]);

  const effectivePeriodEndDate = useMemo(() => {
    if (selectedPeriodEndDate && availableDates.includes(selectedPeriodEndDate)) {
      return selectedPeriodEndDate;
    }
    return availableDates[0] ?? null;
  }, [availableDates, selectedPeriodEndDate]);

  const queryPeriodEndDate = useMemo(() => {
    if (!selectedPeriodEndDate) return null;
    if (availableDates.length > 0 && !availableDates.includes(selectedPeriodEndDate)) {
      return null;
    }
    if (availableDates[0] && selectedPeriodEndDate === availableDates[0]) {
      return null;
    }
    return selectedPeriodEndDate;
  }, [availableDates, selectedPeriodEndDate]);

  const setStatementScope = useCallback(
    (next: StatementScope) => {
      if (next === "SEPARATE" && !hasSeparateData) {
        setStatementScopeState("CONSOLIDATED");
        setSelectedPeriodEndDate(null);
        return;
      }
      setStatementScopeState(next);
      setSelectedPeriodEndDate(null);
    },
    [hasSeparateData],
  );

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
      statementScope: effectiveStatementScope,
      setStatementScope,
      periodType,
      setPeriodType,
      periodEndDate: effectivePeriodEndDate,
      queryPeriodEndDate,
      setPeriodEndDate: setSelectedPeriodEndDate,
      periodView,
      snapshotId,
      cacheSnapshotKey,
      ragAvailable,
      hasSeparateData,
      publishedAt: metaPeriods.data?.published_at ?? null,
      freshness: metaPeriods.data?.freshness ?? null,
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
      cacheSnapshotKey,
      closeSourceDrawer,
      commandOpen,
      effectivePeriodEndDate,
      effectiveStatementScope,
      hasSeparateData,
      metaPeriods.data,
      openSourceDrawer,
      periodType,
      periodView,
      queryPeriodEndDate,
      ragAvailable,
      resolvedTheme,
      setPeriodType,
      setStatementScope,
      setThemePreference,
      snapshotId,
      sourceTarget,
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
            retry: shouldRetryFinintelQuery,
            staleTime: 60_000,
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
