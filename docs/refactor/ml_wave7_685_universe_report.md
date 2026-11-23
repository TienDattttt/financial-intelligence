# Wave 7B Quantitative ML Re-Evaluation Report — `ML-03b` 685-Issuer Universe (`2021–2025`)

- **Generated (UTC):** `2026-11-22T23:58:14.408327+00:00`
- **Dataset (Read-Only Input):** `data/universe_600_800/curated/financial_facts/ml03b_full/financial_facts.parquet` + `data/universe_600_800/curated/issuers.parquet` (`685` corporate issuers, `1,934,382` raw facts -> `1,930,670` QG1-clean facts, `44/685` critical quarantined = `6.42%`, `as_of = 2026-11-22`, in-memory `industry_snapshot_date = 2026-11-19` per `ML-02`)
- **Exogenous Ground Truth (Port 1):** `data/regulatory_ledger/regulatory_events_600_800.parquet` (`1,406` verified events across `318` distinct positive corporate issuers; `100%` `source_url` + `payload_sha256`)
- **Peer Standardization & Feature Contract:** `PIT_REGIME_SYMMETRIC` + `SECTOR_ADJUSTED_FEATURES` (`23` ML-05 features + contextual missingness indicators)
- **Data Hygiene Notes:** Harmonized `31` stub-transition BS rows (`SJ1` 2016 ANNUAL 9-month stub) in memory to match IS/CF `fiscal_year_start_date`; `0` SMOTE/synthetic rows; `0` lookahead to `y_test.mean()` or future folds.

---

## 1. `GATE-ML-1..6` Compliance Checklist

| Gate | Requirement | Measured 685-Issuer Verification | Status |
|---|---|---|---|
| **`GATE-ML-1`** | Provenance & Anti-Survivorship (`ML-03b`, `H-01`, `H-02`) | `685` corporate issuers (`HOSE 280 / HNX 110 / UPCOM 295`), `1,406` Port 1 events (`318` pos issuers, `101` mandatory delistings), `100%` `source_url` + `payload_sha256`; conservative `STATUTORY_LAG` branch retains `247` distressed UPCOM issuers. | **PASS** |
| **`GATE-ML-2`** | Zero Leakage, Symmetric Horizon (`B-01`), Continuous State (`H-05`), `0%` TTM Overlap | `label_available_at = _period_available_at + 365d` symmetrically for `y=1` & `y=0`; continuous `[event_date, resolution_date)` intervals; Mode A `0%` TTM overlap; zero `2016-01-01` ICB leakage (`2026-11-19` in-memory). | **PASS** |
| **`GATE-ML-3`** | Fair 3-Model Comparison & Unpenalized Platt (`H-07`) | Exact common sample across `Baseline_1_AltmanZ`, `Baseline_2_Logit` (`clip=[-5, 5]`), and `Monotone_XGBoost`; identical inner Purged Temporal OOF `MarginPlattCalibrator(penalty=None)`; BSS benchmarked vs historical `train_prevalence_Y`. | **PASS** |
| **`GATE-ML-4`** | 2-Way Purged Expanding Walk-Forward (`2021–2025`), Bootstrap 95% CI, `True Unseen Issuers` | Full 5-year expanding evaluation (`2021 -> 2025`), `1,000` issuer-clustered bootstrap draws (`issuer_id`), full `True Unseen Issuers` isolation on BOTH H-01 branches. | **PASS** |
| **`GATE-ML-5`** | Anti-Overfitting, `R1 → R5` Monotonicity & `TT200 → TT99` PSI (`M-05`) | Train-to-OOS PR-AUC ratio controlled; positive XGBoost BSS in **5/5** (Branch 1) & **5/5** (Branch 2) years; `TT200 -> TT99` mean PSI = **`0.0284`** (`0/23` > `0.25`, `R4+R5` `-4.67 pp` in 2026); OOF (`19.1×`) & Branch 2 (`12.1×`) `R1 → R5` strictly monotonic. | **PASS** |
| **`GATE-ML-6`** | End-to-End DuckLake Persistence & Serving Parity (`ML-08`, `H-04`) | Published `model_card_meta.parquet` + `ml_scores_and_explanations.parquet` (`23` features) into DuckLake snapshot; read back cleanly via `ParquetRepository` & `DashboardQueryService.get_model_card()`. | **PASS** |

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
| 2021 | `STRATIFIED_GROUP_OOF` | 17 | 5.00 | 1,645 (32 / 18) | 1,585 (46 / 17) | 1,553 (46 / 17) | 0.5816 | 0.522 / 0.628 / **0.629** | 0.088 / 0.137 / **0.136** | +0.024 / +0.032 / **+0.060** | 21.7% / 34.8% / **30.4%** | 23.9% / 41.3% / **37.0%** | 0.522 / 0.632 / **0.633** |
| 2022 | `PURGED_TEMPORAL_OOF` | 15 | 4.80 | 3,258 (68 / 29) | 1,600 (50 / 20) | 1,542 (42 / 17) | 0.4054 | 0.702 / 0.726 / **0.760** | 0.258 / 0.185 / **0.175** | +0.035 / +0.077 / **+0.071** | 40.0% / 32.0% / **36.0%** | 46.0% / 44.0% / **42.0%** | 0.651 / 0.693 / **0.748** |
| 2023 | `PURGED_TEMPORAL_OOF` | 33 | 1.92 | 4,873 (111 / 40) | 1,639 (47 / 21) | 1,538 (35 / 17) | 0.3871 | 0.632 / 0.807 / **0.891** | 0.070 / 0.111 / **0.164** | +0.008 / +0.035 / **+0.102** | 21.3% / 21.3% / **34.0%** | 34.0% / 46.8% / **66.0%** | 0.685 / 0.857 / **0.894** |
| 2024 | `PURGED_TEMPORAL_OOF` | 62 | 0.93 | 6,490 (164 / 54) | 1,630 (58 / 26) | 1,522 (49 / 23) | 0.4054 | 0.616 / 0.799 / **0.860** | 0.115 / 0.231 / **0.173** | +0.017 / +0.081 / **+0.080** | 22.4% / 39.7% / **27.6%** | 31.0% / 51.7% / **60.3%** | 0.577 / 0.790 / **0.853** |
| 2025 | `PURGED_TEMPORAL_OOF` | 72 | 1.03 | 8,115 (207 / 64) | 1,195 (35 / 21) | 1,099 (31 / 19) | 0.3895 | 0.483 / 0.689 / **0.712** | 0.042 / 0.205 / **0.145** | -0.008 / +0.065 / **+0.078** | 8.6% / 37.1% / **40.0%** | 11.4% / 45.7% / **45.7%** | 0.520 / 0.656 / **0.693** |

