"use client";

import * as React from "react";
import { Check, RotateCcw, SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { Input } from "@/components/ui/primitives";
import type { ScreenerFilterState, ScreenerRiskTier } from "@/lib/saved-screens-store";
import { cn } from "@/lib/utils";

export function ScreenerFilterDrawer({
  mobileFiltersOpen,
  setMobileFiltersOpen,
  qDraft,
  setQDraft,
  onSubmitKeyword,
  updateParams,
  exchangeOptions,
  exchangeParam,
  sectorOptions,
  sectorParam,
  riskTierMenuRef,
  riskTierMenuOpen,
  setRiskTierMenuOpen,
  riskTierTriggerLabel,
  riskTiersParam,
  riskTierOptions,
  applyRiskTiers,
  altmanOptions,
  altmanParam,
  advancedFiltersOpen,
  setAdvancedFiltersOpen,
  beneishOptions,
  beneishParam,
  fScoreOptions,
  minFParam,
  minProbParam,
  maxProbParam,
  minRfParam,
  maxRfParam,
  toPctDisplay,
  applyFilterPreset,
  total,
}: {
  mobileFiltersOpen: boolean;
  setMobileFiltersOpen: (open: boolean) => void;
  qDraft: string;
  setQDraft: (q: string) => void;
  onSubmitKeyword: (e?: React.FormEvent) => void;
  updateParams: (patch: Record<string, string | null>, resetPage?: boolean) => void;
  exchangeOptions: ComboboxOption[];
  exchangeParam: string;
  sectorOptions: ComboboxOption[];
  sectorParam: string;
  riskTierMenuRef: React.RefObject<HTMLDivElement | null>;
  riskTierMenuOpen: boolean;
  setRiskTierMenuOpen: React.Dispatch<React.SetStateAction<boolean>>;
  riskTierTriggerLabel: string;
  riskTiersParam: readonly ScreenerRiskTier[];
  riskTierOptions: readonly { value: ScreenerRiskTier; label: string }[];
  applyRiskTiers: (next: readonly ScreenerRiskTier[]) => void;
  altmanOptions: ComboboxOption[];
  altmanParam: string;
  advancedFiltersOpen: boolean;
  setAdvancedFiltersOpen: React.Dispatch<React.SetStateAction<boolean>>;
  beneishOptions: ComboboxOption[];
  beneishParam: string;
  fScoreOptions: ComboboxOption[];
  minFParam: string;
  minProbParam: string;
  maxProbParam: string;
  minRfParam: string;
  maxRfParam: string;
  toPctDisplay: (decimalStr: string) => string;
  applyFilterPreset: (filters: Partial<ScreenerFilterState>) => void;
  total: number;
}) {
  return (
    <div
      id="screener-filter-drawer"
      role={mobileFiltersOpen ? "dialog" : undefined}
      aria-modal={mobileFiltersOpen ? true : undefined}
      aria-label={mobileFiltersOpen ? "Bộ lọc doanh nghiệp" : undefined}
      className={cn(
        "lg:block",
        mobileFiltersOpen
          ? "fixed inset-0 z-40 flex bg-ink/30 lg:static lg:inset-auto lg:z-auto lg:bg-transparent"
          : "hidden",
      )}
      onClick={() => setMobileFiltersOpen(false)}
    >
      <Card
        className="flex h-full w-80 max-w-[85vw] flex-col overflow-y-auto rounded-none border-r border-border lg:h-auto lg:w-full lg:max-w-none lg:overflow-visible lg:rounded-lg lg:border"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-3.5 py-2.5 lg:hidden">
          <span className="text-sm font-semibold text-ink">
            Bộ lọc doanh nghiệp
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Đóng bộ lọc"
            onClick={() => setMobileFiltersOpen(false)}
          >
            <X className="size-4" />
          </Button>
        </div>
        <CardContent className="space-y-3 p-3 text-xs">
          {/* Primary Horizontal Filter Row on Desktop */}
          <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-[minmax(210px,1.4fr)_116px_minmax(170px,1.15fr)_minmax(160px,1fr)_minmax(150px,1fr)_auto] lg:items-end">
            <div className="space-y-1">
              <label htmlFor="scr-q" className="block text-[11px] font-semibold text-ink-muted">
                Tìm mã / tên doanh nghiệp
              </label>
              <form onSubmit={onSubmitKeyword} className="flex items-center gap-1">
                <div className="relative flex-1">
                  <Input
                    id="scr-q"
                    value={qDraft}
                    onChange={(e) => setQDraft(e.target.value)}
                    placeholder="VD: HPG, thép..."
                    className={qDraft ? "pr-7" : undefined}
                  />
                  {qDraft ? (
                    <button
                      type="button"
                      aria-label="Xóa từ khóa tìm kiếm"
                      onClick={() => {
                        setQDraft("");
                        updateParams({ q: null }, true);
                      }}
                      className="absolute top-1/2 right-1.5 inline-flex size-5 -translate-y-1/2 items-center justify-center rounded text-ink-muted hover:bg-surface-muted hover:text-ink"
                    >
                      <X className="size-3.5" aria-hidden="true" />
                    </button>
                  ) : null}
                </div>
                <Button type="submit" variant="outline" size="default" className="shrink-0">
                  Tìm
                </Button>
              </form>
            </div>

            <div className="space-y-1">
              <label htmlFor="scr-exchange" className="block text-[11px] font-semibold text-ink-muted">
                Sàn GD
              </label>
              <Combobox
                id="scr-exchange"
                options={exchangeOptions}
                value={exchangeParam}
                onValueChange={(next) =>
                  updateParams({ exchange: next || null })
                }
                placeholder="Tất cả sàn"
                ariaLabel="Sàn giao dịch"
                searchable={false}
                popupClassName="min-w-[140px]"
              />
            </div>

            <div className="space-y-1">
              <label htmlFor="scr-sector" className="block text-[11px] font-semibold text-ink-muted">
                Ngành ICB cấp 2
              </label>
              <Combobox
                id="scr-sector"
                options={sectorOptions}
                value={sectorParam}
                onValueChange={(next) =>
                  updateParams({ icb_code_level2: next || null })
                }
                placeholder="Tất cả ngành"
                searchPlaceholder="Tìm ngành (VD: thép, điện)..."
                ariaLabel="Ngành ICB cấp 2"
              />
            </div>

            <div className="space-y-1">
              <label htmlFor="scr-risk-tier" className="block text-[11px] font-semibold text-ink-muted">
                Bậc rủi ro (R1–R5)
              </label>
              <div className="relative" ref={riskTierMenuRef}>
                <button
                  id="scr-risk-tier"
                  type="button"
                  aria-haspopup="listbox"
                  aria-expanded={riskTierMenuOpen}
                  aria-label={`Bậc rủi ro (R1–R5): ${riskTierTriggerLabel}`}
                  onClick={() => setRiskTierMenuOpen((v) => !v)}
                  className="flex h-8 w-full items-center justify-between gap-2 rounded-md border border-border-strong bg-surface px-2.5 text-left text-[13px] text-ink shadow-xs transition-colors duration-150 hover:border-accent-ink/50 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                >
                  <span
                    className={cn(
                      "truncate",
                      riskTiersParam.length > 0 ? "text-ink" : "text-ink-subtle",
                    )}
                  >
                    {riskTierTriggerLabel}
                  </span>
                  <span className="shrink-0 text-[10px] text-ink-muted" aria-hidden="true">
                    ▾
                  </span>
                </button>

                {riskTierMenuOpen ? (
                  <div
                    role="listbox"
                    aria-multiselectable="true"
                    aria-label="Chọn bậc rủi ro (R1–R5)"
                    className="absolute left-0 z-30 mt-1 w-full min-w-[220px] rounded-lg border border-border-strong bg-surface p-1 text-xs text-ink shadow-popover"
                  >
                    <button
                      type="button"
                      role="option"
                      aria-selected={riskTiersParam.length === 0}
                      onClick={() => {
                        applyRiskTiers([]);
                        setRiskTierMenuOpen(false);
                      }}
                      className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-1.5 text-left hover:bg-accent-soft hover:text-accent-ink"
                    >
                      <span className={riskTiersParam.length === 0 ? "font-semibold" : ""}>
                        Tất cả bậc R1–R5
                      </span>
                      {riskTiersParam.length === 0 ? (
                        <Check className="size-3.5 text-accent-ink" aria-hidden="true" />
                      ) : null}
                    </button>
                    {riskTierOptions.map((opt) => {
                      const selected = riskTiersParam.includes(opt.value);
                      return (
                        <button
                          key={opt.value}
                          type="button"
                          role="option"
                          aria-selected={selected}
                          onClick={() => {
                            const next = selected
                              ? riskTiersParam.filter((t) => t !== opt.value)
                              : [...riskTiersParam, opt.value];
                            applyRiskTiers(next);
                          }}
                          className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-1.5 text-left hover:bg-accent-soft hover:text-accent-ink"
                        >
                          <span className={selected ? "font-semibold" : ""}>
                            {opt.label}
                          </span>
                          {selected ? (
                            <Check className="size-3.5 text-accent-ink" aria-hidden="true" />
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            </div>

            <div className="space-y-1">
              <label htmlFor="scr-altman" className="block text-[11px] font-semibold text-ink-muted">
                Phân vùng Altman Z″
              </label>
              <Combobox
                id="scr-altman"
                options={altmanOptions}
                value={altmanParam}
                onValueChange={(next) =>
                  updateParams({ altman_zone: next || null })
                }
                placeholder="Tất cả"
                searchPlaceholder="Tìm phân vùng Altman..."
                ariaLabel="Phân vùng Altman Z″"
              />
            </div>

            <div className="hidden lg:flex lg:justify-end">
              <Button
                type="button"
                variant={advancedFiltersOpen ? "secondary" : "outline"}
                size="default"
                className="whitespace-nowrap"
                aria-expanded={advancedFiltersOpen}
                onClick={() => setAdvancedFiltersOpen((v) => !v)}
              >
                <SlidersHorizontal className="size-3.5" />
                <span>Bộ lọc nâng cao</span>
                {[beneishParam, minFParam, minProbParam, maxProbParam, minRfParam, maxRfParam].filter(Boolean).length > 0 ? (
                  <span className="num rounded-sm bg-accent-soft px-1.5 py-0.5 text-[10px] font-semibold text-accent-ink">
                    {[beneishParam, minFParam, minProbParam, maxProbParam, minRfParam, maxRfParam].filter(Boolean).length}
                  </span>
                ) : null}
              </Button>
            </div>
          </div>

          {/* Advanced Filters Row (always visible inside mobile drawer; toggleable on desktop) */}
          <div
            className={cn(
              "grid grid-cols-1 gap-2.5 border-t border-border/70 pt-2.5 lg:grid-cols-12 lg:items-end",
              !advancedFiltersOpen && "lg:hidden",
            )}
          >
            <div className="space-y-1 lg:col-span-3">
              <label htmlFor="scr-beneish" className="block text-[11px] font-semibold text-ink-muted">
                Beneish M-Score
              </label>
              <Combobox
                id="scr-beneish"
                options={beneishOptions}
                value={beneishParam}
                onValueChange={(next) =>
                  updateParams({ beneish_flag: next || null })
                }
                placeholder="Tất cả"
                searchPlaceholder="Tìm trạng thái Beneish..."
                ariaLabel="Beneish M-Score"
              />
            </div>

            <div className="space-y-1 lg:col-span-3">
              <label htmlFor="scr-fscore" className="block text-[11px] font-semibold text-ink-muted">
                Piotroski F-Score tối thiểu
              </label>
              <Combobox
                id="scr-fscore"
                options={fScoreOptions}
                value={minFParam}
                onValueChange={(next) =>
                  updateParams({ min_f_score: next || null })
                }
                placeholder="Không giới hạn"
                searchPlaceholder="Chọn ngưỡng F-Score..."
                ariaLabel="Piotroski F-Score tối thiểu"
              />
            </div>

            <div className="grid grid-cols-2 gap-2 lg:col-span-3">
              <div className="space-y-1">
                <label htmlFor="scr-prob-min" className="block text-[11px] font-semibold text-ink-muted">
                  XS rủi ro 1 năm từ (%)
                </label>
                <Input
                  id="scr-prob-min"
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={toPctDisplay(minProbParam)}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (!v) return updateParams({ min_distress_prob: null });
                    const n = Number(v);
                    if (Number.isFinite(n)) {
                      const dec = Math.max(0, Math.min(1, n / 100));
                      updateParams({ min_distress_prob: dec.toFixed(3) });
                    }
                  }}
                  placeholder="0"
                />
              </div>
              <div className="space-y-1">
                <label htmlFor="scr-prob-max" className="block text-[11px] font-semibold text-ink-muted">
                  Đến (%)
                </label>
                <Input
                  id="scr-prob-max"
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={toPctDisplay(maxProbParam)}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (!v) return updateParams({ max_distress_prob: null });
                    const n = Number(v);
                    if (Number.isFinite(n)) {
                      const dec = Math.max(0, Math.min(1, n / 100));
                      updateParams({ max_distress_prob: dec.toFixed(3) });
                    }
                  }}
                  placeholder="100"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2 lg:col-span-3">
              <div className="space-y-1">
                <label htmlFor="scr-rf-min" className="block text-[11px] font-semibold text-ink-muted">
                  Cờ đỏ từ
                </label>
                <Input
                  id="scr-rf-min"
                  type="number"
                  min={0}
                  max={10}
                  value={minRfParam}
                  onChange={(e) =>
                    updateParams({ min_red_flags: e.target.value || null })
                  }
                  placeholder="0"
                />
              </div>
              <div className="space-y-1">
                <label htmlFor="scr-rf-max" className="block text-[11px] font-semibold text-ink-muted">
                  Đến
                </label>
                <Input
                  id="scr-rf-max"
                  type="number"
                  min={0}
                  max={10}
                  value={maxRfParam}
                  onChange={(e) =>
                    updateParams({ max_red_flags: e.target.value || null })
                  }
                  placeholder="10"
                />
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 pt-1 lg:hidden">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                applyFilterPreset({});
                setMobileFiltersOpen(false);
              }}
            >
              <RotateCcw className="size-3" />
              <span>Đặt lại</span>
            </Button>
            <Button
              type="button"
              size="sm"
              className="flex-1"
              onClick={() => setMobileFiltersOpen(false)}
            >
              Xem kết quả ({total})
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
