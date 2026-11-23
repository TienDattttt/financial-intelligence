# Wave 7B Quantitative ML Re-Evaluation Report — `ML-03b` 685-Issuer Universe (`2021–2025`)

- **Generated (UTC):** `2026-11-23T06:12:12.252971+00:00`
- **Dataset (Read-Only Input):** `data/universe_600_800/curated/financial_facts/ml03b_full/financial_facts.parquet` + `data/universe_600_800/curated/issuers.parquet` (`685` corporate issuers, `1,934,382` raw facts -> `1,930,670` QG1-clean facts, `44/685` critical quarantined = `6.42%`, `as_of = 2026-11-22`, `industry_snapshot_date = 2026-11-19` per `ML-02`)
- **Exogenous Ground Truth (Port 1):** `data/regulatory_ledger/regulatory_events_600_800.parquet` (`1,406` verified events across `318` distinct positive corporate issuers; `100%` `source_url` + `payload_sha256`)
- **Peer Standardization & Feature Contract:** `PIT_REGIME_SYMMETRIC` + `SECTOR_ADJUSTED_FEATURES` (`23` ML-05 features + contextual missingness indicators)
- **Data Hygiene Notes:** Harmonized `0` stub-transition BS rows (`SJ1` 2016 ANNUAL 9-month stub) to match IS/CF `fiscal_year_start_date`; `0` SMOTE/synthetic rows; `0` lookahead to `y_test.mean()` or future folds.

---

## 1. `GATE-ML-1..6` Compliance Checklist

| Gate | Requirement | Measured 685-Issuer Verification | Status |
|---|---|---|---|
| **`GATE-ML-1`** | Provenance & Anti-Survivorship (`ML-03b`, `H-01`, `H-02`) | `685` corporate issuers (HOSE 280 / HNX 110 / UPCOM 295), `1,406` Port 1 events (`318` pos issuers), `100%` `source_url` + `payload_sha256`; unpenalized `STATUTORY_LAG (+0d)` upper-bound & `STATUTORY_LAG + 37d` sensitivity evaluated. | **PASS** |
| **`GATE-ML-2`** | Zero Leakage, Symmetric Horizon (`B-01`), Continuous State (`H-05`), `0%` TTM Overlap | `label_available_at = _period_available_at + 365d` symmetrically for `y=1` & `y=0`; continuous `[event_date, resolution_date)` intervals; Mode A `0%` TTM overlap; backdated `2016-01-01` issuers = `0` (`industry_snapshot_date = 2026-11-19`), ML training uses `PIT_REGIME_SYMMETRIC` (ICB 2026 scope applies only to Layer 4A `industry_benchmarks`). | **PASS** |
| **`GATE-ML-3`** | Fair 3-Model Comparison & Unpenalized Platt (`H-07`) | Exact common sample across `Baseline_1_AltmanZ`, `Baseline_2_Logit` (`clip=[-5, 5]`), and `Monotone_XGBoost`; Fold(s) 2021 fallback `STRATIFIED_GROUP_OOF`, remaining folds `PURGED_TEMPORAL_OOF` (2021:STRATIFIED_GROUP_OOF, 2022:PURGED_TEMPORAL_OOF, 2023:PURGED_TEMPORAL_OOF, 2024:PURGED_TEMPORAL_OOF, 2025:PURGED_TEMPORAL_OOF); `MarginPlattCalibrator(penalty=None, slope clamped [0.15, 5.0])`; BSS benchmarked vs historical `train_prevalence_Y`. | **PASS** |
| **`GATE-ML-4`** | 2-Way Purged Expanding Walk-Forward (`2021–2025`), Bootstrap 95% CI, `True Unseen Issuers` | Full 5-year expanding evaluation (`2021 -> 2025`), issuer-clustered bootstrap (`issuer_id`), full `True Unseen Issuers` isolation across Branch 1, Branch 2 (`+0d`), and Branch 2b (`+37d`). | **PASS** |
| **`GATE-ML-5`** | Anti-Overfitting, `R1 → R5` Monotonicity & `TT200 → TT99` PSI (`M-05`) | Positive XGBoost BSS in **5/5** (Branch 1) & **5/5** (Branch 2) years; `TT200 -> TT99` mean PSI = **`0.0273`** (`0/23` > `0.25`, `R4+R5` `-5.06 pp` in 2026); PIT-cutoff OOS monotonicity: Branch 1=`False` (15.6×), Branch 2=`True` (19.2×), OOF=`False` (17.3×). | **FAIL** |
| **`GATE-ML-6`** | End-to-End DuckLake Persistence & Serving Parity (`ML-08`, `H-04`) | Published `model_card_meta.parquet` + `ml_scores_and_explanations.parquet` (`23` features, `model_role` preserved for `OUT_OF_SAMPLE_*` vs `IN_SAMPLE_TRAIN`) into DuckLake snapshot; read back cleanly via `ParquetRepository` & `DashboardQueryService.get_model_card()`. | **PASS** |

---

## 2. Mode B & Mode A Onset Label Distribution (`685` Issuers, `as_of = 2026-11-22`)

