"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  QueryClient,
} from "@tanstack/react-query";
import { useOptionalQueryClient } from "@/hooks/use-auth-workspace";
import { alertsApi } from "@/lib/alerts-api";
import type {
  AlertSubscriptionUpdateRequest,
  WebhookTestRequest,
} from "@/types/api";

export const ALERTS_NOTIFICATIONS_KEY = ["alerts", "notifications"] as const;
export const ALERTS_SUBSCRIPTION_KEY = ["alerts", "subscription"] as const;

const FALLBACK_QUERY_CLIENT = new QueryClient({
  defaultOptions: { queries: { retry: false, enabled: false } },
});

export function useAlertNotifications(
  enabled: boolean,
  opts?: { unreadOnly?: boolean; limit?: number; offset?: number },
) {
  const client = useOptionalQueryClient();
  const unreadOnly = opts?.unreadOnly ?? false;
  const limit = opts?.limit ?? 30;
  const offset = opts?.offset ?? 0;

  return useQuery(
    {
      queryKey: [...ALERTS_NOTIFICATIONS_KEY, { unreadOnly, limit, offset }],
      queryFn: ({ signal }) =>
        alertsApi.listNotifications({ unreadOnly, limit, offset }, signal),
      enabled: enabled && client !== undefined,
      retry: false,
      staleTime: 15_000,
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}

export function useAlertSubscription(enabled: boolean) {
  const client = useOptionalQueryClient();
  return useQuery(
    {
      queryKey: ALERTS_SUBSCRIPTION_KEY,
      queryFn: ({ signal }) => alertsApi.getSubscription(signal),
      enabled: enabled && client !== undefined,
      retry: false,
      staleTime: 30_000,
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}

export function useMarkNotificationReadMutation() {
  const client = useOptionalQueryClient();
  return useMutation(
    {
      mutationFn: (notificationId: string) =>
        alertsApi.markNotificationRead(notificationId),
      onSuccess: async () => {
        if (client) {
          await client.invalidateQueries({ queryKey: ALERTS_NOTIFICATIONS_KEY });
        }
      },
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}

export function useMarkAllNotificationsReadMutation() {
  const client = useOptionalQueryClient();
  return useMutation(
    {
      mutationFn: () => alertsApi.markAllRead(),
      onSuccess: async () => {
        if (client) {
          await client.invalidateQueries({ queryKey: ALERTS_NOTIFICATIONS_KEY });
        }
      },
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}

export function useUpdateAlertSubscriptionMutation() {
  const client = useOptionalQueryClient();
  return useMutation(
    {
      mutationFn: (payload: AlertSubscriptionUpdateRequest) =>
        alertsApi.updateSubscription(payload),
      onSuccess: async () => {
        if (client) {
          await client.invalidateQueries({ queryKey: ALERTS_SUBSCRIPTION_KEY });
        }
      },
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}

export function useTestAlertWebhookMutation() {
  const client = useOptionalQueryClient();
  return useMutation(
    {
      mutationFn: (payload: WebhookTestRequest = {}) =>
        alertsApi.testWebhook(payload),
    },
    client ?? FALLBACK_QUERY_CLIENT,
  );
}
