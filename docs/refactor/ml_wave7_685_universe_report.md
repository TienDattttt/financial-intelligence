# Wave 7B Quantitative ML Re-Evaluation Report — `ML-03b` 685-Issuer Universe (`2021–2025`)

- **Generated (UTC):** `2026-11-26T12:44:55.331727+00:00`
- **Dataset (Read-Only Input):** `data/universe_600_800/curated/financial_facts/ml03b_full/financial_facts.parquet` + `data/universe_600_800/curated/issuers.parquet` (`685` corporate issuers, `2,379,058` raw facts -> `2,320,823` QG1-clean facts, `48/685` critical quarantined = `7.02%`, `as_of = 2026-11-26`, `industry_snapshot_date = 2026-11-19` per `ML-02`)
- **Exogenous Ground Truth (Port 1):** `data/regulatory_ledger/regulatory_events_600_800.parquet` (`1,406` verified events across `318` distinct positive corporate issuers; `100%` `source_url` + `payload_sha256`)
- **Peer Standardization & Feature Contract:** `PIT_REGIME_SYMMETRIC` + `SECTOR_ADJUSTED_FEATURES` (`23` ML-05 features + contextual missingness indicators)
- **Data Hygiene Notes:** Harmonized `20` stub-transition BS rows (`SJ1` 2016 ANNUAL 9-month stub) to match IS/CF `fiscal_year_start_date`; `0` SMOTE/synthetic rows; `0` lookahead to `y_test.mean()` or future folds.

---

## 1. `GATE-ML-1..6` Compliance Checklist

| Gate | Requirement | Measured 685-Issuer Verification | Status |
|---|---|---|---|
| **`GATE-ML-1`** | Provenance & Anti-Survivorship (`ML-03b`, `H-01`, `H-02`) | `685` corporate issuers (HOSE 280 / HNX 110 / UPCOM 295), `1,406` Port 1 events (`318` pos issuers), `100%` `source_url` + `payload_sha256`; unpenalized `STATUTORY_LAG (+0d)` upper-bound & `STATUTORY_LAG + 37d` sensitivity evaluated. | **PASS** |
| **`GATE-ML-2`** | Zero Leakage, Symmetric Horizon (`B-01`), Continuous State (`H-05`), `0%` TTM Overlap | `label_available_at = _period_available_at + 365d` symmetrically for `y=1` & `y=0`; continuous `[event_date, resolution_date)` intervals; Mode A `0%` TTM overlap; backdated `2016-01-01` issuers = `0` (`industry_snapshot_date = 2026-11-19`), ML training uses `PIT_REGIME_SYMMETRIC` (ICB 2026 scope applies only to Layer 4A `industry_benchmarks`). | **PASS** |
| **`GATE-ML-3`** | Fair 3-Model Comparison & Unpenalized Platt (`H-07`) | Exact common sample across `Baseline_1_AltmanZ`, `Baseline_2_Logit` (`clip=[-5, 5]`), and `Monotone_XGBoost`; Fold(s) 2021 fallback `STRATIFIED_GROUP_OOF`, remaining folds `PURGED_TEMPORAL_OOF` (2021:STRATIFIED_GROUP_OOF, 2022:PURGED_TEMPORAL_OOF, 2023:PURGED_TEMPORAL_OOF, 2024:PURGED_TEMPORAL_OOF, 2025:PURGED_TEMPORAL_OOF); `MarginPlattCalibrator(penalty=None, slope clamped [0.15, 5.0])`; BSS benchmarked vs historical `train_prevalence_Y`. | **PASS** |
| **`GATE-ML-4`** | 2-Way Purged Expanding Walk-Forward (`2021–2025`), Bootstrap 95% CI, `True Unseen Issuers` | Full 5-year expanding evaluation (`2021 -> 2025`), issuer-clustered bootstrap (`issuer_id`), full `True Unseen Issuers` isolation across Branch 1, Branch 2 (`+0d`), and Branch 2b (`+37d`). | **PASS** |
| **`GATE-ML-5`** | Anti-Overfitting, `R1 → R5` Monotonicity & `TT200 → TT99` PSI (`M-05`) | Positive XGBoost BSS in **5/5** (Branch 1) & **5/5** (Branch 2) years; `TT200 -> TT99` mean PSI = **`0.0235`** (`0/23` > `0.25`, `R4+R5` `-2.97 pp` in 2026); PIT-cutoff OOS monotonicity: Branch 1=`True` (20.1×), Branch 2=`True` (27.4×), OOF=`True` (19.0×). | **PASS** |
| **`GATE-ML-6`** | End-to-End DuckLake Persistence & Serving Parity (`ML-08`, `H-04`) | Published `model_card_meta.parquet` + `ml_scores_and_explanations.parquet` (`23` features, `model_role` preserved for `OUT_OF_SAMPLE_*` vs `IN_SAMPLE_TRAIN`) into DuckLake snapshot; read back cleanly via `ParquetRepository` & `DashboardQueryService.get_model_card()`. | **PASS** |

---

## 2. Mode B & Mode A Onset Label Distribution (`685` Issuers, `as_of = 2026-11-26`)