| Fiscal Year | Mode B Branch 1 (`EXACT_FILING`) Rows | Pos Rows | Pos Issuers | Mode B Branch 2 (`EXACT + STATUTORY_LAG`) Rows | Pos Rows | Pos Issuers | Mode A (`0%` TTM) Rows | Pos Rows | Pos Issuers |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2018 | 471 | 9 | 9 | 2,010 | 77 | 27 | 363 | 8 | 8 |
| 2019 | 1,587 | 33 | 9 | 2,031 | 62 | 21 | 1,534 | 28 | 16 |
| 2020 | 1,617 | 38 | 13 | 2,026 | 79 | 30 | 1,605 | 18 | 10 |
| 2021 | 1,612 | 46 | 17 | 2,001 | 159 | 62 | 1,625 | 18 | 12 |
| 2022 | 1,612 | 50 | 20 | 1,897 | 172 | 74 | 1,675 | 39 | 20 |
| 2023 | 1,639 | 47 | 21 | 1,784 | 98 | 40 | 1,739 | 31 | 23 |
| 2024 | 1,639 | 61 | 28 | 1,723 | 85 | 36 | 1,788 | 30 | 17 |
| 2025 | 1,197 | 35 | 21 | 1,234 | 43 | 26 | 0 | 0 | 0 |
| **Total (`2018–2025`)** | **11,374** | **319** | — | **14,706** | **775** | — | **10,329** | **172** | — |

---

## 3. 2-Way Purged Expanding Walk-Forward Results (`2021 → 2025`)

### 3.1. `H-01` Branch 1 — Strict `EXACT_FILING` (Guardrail G1: `require_exact_filing=True`)

| Test Year | Inner OOF Strategy | `n_est` | Platt Slope | Train Rows (Pos / Iss) | Test Common (Pos / Iss) | Unseen Common (Pos / Iss) | XGB Train PR-AUC | OOS ROC-AUC (Alt / Log / **XGB**) | OOS PR-AUC (Alt / Log / **XGB**) | OOS BSS (Alt / Log / **XGB**) | OOS R@5% (Alt / Log / **XGB**) | OOS R@10% (Alt / Log / **XGB**) | Unseen ROC-AUC (Alt / Log / **XGB**) |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| 2021 | `STRATIFIED_GROUP_OOF` | 17 | 5.00 | 1,645 (32 / 18) | 1,585 (46 / 17) | 1,553 (46 / 17) | 0.5910 | 0.522 / 0.617 / **0.637** | 0.088 / 0.130 / **0.138** | +0.024 / +0.028 / **+0.058** | 21.7% / 34.8% / **32.6%** | 23.9% / 43.5% / **37.0%** | 0.522 / 0.620 / **0.641** |
| 2022 | `PURGED_TEMPORAL_OOF` | 44 | 1.70 | 3,258 (68 / 29) | 1,600 (50 / 20) | 1,542 (42 / 17) | 0.4899 | 0.702 / 0.728 / **0.755** | 0.258 / 0.186 / **0.175** | +0.035 / +0.081 / **+0.077** | 40.0% / 32.0% / **36.0%** | 46.0% / 40.0% / **44.0%** | 0.651 / 0.697 / **0.743** |
| 2023 | `PURGED_TEMPORAL_OOF` | 50 | 1.31 | 4,873 (111 / 40) | 1,639 (47 / 21) | 1,538 (35 / 17) | 0.3867 | 0.632 / 0.810 / **0.894** | 0.070 / 0.121 / **0.176** | +0.008 / +0.042 / **+0.105** | 21.3% / 25.5% / **36.2%** | 34.0% / 48.9% / **66.0%** | 0.685 / 0.867 / **0.897** |
| 2024 | `PURGED_TEMPORAL_OOF` | 56 | 1.11 | 6,490 (164 / 54) | 1,630 (58 / 26) | 1,522 (49 / 23) | 0.3852 | 0.616 / 0.796 / **0.860** | 0.115 / 0.245 / **0.165** | +0.017 / +0.084 / **+0.090** | 22.4% / 39.7% / **29.3%** | 31.0% / 53.4% / **58.6%** | 0.577 / 0.786 / **0.853** |
| 2025 | `PURGED_TEMPORAL_OOF` | 31 | 2.07 | 8,115 (207 / 64) | 1,195 (35 / 21) | 1,099 (31 / 19) | 0.2836 | 0.483 / 0.685 / **0.704** | 0.042 / 0.204 / **0.155** | -0.008 / +0.064 / **+0.069** | 8.6% / 37.1% / **37.1%** | 11.4% / 45.7% / **45.7%** | 0.520 / 0.653 / **0.682** |

#### Branch 1 Summary (`ALL OOS` & `TRUE UNSEEN ISSUERS`, `n=1,000` Cluster Bootstrap 95% CI)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=7,649`, `236` pos)** | `Baseline_1_AltmanZ` | 0.591 ± 0.088 | 0.600 [0.521, 0.679] | 0.115 ± 0.084 | 0.099 [0.059, 0.160] | 0.0295 | +0.017 [0.003, 0.033] | 20.3% | 30.9% |
| **ALL OOS (`n=7,649`, `236` pos)** | `Baseline_2_Logit` | 0.727 ± 0.080 | 0.722 [0.655, 0.790] | 0.177 ± 0.052 | 0.159 [0.104, 0.235] | 0.0281 | +0.061 [0.017, 0.101] | 33.9% | 46.2% |
| **ALL OOS (`n=7,649`, `236` pos)** | **`XGBoost`** | 0.770 ± 0.107 | 0.752 [0.682, 0.819] | 0.162 ± 0.016 | 0.161 [0.109, 0.238] | 0.0275 | +0.081 [0.035, 0.126] | 35.2% | 53.0% |
| **TRUE UNSEEN ISSUERS (`n=7,254`, `203` pos)** | `Baseline_1_AltmanZ` | 0.591 ± 0.075 | 0.588 [0.496, 0.677] | 0.089 ± 0.050 | 0.075 [0.043, 0.129] | 0.0269 | +0.011 [-0.003, 0.025] | 20.2% | 27.6% |
| **TRUE UNSEEN ISSUERS (`n=7,254`, `203` pos)** | `Baseline_2_Logit` | 0.725 ± 0.101 | 0.709 [0.632, 0.780] | 0.146 ± 0.029 | 0.144 [0.090, 0.227] | 0.0258 | +0.051 [0.012, 0.092] | 33.5% | 44.8% |
| **TRUE UNSEEN ISSUERS (`n=7,254`, `203` pos)** | **`XGBoost`** | 0.763 ± 0.110 | 0.735 [0.657, 0.806] | 0.144 ± 0.013 | 0.148 [0.092, 0.233] | 0.0254 | +0.068 [0.018, 0.115] | 37.4% | 51.7% |

