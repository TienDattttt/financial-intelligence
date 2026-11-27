"use client";

import * as React from "react";
import {
  QueryClient,
  QueryClientContext,
  useMutation,
  useQuery,
} from "@tanstack/react-query";
import { authApi, workspaceApi } from "@/lib/auth-api";

export const AUTH_SESSION_QUERY_KEY = ["auth", "session"] as const;
export const WORKSPACE_WATCHLISTS_QUERY_KEY = ["workspace", "watchlists"] as const;

const FALLBACK_QUERY_CLIENT = new QueryClient({
  defaultOptions: { queries: { retry: false, enabled: false } },
});

export function useOptionalQueryClient(): QueryClient | undefined {
  return React.useContext(QueryClientContext);
}

export function useAuthSession() {
  const client = useOptionalQueryClient();
  return useQuery(
    {
      queryKey: AUTH_SESSION_QUERY_KEY,
      queryFn: ({ signal }) => authApi.getSessionStatus(signal),
      enabled: client !== undefined,
      retry: false,
      staleTime: 30_000,
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}

export function useServerWatchlists(enabled: boolean) {
  const client = useOptionalQueryClient();
  return useQuery(
    {
      queryKey: WORKSPACE_WATCHLISTS_QUERY_KEY,
      queryFn: ({ signal }) => workspaceApi.listWatchlists(signal),
      enabled: enabled && client !== undefined,
      retry: false,
      staleTime: 15_000,
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}

export function useTickerAnalystNotes(ticker: string | null, enabled: boolean) {
  const client = useOptionalQueryClient();
  const cleanTicker = (ticker ?? "").trim().toUpperCase();
  return useQuery(
    {
      queryKey: ["workspace", "notes", cleanTicker],
      queryFn: ({ signal }) => workspaceApi.listNotes(cleanTicker, signal),
      enabled: enabled && client !== undefined && cleanTicker.length > 0,
      retry: false,
      staleTime: 15_000,
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}

export function useAuthSessionsList(enabled: boolean) {
  const client = useOptionalQueryClient();
  return useQuery(
    {
      queryKey: ["auth", "sessions"],
      queryFn: ({ signal }) => authApi.getSessions(signal),
      enabled: enabled && client !== undefined,
      retry: false,
      staleTime: 15_000,
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}

export function useMyAuditActivity(enabled: boolean) {
  const client = useOptionalQueryClient();
  return useQuery(
    {
      queryKey: ["audit", "my-activity"],
      queryFn: ({ signal }) => authApi.getMyActivity(30, 0, signal),
      enabled: enabled && client !== undefined,
      retry: false,
      staleTime: 15_000,
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}

export function useInvalidateAuthAndWorkspace() {
  const queryClient = useOptionalQueryClient();
  return async () => {
    if (!queryClient) return;
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: AUTH_SESSION_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: WORKSPACE_WATCHLISTS_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: ["auth", "sessions"] }),
      queryClient.invalidateQueries({ queryKey: ["audit", "my-activity"] }),
    ]);
  };
}

export function useLogoutMutation() {
  const invalidateAll = useInvalidateAuthAndWorkspace();
  return useMutation({
    mutationFn: () => authApi.logout(),
    onSuccess: async () => {
      await invalidateAll();
    },
  });
}