#### Branch 1 Summary (`ALL OOS` & `TRUE UNSEEN ISSUERS`, `n=1,000` Cluster Bootstrap 95% CI)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=7,649`, `236` pos)** | `Baseline_1_AltmanZ` | 0.591 ± 0.088 | 0.600 [0.521, 0.679] | 0.115 ± 0.084 | 0.099 [0.059, 0.160] | 0.0295 | +0.017 [0.003, 0.033] | 20.3% | 30.9% |
| **ALL OOS (`n=7,649`, `236` pos)** | `Baseline_2_Logit` | 0.730 ± 0.076 | 0.724 [0.656, 0.791] | 0.174 ± 0.049 | 0.156 [0.101, 0.231] | 0.0282 | +0.059 [0.010, 0.102] | 33.1% | 46.6% |
| **ALL OOS (`n=7,649`, `236` pos)** | **`XGBoost`** | 0.770 ± 0.107 | 0.747 [0.676, 0.815] | 0.159 ± 0.017 | 0.159 [0.106, 0.232] | 0.0276 | +0.078 [0.036, 0.120] | 36.0% | 52.5% |
| **TRUE UNSEEN ISSUERS (`n=7,254`, `203` pos)** | `Baseline_1_AltmanZ` | 0.591 ± 0.075 | 0.588 [0.496, 0.677] | 0.089 ± 0.050 | 0.075 [0.043, 0.129] | 0.0269 | +0.011 [-0.003, 0.025] | 20.2% | 27.6% |
| **TRUE UNSEEN ISSUERS (`n=7,254`, `203` pos)** | `Baseline_2_Logit` | 0.725 ± 0.095 | 0.710 [0.634, 0.782] | 0.147 ± 0.034 | 0.140 [0.087, 0.220] | 0.0259 | +0.049 [0.005, 0.094] | 34.0% | 43.8% |
| **TRUE UNSEEN ISSUERS (`n=7,254`, `203` pos)** | **`XGBoost`** | 0.764 ± 0.109 | 0.730 [0.651, 0.802] | 0.144 ± 0.016 | 0.146 [0.092, 0.228] | 0.0254 | +0.069 [0.023, 0.114] | 36.0% | 49.8% |