### 3.2. `H-01` Branch 2 — Expanded `EXACT + STATUTORY_LAG (+0d)` Unpenalized Statutory Deadline Upper-Bound (`require_exact_filing=False`)

Retains mandatory-delisted UPCOM/HNX issuers missing CafeF/VCI PDF disclosure links at the unpenalized Circular 96 statutory deadline (`+0d` upper-bound, preventing survivorship bias across the 247 distressed UPCOM firms; see Section 3.2b for empirical `+37d` late-filing delay sensitivity).

| Test Year | Inner OOF Strategy | `n_est` | Platt Slope | Train Rows (Pos / Iss) | Test Common (Pos / Iss) | Unseen Common (Pos / Iss) | XGB Train PR-AUC | OOS ROC-AUC (Alt / Log / **XGB**) | OOS PR-AUC (Alt / Log / **XGB**) | OOS BSS (Alt / Log / **XGB**) | OOS R@5% (Alt / Log / **XGB**) | OOS R@10% (Alt / Log / **XGB**) | Unseen ROC-AUC (Alt / Log / **XGB**) |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| 2021 | `STRATIFIED_GROUP_OOF` | 19 | 3.77 | 7,706 (214 / 59) | 1,957 (153 / 61) | 1,925 (153 / 61) | 0.2395 | 0.642 / 0.785 / **0.766** | 0.268 / 0.295 / **0.208** | +0.032 / +0.076 / **+0.063** | 30.7% / 21.6% / **16.3%** | 37.9% / 41.8% / **30.1%** | 0.642 / 0.788 / **0.770** |
| 2022 | `PURGED_TEMPORAL_OOF` | 55 | 1.82 | 9,734 (285 / 81) | 1,872 (170 / 73) | 1,814 (162 / 70) | 0.2841 | 0.739 / 0.815 / **0.839** | 0.435 / 0.392 / **0.367** | +0.073 / +0.086 / **+0.161** | 33.5% / 28.8% / **27.6%** | 45.3% / 44.1% / **44.7%** | 0.730 / 0.818 / **0.840** |
| 2023 | `PURGED_TEMPORAL_OOF` | 79 | 1.01 | 11,745 (419 / 129) | 1,784 (98 / 40) | 1,673 (86 / 36) | 0.3011 | 0.668 / 0.869 / **0.887** | 0.175 / 0.334 / **0.330** | +0.020 / +0.115 / **+0.172** | 24.5% / 34.7% / **35.7%** | 41.8% / 59.2% / **59.2%** | 0.702 / 0.881 / **0.887** |
| 2024 | `PURGED_TEMPORAL_OOF` | 78 | 0.96 | 13,682 (607 / 167) | 1,709 (78 / 35) | 1,582 (69 / 32) | 0.3052 | 0.621 / 0.830 / **0.852** | 0.153 / 0.262 / **0.255** | +0.012 / +0.108 / **+0.103** | 25.6% / 38.5% / **35.9%** | 33.3% / 56.4% / **55.1%** | 0.597 / 0.825 / **0.848** |
| 2025 | `PURGED_TEMPORAL_OOF` | 40 | 1.75 | 15,483 (714 / 187) | 1,232 (43 / 26) | 1,118 (39 / 24) | 0.2769 | 0.476 / 0.769 / **0.783** | 0.044 / 0.261 / **0.261** | -0.014 / +0.067 / **+0.090** | 9.3% / 37.2% / **39.5%** | 14.0% / 48.8% / **53.5%** | 0.509 / 0.760 / **0.781** |

#### Branch 2 Summary (`ALL OOS` & `TRUE UNSEEN ISSUERS`, `n=1,000` Cluster Bootstrap 95% CI)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=8,554`, `542` pos)** | `Baseline_1_AltmanZ` | 0.629 ± 0.097 | 0.583 [0.534, 0.635] | 0.215 ± 0.146 | 0.153 [0.114, 0.201] | 0.0583 | +0.036 [0.016, 0.059] | 14.0% | 21.8% |
| **ALL OOS (`n=8,554`, `542` pos)** | `Baseline_2_Logit` | 0.813 ± 0.039 | 0.751 [0.711, 0.788] | 0.309 ± 0.055 | 0.228 [0.177, 0.290] | 0.0550 | +0.090 [0.065, 0.113] | 26.6% | 38.0% |
| **ALL OOS (`n=8,554`, `542` pos)** | **`XGBoost`** | 0.825 ± 0.050 | 0.770 [0.728, 0.811] | 0.284 ± 0.064 | 0.249 [0.198, 0.314] | 0.0531 | +0.121 [0.087, 0.154] | 27.3% | 43.2% |
| **TRUE UNSEEN ISSUERS (`n=8,112`, `509` pos)** | `Baseline_1_AltmanZ` | 0.636 ± 0.088 | 0.585 [0.534, 0.633] | 0.226 ± 0.141 | 0.157 [0.116, 0.207] | 0.0577 | +0.037 [0.016, 0.063] | 14.3% | 22.2% |
| **TRUE UNSEEN ISSUERS (`n=8,112`, `509` pos)** | `Baseline_2_Logit` | 0.814 ± 0.045 | 0.754 [0.716, 0.793] | 0.315 ± 0.062 | 0.237 [0.183, 0.302] | 0.0544 | +0.091 [0.067, 0.115] | 26.5% | 38.3% |
| **TRUE UNSEEN ISSUERS (`n=8,112`, `509` pos)** | **`XGBoost`** | 0.825 ± 0.049 | 0.770 [0.729, 0.809] | 0.295 ± 0.061 | 0.251 [0.201, 0.318] | 0.0527 | +0.120 [0.088, 0.153] | 27.3% | 42.6% |

