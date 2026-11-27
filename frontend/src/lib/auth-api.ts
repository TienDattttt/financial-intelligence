import { ApiError } from "@/lib/api-client";
import type {
  AnalystNoteCreateRequest,
  AnalystNoteItem,
  AnalystNotesListResponse,
  AnalystNoteUpdateRequest,
  AuditLogsPageResponse,
  AuthActionMessageResponse,
  DataDeletionReceiptResponse,
  DeleteAccountRequest,
  HitlFeedbackItem,
  HitlFeedbackRequest,
  HitlSummaryResponse,
  ImportLocalWatchlistResponse,
  LoginRequest,
  MyQuotaResponse,
  OpsAssignTicketRequest,
  OpsDecisionRequest,
  OpsIssuerLifecycleResponse,
  OpsIssuerSyncRequest,
  OpsIssuerSyncResponse,
  OpsQaQueueResponse,
  OpsSubmitRevisionRequest,
  OpsTicketMutationResponse,
  PasswordChangeBody,
  PasswordResetConfirmBody,
  PasswordResetRequestBody,
  PrivateDossierDeleteResponse,
  PrivateDossierListResponse,
  PrivateDossierPreviewResponse,
  PrivateScoreRequest,
  PrivateScoreResponse,
  RegisterRequest,
  SavedScreenItem,
  SavedScreensListResponse,
  SessionsListResponse,
  SessionStatusResponse,
  UpdateProfileRequest,
  UserProfileResponse,
  WatchlistResponseItem,
  WatchlistsListResponse,
} from "@/types/api";

const INTERNAL_LEAK_RE =
  /([A-Za-z]:\\|\/(?:src|data|Users)\/|\.(?:parquet|sqlite|ducklake|py)\b|Traceback|\b(?:postgres|postgresql|redis|valkey):\/\/|\b(?:BS|IS|CF)_[A-Z0-9_]+\b)/i;

function sanitizeAuthDetail(raw: string, status: number): string {
  const cleaned = raw.replace(/\s+/g, " ").trim();
  if (cleaned && !INTERNAL_LEAK_RE.test(cleaned)) {
    return cleaned.slice(0, 220);
  }
  if (status === 401) return "Phiên đăng nhập không hợp lệ hoặc đã hết hạn.";
  if (status === 403) return "Bạn không có quyền thực hiện thao tác này.";
  if (status === 404) return "Không tìm thấy dữ liệu được yêu cầu.";
  if (status === 422) return "Dữ liệu nhập chưa hợp lệ, vui lòng kiểm tra lại.";
  if (status === 429) return "Quá nhiều yêu cầu trong thời gian ngắn. Vui lòng thử lại sau.";
  if (status === 503) return "Dịch vụ tài khoản hiện chưa sẵn sàng trên máy chủ.";
  return "Không thể hoàn tất yêu cầu với máy chủ.";
}

async function authFetchJson<T>(
  input: string,
  init?: RequestInit & { mutating?: boolean },
): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const isMutating = init?.mutating ?? method !== "GET";
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (init?.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (isMutating) {
    headers["X-Requested-With"] = "FinIntel-Web";
  }

  const res = await fetch(input, {
    ...init,
    credentials: "include",
    headers: {
      ...headers,
      ...(init?.headers as Record<string, string> | undefined),
    },
  });

  if (!res.ok) {
    let detail = "";
    try {
      const errBody = (await res.json()) as { detail?: unknown };
      if (typeof errBody.detail === "string") {
        detail = errBody.detail;
      } else if (Array.isArray(errBody.detail) && errBody.detail.length > 0) {
        const first = errBody.detail[0] as { msg?: unknown } | undefined;
        if (first && typeof first.msg === "string") {
          detail = first.msg;
        }
      }
    } catch {
      detail = "";
    }
    throw new ApiError(res.status, sanitizeAuthDetail(detail, res.status));
  }

  return (await res.json()) as T;
}

