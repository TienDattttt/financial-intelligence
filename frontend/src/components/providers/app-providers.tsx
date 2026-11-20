"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React, { createContext, useCallback, useContext, useMemo, useState } from "react";
import { useHealth, useMetaPeriods } from "@/hooks/use-finintel-queries";
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
}

const AppContext = createContext<AppContextValue | null>(null);

function AppStateInner({ children }: { children: React.ReactNode }) {
  const [statementScope, setStatementScopeState] =
    useState<StatementScope>("CONSOLIDATED");
  const [periodType, setPeriodTypeState] = useState<PeriodType>("QUARTER");
  const [selectedPeriodEndDate, setSelectedPeriodEndDate] = useState<string | null>(null);
  const [sourceTarget, setSourceTarget] = useState<SourceLookupTarget | null>(null);
  const [commandOpen, setCommandOpen] = useState(false);

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
      setPeriodType,
      setStatementScope,
      snapshotId,
      sourceTarget,
      statementScope,
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
