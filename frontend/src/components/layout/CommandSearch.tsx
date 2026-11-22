"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Dialog } from "@base-ui/react/dialog";
import { Search, X } from "lucide-react";
import { useAppState } from "@/components/providers/app-providers";
import { useAllIssuers } from "@/hooks/use-finintel-queries";
import { normalizeSearchText } from "@/lib/format";
import type { IssuerItem } from "@/types/api";

function CommandSearchDialog({
  open,
  onOpenChange,
  issuers,
  isLoading,
  onNavigate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  issuers: readonly IssuerItem[];
  isLoading: boolean;
  onNavigate: (ticker: string) => void;
}) {
  const [query, setQuery] = React.useState("");
  const [activeIndex, setActiveIndex] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const handleOpenChange = React.useCallback(
    (nextOpen: boolean) => {
      onOpenChange(nextOpen);
      if (!nextOpen) {
        setQuery("");
        setActiveIndex(0);
      }
    },
    [onOpenChange],
  );

  const results = React.useMemo(() => {
    const norm = normalizeSearchText(query);
    if (!norm) {
      return issuers.slice(0, 12);
    }
    const prefixMatches: IssuerItem[] = [];
    const subMatches: IssuerItem[] = [];
    for (const item of issuers) {
      const tNorm = normalizeSearchText(item.ticker);
      const nNorm = normalizeSearchText(item.company_name_vn ?? "");
      const iNorm = normalizeSearchText(item.icb_name_level2 ?? "");
      if (tNorm.startsWith(norm)) {
        prefixMatches.push(item);
      } else if (tNorm.includes(norm) || nNorm.includes(norm) || iNorm.includes(norm)) {
        subMatches.push(item);
      }
      if (prefixMatches.length + subMatches.length >= 20) break;
    }
    return [...prefixMatches, ...subMatches].slice(0, 15);
  }, [issuers, query]);

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-ink/40 transition-opacity duration-150 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 motion-reduce:transition-none dark:bg-black/60" />
        <Dialog.Viewport className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]">
          <Dialog.Popup
            initialFocus={inputRef}
            aria-modal="true"
            aria-label="Tìm kiếm doanh nghiệp"
            className="w-full max-w-xl overflow-hidden rounded-[var(--radius-md)] border border-border-strong bg-surface text-ink shadow-[0_8px_30px_rgba(15,23,42,0.18)] transition-all duration-150 ease-[cubic-bezier(0.2,0,0,1)] focus:outline-none data-[starting-style]:-translate-y-1 data-[starting-style]:opacity-0 data-[ending-style]:-translate-y-1 data-[ending-style]:opacity-0 motion-reduce:transition-none dark:shadow-[0_8px_30px_rgba(0,0,0,0.55)]"
          >
            <Dialog.Title className="sr-only">Tìm kiếm doanh nghiệp</Dialog.Title>
            <Dialog.Description className="sr-only">
              Tìm kiếm nhanh theo mã chứng khoán, tên doanh nghiệp hoặc ngành ICB.
            </Dialog.Description>
            <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
              <Search className="size-4 text-ink-muted" aria-hidden="true" />
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActiveIndex(0);
                }}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setActiveIndex((idx) => Math.min(results.length - 1, idx + 1));
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setActiveIndex((idx) => Math.max(0, idx - 1));
                  } else if (e.key === "Enter" && results[activeIndex]) {
                    e.preventDefault();
                    onNavigate(results[activeIndex].ticker);
                  }
                }}
                placeholder="Nhập mã CK hoặc tên doanh nghiệp (hỗ trợ không dấu, VD: hoa phat)..."
                className="w-full bg-transparent text-sm text-ink placeholder:text-ink-subtle focus:outline-none"
              />
              <Dialog.Close
                type="button"
                aria-label="Đóng tìm kiếm"
                className="rounded p-1 text-ink-muted transition-colors duration-150 hover:bg-surface-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="size-4" aria-hidden="true" />
              </Dialog.Close>
            </div>

            <div className="max-h-80 divide-y divide-border/60 overflow-y-auto">
              {isLoading ? (
                <div className="p-4 text-[13px] text-ink-muted">
                  Đang tải danh sách doanh nghiệp...
                </div>
              ) : results.length === 0 ? (
                <div className="p-4 text-[13px] text-ink-muted">
                  Không tìm thấy doanh nghiệp phù hợp với &ldquo;{query}&rdquo;.
                </div>
              ) : (
                results.map((item, idx) => (
                  <button
                    key={item.issuer_id}
                    type="button"
                    onClick={() => onNavigate(item.ticker)}
                    onMouseEnter={() => setActiveIndex(idx)}
                    className={`flex w-full items-center justify-between gap-3 px-3.5 py-2 text-left transition-colors ${
                      idx === activeIndex ? "bg-accent-soft" : "hover:bg-surface-muted/70"
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-semibold text-accent-ink">
                          {item.ticker}
                        </span>
                        <span className="truncate text-[13px] font-medium text-ink">
                          {item.company_name_vn ?? "Chưa cập nhật tên"}
                        </span>
                      </div>
                      <p className="truncate text-xs text-ink-muted">
                        {item.exchange ?? "—"} ·{" "}
                        {item.icb_name_level3 ?? item.icb_name_level2 ?? "Chưa phân ngành"}
                      </p>
                    </div>
                    <span className="shrink-0 text-xs text-ink-subtle">Hồ sơ →</span>
                  </button>
                ))
              )}
            </div>
            <div className="flex items-center justify-between border-t border-border bg-surface-muted/60 px-3 py-1.5 text-[11px] text-ink-muted">
              <span>↑↓ di chuyển · Enter để mở · Esc để đóng</span>
              <span>{issuers.length} DN niêm yết</span>
            </div>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function CommandSearch() {
  const router = useRouter();
  const { commandOpen, setCommandOpen, snapshotId } = useAppState();
  const { data: issuers = [], isLoading } = useAllIssuers(snapshotId);

  React.useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandOpen(!commandOpen);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [commandOpen, setCommandOpen]);

  const navigateTo = React.useCallback(
    (ticker: string) => {
      setCommandOpen(false);
      router.push(`/companies/${encodeURIComponent(ticker)}`);
    },
    [router, setCommandOpen],
  );

  return (
    <CommandSearchDialog
      open={commandOpen}
      onOpenChange={setCommandOpen}
      issuers={issuers}
      isLoading={isLoading}
      onNavigate={navigateTo}
    />
  );
}