### 3.2. `H-01` Branch 2 — Expanded `EXACT + STATUTORY_LAG` Conservative (`require_exact_filing=False`)

Retains mandatory-delisted UPCOM/HNX issuers missing CafeF/VCI PDF disclosure links via conservative `effective_available_at` (`STATUTORY_LAG`), preventing survivorship bias across the 247 distressed UPCOM firms.

| Test Year | Inner OOF Strategy | `n_est` | Platt Slope | Train Rows (Pos / Iss) | Test Common (Pos / Iss) | Unseen Common (Pos / Iss) | XGB Train PR-AUC | OOS ROC-AUC (Alt / Log / **XGB**) | OOS PR-AUC (Alt / Log / **XGB**) | OOS BSS (Alt / Log / **XGB**) | OOS R@5% (Alt / Log / **XGB**) | OOS R@10% (Alt / Log / **XGB**) | Unseen ROC-AUC (Alt / Log / **XGB**) |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| 2021 | `STRATIFIED_GROUP_OOF` | 22 | 3.31 | 7,706 (214 / 59) | 1,957 (153 / 61) | 1,925 (153 / 61) | 0.2383 | 0.642 / 0.793 / **0.772** | 0.268 / 0.305 / **0.211** | +0.032 / +0.079 / **+0.063** | 30.7% / 21.6% / **15.7%** | 37.9% / 41.8% / **30.1%** | 0.642 / 0.796 / **0.777** |
| 2022 | `PURGED_TEMPORAL_OOF` | 19 | 4.51 | 9,734 (285 / 81) | 1,872 (170 / 73) | 1,814 (162 / 70) | 0.2056 | 0.739 / 0.821 / **0.826** | 0.435 / 0.396 / **0.352** | +0.073 / +0.097 / **+0.147** | 33.5% / 30.6% / **25.9%** | 45.3% / 45.9% / **41.2%** | 0.730 / 0.824 / **0.827** |
| 2023 | `PURGED_TEMPORAL_OOF` | 79 | 1.03 | 11,745 (419 / 129) | 1,784 (98 / 40) | 1,673 (86 / 36) | 0.3067 | 0.668 / 0.873 / **0.886** | 0.175 / 0.357 / **0.340** | +0.020 / +0.126 / **+0.177** | 24.5% / 36.7% / **32.7%** | 41.8% / 58.2% / **62.2%** | 0.702 / 0.885 / **0.887** |
| 2024 | `PURGED_TEMPORAL_OOF` | 15 | 3.83 | 13,682 (607 / 167) | 1,709 (78 / 35) | 1,582 (69 / 32) | 0.2196 | 0.621 / 0.831 / **0.825** | 0.153 / 0.265 / **0.158** | +0.012 / +0.103 / **+0.011** | 25.6% / 42.3% / **16.7%** | 33.3% / 57.7% / **35.9%** | 0.597 / 0.828 / **0.823** |
| 2025 | `PURGED_TEMPORAL_OOF` | 58 | 1.34 | 15,483 (714 / 187) | 1,232 (43 / 26) | 1,118 (39 / 24) | 0.3121 | 0.476 / 0.775 / **0.787** | 0.044 / 0.262 / **0.230** | -0.014 / +0.070 / **+0.102** | 9.3% / 39.5% / **39.5%** | 14.0% / 51.2% / **58.1%** | 0.509 / 0.766 / **0.786** |

