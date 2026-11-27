"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { RiskBadge } from "@/components/financial/RiskBadge";
import { ErrorState, PageSkeleton } from "@/components/layout/StateBlocks";
import { MethodologyModelsTab } from "@/components/methodology/MethodologyModelsTab";
import { MethodologyQualityTab } from "@/components/methodology/MethodologyQualityTab";
import { useAppState } from "@/components/providers/app-providers";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/primitives";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
  useAllIssuers,
  useMetaDictionary,
  useModelCard,
  useQualityQuarantine,
  useQualitySummary,
  useSnapshotOverview,
} from "@/hooks/use-finintel-queries";
import {
  formatFormulaVi,
  formatRedFlagCodeLabel,
  RATIO_CATEGORY_LABELS,
  SEVERITY_META,
} from "@/lib/domain-labels";
import { normalizeSearchText } from "@/lib/format";

type MethodTab = "dictionary" | "models" | "quality";
const SCROLL_REGION_CLS =
  "overflow-x-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const METHOD_TABS: readonly { value: MethodTab; label: string }[] = [
  { value: "dictionary", label: "Từ điển chỉ số & Cờ đỏ" },
  { value: "models", label: "Phương pháp mô hình" },
  { value: "quality", label: "Chất lượng dữ liệu" },
];

const DIRECTION_LABELS: Record<string, string> = {
  HIGHER_IS_BETTER: "Cao hơn tích cực hơn",
  LOWER_IS_BETTER: "Thấp hơn tích cực hơn",
  NEUTRAL: "Trung tính (phụ thuộc bối cảnh)",
};

function MethodologyContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { snapshotId, cacheSnapshotKey } = useAppState();
  const effectiveCacheKey = cacheSnapshotKey ?? snapshotId;

  const rawTab = searchParams.get("tab");
  const activeTab: MethodTab =
    rawTab === "models" || rawTab === "model"
      ? "models"
      : rawTab === "quality"
        ? "quality"
        : "dictionary";

  const [dictSearch, setDictSearch] = React.useState("");
  const [catFilter, setCatFilter] = React.useState("");
  const [qOffset, setQOffset] = React.useState(0);
  const [quarOffset, setQuarOffset] = React.useState(0);

  const dictQuery = useMetaDictionary(effectiveCacheKey);
  const modelCardQuery = useModelCard(effectiveCacheKey, {
    enabled: activeTab === "models",
  });
  const overviewQuery = useSnapshotOverview(effectiveCacheKey);
  const summaryQuery = useQualitySummary(25, qOffset, effectiveCacheKey);
  const quarantineQuery = useQualityQuarantine(25, quarOffset, effectiveCacheKey);
  const issuersQuery = useAllIssuers(effectiveCacheKey);

  const issuerTickerMap = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const iss of issuersQuery.data ?? []) {
      map.set(iss.issuer_id, iss.ticker);
    }
    return map;
  }, [issuersQuery.data]);

  const setTab = (tab: MethodTab) => {
    router.replace(tab === "dictionary" ? "/methodology" : `/methodology?tab=${tab}`, {
      scroll: false,
    });
  };

  const totalRatios = dictQuery.data?.ratios.length ?? 38;
  const totalRedFlags = dictQuery.data?.red_flags.length ?? 10;
  const isRatiosFiltered = Boolean(dictSearch.trim() || catFilter);

  const filteredRatios = React.useMemo(() => {
    const list = dictQuery.data?.ratios ?? [];
    const q = normalizeSearchText(dictSearch);
    return list.filter((r) => {
      if (catFilter && r.category !== catFilter) return false;
      if (!q) return true;
      return (
        normalizeSearchText(r.display_name_vn).includes(q) ||
        normalizeSearchText(r.ratio_name).includes(q) ||
        normalizeSearchText(r.description_vn).includes(q) ||
        normalizeSearchText(r.formula_display).includes(q)
      );
    });
  }, [catFilter, dictQuery.data?.ratios, dictSearch]);

  return (
    <div className="space-y-4">
      <SegmentedControl<MethodTab>
        ariaLabel="Các mục tài liệu phương pháp"
        variant="underline"
        value={activeTab}
        onChange={setTab}
        idPrefix="methodology"
        controlsPanelId={(t) => `methodology-panel-${t}`}
        options={METHOD_TABS}
      />

      <div
        id={`methodology-panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`methodology-tab-${activeTab}`}
      >
      {activeTab === "dictionary" ? (
        dictQuery.isLoading ? (
          <PageSkeleton />
        ) : dictQuery.isError ? (
          <ErrorState message={dictQuery.error.message} onRetry={() => dictQuery.refetch()} />
        ) : (
          <div className="space-y-5">
            <Card>
              <CardHeader className="flex-wrap gap-2 py-2.5">
                <CardTitle className="text-[14px]">
                  {isRatiosFiltered
                    ? `Hiển thị ${filteredRatios.length} / ${totalRatios} tỷ số tài chính chuẩn hóa`
                    : `${totalRatios} tỷ số tài chính chuẩn hóa`}
                </CardTitle>
                <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
                  <Input
                    value={dictSearch}
                    onChange={(e) => setDictSearch(e.target.value)}
                    placeholder="Tìm tên chỉ số hoặc công thức..."
                    className="h-8 w-full text-xs sm:w-56"
                    aria-label="Tìm trong từ điển chỉ số"
                  />
                  <Select
                    value={catFilter}
                    onChange={(e) => setCatFilter(e.target.value)}
                    className="h-8 text-xs"
                    aria-label="Lọc theo nhóm chỉ số"
                  >
                    <option value="">Tất cả nhóm</option>
                    {Object.entries(RATIO_CATEGORY_LABELS).map(([k, lbl]) => (
                      <option key={k} value={k}>
                        {lbl}
                      </option>
                    ))}
                  </Select>
                </div>
              </CardHeader>
              <CardContent className="p-0">
                <div
                  tabIndex={0}
                  role="region"
                  aria-label={`Bảng từ điển ${totalRatios} tỷ số tài chính`}
                  className={SCROLL_REGION_CLS}
                >
                  <table className="w-full min-w-[720px] border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Chỉ số
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Nhóm
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Công thức chuẩn hóa
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Chiều đánh giá
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Ý nghĩa phân tích
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredRatios.map((r) => (
                        <tr
                          key={r.ratio_name}
                          className="border-b border-border/60 hover:bg-surface-muted/40"
                        >
                          <td className="px-3 py-2">
                            <div className="font-medium text-ink">{r.display_name_vn}</div>
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {RATIO_CATEGORY_LABELS[r.category] ?? r.category}
                          </td>
                          <td className="px-3 py-2 text-xs text-ink">
                            {formatFormulaVi(r.formula_display)}
                          </td>
                          <td className="px-3 py-2 text-ink-muted">
                            {DIRECTION_LABELS[r.direction] ?? r.direction}
                          </td>
                          <td className="max-w-md px-3 py-2 text-ink-muted">
                            {r.description_vn}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="py-2.5">
                <CardTitle className="text-[14px]">
                  {`${totalRedFlags} quy tắc phát hiện bất thường kế toán (Quy tắc 01 – ${String(totalRedFlags).padStart(2, "0")})`}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div
                  tabIndex={0}
                  role="region"
                  aria-label={`Bảng ${totalRedFlags} quy tắc phát hiện bất thường kế toán`}
                  className={SCROLL_REGION_CLS}
                >
                  <table className="w-full min-w-[720px] border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-border bg-surface-muted/70 text-ink-muted">
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Mã quy tắc
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Tên dấu hiệu
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Mức độ
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Cơ sở phương pháp
                        </th>
                        <th scope="col" className="px-3 py-2 text-left font-semibold">
                          Điều kiện kích hoạt
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {(dictQuery.data?.red_flags ?? []).map((rf) => {
                        const sev = SEVERITY_META[rf.default_severity] ?? SEVERITY_META.MEDIUM;
                        return (
                          <tr
                            key={rf.flag_code}
                            className="border-b border-border/60 hover:bg-surface-muted/40"
                          >
                            <td className="px-3 py-2 font-mono font-semibold text-ink">
                              {formatRedFlagCodeLabel(rf.flag_code)}
                            </td>
                            <td className="px-3 py-2 font-medium text-ink">
                              {rf.title_vn}
                            </td>
                            <td className="px-3 py-2">
                              <RiskBadge tone={sev.tone} label={sev.label} />
                            </td>
                            <td className="px-3 py-2 text-ink-muted">
                              {rf.methodology_ref}
                            </td>
                            <td className="max-w-md px-3 py-2 text-ink-muted">
                              {rf.description_vn}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </div>
        )
      ) : activeTab === "models" ? (
        <MethodologyModelsTab
          modelCardQuery={modelCardQuery}
          scrollRegionClassName={SCROLL_REGION_CLS}
        />
      ) : (
        <MethodologyQualityTab
          overviewQuery={overviewQuery}
          summaryQuery={summaryQuery}
          quarantineQuery={quarantineQuery}
          issuerTickerMap={issuerTickerMap}
          qOffset={qOffset}
          setQOffset={setQOffset}
          quarOffset={quarOffset}
          setQuarOffset={setQuarOffset}
          scrollRegionClassName={SCROLL_REGION_CLS}
        />
      )}
      </div>
    </div>
  );
}

export default function MethodologyPage() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          Dữ liệu, Từ điển chỉ số &amp; Phương pháp mô hình
        </h1>
        <p className="mt-0.5 text-xs text-ink-muted">
          Tài liệu minh bạch công thức các tỷ số tài chính chuẩn hóa, quy tắc phát hiện bất thường kế toán, mô hình định lượng và kiểm tra chất lượng dữ liệu
        </p>
      </div>
      <React.Suspense fallback={<PageSkeleton />}>
        <MethodologyContent />
      </React.Suspense>
    </div>
  );
}