| Fiscal Year | Mode B Branch 1 (`EXACT_FILING`) Rows | Pos Rows | Pos Issuers | Mode B Branch 2 (`EXACT + STATUTORY_LAG`) Rows | Pos Rows | Pos Issuers | Mode A (`0%` TTM) Rows | Pos Rows | Pos Issuers |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2018 | 596 | 23 | 19 | 2,007 | 73 | 26 | 451 | 13 | 13 |
| 2019 | 1,976 | 57 | 19 | 2,027 | 58 | 19 | 1,898 | 53 | 31 |
| 2020 | 1,995 | 74 | 29 | 2,026 | 78 | 31 | 1,983 | 54 | 31 |
| 2021 | 1,970 | 154 | 60 | 2,002 | 161 | 62 | 1,986 | 48 | 29 |
| 2022 | 1,876 | 161 | 70 | 1,894 | 169 | 73 | 2,026 | 93 | 46 |
| 2023 | 1,781 | 99 | 42 | 1,788 | 100 | 43 | 1,977 | 49 | 33 |
| 2024 | 1,724 | 87 | 39 | 1,728 | 89 | 39 | 1,960 | 40 | 23 |
| 2025 | 1,236 | 46 | 28 | 1,238 | 46 | 28 | 0 | 0 | 0 |
| **Total (`2018–2025`)** | **13,154** | **701** | — | **14,710** | **774** | — | **12,281** | **350** | — |

---

## 3. 2-Way Purged Expanding Walk-Forward Results (`2021 → 2025`)

### 3.1. `H-01` Branch 1 — Strict `EXACT_FILING` (Guardrail G1: `require_exact_filing=True`)

| Test Year | Inner OOF Strategy | `n_est` | Platt Slope | Train Rows (Pos / Iss) | Test Common (Pos / Iss) | Unseen Common (Pos / Iss) | XGB Train PR-AUC | OOS ROC-AUC (Alt / Log / **XGB**) | OOS PR-AUC (Alt / Log / **XGB**) | OOS BSS (Alt / Log / **XGB**) | OOS R@5% (Alt / Log / **XGB**) | OOS R@10% (Alt / Log / **XGB**) | Unseen ROC-AUC (Alt / Log / **XGB**) |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| 2021 | `STRATIFIED_GROUP_OOF` | 26 | 3.28 | 2,059 (64 / 33) | 1,927 (146 / 58) | 1,895 (146 / 58) | 0.3833 | 0.631 / 0.710 / **0.742** | 0.246 / 0.284 / **0.235** | +0.009 / +0.083 / **+0.081** | 30.1% / 27.4% / **21.2%** | 36.3% / 39.0% / **40.4%** | 0.632 / 0.713 / **0.746** |
| 2022 | `PURGED_TEMPORAL_OOF` | 17 | 3.67 | 4,050 (130 / 54) | 1,854 (159 / 69) | 1,796 (151 / 66) | 0.2628 | 0.753 / 0.798 / **0.858** | 0.458 / 0.363 / **0.395** | +0.098 / +0.096 / **+0.137** | 36.5% / 28.3% / **29.6%** | 47.2% / 46.5% / **47.2%** | 0.743 / 0.795 / **0.858** |
| 2023 | `PURGED_TEMPORAL_OOF` | 78 | 0.86 | 6,031 (257 / 99) | 1,781 (99 / 42) | 1,670 (87 / 38) | 0.3606 | 0.678 / 0.873 / **0.894** | 0.208 / 0.307 / **0.341** | +0.028 / +0.112 / **+0.173** | 26.3% / 33.3% / **36.4%** | 43.4% / 58.6% / **60.6%** | 0.713 / 0.887 / **0.897** |
| 2024 | `PURGED_TEMPORAL_OOF` | 61 | 1.14 | 7,949 (437 / 140) | 1,712 (81 / 37) | 1,581 (70 / 33) | 0.3281 | 0.638 / 0.837 / **0.866** | 0.172 / 0.335 / **0.279** | +0.022 / +0.120 / **+0.138** | 27.2% / 45.7% / **35.8%** | 37.0% / 61.7% / **58.0%** | 0.607 / 0.830 / **0.860** |
| 2025 | `PURGED_TEMPORAL_OOF` | 48 | 1.42 | 9,748 (543 / 160) | 1,234 (46 / 28) | 1,118 (40 / 25) | 0.3274 | 0.508 / 0.795 / **0.807** | 0.064 / 0.355 / **0.315** | -0.020 / +0.119 / **+0.119** | 10.9% / 45.7% / **43.5%** | 19.6% / 54.3% / **56.5%** | 0.520 / 0.778 / **0.797** |

#### Branch 1 Summary (`ALL OOS` & `TRUE UNSEEN ISSUERS`, `n=1,000` Cluster Bootstrap 95% CI)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=8,508`, `531` pos)** | `Baseline_1_AltmanZ` | 0.642 ± 0.089 | 0.595 [0.545, 0.648] | 0.230 ± 0.145 | 0.168 [0.126, 0.224] | 0.0571 | +0.039 [0.020, 0.063] | 18.8% | 24.9% |
| **ALL OOS (`n=8,508`, `531` pos)** | `Baseline_2_Logit` | 0.803 ± 0.061 | 0.729 [0.680, 0.778] | 0.329 ± 0.033 | 0.262 [0.204, 0.327] | 0.0534 | +0.101 [0.076, 0.126] | 30.7% | 41.8% |
| **ALL OOS (`n=8,508`, `531` pos)** | **`XGBoost`** | 0.833 ± 0.060 | 0.762 [0.717, 0.810] | 0.313 ± 0.061 | 0.252 [0.200, 0.321] | 0.0518 | +0.127 [0.093, 0.160] | 25.4% | 47.3% |
| **TRUE UNSEEN ISSUERS (`n=8,060`, `494` pos)** | `Baseline_1_AltmanZ` | 0.643 ± 0.089 | 0.593 [0.537, 0.651] | 0.238 ± 0.147 | 0.175 [0.129, 0.230] | 0.0561 | +0.039 [0.018, 0.064] | 19.2% | 25.9% |
| **TRUE UNSEEN ISSUERS (`n=8,060`, `494` pos)** | `Baseline_2_Logit` | 0.801 ± 0.065 | 0.724 [0.670, 0.774] | 0.322 ± 0.028 | 0.260 [0.205, 0.323] | 0.0527 | +0.097 [0.072, 0.119] | 31.0% | 41.3% |
| **TRUE UNSEEN ISSUERS (`n=8,060`, `494` pos)** | **`XGBoost`** | 0.832 ± 0.060 | 0.758 [0.708, 0.807] | 0.313 ± 0.061 | 0.250 [0.200, 0.323] | 0.0511 | +0.125 [0.094, 0.158] | 27.9% | 47.6% |