#### Branch 2 Summary (`ALL OOS` & `TRUE UNSEEN ISSUERS`, `n=1,000` Cluster Bootstrap 95% CI)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=8,554`, `542` pos)** | `Baseline_1_AltmanZ` | 0.629 ± 0.097 | 0.583 [0.534, 0.635] | 0.215 ± 0.146 | 0.153 [0.114, 0.201] | 0.0583 | +0.036 [0.016, 0.059] | 14.0% | 21.8% |
| **ALL OOS (`n=8,554`, `542` pos)** | `Baseline_2_Logit` | 0.819 ± 0.038 | 0.757 [0.717, 0.794] | 0.317 ± 0.059 | 0.236 [0.183, 0.300] | 0.0547 | +0.096 [0.070, 0.120] | 26.6% | 38.9% |
| **ALL OOS (`n=8,554`, `542` pos)** | **`XGBoost`** | 0.819 ± 0.044 | 0.768 [0.728, 0.806] | 0.258 ± 0.085 | 0.208 [0.170, 0.255] | 0.0541 | +0.106 [0.072, 0.137] | 24.4% | 42.3% |
| **TRUE UNSEEN ISSUERS (`n=8,112`, `509` pos)** | `Baseline_1_AltmanZ` | 0.636 ± 0.088 | 0.585 [0.534, 0.633] | 0.226 ± 0.141 | 0.157 [0.116, 0.207] | 0.0577 | +0.037 [0.016, 0.063] | 14.3% | 22.2% |
| **TRUE UNSEEN ISSUERS (`n=8,112`, `509` pos)** | `Baseline_2_Logit` | 0.820 ± 0.044 | 0.760 [0.722, 0.799] | 0.323 ± 0.069 | 0.245 [0.190, 0.311] | 0.0541 | +0.097 [0.072, 0.124] | 27.1% | 39.5% |
| **TRUE UNSEEN ISSUERS (`n=8,112`, `509` pos)** | **`XGBoost`** | 0.820 ± 0.043 | 0.768 [0.727, 0.807] | 0.270 ± 0.081 | 0.211 [0.171, 0.265] | 0.0535 | +0.106 [0.071, 0.141] | 24.4% | 42.8% |

### 3.3. Mode A Ablation Cross-Check (`FUNDAMENTAL_DISTRESS`, `0%` TTM Overlap, `2021–2024`, `EXACT_FILING`)

| Evaluation Scope | Model | Yearly ROC-AUC (Mean ± Std) | Pooled ROC-AUC [95% CI] | Yearly PR-AUC (Mean ± Std) | Pooled PR-AUC [95% CI] | Pooled Brier | Pooled BSS (`train_prev_Y`) [95% CI] | Pooled R@5% | Pooled R@10% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL OOS (`n=6,771`, `117` pos)** | `Baseline_1_AltmanZ` | 0.765 ± 0.086 | 0.729 [0.644, 0.812] | 0.129 ± 0.087 | 0.069 [0.037, 0.140] | 0.0168 | +0.014 [-0.021, 0.053] | 25.6% | 37.6% |
| **ALL OOS (`n=6,771`, `117` pos)** | `Baseline_2_Logit` | 0.750 ± 0.084 | 0.707 [0.627, 0.781] | 0.107 ± 0.046 | 0.054 [0.026, 0.095] | 0.0168 | +0.015 [0.003, 0.026] | 19.7% | 29.9% |
| **ALL OOS (`n=6,771`, `117` pos)** | **`XGBoost`** | 0.861 ± 0.045 | 0.837 [0.794, 0.876] | 0.113 ± 0.067 | 0.070 [0.045, 0.103] | 0.0167 | +0.017 [-0.012, 0.039] | 24.8% | 46.2% |
| **TRUE UNSEEN ISSUERS (`n=6,536`, `83` pos)** | `Baseline_1_AltmanZ` | 0.736 ± 0.060 | 0.697 [0.597, 0.786] | 0.089 ± 0.061 | 0.039 [0.019, 0.088] | 0.0125 | +0.003 [-0.021, 0.024] | 19.3% | 34.9% |
| **TRUE UNSEEN ISSUERS (`n=6,536`, `83` pos)** | `Baseline_2_Logit` | 0.737 ± 0.118 | 0.689 [0.596, 0.771] | 0.120 ± 0.097 | 0.048 [0.020, 0.100] | 0.0124 | +0.013 [-0.001, 0.028] | 22.9% | 31.3% |
| **TRUE UNSEEN ISSUERS (`n=6,536`, `83` pos)** | **`XGBoost`** | 0.843 ± 0.056 | 0.816 [0.766, 0.866] | 0.070 ± 0.025 | 0.049 [0.031, 0.078] | 0.0126 | +0.001 [-0.031, 0.025] | 26.5% | 42.2% |

---

## 4. Direct Comparison: `375` Issuers (`go_live_nfr_run`) vs `685` Issuers (`ML-03b`)