export const authApi = {
  getSessionStatus: (signal?: AbortSignal) =>
    authFetchJson<SessionStatusResponse>("/api/v3/auth/session", {
      signal,
      mutating: false,
    }),

  register: (payload: RegisterRequest) =>
    authFetchJson<UserProfileResponse>("/api/v3/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  login: (payload: LoginRequest) =>
    authFetchJson<UserProfileResponse>("/api/v3/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  logout: () =>
    authFetchJson<AuthActionMessageResponse>("/api/v3/auth/logout", {
      method: "POST",
    }),

  logoutAll: () =>
    authFetchJson<AuthActionMessageResponse>("/api/v3/auth/logout-all", {
      method: "POST",
    }),

  requestPasswordReset: (payload: PasswordResetRequestBody) =>
    authFetchJson<AuthActionMessageResponse>("/api/v3/auth/password-reset/request", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  confirmPasswordReset: (payload: PasswordResetConfirmBody) =>
    authFetchJson<AuthActionMessageResponse>("/api/v3/auth/password-reset/confirm", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  changePassword: (payload: PasswordChangeBody) =>
    authFetchJson<AuthActionMessageResponse>("/api/v3/auth/password-change", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getMe: (signal?: AbortSignal) =>
    authFetchJson<UserProfileResponse>("/api/v3/auth/me", {
      signal,
      mutating: false,
    }),

  updateProfile: (payload: UpdateProfileRequest) =>
    authFetchJson<UserProfileResponse>("/api/v3/auth/me", {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  getSessions: (signal?: AbortSignal) =>
    authFetchJson<SessionsListResponse>("/api/v3/auth/sessions", {
      signal,
      mutating: false,
    }),

  revokeSession: (sessionId: string) =>
    authFetchJson<AuthActionMessageResponse>(
      `/api/v3/auth/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    ),

  deleteAccount: (payload: DeleteAccountRequest) =>
    authFetchJson<DataDeletionReceiptResponse>("/api/v3/auth/me", {
      method: "DELETE",
      body: JSON.stringify(payload),
    }),

  getMyActivity: (limit = 30, offset = 0, signal?: AbortSignal) =>
    authFetchJson<AuditLogsPageResponse>(
      `/api/v3/audit/my-activity?limit=${limit}&offset=${offset}`,
      { signal, mutating: false },
    ),

  getMyQuota: (signal?: AbortSignal) =>
    authFetchJson<MyQuotaResponse>("/api/v3/quota/me", {
      signal,
      mutating: false,
    }),
};

export const workspaceApi = {
  listWatchlists: (signal?: AbortSignal) =>
    authFetchJson<WatchlistsListResponse>("/api/v3/workspace/watchlists", {
      signal,
      mutating: false,
    }),

  createWatchlist: (name: string) =>
    authFetchJson<WatchlistResponseItem>("/api/v3/workspace/watchlists", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  renameWatchlist: (watchlistId: string, name: string) =>
    authFetchJson<WatchlistResponseItem>(
      `/api/v3/workspace/watchlists/${encodeURIComponent(watchlistId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ name }),
      },
    ),

  deleteWatchlist: (watchlistId: string) =>
    authFetchJson<{ status: string; id: string }>(
      `/api/v3/workspace/watchlists/${encodeURIComponent(watchlistId)}`,
      { method: "DELETE" },
    ),

  importLocalWatchlist: (tickers: readonly string[], watchlistName?: string) =>
    authFetchJson<ImportLocalWatchlistResponse>(
      "/api/v3/workspace/watchlists/import-local",
      {
        method: "POST",
        body: JSON.stringify({
          tickers: [...tickers],
          watchlist_name: watchlistName ?? null,
        }),
      },
    ),

  addTicker: (watchlistId: string, ticker: string) =>
    authFetchJson<WatchlistResponseItem>(
      `/api/v3/workspace/watchlists/${encodeURIComponent(watchlistId)}/items/${encodeURIComponent(ticker.toUpperCase())}`,
      { method: "PUT" },
    ),

  removeTicker: (watchlistId: string, ticker: string) =>
    authFetchJson<{ status: string; id: string }>(
      `/api/v3/workspace/watchlists/${encodeURIComponent(watchlistId)}/items/${encodeURIComponent(ticker.toUpperCase())}`,
      { method: "DELETE" },
    ),

  listNotes: (ticker: string, signal?: AbortSignal) =>
    authFetchJson<AnalystNotesListResponse>(
      `/api/v3/workspace/notes/${encodeURIComponent(ticker.toUpperCase())}`,
      { signal, mutating: false },
    ),

  createNote: (ticker: string, payload: AnalystNoteCreateRequest) =>
    authFetchJson<AnalystNoteItem>(
      `/api/v3/workspace/notes/${encodeURIComponent(ticker.toUpperCase())}`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),

  updateNote: (
    ticker: string,
    noteId: string,
    payload: AnalystNoteUpdateRequest,
  ) =>
    authFetchJson<AnalystNoteItem>(
      `/api/v3/workspace/notes/${encodeURIComponent(ticker.toUpperCase())}/${encodeURIComponent(noteId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(payload),
      },
    ),

  deleteNote: (ticker: string, noteId: string) =>
    authFetchJson<{ status: string; id: string }>(
      `/api/v3/workspace/notes/${encodeURIComponent(ticker.toUpperCase())}/${encodeURIComponent(noteId)}`,
      { method: "DELETE" },
    ),

  listSavedScreens: (signal?: AbortSignal) =>
    authFetchJson<SavedScreensListResponse>("/api/v3/workspace/saved-screens", {
      signal,
      mutating: false,
    }),

  createSavedScreen: (name: string, filtersJson: Record<string, unknown>) =>
    authFetchJson<SavedScreenItem>("/api/v3/workspace/saved-screens", {
      method: "POST",
      body: JSON.stringify({ name, filters_json: filtersJson }),
    }),

  deleteSavedScreen: (screenId: string) =>
    authFetchJson<{ status: string; id: string }>(
      `/api/v3/workspace/saved-screens/${encodeURIComponent(screenId)}`,
      { method: "DELETE" },
    ),
};

export const opsApi = {
  getQaQueue: (status?: "OPEN" | "IN_REVIEW" | "RESOLVED" | null, signal?: AbortSignal) => {
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    return authFetchJson<OpsQaQueueResponse>(`/api/v3/ops/qa-queue${qs}`, {
      signal,
      mutating: false,
    });
  },

  assignTicket: (ticketId: string, payload: OpsAssignTicketRequest) =>
    authFetchJson<OpsTicketMutationResponse>(
      `/api/v3/ops/qa-queue/${encodeURIComponent(ticketId)}/assign`,
      { method: "POST", body: JSON.stringify(payload) },
    ),

  submitRevision: (ticketId: string, payload: OpsSubmitRevisionRequest) =>
    authFetchJson<OpsTicketMutationResponse>(
      `/api/v3/ops/qa-queue/${encodeURIComponent(ticketId)}/submit-revision`,
      { method: "POST", body: JSON.stringify(payload) },
    ),

  decideTicket: (ticketId: string, payload: OpsDecisionRequest) =>
    authFetchJson<OpsTicketMutationResponse>(
      `/api/v3/ops/qa-queue/${encodeURIComponent(ticketId)}/decision`,
      { method: "POST", body: JSON.stringify(payload) },
    ),

  getIssuerLifecycle: (
    opts?: { exchange?: string; q?: string; limit?: number; offset?: number },
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    if (opts?.exchange) params.set("exchange", opts.exchange);
    if (opts?.q) params.set("q", opts.q);
    if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts?.offset !== undefined) params.set("offset", String(opts.offset));
    const qs = params.toString();
    return authFetchJson<OpsIssuerLifecycleResponse>(
      `/api/v3/ops/issuers/lifecycle${qs ? `?${qs}` : ""}`,
      { signal, mutating: false },
    );
  },

  requestIssuerSync: (payload: OpsIssuerSyncRequest) =>
    authFetchJson<OpsIssuerSyncResponse>("/api/v3/ops/issuers/sync-request", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};

export const privateCreditApi = {
  getTemplateUrl: (taxonomy: "VAS_TT99_2025" | "VAS_TT200_2014" = "VAS_TT99_2025") =>
    `/api/v3/private-credit/template.xlsx?taxonomy=${encodeURIComponent(taxonomy)}`,

  downloadTemplate: async (
    taxonomy: "VAS_TT99_2025" | "VAS_TT200_2014" = "VAS_TT99_2025",
  ): Promise<Blob> => {
    const res = await fetch(
      `/api/v3/private-credit/template.xlsx?taxonomy=${encodeURIComponent(taxonomy)}`,
      { credentials: "include" },
    );
    if (!res.ok) {
      let detail = "";
      try {
        const errBody = (await res.json()) as { detail?: unknown };
        if (typeof errBody.detail === "string") {
          detail = errBody.detail;
        }
      } catch {
        detail = "";
      }
      throw new ApiError(res.status, sanitizeAuthDetail(detail, res.status));
    }
    return res.blob();
  },

  parseWorkbook: async (formData: FormData): Promise<PrivateDossierPreviewResponse> => {
    const res = await fetch("/api/v3/private-credit/parse", {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "FinIntel-Web",
      },
      body: formData,
    });
    if (!res.ok) {
      let detail = "";
      try {
        const errBody = (await res.json()) as { detail?: unknown };
        if (typeof errBody.detail === "string") {
          detail = errBody.detail;
        } else if (Array.isArray(errBody.detail) && errBody.detail.length > 0) {
          const first = errBody.detail[0] as { msg?: unknown } | undefined;
          if (typeof first?.msg === "string") detail = first.msg;
        }
      } catch {
        detail = "";
      }
      throw new ApiError(res.status, sanitizeAuthDetail(detail, res.status));
    }
    return (await res.json()) as PrivateDossierPreviewResponse;
  },

  listDossiers: (signal?: AbortSignal) =>
    authFetchJson<PrivateDossierListResponse>("/api/v3/private-credit/dossiers", {
      signal,
      mutating: false,
    }),

  getDossier: (dossierId: string, signal?: AbortSignal) =>
    authFetchJson<PrivateDossierPreviewResponse>(
      `/api/v3/private-credit/dossiers/${encodeURIComponent(dossierId)}`,
      { signal, mutating: false },
    ),

  scoreDossier: (dossierId: string, payload?: Partial<PrivateScoreRequest>) =>
    authFetchJson<PrivateScoreResponse>(
      `/api/v3/private-credit/dossiers/${encodeURIComponent(dossierId)}/score`,
      {
        method: "POST",
        body: JSON.stringify(payload ?? {}),
      },
    ),

  deleteDossier: (dossierId: string) =>
    authFetchJson<PrivateDossierDeleteResponse>(
      `/api/v3/private-credit/dossiers/${encodeURIComponent(dossierId)}`,
      { method: "DELETE" },
    ),
};

export const hitlApi = {
  submitFeedback: (payload: HitlFeedbackRequest) =>
    authFetchJson<HitlFeedbackItem>("/api/v3/hitl/feedback", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getSummary: (signal?: AbortSignal) =>
    authFetchJson<HitlSummaryResponse>("/api/v3/hitl/summary", {
      signal,
      mutating: false,
    }),
};