### 3.2. `H-01` Branch 2 — Expanded `EXACT + STATUTORY_LAG (+0d)` Unpenalized Statutory Deadline Upper-Bound (`require_exact_filing=False`)

Retains mandatory-delisted UPCOM/HNX issuers missing CafeF/VCI PDF disclosure links at the unpenalized Circular 96 statutory deadline (`+0d` upper-bound, preventing survivorship bias across the 247 distressed UPCOM firms; see Section 3.2b for empirical `+37d` late-filing delay sensitivity).

| Test Year | Inner OOF Strategy | `n_est` | Platt Slope | Train Rows (Pos / Iss) | Test Common (Pos / Iss) | Unseen Common (Pos / Iss) | XGB Train PR-AUC | OOS ROC-AUC (Alt / Log / **XGB**) | OOS PR-AUC (Alt / Log / **XGB**) | OOS BSS (Alt / Log / **XGB**) | OOS R@5% (Alt / Log / **XGB**) | OOS R@10% (Alt / Log / **XGB**) | Unseen ROC-AUC (Alt / Log / **XGB**) |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| 2021 | `STRATIFIED_GROUP_OOF` | 70 | 1.35 | 11,508 (206 / 59) | 1,958 (153 / 61) | 1,926 (153 / 61) | 0.2611 | 0.641 / 0.774 / **0.774** | 0.254 / 0.337 / **0.235** | +0.018 / +0.100 / **+0.073** | 29.4% / 26.8% / **15.7%** | 37.3% / 40.5% / **32.0%** | 0.641 / 0.777 / **0.779** |
| 2022 | `PURGED_TEMPORAL_OOF` | 48 | 1.48 | 13,536 (274 / 81) | 1,869 (167 / 72) | 1,811 (159 / 69) | 0.2533 | 0.746 / 0.832 / **0.848** | 0.451 / 0.413 / **0.385** | +0.091 / +0.049 / **+0.123** | 35.3% / 29.9% / **29.3%** | 45.5% / 45.5% / **41.9%** | 0.737 / 0.834 / **0.849** |
| 2023 | `PURGED_TEMPORAL_OOF` | 15 | 3.10 | 15,544 (407 / 127) | 1,788 (100 / 43) | 1,677 (88 / 39) | 0.1844 | 0.677 / 0.885 / **0.898** | 0.207 / 0.374 / **0.294** | +0.033 / +0.092 / **+0.142** | 26.0% / 38.0% / **27.0%** | 43.0% / 61.0% / **59.0%** | 0.711 / 0.905 / **0.898** |
| 2024 | `PURGED_TEMPORAL_OOF` | 85 | 0.84 | 17,491 (598 / 167) | 1,715 (82 / 38) | 1,584 (71 / 34) | 0.3015 | 0.638 / 0.842 / **0.853** | 0.171 / 0.315 / **0.241** | +0.023 / +0.154 / **+0.120** | 26.8% / 40.2% / **37.8%** | 36.6% / 58.5% / **52.4%** | 0.608 / 0.837 / **0.847** |
| 2025 | `PURGED_TEMPORAL_OOF` | 77 | 1.01 | 19,295 (706 / 187) | 1,236 (46 / 28) | 1,120 (40 / 25) | 0.3234 | 0.508 / 0.794 / **0.807** | 0.064 / 0.299 / **0.257** | -0.009 / +0.080 / **+0.096** | 10.9% / 41.3% / **43.5%** | 19.6% / 54.3% / **56.5%** | 0.520 / 0.782 / **0.797** |

#### Branch 2 Summary (`ALL OOS` & `TRUE UNSEEN ISSUERS`, `n=1,000` Cluster Bootstrap 95% CI)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=8,566`, `548` pos)** | `Baseline_1_AltmanZ` | 0.642 ± 0.087 | 0.579 [0.529, 0.631] | 0.229 ± 0.142 | 0.152 [0.111, 0.199] | 0.0591 | +0.041 [0.022, 0.063] | 14.8% | 21.4% |
| **ALL OOS (`n=8,566`, `548` pos)** | `Baseline_2_Logit` | 0.825 ± 0.043 | 0.752 [0.712, 0.788] | 0.347 ± 0.046 | 0.249 [0.193, 0.313] | 0.0561 | +0.089 [0.067, 0.110] | 28.8% | 40.3% |
| **ALL OOS (`n=8,566`, `548` pos)** | **`XGBoost`** | 0.836 ± 0.047 | 0.767 [0.725, 0.808] | 0.282 ± 0.062 | 0.216 [0.174, 0.268] | 0.0549 | +0.110 [0.083, 0.134] | 23.4% | 41.1% |
| **TRUE UNSEEN ISSUERS (`n=8,118`, `511` pos)** | `Baseline_1_AltmanZ` | 0.643 ± 0.087 | 0.577 [0.526, 0.625] | 0.238 ± 0.144 | 0.152 [0.112, 0.205] | 0.0582 | +0.041 [0.021, 0.065] | 14.5% | 21.5% |
| **TRUE UNSEEN ISSUERS (`n=8,118`, `511` pos)** | `Baseline_2_Logit` | 0.827 ± 0.052 | 0.756 [0.716, 0.796] | 0.346 ± 0.070 | 0.256 [0.198, 0.325] | 0.0554 | +0.087 [0.066, 0.107] | 29.0% | 41.5% |
| **TRUE UNSEEN ISSUERS (`n=8,118`, `511` pos)** | **`XGBoost`** | 0.834 ± 0.047 | 0.765 [0.722, 0.806] | 0.277 ± 0.064 | 0.211 [0.168, 0.267] | 0.0542 | +0.106 [0.081, 0.131] | 23.5% | 40.9% |