### 3.2b. `H-01` Branch 2 Empirical Delay Sensitivity — `EXACT + STATUTORY_LAG + 37d` (`p90` UPCOM Distress Delay)

Applies the empirical `p90 = +37d` UPCOM distress late-filing offset onto `STATUTORY_LAG` records (`require_exact_filing=False`) to eliminate early-availability optimism on non-PDF filings.

| Test Year | Inner OOF Strategy | `n_est` | Platt Slope | Train Rows (Pos / Iss) | Test Common (Pos / Iss) | Unseen Common (Pos / Iss) | XGB Train PR-AUC | OOS ROC-AUC (Alt / Log / **XGB**) | OOS PR-AUC (Alt / Log / **XGB**) | OOS BSS (Alt / Log / **XGB**) | OOS R@5% (Alt / Log / **XGB**) | OOS R@10% (Alt / Log / **XGB**) | Unseen ROC-AUC (Alt / Log / **XGB**) |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| 2021 | `STRATIFIED_GROUP_OOF` | 49 | 1.64 | 7,697 (219 / 60) | 1,955 (162 / 64) | 1,923 (162 / 64) | 0.2704 | 0.654 / 0.796 / **0.783** | 0.305 / 0.347 / **0.236** | +0.055 / +0.105 / **+0.080** | 31.5% / 24.7% / **16.7%** | 38.3% / 45.1% / **37.0%** | 0.654 / 0.800 / **0.787** |
| 2022 | `PURGED_TEMPORAL_OOF` | 15 | 5.00 | 9,723 (291 / 83) | 1,863 (166 / 70) | 1,805 (158 / 67) | 0.1995 | 0.736 / 0.845 / **0.838** | 0.409 / 0.415 / **0.367** | +0.060 / +0.093 / **+0.146** | 31.9% / 31.9% / **29.5%** | 42.8% / 49.4% / **43.4%** | 0.726 / 0.848 / **0.839** |
| 2023 | `PURGED_TEMPORAL_OOF` | 59 | 1.26 | 11,732 (432 / 130) | 1,782 (99 / 38) | 1,671 (87 / 34) | 0.2752 | 0.654 / 0.878 / **0.885** | 0.169 / 0.377 / **0.317** | +0.018 / +0.137 / **+0.168** | 24.2% / 38.4% / **32.3%** | 40.4% / 58.6% / **54.5%** | 0.686 / 0.899 / **0.885** |
| 2024 | `PURGED_TEMPORAL_OOF` | 80 | 0.98 | 13,659 (619 / 166) | 1,707 (78 / 34) | 1,580 (69 / 31) | 0.3128 | 0.632 / 0.842 / **0.877** | 0.161 / 0.286 / **0.264** | +0.014 / +0.133 / **+0.128** | 25.6% / 41.0% / **37.2%** | 33.3% / 55.1% / **59.0%** | 0.609 / 0.843 / **0.877** |
| 2025 | `PURGED_TEMPORAL_OOF` | 43 | 1.62 | 15,458 (726 / 186) | 1,222 (39 / 24) | 1,111 (35 / 22) | 0.2961 | 0.472 / 0.772 / **0.790** | 0.037 / 0.191 / **0.242** | -0.016 / +0.051 / **+0.090** | 7.7% / 35.9% / **41.0%** | 10.3% / 46.2% / **56.4%** | 0.507 / 0.769 / **0.788** |

#### Branch 2b (`+37d`) Summary (`ALL OOS` & `TRUE UNSEEN ISSUERS`, `n=1,000` Cluster Bootstrap 95% CI)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=8,529`, `544` pos)** | `Baseline_1_AltmanZ` | 0.629 ± 0.097 | 0.579 [0.532, 0.630] | 0.216 ± 0.143 | 0.154 [0.112, 0.203] | 0.0585 | +0.039 [0.018, 0.064] | 13.8% | 21.5% |
| **ALL OOS (`n=8,529`, `544` pos)** | `Baseline_2_Logit` | 0.827 ± 0.042 | 0.792 [0.753, 0.827] | 0.323 ± 0.088 | 0.284 [0.225, 0.356] | 0.0543 | +0.107 [0.082, 0.133] | 30.5% | 46.5% |
| **ALL OOS (`n=8,529`, `544` pos)** | **`XGBoost`** | 0.834 ± 0.047 | 0.789 [0.747, 0.828] | 0.285 ± 0.056 | 0.246 [0.200, 0.308] | 0.0533 | +0.124 [0.094, 0.152] | 27.6% | 43.9% |
| **TRUE UNSEEN ISSUERS (`n=8,090`, `511` pos)** | `Baseline_1_AltmanZ` | 0.636 ± 0.084 | 0.581 [0.530, 0.629] | 0.225 ± 0.139 | 0.158 [0.113, 0.210] | 0.0579 | +0.040 [0.017, 0.066] | 14.3% | 21.7% |
| **TRUE UNSEEN ISSUERS (`n=8,090`, `511` pos)** | `Baseline_2_Logit` | 0.832 ± 0.050 | 0.797 [0.755, 0.834] | 0.341 ± 0.086 | 0.303 [0.239, 0.379] | 0.0536 | +0.111 [0.086, 0.138] | 31.5% | 47.2% |
| **TRUE UNSEEN ISSUERS (`n=8,090`, `511` pos)** | **`XGBoost`** | 0.835 ± 0.047 | 0.788 [0.746, 0.827] | 0.290 ± 0.050 | 0.250 [0.203, 0.318] | 0.0529 | +0.122 [0.093, 0.153] | 27.0% | 44.0% |

