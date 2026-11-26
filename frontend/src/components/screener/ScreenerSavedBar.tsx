"use client";

import * as React from "react";
import { BookmarkPlus, Check, Pencil, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import {
  deleteSavedScreen,
  type SavedScreen,
  type ScreenerFilterState,
} from "@/lib/saved-screens-store";

export function ScreenerSavedBar({
  savedScreens,
  editingScreenId,
  setEditingScreenId,
  editingNameDraft,
  setEditingNameDraft,
  saveFormOpen,
  setSaveFormOpen,
  saveNameDraft,
  setSaveNameDraft,
  saveFeedback,
  setSaveFeedback,
  onApplyPreset,
  onSaveCurrentScreen,
  onRenameSubmit,
}: {
  savedScreens: readonly SavedScreen[];
  editingScreenId: string | null;
  setEditingScreenId: (id: string | null) => void;
  editingNameDraft: string;
  setEditingNameDraft: (name: string) => void;
  saveFormOpen: boolean;
  setSaveFormOpen: (open: boolean) => void;
  saveNameDraft: string;
  setSaveNameDraft: (name: string) => void;
  saveFeedback: string | null;
  setSaveFeedback: (msg: string | null) => void;
  onApplyPreset: (filters: Partial<ScreenerFilterState>) => void;
  onSaveCurrentScreen: (e: React.FormEvent) => void;
  onRenameSubmit: (e: React.FormEvent, id: string) => void;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="font-semibold text-ink-muted">
            Bộ lọc đã lưu:
          </span>
          {savedScreens.length === 0 ? (
            <span className="text-ink-subtle">
              Chưa có bộ lọc cá nhân. Nhấn &ldquo;Lưu bộ lọc&rdquo; để tái sử dụng nhanh.
            </span>
          ) : (
            savedScreens.map((scr) =>
              editingScreenId === scr.id ? (
                <form
                  key={scr.id}
                  onSubmit={(e) => onRenameSubmit(e, scr.id)}
                  className="inline-flex items-center gap-1 rounded border border-border-strong bg-canvas px-1.5 py-0.5"
                >
                  <input
                    type="text"
                    value={editingNameDraft}
                    onChange={(e) => setEditingNameDraft(e.target.value)}
                    maxLength={60}
                    aria-label="Tên mới cho bộ lọc"
                    className="h-5 w-36 bg-transparent text-xs text-ink focus:outline-none"
                    autoFocus
                  />
                  <button
                    type="submit"
                    aria-label="Xác nhận đổi tên"
                    className="inline-flex size-6 items-center justify-center rounded text-risk-low hover:bg-surface-muted"
                  >
                    <Check className="size-3" />
                  </button>
                  <button
                    type="button"
                    aria-label="Hủy đổi tên"
                    onClick={() => {
                      setEditingScreenId(null);
                      setSaveFeedback(null);
                    }}
                    className="inline-flex size-6 items-center justify-center rounded text-ink-muted hover:bg-surface-muted"
                  >
                    <X className="size-3" />
                  </button>
                </form>
              ) : (
                <div
                  key={scr.id}
                  className="inline-flex items-center gap-0.5 rounded border border-border bg-canvas px-1.5 text-xs text-ink"
                >
                  <button
                    type="button"
                    onClick={() => onApplyPreset(scr.filters)}
                    className="inline-flex min-h-6 items-center px-1 font-medium hover:text-accent-ink hover:underline"
                  >
                    {scr.name}
                  </button>
                  <button
                    type="button"
                    aria-label={`Đổi tên bộ lọc ${scr.name}`}
                    onClick={() => {
                      setEditingScreenId(scr.id);
                      setEditingNameDraft(scr.name);
                      setSaveFeedback(null);
                    }}
                    className="inline-flex size-6 items-center justify-center rounded text-ink-subtle hover:bg-surface-muted hover:text-ink"
                  >
                    <Pencil className="size-3" />
                  </button>
                  <button
                    type="button"
                    aria-label={`Xóa bộ lọc ${scr.name}`}
                    onClick={() => deleteSavedScreen(scr.id)}
                    className="inline-flex size-6 items-center justify-center rounded text-ink-subtle hover:bg-risk-high-soft hover:text-risk-high"
                  >
                    <Trash2 className="size-3" />
                  </button>
                </div>
              ),
            )
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {saveFormOpen ? (
            <form
              onSubmit={onSaveCurrentScreen}
              className="flex flex-wrap items-center gap-1.5"
            >
              <Input
                type="text"
                value={saveNameDraft}
                onChange={(e) => setSaveNameDraft(e.target.value)}
                placeholder="Tên bộ lọc (VD: Thép đòn bẩy cao)..."
                aria-label="Tên bộ lọc muốn lưu"
                maxLength={60}
                className="h-7 w-52 text-xs"
                autoFocus
              />
              <Button type="submit" size="xs">
                Lưu
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="xs"
                onClick={() => {
                  setSaveFormOpen(false);
                  setSaveFeedback(null);
                }}
              >
                Hủy
              </Button>
            </form>
          ) : (
            <Button
              type="button"
              variant="outline"
              size="xs"
              onClick={() => {
                setSaveFormOpen(true);
                setSaveFeedback(null);
              }}
            >
              <BookmarkPlus className="size-3" />
              Lưu bộ lọc
            </Button>
          )}
        </div>
      </div>
      {saveFeedback ? (
        <p role="alert" className="mt-1.5 text-xs text-risk-high">
          {saveFeedback}
        </p>
      ) : null}
    </div>
  );
}