### 3.2b. `H-01` Branch 2 Empirical Delay Sensitivity — `EXACT + STATUTORY_LAG + 37d` (`p90` UPCOM Distress Delay)

Applies the empirical `p90 = +37d` UPCOM distress late-filing offset onto `STATUTORY_LAG` records (`require_exact_filing=False`) to eliminate early-availability optimism on non-PDF filings.

| Test Year | Inner OOF Strategy | `n_est` | Platt Slope | Train Rows (Pos / Iss) | Test Common (Pos / Iss) | Unseen Common (Pos / Iss) | XGB Train PR-AUC | OOS ROC-AUC (Alt / Log / **XGB**) | OOS PR-AUC (Alt / Log / **XGB**) | OOS BSS (Alt / Log / **XGB**) | OOS R@5% (Alt / Log / **XGB**) | OOS R@10% (Alt / Log / **XGB**) | Unseen ROC-AUC (Alt / Log / **XGB**) |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| 2021 | `STRATIFIED_GROUP_OOF` | 43 | 1.88 | 11,504 (210 / 59) | 1,958 (153 / 61) | 1,926 (153 / 61) | 0.2299 | 0.641 / 0.775 / **0.771** | 0.254 / 0.302 / **0.209** | +0.018 / +0.086 / **+0.064** | 29.4% / 24.8% / **15.0%** | 37.3% / 43.1% / **28.1%** | 0.641 / 0.779 / **0.776** |
| 2022 | `PURGED_TEMPORAL_OOF` | 50 | 1.39 | 13,532 (279 / 81) | 1,869 (168 / 72) | 1,811 (160 / 69) | 0.2326 | 0.743 / 0.833 / **0.839** | 0.449 / 0.410 / **0.359** | +0.090 / +0.166 / **+0.120** | 35.1% / 29.8% / **28.6%** | 45.2% / 45.8% / **38.1%** | 0.733 / 0.835 / **0.840** |
| 2023 | `PURGED_TEMPORAL_OOF` | 102 | 0.75 | 15,540 (412 / 127) | 1,788 (100 / 43) | 1,677 (88 / 39) | 0.2869 | 0.677 / 0.878 / **0.895** | 0.207 / 0.344 / **0.339** | +0.033 / +0.138 / **+0.169** | 26.0% / 37.0% / **31.0%** | 43.0% / 61.0% / **61.0%** | 0.711 / 0.891 / **0.899** |
| 2024 | `PURGED_TEMPORAL_OOF` | 73 | 0.88 | 17,487 (604 / 167) | 1,715 (82 / 38) | 1,584 (71 / 34) | 0.2947 | 0.638 / 0.841 / **0.854** | 0.171 / 0.299 / **0.258** | +0.023 / +0.137 / **+0.109** | 26.8% / 45.1% / **31.7%** | 36.6% / 57.3% / **53.7%** | 0.608 / 0.835 / **0.848** |
| 2025 | `PURGED_TEMPORAL_OOF` | 70 | 1.03 | 19,291 (712 / 187) | 1,236 (46 / 28) | 1,120 (40 / 25) | 0.3055 | 0.508 / 0.786 / **0.803** | 0.064 / 0.316 / **0.331** | -0.009 / +0.076 / **+0.109** | 10.9% / 41.3% / **45.7%** | 19.6% / 54.3% / **58.7%** | 0.520 / 0.768 / **0.793** |

#### Branch 2b (`+37d`) Summary (`ALL OOS` & `TRUE UNSEEN ISSUERS`, `n=1,000` Cluster Bootstrap 95% CI)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=8,566`, `549` pos)** | `Baseline_1_AltmanZ` | 0.641 ± 0.086 | 0.579 [0.529, 0.631] | 0.229 ± 0.141 | 0.152 [0.111, 0.199] | 0.0592 | +0.041 [0.022, 0.062] | 14.8% | 21.5% |
| **ALL OOS (`n=8,566`, `549` pos)** | `Baseline_2_Logit` | 0.823 ± 0.042 | 0.771 [0.731, 0.809] | 0.334 ± 0.046 | 0.276 [0.219, 0.344] | 0.0539 | +0.127 [0.098, 0.152] | 30.4% | 42.8% |
| **ALL OOS (`n=8,566`, `549` pos)** | **`XGBoost`** | 0.832 ± 0.047 | 0.761 [0.720, 0.802] | 0.299 ± 0.063 | 0.226 [0.180, 0.280] | 0.0549 | +0.111 [0.087, 0.134] | 24.0% | 41.2% |
| **TRUE UNSEEN ISSUERS (`n=8,118`, `512` pos)** | `Baseline_1_AltmanZ` | 0.643 ± 0.085 | 0.577 [0.527, 0.625] | 0.238 ± 0.144 | 0.153 [0.112, 0.205] | 0.0583 | +0.041 [0.021, 0.065] | 14.5% | 21.9% |
| **TRUE UNSEEN ISSUERS (`n=8,118`, `512` pos)** | `Baseline_2_Logit` | 0.822 ± 0.050 | 0.771 [0.729, 0.811] | 0.336 ± 0.056 | 0.284 [0.221, 0.360] | 0.0531 | +0.126 [0.097, 0.154] | 31.2% | 42.6% |
| **TRUE UNSEEN ISSUERS (`n=8,118`, `512` pos)** | **`XGBoost`** | 0.831 ± 0.049 | 0.759 [0.715, 0.799] | 0.309 ± 0.068 | 0.225 [0.177, 0.287] | 0.0542 | +0.108 [0.081, 0.136] | 26.4% | 40.0% |