### 3.3. Mode A Ablation Cross-Check (`FUNDAMENTAL_DISTRESS`, `0%` TTM Overlap, `2021–2024`, `EXACT_FILING`)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=6,771`, `117` pos)** | `Baseline_1_AltmanZ` | 0.765 ± 0.086 | 0.729 [0.644, 0.812] | 0.129 ± 0.087 | 0.069 [0.037, 0.140] | 0.0168 | +0.014 [-0.021, 0.053] | 25.6% | 37.6% |
| **ALL OOS (`n=6,771`, `117` pos)** | `Baseline_2_Logit` | 0.751 ± 0.084 | 0.707 [0.628, 0.782] | 0.104 ± 0.043 | 0.051 [0.027, 0.094] | 0.0168 | +0.014 [0.002, 0.026] | 20.5% | 30.8% |
| **ALL OOS (`n=6,771`, `117` pos)** | **`XGBoost`** | 0.855 ± 0.044 | 0.837 [0.792, 0.878] | 0.112 ± 0.057 | 0.077 [0.048, 0.118] | 0.0166 | +0.022 [-0.010, 0.045] | 32.5% | 49.6% |
| **TRUE UNSEEN ISSUERS (`n=6,536`, `83` pos)** | `Baseline_1_AltmanZ` | 0.736 ± 0.060 | 0.697 [0.597, 0.786] | 0.089 ± 0.061 | 0.039 [0.019, 0.088] | 0.0125 | +0.003 [-0.021, 0.024] | 19.3% | 34.9% |
| **TRUE UNSEEN ISSUERS (`n=6,536`, `83` pos)** | `Baseline_2_Logit` | 0.739 ± 0.113 | 0.692 [0.595, 0.775] | 0.123 ± 0.090 | 0.041 [0.020, 0.093] | 0.0124 | +0.012 [-0.002, 0.027] | 22.9% | 31.3% |
| **TRUE UNSEEN ISSUERS (`n=6,536`, `83` pos)** | **`XGBoost`** | 0.834 ± 0.060 | 0.815 [0.761, 0.866] | 0.077 ± 0.016 | 0.055 [0.033, 0.097] | 0.0125 | +0.009 [-0.024, 0.036] | 28.9% | 42.2% |

---

## 4. Direct Comparison: `375` Issuers (`go_live_nfr_run`) vs `685` Issuers (`ML-03b`)

| Cohort / Arm | Model | Yearly ROC-AUC | Pooled ROC-AUC [95% CI] (Width) | Yearly PR-AUC | Pooled PR-AUC [95% CI] (Width) | Pooled Brier | Pooled BSS | Pooled R@5% | Pooled R@10% |
|---|---|---:|---|---:|---|---:|---:|---:|---:|
| `375` Issuers (`EXACT_FILING`, Wave 3) | `XGBoost` | 0.809 ± 0.104 | 0.765 [0.678, 0.843] | 0.169 ± 0.053 | 0.157 [0.087, 0.258] | 0.0232 | +0.065 | 34.5% | 53.9% |
| `375` Issuers (`EXACT_FILING`, Wave 3) | `Baseline_2_Logit` | 0.726 ± 0.084 | 0.717 [0.626, 0.801] | 0.132 ± 0.056 | 0.128 [0.070, 0.216] | 0.0240 | +0.034 | 31.5% | 42.4% |
| `375` Issuers (`EXACT_FILING`, Wave 3) | `Baseline_1_AltmanZ` | 0.628 ± 0.132 | 0.631 [0.539, 0.726] | 0.129 ± 0.105 | 0.105 [0.055, 0.184] | 0.0241 | +0.028 | 24.2% | 33.3% |
| `685` Issuers (`EXACT_FILING`, Branch 1) | `XGBoost` | 0.770 ± 0.107 | 0.752 [0.682, 0.819] (`±0.069`) | 0.162 ± 0.016 | 0.161 [0.109, 0.238] (`±0.065`) | 0.0275 | +0.081 | 35.2% | 53.0% |
| `685` Issuers (`EXACT_FILING`, Branch 1) | `Baseline_2_Logit` | 0.727 ± 0.080 | 0.722 [0.655, 0.790] (`±0.067`) | 0.177 ± 0.052 | 0.159 [0.104, 0.235] (`±0.066`) | 0.0281 | +0.061 | 33.9% | 46.2% |
| `685` Issuers (`EXACT_FILING`, Branch 1) | `Baseline_1_AltmanZ` | 0.591 ± 0.088 | 0.600 [0.521, 0.679] (`±0.079`) | 0.115 ± 0.084 | 0.099 [0.059, 0.160] (`±0.050`) | 0.0295 | +0.017 | 20.3% | 30.9% |
| `685` Issuers (`STATUTORY_LAG +0d`, Branch 2) | `XGBoost` | 0.825 ± 0.050 | 0.770 [0.728, 0.811] (`±0.041`) | 0.284 ± 0.064 | 0.249 [0.198, 0.314] (`±0.058`) | 0.0531 | +0.121 | 27.3% | 43.2% |
| `685` Issuers (`STATUTORY_LAG +0d`, Branch 2) | `Baseline_2_Logit` | 0.813 ± 0.039 | 0.751 [0.711, 0.788] (`±0.039`) | 0.309 ± 0.055 | 0.228 [0.177, 0.290] (`±0.057`) | 0.0550 | +0.090 | 26.6% | 38.0% |
| `685` Issuers (`STATUTORY_LAG +0d`, Branch 2) | `Baseline_1_AltmanZ` | 0.629 ± 0.097 | 0.583 [0.534, 0.635] (`±0.050`) | 0.215 ± 0.146 | 0.153 [0.114, 0.201] (`±0.044`) | 0.0583 | +0.036 | 14.0% | 21.8% |
| `685` Issuers (`STATUTORY_LAG +37d`, Branch 2b) | `XGBoost` | 0.834 ± 0.047 | 0.789 [0.747, 0.828] (`±0.040`) | 0.285 ± 0.056 | 0.246 [0.200, 0.308] (`±0.054`) | 0.0533 | +0.124 | 27.6% | 43.9% |
| `685` Issuers (`STATUTORY_LAG +37d`, Branch 2b) | `Baseline_2_Logit` | 0.827 ± 0.042 | 0.792 [0.753, 0.827] (`±0.037`) | 0.323 ± 0.088 | 0.284 [0.225, 0.356] (`±0.066`) | 0.0543 | +0.107 | 30.5% | 46.5% |
| `685` Issuers (`STATUTORY_LAG +37d`, Branch 2b) | `Baseline_1_AltmanZ` | 0.629 ± 0.097 | 0.579 [0.532, 0.630] (`±0.049`) | 0.216 ± 0.143 | 0.154 [0.112, 0.203] (`±0.045`) | 0.0585 | +0.039 | 13.8% | 21.5% |

