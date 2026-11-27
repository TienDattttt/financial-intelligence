"use client";

import * as React from "react";
import { AlertTriangle, Pin, PinOff, RotateCcw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InfoTooltip } from "@/components/ui/info-tooltip";
import { Input } from "@/components/ui/primitives";
import {
  clampShockValue,
  DEFAULT_SHOCKS,
  SHOCK_BOUNDS,
  type ShockUiState,
} from "@/lib/stress-test-helpers";
import { cn } from "@/lib/utils";
import type { StressTestResponse } from "@/types/api";

function DualShockControl({
  id,
  label,
  unit,
  min,
  max,
  step,
  value,
  tooltip,
  onChange,
}: {
  id: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  value: number;
  tooltip?: { text: string; label?: string };
  onChange: (next: number) => void;
}) {
  const span = max - min;
  const hasZeroInside = min < 0 && max > 0 && span > 0;
  const zeroPct = hasZeroInside ? ((0 - min) / span) * 100 : null;

  return (
    <div className="space-y-1.5 border-b border-border/60 pb-2.5 last:border-b-0 last:pb-0">
      <div className="flex items-center justify-between gap-2 text-xs">
        <div className="flex min-w-0 items-center gap-1">
          <label htmlFor={id} className="truncate font-medium text-ink">
            {label}
          </label>
          {tooltip ? (
            <InfoTooltip text={tooltip.text} label={tooltip.label} />
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Input
            id={id}
            type="number"
            min={min}
            max={max}
            step={step}
            value={value}
            onChange={(e) => onChange(clampShockValue(e.target.value, min, max, 0))}
            className="num h-7 w-20 px-1.5 text-right text-xs"
          />
          <span className="w-16 shrink-0 text-[11px] text-ink-muted">{unit}</span>
        </div>
      </div>
      <div className="relative pt-0.5">
        {zeroPct !== null ? (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute top-0 h-2.5 w-px bg-border-strong"
            style={{ left: `${zeroPct}%` }}
          />
        ) : null}
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          aria-label={`${label} thanh trượt`}
          onChange={(e) => onChange(clampShockValue(e.target.value, min, max, 0))}
          className="relative z-10 w-full accent-accent-ink"
        />
      </div>
      <div className="num relative h-3.5 text-[10px] text-ink-subtle">
        <span className="absolute left-0 top-0">{min}</span>
        {zeroPct !== null ? (
          <span
            className="absolute top-0 -translate-x-1/2 font-medium text-ink-muted"
            style={{ left: `${zeroPct}%` }}
          >
            0
          </span>
        ) : null}
        <span className="absolute right-0 top-0">
          {max > 0 ? `+${max}` : max}
        </span>
      </div>
    </div>
  );
}

export function StressControlsDrawer({
  mobileControlsOpen,
  setMobileControlsOpen,
  shocks,
  setShocks,
  isExtreme,
  activeResult,
  activeIsCalculating,
  pinnedA,
  setPinnedA,
}: {
  mobileControlsOpen: boolean;
  setMobileControlsOpen: (open: boolean) => void;
  shocks: ShockUiState;
  setShocks: React.Dispatch<React.SetStateAction<ShockUiState>>;
  isExtreme: boolean;
  activeResult: StressTestResponse | null;
  activeIsCalculating: boolean;
  pinnedA: StressTestResponse | null;
  setPinnedA: (next: StressTestResponse | null) => void;
}) {
  return (
    <div
      id="stress-controls-drawer"
      role={mobileControlsOpen ? "dialog" : undefined}
      aria-modal={mobileControlsOpen ? true : undefined}
      aria-label={mobileControlsOpen ? "Tham số cú sốc vĩ mô" : undefined}
      className={cn(
        "lg:block lg:w-72 lg:shrink-0",
        mobileControlsOpen
          ? "fixed inset-0 z-40 flex bg-ink/30 lg:static lg:inset-auto lg:z-auto lg:bg-transparent"
          : "hidden",
      )}
      onClick={() => setMobileControlsOpen(false)}
    >
      <Card
        className="flex h-full w-80 max-w-[85vw] flex-col overflow-y-auto rounded-none border-r border-border lg:h-auto lg:w-full lg:max-w-none lg:overflow-visible lg:rounded-lg lg:border"
        onClick={(e) => e.stopPropagation()}
      >
        <CardHeader className="py-2.5">
          <CardTitle className="text-[14px]">Tham số cú sốc vĩ mô</CardTitle>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="xs"
              onClick={() => setShocks(DEFAULT_SHOCKS)}
            >
              <RotateCcw className="size-3" />
              Về 0
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="lg:hidden"
              aria-label="Đóng bảng tham số sốc"
              onClick={() => setMobileControlsOpen(false)}
            >
              <X className="size-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3 p-3.5">
          <div className="space-y-1">
            <label
              htmlFor="scenario-name-input"
              className="text-[11px] font-medium text-ink-muted"
            >
              Tên kịch bản hiện tại (Kịch bản B)
            </label>
            <Input
              id="scenario-name-input"
              value={shocks.scenarioName}
              maxLength={60}
              onChange={(e) =>
                setShocks((s) => ({ ...s, scenarioName: e.target.value }))
              }
              placeholder="Nhập tên kịch bản..."
              className="h-7 text-xs"
            />
          </div>

          <DualShockControl
            id="shock-rev"
            label="Biến động doanh thu"
            unit="%"
            min={SHOCK_BOUNDS.revPct.min}
            max={SHOCK_BOUNDS.revPct.max}
            step={SHOCK_BOUNDS.revPct.step}
            value={shocks.revPct}
            onChange={(v) =>
              setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", revPct: v }))
            }
          />
          <DualShockControl
            id="shock-gm"
            label="Biến động biên gộp"
            unit="điểm %"
            min={SHOCK_BOUNDS.gmPp.min}
            max={SHOCK_BOUNDS.gmPp.max}
            step={SHOCK_BOUNDS.gmPp.step}
            value={shocks.gmPp}
            tooltip={{
              label: "Giải thích đơn vị điểm %",
              text: "Điểm phần trăm (điểm %): thay đổi tuyệt đối của biên lợi nhuận gộp. Ví dụ biên gộp từ 20% giảm −2 điểm % còn 18%.",
            }}
            onChange={(v) =>
              setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", gmPp: v }))
            }
          />
          <DualShockControl
            id="shock-rate"
            label="Cú sốc lãi suất vay"
            unit="bps"
            min={SHOCK_BOUNDS.rateBps.min}
            max={SHOCK_BOUNDS.rateBps.max}
            step={SHOCK_BOUNDS.rateBps.step}
            value={shocks.rateBps}
            tooltip={{
              label: "Giải thích đơn vị bps",
              text: "100 bps (điểm cơ bản) = 1,0 điểm % lãi suất vay. Ví dụ +200 bps tương đương lãi suất vay tăng thêm 2,0 điểm %.",
            }}
            onChange={(v) =>
              setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", rateBps: v }))
            }
          />
          <DualShockControl
            id="shock-dso"
            label="Tăng số ngày thu tiền (DSO)"
            unit="ngày"
            min={SHOCK_BOUNDS.dsoDays.min}
            max={SHOCK_BOUNDS.dsoDays.max}
            step={SHOCK_BOUNDS.dsoDays.step}
            value={shocks.dsoDays}
            tooltip={{
              label: "Giải thích chỉ số DSO",
              text: "DSO (Days Sales Outstanding): số ngày thu tiền bình quân từ khách hàng. DSO tăng làm tăng vốn lưu động bị chiếm dụng và giảm dòng tiền CFO.",
            }}
            onChange={(v) =>
              setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", dsoDays: v }))
            }
          />
          <DualShockControl
            id="shock-dio"
            label="Tăng số ngày tồn kho (DIO)"
            unit="ngày"
            min={SHOCK_BOUNDS.dioDays.min}
            max={SHOCK_BOUNDS.dioDays.max}
            step={SHOCK_BOUNDS.dioDays.step}
            value={shocks.dioDays}
            tooltip={{
              label: "Giải thích chỉ số DIO",
              text: "DIO (Days Inventory Outstanding): số ngày lưu kho bình quân. DIO tăng làm ứ đọng vốn ở hàng tồn kho và gây áp lực lên dòng tiền CFO.",
            }}
            onChange={(v) =>
              setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", dioDays: v }))
            }
          />
          <DualShockControl
            id="shock-fx"
            label="Mất giá tỷ giá VND (USD/VND)"
            unit="%"
            min={SHOCK_BOUNDS.fxPct.min}
            max={SHOCK_BOUNDS.fxPct.max}
            step={SHOCK_BOUNDS.fxPct.step}
            value={shocks.fxPct}
            tooltip={{
              label: "Giải thích cú sốc tỷ giá FX",
              text: "Mức mất giá của VND so với USD (ví dụ +10% làm tăng giá trị quy đổi VND của nợ vay ngoại tệ ròng). Chỉ áp dụng cho doanh nghiệp có công bố nợ ngoại tệ ròng.",
            }}
            onChange={(v) =>
              setShocks((s) => ({ ...s, scenarioName: "Tùy chỉnh", fxPct: v }))
            }
          />
          <p className="rounded border border-border bg-surface-muted/60 px-2.5 py-1.5 text-[11px] leading-4 text-ink-muted">
            Cú sốc tỷ giá chỉ áp dụng cho doanh nghiệp có công bố nợ ngoại tệ ròng trong thuyết minh; các doanh nghiệp khác giữ nguyên trạng thái chưa công bố.
          </p>

          {isExtreme ? (
            <div className="flex items-start gap-1.5 rounded border border-risk-mid/35 bg-risk-mid-soft p-2 text-[11px] text-risk-mid">
              <AlertTriangle
                className="mt-0.5 size-3.5 shrink-0"
                aria-hidden="true"
              />
              <span>
                Mức sốc cực đoan so với lịch sử doanh nghiệp niêm yết Việt Nam; kết quả phản ánh kịch bản căng thẳng hệ thống.
              </span>
            </div>
          ) : null}

          <div className="space-y-1.5 border-t border-border/60 pt-2">
            <p className="text-[11px] font-medium text-ink-muted">
              Đối chiếu song song A vs B
            </p>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="flex-1"
                disabled={!activeResult || activeIsCalculating}
                onClick={() => activeResult && setPinnedA(activeResult)}
              >
                <Pin className="size-3.5" />
                {pinnedA ? "Cập nhật kịch bản A" : "So sánh A vs B (Ghim A)"}
              </Button>
              {pinnedA ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label="Thoát chế độ so sánh A vs B"
                  onClick={() => setPinnedA(null)}
                >
                  <PinOff className="size-3.5" />
                </Button>
              ) : null}
            </div>
          </div>

          <div className="pt-1 lg:hidden">
            <Button
              type="button"
              size="sm"
              className="w-full"
              onClick={() => setMobileControlsOpen(false)}
            >
              Xem kết quả mô phỏng
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