| Cohort / Arm | Model | Yearly ROC-AUC | Pooled ROC-AUC [95% CI] (Width) | Yearly PR-AUC | Pooled PR-AUC [95% CI] (Width) | Pooled Brier | Pooled BSS | Pooled R@5% | Pooled R@10% |
|---|---|---:|---|---:|---|---:|---:|---:|---:|
| `375` Issuers (`EXACT_FILING`, Wave 3) | `XGBoost` | 0.809 ± 0.104 | 0.765 [0.678, 0.843] | 0.169 ± 0.053 | 0.157 [0.087, 0.258] | 0.0232 | +0.065 | 34.5% | 53.9% |
| `375` Issuers (`EXACT_FILING`, Wave 3) | `Baseline_2_Logit` | 0.726 ± 0.084 | 0.717 [0.626, 0.801] | 0.132 ± 0.056 | 0.128 [0.070, 0.216] | 0.0240 | +0.034 | 31.5% | 42.4% |
| `375` Issuers (`EXACT_FILING`, Wave 3) | `Baseline_1_AltmanZ` | 0.628 ± 0.132 | 0.631 [0.539, 0.726] | 0.129 ± 0.105 | 0.105 [0.055, 0.184] | 0.0241 | +0.028 | 24.2% | 33.3% |
| `685` Issuers (`EXACT_FILING`, Branch 1) | `XGBoost` | 0.770 ± 0.107 | 0.747 [0.676, 0.815] (`±0.070`) | 0.159 ± 0.017 | 0.159 [0.106, 0.232] (`±0.063`) | 0.0276 | +0.078 | 36.0% | 52.5% |
| `685` Issuers (`EXACT_FILING`, Branch 1) | `Baseline_2_Logit` | 0.730 ± 0.076 | 0.724 [0.656, 0.791] (`±0.068`) | 0.174 ± 0.049 | 0.156 [0.101, 0.231] (`±0.065`) | 0.0282 | +0.059 | 33.1% | 46.6% |
| `685` Issuers (`EXACT_FILING`, Branch 1) | `Baseline_1_AltmanZ` | 0.591 ± 0.088 | 0.600 [0.521, 0.679] (`±0.079`) | 0.115 ± 0.084 | 0.099 [0.059, 0.160] (`±0.050`) | 0.0295 | +0.017 | 20.3% | 30.9% |
| `685` Issuers (`EXACT + STATUTORY_LAG`, Branch 2) | `XGBoost` | 0.819 ± 0.044 | 0.768 [0.728, 0.806] (`±0.039`) | 0.258 ± 0.085 | 0.208 [0.170, 0.255] (`±0.043`) | 0.0541 | +0.106 | 24.4% | 42.3% |
| `685` Issuers (`EXACT + STATUTORY_LAG`, Branch 2) | `Baseline_2_Logit` | 0.819 ± 0.038 | 0.757 [0.717, 0.794] (`±0.038`) | 0.317 ± 0.059 | 0.236 [0.183, 0.300] (`±0.059`) | 0.0547 | +0.096 | 26.6% | 38.9% |
| `685` Issuers (`EXACT + STATUTORY_LAG`, Branch 2) | `Baseline_1_AltmanZ` | 0.629 ± 0.097 | 0.583 [0.534, 0.635] (`±0.050`) | 0.215 ± 0.146 | 0.153 [0.114, 0.201] (`±0.044`) | 0.0583 | +0.036 | 14.0% | 21.8% |

**Key Scientific Observations (375 vs 685):**
1. **Confidence Interval Tightening (`H-01` & `GATE-ML-4`):** Expanding from `375` (`71` positives) to `685` issuers (`318` positives) tightens `XGBoost` Pooled ROC-AUC 95% CI width from **`0.165`** (`[0.678, 0.843]`) to **`0.139`** (`Branch 1`) and **`0.078`** (`Branch 2: [0.728, 0.806]`), establishing a non-overlapping separation over `Altman Z''` (`[0.534, 0.635]`).
2. **5/5 & 5/5 Positive BSS Across Walk-Forward Years:** Unlike the `375`-issuer Wave 3 run where 2025 turned negative (`-0.063`), `685`-issuer `Monotone_XGBoost` achieves strictly **positive Brier Skill Score across test years (`2021–2025`)** in both Branch 1 (`+0.060` to `+0.102`, pooled `+0.078`) and Branch 2 (`+0.011` to `+0.177`, pooled `+0.106`).
3. **Branch 1 vs Branch 2 Trade-off (`EXACT_FILING` vs `STATUTORY_LAG`):** Retaining UPCOM delisted firms in Branch 2 raises positive OOS rows from `236` to `542` and pushes `XGBoost` yearly ROC-AUC up to `0.886` (`PR-AUC 0.158–0.352`), while Branch 1 (`EXACT_FILING`) maintains higher Top-10% concentration (`52.5%` vs `42.3%`) because `EXACT_FILING` has lower baseline prevalence (`3.09%` vs `6.34%`).

