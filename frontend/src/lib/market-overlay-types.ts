/**
 * TypeScript interfaces for GET /api/v2/companies/{ticker}/market-overlay (UI-09b / B-06).
 * Matches `PriceRiskOverlayPayload` in `src/finintel/analytics/market_valuation.py` 100% field-for-field.
 */

export type ValuationFlag =
  | "VALID"
  | "NEGATIVE_EARNINGS"
  | "NEGATIVE_EQUITY"
  | "MISSING_EARNINGS"
  | "MISSING_EQUITY"
  | "MISSING_SHARES";

export type DrawdownHorizonKey = "T+30d" | "T+90d" | "T+180d" | "T+365d";

export interface DrawdownWindowMetrics {
  start_trade_date: string;
  end_trade_date: string;
  target_end_date: string;
  session_count: number;
  base_close_adj: number;
  peak_close_adj: number;
  peak_date: string;
  trough_close_adj: number;
  trough_date: string;
  end_close_adj: number;
  return_pct: number;
  drawdown_from_marker_pct: number;
  max_peak_to_trough_drawdown_pct: number;
}

export interface MarkerDrawdownBundle {
  horizons: {
    "T+30d"?: DrawdownWindowMetrics | null;
    "T+90d"?: DrawdownWindowMetrics | null;
    "T+180d"?: DrawdownWindowMetrics | null;
    "T+365d"?: DrawdownWindowMetrics | null;
    [key: string]: DrawdownWindowMetrics | null | undefined;
  };
  to_first_regulatory_event: DrawdownWindowMetrics | null;
  to_current: DrawdownWindowMetrics | null;
}

export interface ModelRiskMarker {
  marker_type: "MODEL_RISK_TIER";
  marker_kind: "FIRST_R4_UPGRADE" | "FIRST_R5_UPGRADE";
  risk_tier: "R4" | "R5";
  is_first_r4_or_r5: boolean;
  disclosure_date: string;
  date_basis: "PIT_DISCLOSURE" | "PERIOD_END_FALLBACK";
  aligned_trade_date: string | null;
  period_end_date: string;
  close_adj_at_marker: number | null;
  close_raw_at_marker: number | null;
  calibrated_distress_prob: number | null;
  risk_market_percentile: number | null;
  relative_risk_lift: number | null;
  drawdowns: MarkerDrawdownBundle | null;
}

export interface RegulatoryEventMarker {
  marker_type: "REGULATORY_EVENT";
  event_type: string;
  event_stage: string;
  event_date: string;
  effective_from: string;
  resolution_date: string | null;
  aligned_trade_date: string | null;
  close_adj_at_marker: number | null;
  close_raw_at_marker: number | null;
  raw_headline: string;
  source_provider: string;
  source_url: string;
  drawdowns: MarkerDrawdownBundle | null;
}

export interface PriceOverlayPoint {
  trade_date: string;
  close_adj: number;
  close_raw: number;
  volume: number;
  shares_outstanding_market: number | null;
  running_drawdown_pct: number;
  drawdown_from_first_warning_pct: number | null;
  market_cap_vnd: number | null;
  pe_ttm: number | null;
  pb_ratio: number | null;
  pe_flag: string | null;
  pb_flag: string | null;
  shares_source: string | null;
}

export interface WarningSummary {
  warning_source: "MODEL_RISK_TIER" | "REGULATORY_EVENT";
  warning_label: string;
  warning_date: string;
  aligned_trade_date: string | null;
  base_close_adj: number | null;
  latest_trade_date: string | null;
  latest_close_adj: number | null;
  horizons: {
    "T+30d"?: DrawdownWindowMetrics | null;
    "T+90d"?: DrawdownWindowMetrics | null;
    "T+180d"?: DrawdownWindowMetrics | null;
    "T+365d"?: DrawdownWindowMetrics | null;
    [key: string]: DrawdownWindowMetrics | null | undefined;
  };
  to_first_regulatory_event: DrawdownWindowMetrics | null;
  to_current: DrawdownWindowMetrics | null;
}

export interface CompanyMarketOverlayResponse {
  snapshot_id: string | null;
  issuer_id: string;
  ticker: string;
  as_of: string | null;
  latest_valuation: PriceOverlayPoint | null;
  price_series: PriceOverlayPoint[];
  model_markers: ModelRiskMarker[];
  regulatory_markers: RegulatoryEventMarker[];
  earliest_warning_summary: WarningSummary | null;
}