### 3.3. Mode A Ablation Cross-Check (`FUNDAMENTAL_DISTRESS`, `0%` TTM Overlap, `2021–2024`, `EXACT_FILING`)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=7,931`, `229` pos)** | `Baseline_1_AltmanZ` | 0.746 ± 0.048 | 0.720 [0.661, 0.779] | 0.136 ± 0.050 | 0.085 [0.060, 0.129] | 0.0275 | +0.019 [0.006, 0.034] | 21.0% | 29.7% |
| **ALL OOS (`n=7,931`, `229` pos)** | `Baseline_2_Logit` | 0.776 ± 0.059 | 0.728 [0.674, 0.779] | 0.134 ± 0.038 | 0.099 [0.064, 0.157] | 0.0275 | +0.020 [-0.025, 0.058] | 24.0% | 40.2% |
| **ALL OOS (`n=7,931`, `229` pos)** | **`XGBoost`** | 0.862 ± 0.048 | 0.829 [0.789, 0.866] | 0.160 ± 0.056 | 0.126 [0.090, 0.182] | 0.0264 | +0.058 [0.027, 0.089] | 26.2% | 51.5% |
| **TRUE UNSEEN ISSUERS (`n=7,607`, `184` pos)** | `Baseline_1_AltmanZ` | 0.740 ± 0.024 | 0.706 [0.636, 0.765] | 0.101 ± 0.034 | 0.061 [0.042, 0.097] | 0.0234 | +0.010 [-0.001, 0.022] | 21.2% | 26.1% |
| **TRUE UNSEEN ISSUERS (`n=7,607`, `184` pos)** | `Baseline_2_Logit` | 0.784 ± 0.064 | 0.728 [0.664, 0.784] | 0.128 ± 0.051 | 0.083 [0.054, 0.136] | 0.0235 | +0.004 [-0.045, 0.045] | 26.1% | 40.2% |
| **TRUE UNSEEN ISSUERS (`n=7,607`, `184` pos)** | **`XGBoost`** | 0.863 ± 0.052 | 0.825 [0.780, 0.865] | 0.152 ± 0.029 | 0.119 [0.082, 0.181] | 0.0223 | +0.054 [0.020, 0.084] | 27.2% | 51.6% |

---

## 4. Direct Comparison: `375` Issuers (`go_live_nfr_run`) vs `685` Issuers (`ML-03b`)

| Cohort / Arm | Model | Yearly ROC-AUC | Pooled ROC-AUC [95% CI] (Width) | Yearly PR-AUC | Pooled PR-AUC [95% CI] (Width) | Pooled Brier | Pooled BSS | Pooled R@5% | Pooled R@10% |
|---|---|---:|---|---:|---|---:|---:|---:|---:|
| `375` Issuers (`EXACT_FILING`, Wave 3) | `XGBoost` | 0.809 ± 0.104 | 0.765 [0.678, 0.843] | 0.169 ± 0.053 | 0.157 [0.087, 0.258] | 0.0232 | +0.065 | 34.5% | 53.9% |
| `375` Issuers (`EXACT_FILING`, Wave 3) | `Baseline_2_Logit` | 0.726 ± 0.084 | 0.717 [0.626, 0.801] | 0.132 ± 0.056 | 0.128 [0.070, 0.216] | 0.0240 | +0.034 | 31.5% | 42.4% |
| `375` Issuers (`EXACT_FILING`, Wave 3) | `Baseline_1_AltmanZ` | 0.628 ± 0.132 | 0.631 [0.539, 0.726] | 0.129 ± 0.105 | 0.105 [0.055, 0.184] | 0.0241 | +0.028 | 24.2% | 33.3% |
| `685` Issuers (`EXACT_FILING`, Branch 1) | `XGBoost` | 0.833 ± 0.060 | 0.762 [0.717, 0.810] (`±0.047`) | 0.313 ± 0.061 | 0.252 [0.200, 0.321] (`±0.060`) | 0.0518 | +0.127 | 25.4% | 47.3% |
| `685` Issuers (`EXACT_FILING`, Branch 1) | `Baseline_2_Logit` | 0.803 ± 0.061 | 0.729 [0.680, 0.778] (`±0.049`) | 0.329 ± 0.033 | 0.262 [0.204, 0.327] (`±0.061`) | 0.0534 | +0.101 | 30.7% | 41.8% |
| `685` Issuers (`EXACT_FILING`, Branch 1) | `Baseline_1_AltmanZ` | 0.642 ± 0.089 | 0.595 [0.545, 0.648] (`±0.052`) | 0.230 ± 0.145 | 0.168 [0.126, 0.224] (`±0.049`) | 0.0571 | +0.039 | 18.8% | 24.9% |
| `685` Issuers (`STATUTORY_LAG +0d`, Branch 2) | `XGBoost` | 0.836 ± 0.047 | 0.767 [0.725, 0.808] (`±0.041`) | 0.282 ± 0.062 | 0.216 [0.174, 0.268] (`±0.047`) | 0.0549 | +0.110 | 23.4% | 41.1% |
| `685` Issuers (`STATUTORY_LAG +0d`, Branch 2) | `Baseline_2_Logit` | 0.825 ± 0.043 | 0.752 [0.712, 0.788] (`±0.038`) | 0.347 ± 0.046 | 0.249 [0.193, 0.313] (`±0.060`) | 0.0561 | +0.089 | 28.8% | 40.3% |
| `685` Issuers (`STATUTORY_LAG +0d`, Branch 2) | `Baseline_1_AltmanZ` | 0.642 ± 0.087 | 0.579 [0.529, 0.631] (`±0.051`) | 0.229 ± 0.142 | 0.152 [0.111, 0.199] (`±0.044`) | 0.0591 | +0.041 | 14.8% | 21.4% |
| `685` Issuers (`STATUTORY_LAG +37d`, Branch 2b) | `XGBoost` | 0.832 ± 0.047 | 0.761 [0.720, 0.802] (`±0.041`) | 0.299 ± 0.063 | 0.226 [0.180, 0.280] (`±0.050`) | 0.0549 | +0.111 | 24.0% | 41.2% |
| `685` Issuers (`STATUTORY_LAG +37d`, Branch 2b) | `Baseline_2_Logit` | 0.823 ± 0.042 | 0.771 [0.731, 0.809] (`±0.039`) | 0.334 ± 0.046 | 0.276 [0.219, 0.344] (`±0.063`) | 0.0539 | +0.127 | 30.4% | 42.8% |
| `685` Issuers (`STATUTORY_LAG +37d`, Branch 2b) | `Baseline_1_AltmanZ` | 0.641 ± 0.086 | 0.579 [0.529, 0.631] (`±0.051`) | 0.229 ± 0.141 | 0.152 [0.111, 0.199] (`±0.044`) | 0.0592 | +0.041 | 14.8% | 21.5% |

