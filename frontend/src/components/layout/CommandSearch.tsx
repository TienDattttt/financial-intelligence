"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Dialog } from "@base-ui/react/dialog";
import {
  Activity,
  Bookmark,
  BookOpen,
  Building2,
  Filter,
  GitCompare,
  LayoutDashboard,
  Moon,
  Search,
  Sun,
  X,
} from "lucide-react";
import { useAppState } from "@/components/providers/app-providers";
import { useAllIssuers } from "@/hooks/use-finintel-queries";
import { normalizeSearchText } from "@/lib/format";
import {
  getLastViewedTickerServerSnapshot,
  getLastViewedTickerSnapshot,
  getWatchlistServerSnapshot,
  getWatchlistSnapshot,
  subscribePreferences,
} from "@/lib/watchlist-store";
import type { IssuerItem } from "@/types/api";

interface PaletteItem {
  id: string;
  group: "recent" | "company" | "screen" | "action";
  title: string;
  subtitle?: string;
  badge?: string;
  trailing?: string;
  onSelect: () => void;
}

const GROUP_LABELS: Record<PaletteItem["group"], string> = {
  recent: "Mã gần đây & Theo dõi",
  company: "Doanh nghiệp niêm yết",
  screen: "Đi nhanh tới màn hình",
  action: "Thao tác nhanh",
};

