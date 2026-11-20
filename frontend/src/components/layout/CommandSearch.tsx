"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Search, X } from "lucide-react";
import { useAppState } from "@/components/providers/app-providers";
import { useAllIssuers } from "@/hooks/use-finintel-queries";
import { normalizeSearchText } from "@/lib/format";
import type { IssuerItem } from "@/types/api";

function CommandSearchDialog({
  issuers,
  isLoading,
  onClose,
  onNavigate,
}: {
  issuers: readonly IssuerItem[];
  isLoading: boolean;
  onClose: () => void;
  onNavigate: (ticker: string) => void;
}) {
  const [query, setQuery] = React.useState("");
  const [activeIndex, setActiveIndex] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    const id = setTimeout(() => inputRef.current?.focus(), 20);
    return () => clearTimeout(id);
  }, []);

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
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-ink/40 pt-[12vh] px-4"
      role="dialog"
      aria-modal="true"
      aria-label="Tìm kiếm doanh nghiệp"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl overflow-hidden rounded border border-border-strong bg-surface shadow-[0_8px_30px_rgba(26,29,33,0.18)]"
        onClick={(e) => e.stopPropagation()}
      >
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
          <button
            type="button"
            aria-label="Đóng tìm kiếm"
            onClick={onClose}
            className="rounded p-1 text-ink-muted hover:bg-surface-muted hover:text-ink"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="max-h-80 overflow-y-auto divide-y divide-border/60">
          {isLoading ? (
            <div className="p-4 text-[13px] text-ink-muted">Đang tải danh sách doanh nghiệp...</div>
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
                    {item.exchange ?? "—"} · {item.icb_name_level3 ?? item.icb_name_level2 ?? "Chưa phân ngành"}
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
      </div>
    </div>
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
      } else if (e.key === "Escape" && commandOpen) {
        e.preventDefault();
        setCommandOpen(false);
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

  if (!commandOpen) return null;

  return (
    <CommandSearchDialog
      issuers={issuers}
      isLoading={isLoading}
      onClose={() => setCommandOpen(false)}
      onNavigate={navigateTo}
    />
  );
}