**Key Scientific Observations (375 vs 685):**
1. **Confidence Interval Tightening (`H-01` & `GATE-ML-4`):** Expanding from `375` (`71` positives) to `685` issuers (`318` positives) tightens `XGBoost` Pooled ROC-AUC 95% CI width from **`0.165`** (`[0.678, 0.843]`) to **`0.093`** (`Branch 1`) and **`0.083`** (`Branch 2: [0.725, 0.808]`), establishing a non-overlapping separation over `Altman Z''` (`[0.529, 0.631]`).
2. **5/5 & 5/5 Positive BSS Across Walk-Forward Years:** Unlike the `375`-issuer Wave 3 run where 2025 turned negative (`-0.063`), `685`-issuer `Monotone_XGBoost` achieves strictly **positive Brier Skill Score across test years (`2021–2025`)** in both Branch 1 (`+0.081` to `+0.173`, pooled `+0.127`) and Branch 2 (`+0.073` to `+0.142`, pooled `+0.110`).
3. **Branch 1 vs Branch 2 Trade-off (`EXACT_FILING` vs `STATUTORY_LAG`):** Retaining UPCOM delisted firms in Branch 2 raises positive OOS rows from `531` to `548` and pushes `XGBoost` yearly ROC-AUC up to `0.898` (`PR-AUC 0.235–0.385`), while Branch 1 (`EXACT_FILING`) maintains higher Top-10% concentration (`47.3%` vs `41.1%`) because `EXACT_FILING` has lower baseline prevalence (`6.24%` vs `6.40%`).

---

## 5. Through-the-Cycle `R1 → R5` Monotonicity & `TT200 → TT99` PSI Audit (`GATE-ML-5`)

### 5.1. Empirical Event Rate Monotonicity Across `R1 → R5` (PIT Per-Fold Cutoffs)

| `risk_tier` (TTC) | Branch 1 (`EXACT`, `2021–2025` Pooled) | Branch 1 (`EXACT`, `2022–2025`) | Branch 1 Prod Inner OOF (`2016–2024`) | Branch 2 (`EXACT + STATUTORY_LAG +0d`, `2021–2025` Pooled) |
|---|---:|---:|---:|---:|
| **`R1`** | `79 / 4,152` (`1.90%`) | `42 / 3,090` (`1.36%`) | `63 / 3,049` (**`2.07%`**) | `35 / 3,113` (**`1.12%`**) |
| **`R2`** | `61 / 2,315` (`2.63%`) | `39 / 1,856` (`2.10%`) | `53 / 1,525` (**`3.48%`**) | `89 / 3,194` (**`2.79%`**) |
| **`R3`** | `113 / 1,143` (`9.89%`) | `101 / 987` (`10.23%`) | `105 / 915` (**`11.48%`**) | `160 / 1,308` (**`12.23%`**) |
| **`R4`** | `121 / 488` (`24.80%`) | `86 / 376` (`22.87%`) | `82 / 305` (**`26.89%`**) | `78 / 348` (**`22.41%`**) |
| **`R5`** | `157 / 410` (`38.29%`) | `125 / 308` (`40.58%`) | `120 / 305` (**`39.34%`**) | `186 / 603` (**`30.85%`**) |

- **Monotonicity Audit (PIT Per-Fold Cutoffs):** Branch 1 (`EXACT_FILING`, `2021–2025` Pooled, `n=8,508`) `is_monotone_non_decreasing = True` (`1.90% -> 2.63% -> 9.89% -> 24.80% -> 38.29%`, `20.1×` `R5/R1` lift). Branch 2 (`EXACT + STATUTORY_LAG +0d`, `2021–2025` Pooled, `n=8,566`) `is_monotone_non_decreasing = True` (`1.12% -> 2.79% -> 12.23% -> 22.41% -> 30.85%`, `27.4×` `R5/R1` lift). Production inner OOF (`2016–2024`, `n=6,099`) (`2.07% -> 3.48% -> 11.48% -> 26.89% -> 39.34%`, `19.0×` `R5/R1` lift).

### 5.2. `M-05` Taxonomy Drift Audit: `VAS_TT200_2014` (`2024–2025`) → `VAS_TT99_2025` (`2026`)