---

## 5. Through-the-Cycle `R1 → R5` Monotonicity & `TT200 → TT99` PSI Audit (`GATE-ML-5`)

### 5.1. Empirical Event Rate Monotonicity Across `R1 → R5`

| `risk_tier` (TTC) | Branch 1 (`EXACT`, `2021–2025` Pooled) | Branch 1 (`EXACT`, `2022–2025`) | Branch 1 Prod Inner OOF (`2016–2024`) | Branch 2 (`EXACT + STATUTORY_LAG`, `2021–2025` Pooled — Strictly Monotonic) |
|---|---:|---:|---:|---:|
| **`R1`** | `51 / 3,587` (`1.42%`) | `29 / 2,470` (`1.17%`) | `34 / 2,601` (**`1.31%`**) | `108 / 4,558` (**`2.37%`**) |
| **`R2`** | `23 / 1,981` (`1.16%`) | `19 / 1,715` (`1.11%`) | `21 / 1,301` (**`1.61%`**) | `76 / 2,199` (**`3.46%`**) |
| **`R3`** | `34 / 1,226` (`2.77%`) | `31 / 1,110` (`2.79%`) | `17 / 780` (**`2.18%`**) | `112 / 819` (**`13.68%`**) |
| **`R4`** | `34 / 435` (`7.82%`) | `33 / 429` (`7.69%`) | `12 / 260` (**`4.62%`**) | `94 / 446` (**`21.08%`**) |
| **`R5`** | `94 / 420` (`22.38%`) | `81 / 363` (`22.31%`) | `65 / 261` (**`24.90%`**) | `152 / 532` (**`28.57%`**) |

- **Monotonicity Audit:** Branch 2 (`EXACT + STATUTORY_LAG`, `2021–2025` Pooled, `n=8,554`) is **strictly monotonic** across all 5 tiers (`2.37% < 3.46% < 13.68% < 21.08% < 28.57%`, `12.1×` `R5/R1` lift). Production inner OOF (`2016–2024`, `n=5,203`) is also **strictly monotonic** (`1.31% < 1.61% < 2.18% < 4.62% < 24.90%`, `19.1×` `R5/R1` lift). On Branch 1 (`EXACT_FILING`) pooled `2021–2025`, `R1` (`1.42%`) sits slightly above `R2` (`1.16%`), before rising monotonically `R3 (2.77%) < R4 (7.82%) < R5 (22.38%)`.

### 5.2. `M-05` Taxonomy Drift Audit: `VAS_TT200_2014` (`2024–2025`) → `VAS_TT99_2025` (`2026`)

| Arm | Ref Rows (`2024–2025`) | Tgt Rows (`2026`) | Mean Feature PSI | Max Feature PSI | Features `> 0.25` | Mean Missing (`TT200 -> TT99`) | Score PSI | `R4+R5` Share (`TT200 -> TT99`) |
|---|---:|---:|---:|---:|---:|---|---:|---|
| **Branch 1 (`EXACT_FILING`)** | 3,932 | 1,441 | **`0.0284`** | `0.1018` | `0 / 23` | `2.321% -> 2.191%` | `0.0275` | `21.19% -> 16.52%` (`-4.67 pp`) |
| **Branch 2 (`EXACT + STATUTORY_LAG`)** | 4,559 | 1,662 | **`0.0279`** | `0.0946` | `0 / 23` | `3.222% -> 3.254%` | `0.0374` | `20.99% -> 17.27%` (`-3.72 pp`) |

---

## 6. Historical VN Corporate Distress Case Studies (100% Out-of-Sample Expanding PIT)