**Key Scientific Observations (375 vs 685):**
1. **Confidence Interval Tightening (`H-01` & `GATE-ML-4`):** Expanding from `375` (`71` positives) to `685` issuers (`318` positives) tightens `XGBoost` Pooled ROC-AUC 95% CI width from **`0.165`** (`[0.678, 0.843]`) to **`0.137`** (`Branch 1`) and **`0.083`** (`Branch 2: [0.728, 0.811]`), establishing a non-overlapping separation over `Altman Z''` (`[0.534, 0.635]`).
2. **5/5 & 5/5 Positive BSS Across Walk-Forward Years:** Unlike the `375`-issuer Wave 3 run where 2025 turned negative (`-0.063`), `685`-issuer `Monotone_XGBoost` achieves strictly **positive Brier Skill Score across test years (`2021–2025`)** in both Branch 1 (`+0.058` to `+0.105`, pooled `+0.081`) and Branch 2 (`+0.063` to `+0.172`, pooled `+0.121`).
3. **Branch 1 vs Branch 2 Trade-off (`EXACT_FILING` vs `STATUTORY_LAG`):** Retaining UPCOM delisted firms in Branch 2 raises positive OOS rows from `236` to `542` and pushes `XGBoost` yearly ROC-AUC up to `0.887` (`PR-AUC 0.208–0.367`), while Branch 1 (`EXACT_FILING`) maintains higher Top-10% concentration (`53.0%` vs `43.2%`) because `EXACT_FILING` has lower baseline prevalence (`3.09%` vs `6.34%`).

---

## 5. Through-the-Cycle `R1 → R5` Monotonicity & `TT200 → TT99` PSI Audit (`GATE-ML-5`)

### 5.1. Empirical Event Rate Monotonicity Across `R1 → R5` (PIT Per-Fold Cutoffs)

| `risk_tier` (TTC) | Branch 1 (`EXACT`, `2021–2025` Pooled) | Branch 1 (`EXACT`, `2022–2025`) | Branch 1 Prod Inner OOF (`2016–2024`) | Branch 2 (`EXACT + STATUTORY_LAG +0d`, `2021–2025` Pooled) |
|---|---:|---:|---:|---:|
| **`R1`** | `39 / 2,928` (`1.33%`) | `21 / 2,061` (`1.02%`) | `35 / 2,598` (**`1.35%`**) | `76 / 4,484` (**`1.69%`**) |
| **`R2`** | `23 / 2,000` (`1.15%`) | `19 / 1,637` (`1.16%`) | `17 / 1,304` (**`1.30%`**) | `62 / 2,050` (**`3.02%`**) |
| **`R3`** | `35 / 1,535` (`2.28%`) | `33 / 1,384` (`2.38%`) | `19 / 780` (**`2.44%`**) | `143 / 1,089` (**`13.13%`**) |
| **`R4`** | `29 / 658` (`4.41%`) | `25 / 553` (`4.52%`) | `17 / 260` (**`6.54%`**) | `83 / 383` (**`21.67%`**) |
| **`R5`** | `110 / 528` (`20.83%`) | `95 / 452` (`21.02%`) | `61 / 261` (**`23.37%`**) | `178 / 548` (**`32.48%`**) |