function CommandSearchDialog({
  open,
  onOpenChange,
  issuers,
  isLoading,
  onNavigatePath,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  issuers: readonly IssuerItem[];
  isLoading: boolean;
  onNavigatePath: (path: string) => void;
}) {
  const { resolvedTheme, setThemePreference } = useAppState();
  const [query, setQuery] = React.useState("");
  const [activeIndex, setActiveIndex] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const watchlist = React.useSyncExternalStore(
    subscribePreferences,
    getWatchlistSnapshot,
    getWatchlistServerSnapshot,
  );
  const lastViewed = React.useSyncExternalStore(
    subscribePreferences,
    getLastViewedTickerSnapshot,
    getLastViewedTickerServerSnapshot,
  );

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

  const items = React.useMemo<PaletteItem[]>(() => {
    const norm = normalizeSearchText(query);
    const issuerByTicker = new Map(issuers.map((i) => [i.ticker, i]));

    const screens: PaletteItem[] = [
      {
        id: "screen-home",
        group: "screen",
        title: "Tổng quan thị trường",
        subtitle: "Bản đồ nhiệt ngành & DN cần chú ý",
        trailing: "/",
        onSelect: () => onNavigatePath("/"),
      },
      {
        id: "screen-screener",
        group: "screen",
        title: "Sàng lọc doanh nghiệp",
        subtitle: "Bộ lọc đa tiêu chí, biểu đồ phân tán & xuất CSV",
        trailing: "/screener",
        onSelect: () => onNavigatePath("/screener"),
      },
      {
        id: "screen-compare",
        group: "screen",
        title: "So sánh doanh nghiệp",
        subtitle: "So sánh tối đa 5 mã theo quỹ đạo 12 kỳ & ma trận chỉ số",
        trailing: "/compare",
        onSelect: () => onNavigatePath("/compare"),
      },
      {
        id: "screen-stress",
        group: "screen",
        title: "Kịch bản sốc tài chính",
        subtitle: "Mô phỏng cú sốc lãi suất & doanh thu IMF WP/15/216",
        trailing: "/stress-test",
        onSelect: () => onNavigatePath("/stress-test"),
      },
      {
        id: "screen-watchlist",
        group: "screen",
        title: "Danh sách theo dõi",
        subtitle: `${watchlist.length} mã đang theo dõi`,
        trailing: "/watchlist",
        onSelect: () => onNavigatePath("/watchlist"),
      },
      {
        id: "screen-methodology",
        group: "screen",
        title: "Dữ liệu & Phương pháp",
        subtitle: "Từ điển 38 tỷ số, 9 cờ đỏ VAS, kiểm định mô hình",
        trailing: "/methodology",
        onSelect: () => onNavigatePath("/methodology"),
      },
    ];

    const actions: PaletteItem[] = [
      {
        id: "action-toggle-theme",
        group: "action",
        title: resolvedTheme === "dark" ? "Chuyển sang Giao diện Sáng" : "Chuyển sang Giao diện Tối",
        subtitle: "Đổi nhanh chủ đề màu hiển thị",
        trailing: resolvedTheme === "dark" ? "Sáng" : "Tối",
        onSelect: () => {
          setThemePreference(resolvedTheme === "dark" ? "light" : "dark");
          handleOpenChange(false);
        },
      },
    ];

    if (!norm) {
      const recentTickers = Array.from(
        new Set([...(lastViewed ? [lastViewed] : []), ...watchlist]),
      ).slice(0, 4);

      const recentItems: PaletteItem[] = recentTickers
        .map((t) => issuerByTicker.get(t))
        .filter((it): it is IssuerItem => Boolean(it))
        .map((item) => ({
          id: `recent-${item.issuer_id}`,
          group: "recent",
          badge: item.ticker,
          title: item.company_name_vn ?? "Chưa cập nhật tên",
          subtitle: `${item.exchange ?? "—"} · ${item.icb_name_level3 ?? item.icb_name_level2 ?? "Chưa phân ngành"}`,
          trailing: "Hồ sơ →",
          onSelect: () => onNavigatePath(`/companies/${encodeURIComponent(item.ticker)}`),
        }));

      const companyItems: PaletteItem[] = issuers.slice(0, 8).map((item) => ({
        id: `co-${item.issuer_id}`,
        group: "company",
        badge: item.ticker,
        title: item.company_name_vn ?? "Chưa cập nhật tên",
        subtitle: `${item.exchange ?? "—"} · ${item.icb_name_level3 ?? item.icb_name_level2 ?? "Chưa phân ngành"}`,
        trailing: "Hồ sơ →",
        onSelect: () => onNavigatePath(`/companies/${encodeURIComponent(item.ticker)}`),
      }));

      return [...recentItems, ...companyItems, ...screens, ...actions];
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
      if (prefixMatches.length + subMatches.length >= 16) break;
    }

    const matchedCompanies: PaletteItem[] = [...prefixMatches, ...subMatches]
      .slice(0, 12)
      .map((item) => ({
        id: `co-${item.issuer_id}`,
        group: "company",
        badge: item.ticker,
        title: item.company_name_vn ?? "Chưa cập nhật tên",
        subtitle: `${item.exchange ?? "—"} · ${item.icb_name_level3 ?? item.icb_name_level2 ?? "Chưa phân ngành"}`,
        trailing: "Hồ sơ →",
        onSelect: () => onNavigatePath(`/companies/${encodeURIComponent(item.ticker)}`),
      }));

    const matchedScreens = screens.filter((s) =>
      normalizeSearchText(`${s.title} ${s.subtitle ?? ""}`).includes(norm),
    );
    const matchedActions = actions.filter((a) =>
      normalizeSearchText(`${a.title} ${a.subtitle ?? ""}`).includes(norm),
    );

    return [...matchedCompanies, ...matchedScreens, ...matchedActions];
  }, [
    handleOpenChange,
    issuers,
    lastViewed,
    onNavigatePath,
    query,
    resolvedTheme,
    setThemePreference,
    watchlist,
  ]);

  const grouped = React.useMemo(() => {
    const order: PaletteItem["group"][] = ["recent", "company", "screen", "action"];
    const out: { group: PaletteItem["group"]; items: { item: PaletteItem; flatIdx: number }[] }[] = [];
    let flatIdx = 0;
    for (const g of order) {
      const gItems = items
        .filter((it) => it.group === g)
        .map((item) => ({ item, flatIdx: flatIdx++ }));
      if (gItems.length > 0) {
        out.push({ group: g, items: gItems });
      }
    }
    return out;
  }, [items]);

  const renderGroupIcon = (id: string, group: PaletteItem["group"]) => {
    if (group === "company" || group === "recent") {
      return <Building2 className="size-3.5 shrink-0 text-ink-subtle" aria-hidden="true" />;
    }
    if (id === "screen-home") return <LayoutDashboard className="size-3.5 shrink-0 text-ink-subtle" aria-hidden="true" />;
    if (id === "screen-screener") return <Filter className="size-3.5 shrink-0 text-ink-subtle" aria-hidden="true" />;
    if (id === "screen-compare") return <GitCompare className="size-3.5 shrink-0 text-ink-subtle" aria-hidden="true" />;
    if (id === "screen-stress") return <Activity className="size-3.5 shrink-0 text-ink-subtle" aria-hidden="true" />;
    if (id === "screen-watchlist") return <Bookmark className="size-3.5 shrink-0 text-ink-subtle" aria-hidden="true" />;
    if (id === "screen-methodology") return <BookOpen className="size-3.5 shrink-0 text-ink-subtle" aria-hidden="true" />;
    return resolvedTheme === "dark" ? (
      <Sun className="size-3.5 shrink-0 text-ink-subtle" aria-hidden="true" />
    ) : (
      <Moon className="size-3.5 shrink-0 text-ink-subtle" aria-hidden="true" />
    );
  };

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-ink/40 transition-opacity duration-150 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0 motion-reduce:transition-none dark:bg-black/60" />
        <Dialog.Viewport className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]">
          <Dialog.Popup
            initialFocus={inputRef}
            aria-modal="true"
            aria-label="Tìm kiếm doanh nghiệp và điều hướng nhanh"
            className="w-full max-w-xl overflow-hidden rounded-lg border border-border-strong bg-surface text-ink shadow-popover transition-all duration-150 ease-[cubic-bezier(0.2,0,0,1)] focus:outline-none data-[starting-style]:-translate-y-1 data-[starting-style]:opacity-0 data-[ending-style]:-translate-y-1 data-[ending-style]:opacity-0 motion-reduce:transition-none"
          >
            <Dialog.Title className="sr-only">Tìm kiếm doanh nghiệp</Dialog.Title>
            <Dialog.Description className="sr-only">
              Tìm kiếm nhanh theo mã chứng khoán, tên doanh nghiệp, màn hình hoặc thao tác nhanh.
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
                    setActiveIndex((idx) => Math.min(Math.max(0, items.length - 1), idx + 1));
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setActiveIndex((idx) => Math.max(0, idx - 1));
                  } else if (e.key === "Enter" && items[activeIndex]) {
                    e.preventDefault();
                    items[activeIndex].onSelect();
                  }
                }}
                placeholder="Nhập mã CK hoặc tên doanh nghiệp (hỗ trợ không dấu, VD: hoa phat)..."
                className="w-full bg-transparent text-sm text-ink placeholder:text-ink-subtle focus:outline-none"
              />
              <Dialog.Close
                type="button"
                aria-label="Đóng tìm kiếm"
                className="rounded-md p-1 text-ink-muted transition-colors duration-150 hover:bg-surface-hover hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="size-4" aria-hidden="true" />
              </Dialog.Close>
            </div>

            <div className="max-h-96 overflow-y-auto py-1">
              {isLoading && items.length === 0 ? (
                <div className="p-4 text-[13px] text-ink-muted">
                  Đang tải danh sách doanh nghiệp...
                </div>
              ) : grouped.length === 0 ? (
                <div className="p-4 text-[13px] text-ink-muted">
                  Không tìm thấy kết quả phù hợp với &ldquo;{query}&rdquo;.
                </div>
              ) : (
                grouped.map((section) => (
                  <div key={section.group} className="py-1">
                    <p className="px-3.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-ink-subtle">
                      {GROUP_LABELS[section.group]}
                    </p>
                    <div>
                      {section.items.map(({ item, flatIdx }) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={item.onSelect}
                          onMouseEnter={() => setActiveIndex(flatIdx)}
                          className={`flex w-full items-center justify-between gap-3 px-3.5 py-2 text-left transition-colors ${
                            flatIdx === activeIndex
                              ? "bg-accent-soft"
                              : "hover:bg-surface-hover"
                          }`}
                        >
                          <div className="flex min-w-0 items-center gap-2.5">
                            {renderGroupIcon(item.id, item.group)}
                            <div className="min-w-0">
                              <div className="flex items-center gap-2">
                                {item.badge ? (
                                  <span className="font-mono text-sm font-semibold text-accent-ink">
                                    {item.badge}
                                  </span>
                                ) : null}
                                <span className="truncate text-[13px] font-medium text-ink">
                                  {item.title}
                                </span>
                              </div>
                              {item.subtitle ? (
                                <p className="truncate text-xs text-ink-muted">
                                  {item.subtitle}
                                </p>
                              ) : null}
                            </div>
                          </div>
                          {item.trailing ? (
                            <span className="shrink-0 font-mono text-[11px] text-ink-subtle">
                              {item.trailing}
                            </span>
                          ) : null}
                        </button>
                      ))}
                    </div>
                  </div>
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

  const navigateToPath = React.useCallback(
    (path: string) => {
      setCommandOpen(false);
      router.push(path);
    },
    [router, setCommandOpen],
  );

  return (
    <CommandSearchDialog
      open={commandOpen}
      onOpenChange={setCommandOpen}
      issuers={issuers}
      isLoading={isLoading}
      onNavigatePath={navigateToPath}
    />
  );
}