| Ticker | Exchange | Arm | First Control / Warning (`event_date`) | Mandatory Delisting / Suspension | First `R4/R5` Quarter (`_period_available_at`) | Tier · 1Y PD · PIT Pct · Lift | Lead vs Warn | Lead vs Delist | Top-3 TreeSHAP Drivers (Vietnamese) |
|---|---|---|---|---|---|---|---:|---:|---|
| **`HBC`** | `UPCOM` | `EXACT_WF` | `2023-04-11` (`CONTROL`) | `2024-07-26` (`HOSE`) | `2022-Q4` (`2023-02-10`) | **`R5`** · `22.6%` · P97.6 · `10.8×` | **0.7 Q** (`60d`) | **5.8 Q** (`532d`) | Khả năng chi trả lãi vay (`+0.24`); Biên EBIT chuẩn hóa toàn thị trường (Z-Score) (`+0.24`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.10`) |
| **`POM`** | `UPCOM` | `EXACT_WF` | `2023-04-11` (`CONTROL`) | `2024-04-03` (`HOSE`) | `2020-Q1` (`2020-04-29`) | **`R4`** · `10.8%` · P94.6 · `4.1×` | **11.8 Q** (`1077d`) | **15.7 Q** (`1435d`) | Khả năng chi trả lãi vay (`+0.42`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.34`); Số quý lỗ ròng liên tiếp (`+0.20`) |
| **`HNG`** | `UPCOM` | `EXACT_WF` | `2020-04-17` (`CONTROL`) | `2024-07-26` (`HOSE`) | `2020-Q1` (`2020-04-28`) | **`R4`** · `9.3%` · P93.4 · `3.5×` | **-0.1 Q** (`-11d`) | **17.0 Q** (`1550d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.38`); Khả năng chi trả lãi vay (`+0.37`); Tỷ số thanh khoản tiền mặt (`+0.06`) |
| **`DLG`** | `HOSE` | `EXACT_WF` | `2021-04-23` (`CONTROL`) | — (`HOSE`) | `2020-Q3` (`2020-11-02`) | **`R4`** · `10.8%` · P92.3 · `4.1×` | **1.9 Q** (`172d`) | — | Khả năng chi trả lãi vay (`+0.41`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.35`); Số quý lỗ ròng liên tiếp (`+0.20`) |
| **`SMC`** | `HOSE` | `EXACT_WF` | `2024-04-04` (`CONTROL`) | — (`HOSE`) | `2022-Q4` (`2023-01-30`) | **`R5`** · `16.2%` · P93.7 · `7.8×` | **4.7 Q** (`430d`) | — | Khả năng chi trả lãi vay (`+0.23`); Biên EBIT chuẩn hóa toàn thị trường (Z-Score) (`+0.18`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.10`) |
| **`QBS`** | `UPCOM` | `EXACT_WF` | `2021-04-09` (`CONTROL`) | `2024-04-04` (`HOSE`) | `2020-Q1` (`2020-05-01`) | **`R5`** · `22.0%` · P99.4 · `8.3×` | **3.8 Q** (`343d`) | **15.7 Q** (`1434d`) | Khả năng chi trả lãi vay (`+0.41`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.38`); Số quý lỗ ròng liên tiếp (`+0.25`) |
| **`PVX`** | `UPCOM` | `EXACT_WF` | `2021-09-04` (`ADVERSE_OR_DISCLAIMER_AUDIT`) | `2020-05-12` (`HNX`) | `2020-Q1` (`2020-05-06`) | **`R4`** · `10.8%` · P94.6 · `4.1×` | **5.3 Q** (`486d`) | **0.1 Q** (`6d`) | Khả năng chi trả lãi vay (`+0.38`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.35`); Số quý lỗ ròng liên tiếp (`+0.21`) |
| **`PVL`** | `UPCOM` | `EXACT_WF` | `2023-04-21` (`TRADING_RESTRICTION`) | `2023-03-17` (`HNX`) | `2020-Q1` (`2020-04-13`) | **`R5`** · `22.0%` · P99.4 · `8.3×` | **12.1 Q** (`1103d`) | **11.7 Q** (`1068d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.40`); Khả năng chi trả lãi vay (`+0.38`); Số quý lỗ ròng liên tiếp (`+0.23`) |
| **`HVN`** | `HOSE` | `EXACT_WF` | `2021-10-27` (`CONTROL`) | — (`HOSE`) | `2021-Q1` (`2021-05-04`) | **`R5`** · `38.2%` · P97.6 · `19.6×` | **1.9 Q** (`176d`) | — | Khả năng chi trả lãi vay (`+0.40`); Biên EBIT chuẩn hóa toàn thị trường (Z-Score) (`+0.15`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.08`) |
| **`NVL`** | `HOSE` | `EXACT_WF` | — | — (`HOSE`) | `2023-Q1` (`2023-04-28`) | **`R4`** · `6.9%` · P85.7 · `3.0×` | — | — | Tăng trưởng doanh thu thuần YoY (`+0.26`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.23`); Phân vị quy mô tổng tài sản theo quý (`+0.09`) |
| **`SII`** | `UPCOM` | `EXACT_WF` | `2023-04-07` (`FORCED_DELISTING`) | `2023-04-07` (`HOSE`) | `2020-Q3` (`2020-10-28`) | **`R5`** · `19.9%` · P96.5 · `7.5×` | **9.8 Q** (`891d`) | **9.8 Q** (`891d`) | Khả năng chi trả lãi vay (`+0.38`); Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.33`); Số quý lỗ ròng liên tiếp (`+0.25`) |
| **`SJF`** | `UPCOM` | `EXACT_WF` | `2023-10-04` (`CONTROL`) | `2023-11-03` (`UPCOM`) | `2020-Q1` (`2020-05-07`) | **`R5`** · `16.6%` · P97.9 · `6.2×` | **13.6 Q** (`1245d`) | **14.0 Q** (`1275d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.55`); Khả năng chi trả lãi vay (`+0.47`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.06`) |
| **`TCD`** | `UPCOM` | `EXACT_WF` | `2025-05-07` (`CONTROL`) | `2025-10-01` (`UPCOM`) | `2023-Q3` (`2023-10-27`) | **`R4`** · `6.8%` · P79.4 · `3.0×` | **6.1 Q** (`558d`) | **7.7 Q** (`705d`) | Tăng trưởng doanh thu thuần YoY (`+0.26`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.23`); Vòng quay tài sản chuẩn hóa toàn thị trường (Z-Score) (`+0.04`) |
| **`DDG`** | `UPCOM` | `EXACT_WF` | `2025-10-28` (`TRADING_RESTRICTION`) | `2026-06-02` (`HOSE`) | `2023-Q4` (`2024-01-30`) | **`R5`** · `23.1%` · P97.8 · `10.1×` | **7.0 Q** (`637d`) | **9.4 Q** (`854d`) | Tỷ suất sinh lời trên tài sản (ROA) (`+0.37`); Khả năng chi trả lãi vay (`+0.28`); Biên EBIT chuẩn hóa toàn thị trường (Z-Score) (`+0.23`) |
| **`VNE`** | `UPCOM` | `EXACT_WF` | `2024-04-05` (`CONTROL`) | `2026-05-20` (`HOSE`) | `2023-Q3` (`2023-10-30`) | **`R4`** · `7.0%` · P79.8 · `3.1×` | **1.7 Q** (`158d`) | **10.2 Q** (`933d`) | Tăng trưởng doanh thu thuần YoY (`+0.26`); Tỷ suất sinh lời trên tài sản (ROA) (`+0.24`); Vòng quay tài sản chuẩn hóa toàn thị trường (Z-Score) (`+0.04`) |
| **`SDA`** | `UPCOM` | `STATUTORY_WF` | `2025-05-19` (`TRADING_RESTRICTION`) | `2026-05-04` (`HOSE`) | `2020-Q1` (`2020-05-04`) | **`R5`** · `16.6%` · P95.3 · `6.2×` | **20.2 Q** (`1841d`) | **24.0 Q** (`2191d`) | Tỷ suất sinh lời trên vốn đầu tư (ROIC) (`+0.53`); Khả năng chi trả lãi vay (`+0.41`); Phân vị quy mô tổng tài sản theo quý (`+0.07`) |

---

## 7. Production Snapshot Refresh (`ARCH-06 / ML-08` Verification)

- **Active Serving Store Root (`FININTEL_DATA_ROOT`):** `D:\KiCuoiCung\DATN\financial-intelligence\data\universe_600_800`
- **Snapshot ID & DuckLake Version:** `snap_20261122T235801Z_ml03b_685_eval_2_55b2f136` (DuckLake `version = 10`)
- **Published Issuers & ML Scores:** `685` issuers (`HOSE 280 / HNX 110 / UPCOM 295`), `32,176` total `ml_scores_and_explanations` rows (`515` issuers with supervised `EXACT_FILING` PD/TreeSHAP scores across `23` ML-05 features; float32 TreeSHAP max residual = `2.70e-06 < 1e-5`)
- **Extension Port Tables Preserved (`B-03`):** `regulatory_events` = `1,406` rows, `market_prices` = `822,072` rows (verified present in DuckLake & on disk after `publish_snapshot()`).
- **Read-Back Verification (`ParquetRepository` + `DashboardQueryService`):** `get_model_card()` -> `run_id=ml03b_685_eval_20261122T234743Z`, `feature_count=23`; `get_company_market_overlay('HBC')` -> `2208` price points, `5` regulatory markers, `2` model markers.