- **Monotonicity Audit (PIT Per-Fold Cutoffs):** Branch 1 (`EXACT_FILING`, `2021–2025` Pooled, `n=7,649`) `is_monotone_non_decreasing = False` (`1.33% -> 1.15% -> 2.28% -> 4.41% -> 20.83%`, `15.6×` `R5/R1` lift). Branch 2 (`EXACT + STATUTORY_LAG +0d`, `2021–2025` Pooled, `n=8,554`) `is_monotone_non_decreasing = True` (`1.69% -> 3.02% -> 13.13% -> 21.67% -> 32.48%`, `19.2×` `R5/R1` lift). Production inner OOF (`2016–2024`, `n=5,203`) (`1.35% -> 1.30% -> 2.44% -> 6.54% -> 23.37%`, `17.3×` `R5/R1` lift).

### 5.2. `M-05` Taxonomy Drift Audit: `VAS_TT200_2014` (`2024–2025`) → `VAS_TT99_2025` (`2026`)

| Arm | Ref Rows (`2024–2025`) | Tgt Rows (`2026`) | Mean Feature PSI | Max Feature PSI | Features `> 0.25` | Mean Missing (`TT200 -> TT99`) | Score PSI | `R4+R5` Share (`TT200 -> TT99`) |
|---|---:|---:|---:|---:|---:|---|---:|---|
| **Branch 1 (`EXACT_FILING`)** | 3,932 | 1,441 | **`0.0273`** | `0.1018` | `0 / 23` | `2.321% -> 2.191%` | `0.0442` | `23.93% -> 18.88%` (`-5.06 pp`) |
| **Branch 2 (`EXACT + STATUTORY_LAG +0d`)** | 4,559 | 1,662 | **`0.0253`** | `0.0817` | `0 / 23` | `3.222% -> 3.254%` | `0.0228` | `21.91% -> 17.87%` (`-4.04 pp`) |

---

## 6. Historical VN Corporate Distress Case Studies (100% Out-of-Sample Expanding PIT)