| Arm | Ref Rows (`2024–2025`) | Tgt Rows (`2026`) | Mean Feature PSI | Max Feature PSI | Features `> 0.25` | Mean Missing (`TT200 -> TT99`) | Score PSI | `R4+R5` Share (`TT200 -> TT99`) |
|---|---:|---:|---:|---:|---:|---|---:|---|
| **Branch 1 (`EXACT_FILING`)** | 4,551 | 1,656 | **`0.0235`** | `0.0810` | `0 / 23` | `3.104% -> 3.072%` | `0.0394` | `19.58% -> 16.61%` (`-2.97 pp`) |
| **Branch 2 (`EXACT + STATUTORY_LAG +0d`)** | 4,559 | 1,662 | **`0.0234`** | `0.0815` | `0 / 23` | `3.126% -> 3.137%` | `0.0475` | `23.25% -> 19.19%` (`-4.06 pp`) |

---

## 6. Historical VN Corporate Distress Case Studies (100% Out-of-Sample Expanding PIT)

| Ticker | Exchange | Arm | First Control / Warning (`event_date`) | Mandatory Delisting / Suspension | First `R4/R5` Quarter (`_period_available_at`) | Tier · 1Y PD · PIT Pct · Lift | Lead vs Warn | Lead vs Delist | Top-3 TreeSHAP Drivers (Vietnamese) |
|---|---|---|---|---|---|---|---:|---:|---|
| **`HBC`** | `UPCOM` | `EXACT_WF` | `2023-04-11` (`CONTROL`) | `2024-07-26` (`HOSE`) | `2022-Q4` (`2023-02-10`) | **`R5`** · `22.3%` · P96.3 · `7.0×` | **0.7 Q** (`60d`) | **5.8 Q** (`532d`) | Khả năng chi trả lãi vay (`+0.26`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.19`); Số quý lỗ ròng liên tiếp (`+0.11`) |
| **`POM`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2023-04-11` (`CONTROL`) | `2024-04-03` (`HOSE`) | `2020-Q1` (`2020-04-29`) | **`R4`** · `8.6%` · P88.5 · `5.6×` | **11.8 Q** (`1077d`) | **15.7 Q** (`1435d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.42`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.28`); Khả năng chi trả lãi vay (`+0.25`) |
| **`HNG`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2020-04-17` (`CONTROL`) | `2024-07-26` (`HOSE`) | `2020-Q1` (`2020-04-28`) | **`R4`** · `7.8%` · P86.9 · `5.1×` | **-0.1 Q** (`-11d`) | **17.0 Q** (`1550d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.44`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.26`); Khả năng chi trả lãi vay (`+0.21`) |
| **`DLG`** | `HOSE` | `STATUTORY_PRE2020_WF` | `2021-04-23` (`CONTROL`) | — (`HOSE`) | `2020-Q2` (`2020-09-18`) | **`R4`** · `6.6%` · P82.4 · `4.3×` | **2.4 Q** (`217d`) | — | Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.31`); Khả năng chi trả lãi vay (`+0.28`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.21`) |
| **`SMC`** | `HOSE` | `EXACT_WF` | `2024-04-04` (`CONTROL`) | — (`HOSE`) | `2022-Q3` (`2022-10-25`) | **`R4`** · `17.5%` · P89.2 · `5.5×` | **5.8 Q** (`527d`) | — | Khả năng chi trả lãi vay (`+0.28`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.18`); Số quý lỗ ròng liên tiếp (`+0.13`) |
| **`QBS`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2021-04-09` (`CONTROL`) | `2024-04-04` (`HOSE`) | `2020-Q1` (`2020-05-01`) | **`R5`** · `16.6%` · P98.4 · `10.9×` | **3.8 Q** (`343d`) | **15.7 Q** (`1434d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.52`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.32`); Khả năng chi trả lãi vay (`+0.25`) |
| **`PVX`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2021-09-04` (`ADVERSE_OR_DISCLAIMER_AUDIT`) | `2020-05-12` (`HNX`) | `2020-Q1` (`2020-05-06`) | **`R5`** · `11.3%` · P93.1 · `7.4×` | **5.3 Q** (`486d`) | **0.1 Q** (`6d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.49`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.30`); Khả năng chi trả lãi vay (`+0.26`) |
| **`PVL`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2023-04-21` (`TRADING_RESTRICTION`) | `2023-03-17` (`HNX`) | `2020-Q1` (`2020-04-13`) | **`R5`** · `16.6%` · P98.4 · `10.9×` | **12.1 Q** (`1103d`) | **11.7 Q** (`1068d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.52`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.31`); Khả năng chi trả lãi vay (`+0.22`) |
| **`HVN`** | `HOSE` | `STATUTORY_PRE2020_WF` | `2021-10-27` (`CONTROL`) | — (`HOSE`) | `2020-Q1` (`2020-04-30`) | **`R4`** · `7.6%` · P86.4 · `5.0×` | **6.0 Q** (`545d`) | — | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.44`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.27`); Khả năng chi trả lãi vay (`+0.26`) |
| **`NVL`** | `HOSE` | `EXACT_WF` | — | — (`HOSE`) | `2024-Q2` (`2024-09-27`) | **`R5`** · `35.9%` · P92.1 · `6.5×` | — | — | Tỷ suất sinh lời trên tài sản (ROA) (`+0.77`); Khả năng chi trả lãi vay (`+0.23`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.23`) |
| **`SII`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2023-04-07` (`FORCED_DELISTING`) | `2023-04-07` (`HOSE`) | `2020-Q3` (`2020-10-28`) | **`R5`** · `9.9%` · P88.7 · `6.5×` | **9.8 Q** (`891d`) | **9.8 Q** (`891d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.42`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.30`); Khả năng chi trả lãi vay (`+0.27`) |
| **`SJF`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2023-10-04` (`CONTROL`) | `2023-11-03` (`UPCOM`) | `2020-Q1` (`2020-05-07`) | **`R5`** · `14.3%` · P94.5 · `9.4×` | **13.6 Q** (`1245d`) | **14.0 Q** (`1275d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.59`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.34`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.23`) |
| **`TCD`** | `UPCOM` | `EXACT_WF` | `2025-05-07` (`CONTROL`) | `2025-10-01` (`UPCOM`) | `2024-Q4` (`2025-01-24`) | **`R5`** · `39.3%` · P95.4 · `7.1×` | **1.1 Q** (`103d`) | **2.7 Q** (`250d`) | Tỷ suất sinh lời trên tài sản (ROA) (`+0.69`); Điểm Altman Z'' (`+0.35`); Khả năng chi trả lãi vay (`+0.21`) |
| **`DDG`** | `UPCOM` | `EXACT_WF` | `2025-10-28` (`TRADING_RESTRICTION`) | `2026-06-02` (`HOSE`) | `2023-Q2` (`2023-08-29`) | **`R5`** · `27.6%` · P93.9 · `6.5×` | **8.7 Q** (`791d`) | **11.0 Q** (`1008d`) | Tỷ suất sinh lời trên tài sản (ROA) (`+0.64`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.40`); Khả năng chi trả lãi vay (`+0.20`) |
| **`VNE`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2024-04-05` (`CONTROL`) | `2026-05-20` (`HOSE`) | `2020-Q1` (`2020-04-29`) | **`R4`** · `8.2%` · P88.0 · `5.4×` | **15.7 Q** (`1437d`) | **24.2 Q** (`2212d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.56`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.27`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.24`) |
| **`SDA`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2025-05-19` (`TRADING_RESTRICTION`) | `2026-05-04` (`HOSE`) | `2020-Q1` (`2020-04-23`) | **`R5`** · `16.6%` · P98.4 · `10.9×` | **20.3 Q** (`1852d`) | **24.1 Q** (`2202d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.55`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.32`); Khả năng chi trả lãi vay (`+0.23`) |

---

## 7. Production Snapshot Refresh (`ARCH-06 / ML-08` Verification)

- **Active Serving Store Root (`FININTEL_DATA_ROOT`):** `D:\KiCuoiCung\DATN\financial-intelligence\data\universe_600_800`
- **Snapshot ID & DuckLake Version:** `snap_20261126T124435Z_ml03b_685_eval_2_2b3e84c5` (DuckLake `version = 22`)
- **Published Issuers & ML Scores:** `685` issuers (HOSE 280 / HNX 110 / UPCOM 295), `38,306` total `ml_scores_and_explanations` rows (`597` issuers with supervised `EXACT_FILING` PD/TreeSHAP scores across `23` ML-05 features; float32 TreeSHAP max residual = `1.41e-06 < 1e-5`)
- **Extension Port Tables Preserved (`B-03`):** `regulatory_events` = `1,406` rows, `market_prices` = `2,009,035` rows (verified present in DuckLake & on disk after `publish_snapshot()`).
- **Read-Back Verification (`ParquetRepository` + `DashboardQueryService`):** `get_model_card()` -> `run_id=ml03b_685_eval_20261126T121157Z`, `feature_count=23`; `get_company_market_overlay('HBC')` -> `3455` price points, `5` regulatory markers, `1` model markers.

---

## 8. Merton Distance-to-Default (`MERTON_ABLATION_FEATURES`) Hybrid Walk-Forward Ablation (`2021 → 2025`)

Evaluates `SECTOR_ADJUSTED_FEATURES` (`23` accounting features) augmented with PIT-aligned `merton_dtd`, `equity_volatility_1y`, and `naive_edf` (Bharath & Shumway 2008; Duan, Sun & Wang 2012) under identical strict `EXACT_FILING` expanding Walk-Forward splits (`2021–2025`).

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=8,506`, `530` pos)** | `Baseline_1_AltmanZ` | 0.641 ± 0.089 | 0.596 [0.545, 0.648] | 0.227 ± 0.145 | 0.167 [0.126, 0.223] | 0.0570 | +0.038 [0.019, 0.062] | 18.9% | 25.1% |
| **ALL OOS (`n=8,506`, `530` pos)** | `Baseline_2_Logit` | 0.803 ± 0.059 | 0.719 [0.667, 0.766] | 0.321 ± 0.040 | 0.253 [0.193, 0.321] | 0.0537 | +0.095 [0.068, 0.120] | 29.1% | 39.1% |
| **ALL OOS (`n=8,506`, `530` pos)** | **`XGBoost`** | 0.832 ± 0.058 | 0.757 [0.712, 0.804] | 0.304 ± 0.058 | 0.259 [0.208, 0.330] | 0.0520 | +0.124 [0.090, 0.159] | 27.5% | 43.6% |
| **TRUE UNSEEN ISSUERS (`n=8,058`, `493` pos)** | `Baseline_1_AltmanZ` | 0.643 ± 0.089 | 0.594 [0.538, 0.651] | 0.236 ± 0.146 | 0.173 [0.129, 0.228] | 0.0560 | +0.039 [0.018, 0.064] | 19.5% | 26.0% |
| **TRUE UNSEEN ISSUERS (`n=8,058`, `493` pos)** | `Baseline_2_Logit` | 0.802 ± 0.062 | 0.713 [0.661, 0.763] | 0.312 ± 0.026 | 0.243 [0.188, 0.307] | 0.0531 | +0.089 [0.065, 0.112] | 28.8% | 38.9% |
| **TRUE UNSEEN ISSUERS (`n=8,058`, `493` pos)** | **`XGBoost`** | 0.830 ± 0.058 | 0.752 [0.703, 0.800] | 0.299 ± 0.056 | 0.248 [0.198, 0.317] | 0.0513 | +0.119 [0.087, 0.154] | 28.0% | 43.6% |
