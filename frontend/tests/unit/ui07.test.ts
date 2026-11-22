import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  buildTierReliabilityData,
  buildWalkForwardComparisonData,
  buildWalkForwardYearlySeries,
} from "@/components/charts/ModelCardCharts";
import {
  formatCreditTrio,
  formatDateBasisLabel,
  formatModelDisplayName,
  formatRegulatoryEventStage,
  formatRegulatoryEventType,
  formatSharesSourceLabel,
  getDistressRiskBadge,
  getMlFeatureLabel,
  getRiskTierMeta,
  getValuationFlagMeta,
  ML_FEATURE_LABELS,
  RISK_TIER_ORDER,
  sanitizeModelLimitationText,
} from "@/lib/domain-labels";
import type {
  RiskTierReliabilityReportItem,
  WalkForwardModelSummaryItem,
  WalkForwardYearItem,
} from "@/types/api";

const WORKSPACE_FRONTEND_ROOT = path.resolve(__dirname, "../..");

describe("UI-07 Credit Trio (R1–R5 TTC + Top X% PIT + 1Y PD × nền) & Domain Translations", () => {
  it("maps R1–R5 tiers monotonically with institutional tones and clean Vietnamese labels", () => {
    const tiers = RISK_TIER_ORDER.map((t) => getRiskTierMeta(t));
    expect(tiers.map((t) => t.badgeText)).toEqual(["R1", "R2", "R3", "R4", "R5"]);
    expect(tiers.map((t) => t.tone)).toEqual(["low", "low", "mid", "high", "high"]);
    expect(tiers.map((t) => t.rankOrder)).toEqual([1, 2, 3, 4, 5]);
    expect(getRiskTierMeta(null).tone).toBe("neutral");
    expect(getRiskTierMeta("UNKNOWN").badgeText).toBe("N/A");
  });

  it("formats the Credit Trio (R1–R5, Top X% rủi ro nhất, 1Y PD, × nền) accurately", () => {
    const trio = formatCreditTrio({
      risk_tier: "R4",
      risk_market_percentile: 96.4,
      calibrated_distress_prob: 0.142,
      relative_risk_lift: 4.8,
      market_base_rate: 0.029,
    });
    expect(trio.tierMeta.badgeText).toBe("R4");
    expect(trio.tierMeta.tone).toBe("high");
    expect(trio.topPercentLabel).toBe("Top 3,6% rủi ro nhất");
    expect(trio.pdLabel).toBe("14,2%");
    expect(trio.liftLabel).toBe("4,8× nền");
    expect(trio.baseRateLabel).toBe("Mức nền thị trường 2,9%");
    expect(trio.compactSummary).toBe("R4 · Top 3,6% rủi ro nhất · 4,8× nền");

    const topEdge = formatCreditTrio({
      risk_tier: "R5",
      risk_market_percentile: 100,
      calibrated_distress_prob: 0.35,
      relative_risk_lift: 12.1,
    });
    expect(topEdge.topPercentLabel).toBe("Top 0,1% rủi ro nhất");
  });

  it("contains zero hardcoded <3% / 3–8% / >=8% thresholds in UI-07 pages and helpers", () => {
    // Deprecated helper no longer branches on hardcoded 0.03 / 0.08 buckets
    expect(getDistressRiskBadge(0.01).tone).toBe("neutral");
    expect(getDistressRiskBadge(0.05).tone).toBe("neutral");
    expect(getDistressRiskBadge(0.15).tone).toBe("neutral");
    expect(getDistressRiskBadge(0.05, "R3").label).toContain("R3");

    const filesToAudit = [
      "src/lib/domain-labels.ts",
      "src/app/page.tsx",
      "src/app/compare/page.tsx",
      "src/app/watchlist/page.tsx",
      "src/app/methodology/page.tsx",
      "src/app/companies/[ticker]/page.tsx",
    ];

    for (const rel of filesToAudit) {
      const content = fs.readFileSync(path.join(WORKSPACE_FRONTEND_ROOT, rel), "utf8");
      expect(content).not.toMatch(/<\s*3%|≥\s*8%|>=\s*8%/);
      expect(content).not.toMatch(/\bprob\s*<\s*0\.03\b|\bprob\s*<\s*0\.08\b/);
    }
  });

  it("translates all technical flags, shares sources, regulatory events, and ML features cleanly", () => {
    const valuationFlags = [
      "VALID",
      "NEGATIVE_EARNINGS",
      "NEGATIVE_EQUITY",
      "MISSING_EARNINGS",
      "MISSING_EQUITY",
      "MISSING_SHARES",
    ];
    for (const flag of valuationFlags) {
      const meta = getValuationFlagMeta(flag);
      expect(meta.label.length).toBeGreaterThan(2);
      expect(meta.explanation).not.toContain(flag);
      expect(meta.isValid).toBe(flag === "VALID");
    }

    const sharesSources = [
      "ESTIMATED_SHARES_OUTSTANDING",
      "BS_ORDINARY_SHARES_EXPLICIT_ZERO_TREASURY",
      "BS_ORDINARY_SHARES_NULL_TREASURY_ASSUMED_ZERO",
      "SHARES_OUTSTANDING_MARKET",
      "MISSING_SHARES",
    ];
    for (const src of sharesSources) {
      const translated = formatSharesSourceLabel(src);
      expect(translated).not.toContain(src);
      expect(translated).not.toContain("BS_");
    }

    expect(formatDateBasisLabel("PIT_DISCLOSURE")).not.toContain("PIT_DISCLOSURE");
    expect(formatDateBasisLabel("PERIOD_END_FALLBACK")).not.toContain("PERIOD_END_FALLBACK");

    for (const evType of [
      "CONTROL",
      "TRADING_RESTRICTION",
      "SUSPENSION",
      "FORCED_DELISTING",
      "ADVERSE_OR_DISCLAIMER_AUDIT",
    ]) {
      const res = formatRegulatoryEventType(evType);
      expect(res.label).not.toContain(evType);
    }
    expect(formatRegulatoryEventStage("FIRST_ENTRY")).toBe("Lần đầu áp dụng");
    expect(formatRegulatoryEventStage("ESCALATION")).toBe("Nâng mức chế tài");

    for (const feat of Object.keys(ML_FEATURE_LABELS)) {
      expect(getMlFeatureLabel(feat)).not.toBe(feat);
    }

    const sanitized = sanitizeModelLimitationText(
      "Phạm vi (CORPORATE); chế độ MARKET_ONLY_BEFORE_2026; revision_no = 1; REGIME_FALLBACK; test.parquet",
    );
    expect(sanitized).not.toMatch(/CORPORATE|MARKET_ONLY_BEFORE_2026|revision_no|REGIME_FALLBACK|\.parquet/);
  });

  it("builds Walk-Forward 3-model comparison with Bootstrap 95% CI, yearly series, and R1–R5 tier reliability", () => {
    const summaries: WalkForwardModelSummaryItem[] = [
      {
        model: "Baseline_1_AltmanZ",
        yearly_roc_auc_mean: 0.71,
        yearly_pr_auc_mean: 0.14,
        pooled_point: { roc_auc: 0.72, pr_auc: 0.15, brier_skill_score: 0.05, recall_at_top_10_percent: 0.42 },
        pooled_ci95: { roc_auc: [0.66, 0.78], pr_auc: [0.1, 0.21] },
        n_bootstrap_valid: 1000,
        n_clusters: 375,
      },
      {
        model: "XGBoost",
        yearly_roc_auc_mean: 0.84,
        yearly_pr_auc_mean: 0.29,
        pooled_point: { roc_auc: 0.85, pr_auc: 0.31, brier_skill_score: 0.22, recall_at_top_10_percent: 0.64 },
        pooled_ci95: { roc_auc: [0.8, 0.9], pr_auc: [0.23, 0.39] },
        n_bootstrap_valid: 1000,
        n_clusters: 375,
      },
      {
        model: "Baseline_2_Logit",
        yearly_roc_auc_mean: 0.78,
        yearly_pr_auc_mean: 0.2,
        pooled_point: { roc_auc: 0.79, pr_auc: 0.21, brier_skill_score: 0.12, recall_at_top_10_percent: 0.52 },
        pooled_ci95: { roc_auc: [0.73, 0.84], pr_auc: [0.15, 0.28] },
        n_bootstrap_valid: 1000,
        n_clusters: 375,
      },
    ];

    const comp = buildWalkForwardComparisonData(summaries, "roc_auc");
    expect(comp.map((c) => c.modelKey)).toEqual([
      "Baseline_1_AltmanZ",
      "Baseline_2_Logit",
      "XGBoost",
    ]);
    expect(comp[2].modelLabel).toBe(formatModelDisplayName("XGBoost"));
    expect(comp[2].point).toBe(0.85);
    expect(comp[2].ciLow).toBe(0.8);
    expect(comp[2].ciHigh).toBe(0.9);

    const perYear: WalkForwardYearItem[] = [
      {
        year: 2022,
        train_rows: 1000,
        train_positive_rows: 30,
        train_unique_positive_issuers: 10,
        test_rows: 350,
        test_positive_rows: 12,
        test_unique_positive_issuers: 6,
        unseen_rows: 50,
        unseen_positive_rows: 3,
        unseen_unique_positive_issuers: 2,
        models: {
          Baseline_1_AltmanZ: { roc_auc: 0.7 },
          Baseline_2_Logit: { roc_auc: 0.77 },
          XGBoost: { roc_auc: 0.83 },
        },
        true_unseen_models: {
          Baseline_1_AltmanZ: { roc_auc: 0.68 },
          Baseline_2_Logit: { roc_auc: 0.74 },
          XGBoost: { roc_auc: 0.81 },
        },
      },
      {
        year: 2021,
        train_rows: 700,
        train_positive_rows: 20,
        train_unique_positive_issuers: 8,
        test_rows: 300,
        test_positive_rows: 10,
        test_unique_positive_issuers: 5,
        unseen_rows: 40,
        unseen_positive_rows: 2,
        unseen_unique_positive_issuers: 1,
        models: {
          Baseline_1_AltmanZ: { roc_auc: 0.69 },
          Baseline_2_Logit: { roc_auc: 0.75 },
          XGBoost: { roc_auc: 0.82 },
        },
        true_unseen_models: {
          Baseline_1_AltmanZ: { roc_auc: 0.65 },
          Baseline_2_Logit: { roc_auc: 0.72 },
          XGBoost: { roc_auc: 0.79 },
        },
      },
    ];

    const yearlyAll = buildWalkForwardYearlySeries(perYear, "roc_auc", false);
    expect(yearlyAll.years).toEqual(["2021", "2022"]);
    expect(yearlyAll.series.find((s) => s.modelKey === "XGBoost")?.values).toEqual([0.82, 0.83]);

    const yearlyUnseen = buildWalkForwardYearlySeries(perYear, "roc_auc", true);
    expect(yearlyUnseen.series.find((s) => s.modelKey === "XGBoost")?.values).toEqual([0.79, 0.81]);

    const reliability: RiskTierReliabilityReportItem = {
      evaluation_source: "WALK_FORWARD_POOLED_OOS",
      cutoffs: { R2_min_pd: 0.01, R3_min_pd: 0.025, R4_min_pd: 0.06, R5_min_pd: 0.15 },
      is_monotone_non_decreasing: true,
      tiers: [
        { risk_tier: "R5", count: 50, share: 0.05, positive_count: 15, empirical_event_rate: 0.3, mean_predicted_pd: 0.25 },
        { risk_tier: "R1", count: 500, share: 0.5, positive_count: 2, empirical_event_rate: 0.004, mean_predicted_pd: 0.006 },
      ],
    };
    const tierRows = buildTierReliabilityData(reliability);
    expect(tierRows.map((r) => r.tier)).toEqual(["R1", "R5"]);
    expect(tierRows[0].empiricalRatePct).toBeCloseTo(0.4, 5);
    expect(tierRows[1].predictedPdPct).toBeCloseTo(25, 5);
  });
});