| Ticker | Exchange | Arm | First Control / Warning (`event_date`) | Mandatory Delisting / Suspension | First `R4/R5` Quarter (`_period_available_at`) | Tier · 1Y PD · PIT Pct · Lift | Lead vs Warn | Lead vs Delist | Top-3 TreeSHAP Drivers (Vietnamese) |
|---|---|---|---|---|---|---|---:|---:|---|
| **`HBC`** | `UPCOM` | `EXACT_WF` | `2023-04-11` (`CONTROL`) | `2024-07-26` (`HOSE`) | `2022-Q4` (`2023-02-10`) | **`R5`** · `25.6%` · P96.1 · `12.3×` | **0.7 Q** (`60d`) | **5.8 Q** (`532d`) | Biên EBIT chuẩn hóa toàn thị trường (Z-Score) (`+0.53`); Khả năng chi trả lãi vay (`+0.50`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.22`) |
| **`POM`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2023-04-11` (`CONTROL`) | `2024-04-03` (`HOSE`) | `2020-Q1` (`2020-04-29`) | **`R4`** · `11.7%` · P94.8 · `4.4×` | **11.8 Q** (`1077d`) | **15.7 Q** (`1435d`) | Khả năng chi trả lãi vay (`+0.46`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.35`); Số quý lỗ ròng liên tiếp (`+0.20`) |
| **`HNG`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2020-04-17` (`CONTROL`) | `2024-07-26` (`HOSE`) | `2020-Q1` (`2020-04-28`) | **`R4`** · `9.8%` · P93.7 · `3.7×` | **-0.1 Q** (`-11d`) | **17.0 Q** (`1550d`) | Khả năng chi trả lãi vay (`+0.42`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.39`); Tỷ số thanh khoản tiền mặt (`+0.06`) |
| **`DLG`** | `HOSE` | `STATUTORY_PRE2020_WF` | `2021-04-23` (`CONTROL`) | — (`HOSE`) | `2020-Q3` (`2020-11-02`) | **`R4`** · `11.7%` · P92.9 · `4.4×` | **1.9 Q** (`172d`) | — | Khả năng chi trả lãi vay (`+0.46`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.37`); Số quý lỗ ròng liên tiếp (`+0.20`) |
| **`SMC`** | `HOSE` | `EXACT_WF` | `2024-04-04` (`CONTROL`) | — (`HOSE`) | `2022-Q3` (`2022-10-25`) | **`R4`** · `8.0%` · P93.9 · `3.8×` | **5.8 Q** (`527d`) | — | Biên EBIT chuẩn hóa toàn thị trường (Z-Score) (`+0.34`); Khả năng chi trả lãi vay (`+0.32`); Số quý lỗ ròng liên tiếp (`+0.20`) |
| **`QBS`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2021-04-09` (`CONTROL`) | `2024-04-04` (`HOSE`) | `2020-Q1` (`2020-05-01`) | **`R5`** · `22.3%` · P99.5 · `8.4×` | **3.8 Q** (`343d`) | **15.7 Q** (`1434d`) | Khả năng chi trả lãi vay (`+0.43`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.37`); Số quý lỗ ròng liên tiếp (`+0.25`) |
| **`PVX`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2021-09-04` (`ADVERSE_OR_DISCLAIMER_AUDIT`) | `2020-05-12` (`HNX`) | `2020-Q1` (`2020-05-06`) | **`R4`** · `11.7%` · P94.8 · `4.4×` | **5.3 Q** (`486d`) | **0.1 Q** (`6d`) | Khả năng chi trả lãi vay (`+0.42`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.37`); Số quý lỗ ròng liên tiếp (`+0.21`) |
| **`PVL`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2023-04-21` (`TRADING_RESTRICTION`) | `2023-03-17` (`HNX`) | `2020-Q1` (`2020-04-13`) | **`R5`** · `22.3%` · P99.5 · `8.4×` | **12.1 Q** (`1103d`) | **11.7 Q** (`1068d`) | Khả năng chi trả lãi vay (`+0.42`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.39`); Số quý lỗ ròng liên tiếp (`+0.23`) |
| **`HVN`** | `HOSE` | `EXACT_WF` | `2021-10-27` (`CONTROL`) | — (`HOSE`) | `2021-Q1` (`2021-05-04`) | **`R5`** · `37.5%` · P97.8 · `19.3×` | **1.9 Q** (`176d`) | — | Khả năng chi trả lãi vay (`+0.30`); Biên EBIT chuẩn hóa toàn thị trường (Z-Score) (`+0.21`); Tỷ suất sinh lời trên VCSH công ty mẹ (ROE) (`+0.10`) |
| **`NVL`** | `HOSE` | `EXACT_WF` | — | — (`HOSE`) | `2023-Q1` (`2023-04-28`) | **`R4`** · `7.5%` · P85.7 · `3.3×` | — | — | Tăng trưởng doanh thu thuần YoY (`+0.34`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.23`); Số quý lỗ ròng liên tiếp (`+0.11`) |
| **`SII`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2023-04-07` (`FORCED_DELISTING`) | `2023-04-07` (`HOSE`) | `2020-Q3` (`2020-10-28`) | **`R5`** · `20.1%` · P96.5 · `7.6×` | **9.8 Q** (`891d`) | **9.8 Q** (`891d`) | Khả năng chi trả lãi vay (`+0.39`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.33`); Số quý lỗ ròng liên tiếp (`+0.24`) |
| **`SJF`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2023-10-04` (`CONTROL`) | `2023-11-03` (`UPCOM`) | `2020-Q1` (`2020-05-07`) | **`R4`** · `15.4%` · P97.4 · `5.8×` | **13.6 Q** (`1245d`) | **14.0 Q** (`1275d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.54`); Khả năng chi trả lãi vay (`+0.48`); Phân vị quy mô tổng tài sản theo quý (`+0.06`) |
| **`TCD`** | `UPCOM` | `EXACT_WF` | `2025-05-07` (`CONTROL`) | `2025-10-01` (`UPCOM`) | `2023-Q3` (`2023-10-27`) | **`R4`** · `6.9%` · P79.2 · `3.0×` | **6.1 Q** (`558d`) | **7.7 Q** (`705d`) | Tăng trưởng doanh thu thuần YoY (`+0.36`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.21`); Tỷ số thanh khoản tiền mặt (`+0.08`) |
| **`DDG`** | `UPCOM` | `EXACT_WF` | `2025-10-28` (`TRADING_RESTRICTION`) | `2026-06-02` (`HOSE`) | `2023-Q4` (`2024-01-30`) | **`R5`** · `25.5%` · P98.9 · `11.2×` | **7.0 Q** (`637d`) | **9.4 Q** (`854d`) | Tỷ suất sinh lời trên tài sản (ROA) (`+0.46`); Khả năng chi trả lãi vay (`+0.42`); Biên EBIT chuẩn hóa toàn thị trường (Z-Score) (`+0.29`) |
| **`VNE`** | `UPCOM` | `EXACT_WF` | `2024-04-05` (`CONTROL`) | `2026-05-20` (`HOSE`) | `2023-Q3` (`2023-10-30`) | **`R4`** · `6.7%` · P78.5 · `3.0×` | **1.7 Q** (`158d`) | **10.2 Q** (`933d`) | Tăng trưởng doanh thu thuần YoY (`+0.34`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.24`); Tỷ số thanh khoản tiền mặt (`+0.08`) |
| **`SDA`** | `UPCOM` | `STATUTORY_PRE2020_WF` | `2025-05-19` (`TRADING_RESTRICTION`) | `2026-05-04` (`HOSE`) | `2020-Q1` (`2020-05-04`) | **`R5`** · `16.9%` · P95.1 · `6.4×` | **20.2 Q** (`1841d`) | **24.0 Q** (`2191d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.53`); Khả năng chi trả lãi vay (`+0.41`); Phân vị quy mô tổng tài sản theo quý (`+0.07`) |

---

## 7. Production Snapshot Refresh (`ARCH-06 / ML-08` Verification)

- **Active Serving Store Root (`FININTEL_DATA_ROOT`):** `D:\KiCuoiCung\DATN\financial-intelligence\data\universe_600_800`
- **Snapshot ID & DuckLake Version:** `snap_20261123T061154Z_ml03b_685_eval_2_dd73e0b0` (DuckLake `version = 13`)
- **Published Issuers & ML Scores:** `685` issuers (HOSE 280 / HNX 110 / UPCOM 295), `32,176` total `ml_scores_and_explanations` rows (`515` issuers with supervised `EXACT_FILING` PD/TreeSHAP scores across `23` ML-05 features; float32 TreeSHAP max residual = `1.18e-06 < 1e-5`)
- **Extension Port Tables Preserved (`B-03`):** `regulatory_events` = `1,406` rows, `market_prices` = `822,072` rows (verified present in DuckLake & on disk after `publish_snapshot()`).
- **Read-Back Verification (`ParquetRepository` + `DashboardQueryService`):** `get_model_card()` -> `run_id=ml03b_685_eval_20261123T055713Z`, `feature_count=23`; `get_company_market_overlay('HBC')` -> `2208` price points, `5` regulatory markers, `2` model markers.
