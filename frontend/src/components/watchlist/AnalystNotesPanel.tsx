"use client";

import * as React from "react";
import { FileText, Pencil, Plus, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/primitives";
import { useOptionalQueryClient, useTickerAnalystNotes } from "@/hooks/use-auth-workspace";
import { ApiError } from "@/lib/api-client";
import { workspaceApi } from "@/lib/auth-api";
import { formatDateVi } from "@/lib/format";

export function AnalystNotesPanel({
  ticker,
  onClose,
}: {
  ticker: string;
  onClose: () => void;
}) {
  const queryClient = useOptionalQueryClient();
  const notesQuery = useTickerAnalystNotes(ticker, true);

  const [title, setTitle] = React.useState("");
  const [content, setContent] = React.useState("");
  const [periodEndDate, setPeriodEndDate] = React.useState("");
  const [editingId, setEditingId] = React.useState<string | null>(null);
  const [errorMsg, setErrorMsg] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const refreshNotes = async () => {
    await queryClient?.invalidateQueries({
      queryKey: ["workspace", "notes", ticker.toUpperCase()],
    });
  };

  const resetForm = () => {
    setTitle("");
    setContent("");
    setPeriodEndDate("");
    setEditingId(null);
    setErrorMsg(null);
  };

  const handleSave = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setErrorMsg(null);
    if (!content.trim()) {
      setErrorMsg("Vui lòng nhập nội dung ghi chú phân tích.");
      return;
    }
    setBusy(true);
    try {
      if (editingId) {
        await workspaceApi.updateNote(ticker, editingId, {
          title: title.trim(),
          content: content.trim(),
          period_end_date: periodEndDate || null,
          clear_period_end_date: !periodEndDate,
        });
      } else {
        await workspaceApi.createNote(ticker, {
          title: title.trim(),
          content: content.trim(),
          period_end_date: periodEndDate || null,
        });
      }
      resetForm();
      await refreshNotes();
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : "Không thể lưu ghi chú.");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (noteId: string) => {
    setBusy(true);
    try {
      await workspaceApi.deleteNote(ticker, noteId);
      if (editingId === noteId) resetForm();
      await refreshNotes();
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : "Không thể xóa ghi chú.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-[14px]">
          <FileText className="size-4 text-accent-ink" aria-hidden="true" />
          <span>Ghi chú phân tích · {ticker}</span>
        </CardTitle>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={`Đóng ghi chú ${ticker}`}
          onClick={onClose}
        >
          <X className="size-3.5" aria-hidden="true" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={handleSave} className="space-y-2.5 rounded-md border border-border bg-canvas p-3">
          {errorMsg ? (
            <p id="note-form-error" role="alert" className="text-xs text-risk-high">
              {errorMsg}
            </p>
          ) : null}
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <div className="space-y-1 sm:col-span-2">
              <label htmlFor="note-title" className="block text-[11px] font-medium text-ink-muted">
                Tiêu đề (tùy chọn)
              </label>
              <Input
                id="note-title"
                value={title}
                maxLength={120}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="VD: Kiểm tra nợ vay ngắn hạn Q4"
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="note-period" className="block text-[11px] font-medium text-ink-muted">
                Mốc kỳ BCTC (tùy chọn)
              </label>
              <Input
                id="note-period"
                type="date"
                value={periodEndDate}
                onChange={(e) => setPeriodEndDate(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-1">
            <label htmlFor="note-content" className="block text-[11px] font-medium text-ink-muted">
              Nội dung phân tích *
            </label>
            <textarea
              id="note-content"
              rows={3}
              maxLength={4000}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              aria-invalid={Boolean(errorMsg)}
              aria-describedby={errorMsg ? "note-form-error" : undefined}
              placeholder="Ghi nhận luận điểm định lượng, lưu ý dòng tiền hoặc thuyết minh..."
              className="w-full rounded-md border border-border-strong bg-surface px-2.5 py-2 text-[13px] text-ink placeholder:text-ink-subtle focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" disabled={busy}>
              <Plus className="size-3.5" aria-hidden="true" />
              <span>{editingId ? "Cập nhật ghi chú" : "Lưu ghi chú"}</span>
            </Button>
            {editingId ? (
              <Button type="button" variant="outline" size="sm" onClick={resetForm}>
                Hủy chỉnh sửa
              </Button>
            ) : null}
          </div>
        </form>

        <div className="space-y-2">
          {notesQuery.isLoading ? (
            <p className="text-xs text-ink-muted">Đang tải ghi chú...</p>
          ) : (notesQuery.data?.notes.length ?? 0) === 0 ? (
            <p className="text-xs text-ink-muted">
              Chưa có ghi chú nào cho mã {ticker}.
            </p>
          ) : (
            notesQuery.data?.notes.map((n) => (
              <div
                key={n.note_id}
                className="rounded-md border border-border bg-surface p-3 text-xs"
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-semibold text-ink">
                      {n.title || `Ghi chú ${ticker}`}
                    </div>
                    <div className="text-[11px] text-ink-subtle">
                      Cập nhật: {formatDateVi(n.updated_at.slice(0, 10))}
                      {n.period_end_date
                        ? ` · Kỳ BCTC: ${formatDateVi(n.period_end_date)}`
                        : ""}
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`Sửa ghi chú ${n.title || ticker}`}
                      onClick={() => {
                        setEditingId(n.note_id);
                        setTitle(n.title);
                        setContent(n.content);
                        setPeriodEndDate(n.period_end_date ?? "");
                      }}
                    >
                      <Pencil className="size-3" aria-hidden="true" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`Xóa ghi chú ${n.title || ticker}`}
                      onClick={() => handleDelete(n.note_id)}
                    >
                      <Trash2 className="size-3 text-risk-high" aria-hidden="true" />
                    </Button>
                  </div>
                </div>
                <p className="mt-1.5 whitespace-pre-wrap leading-5 text-ink">
                  {n.content}
                </p>
              </div>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
}
