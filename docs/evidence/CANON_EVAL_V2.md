# Canon Evaluation v2 — Deterministic, Double-Count Audit, OOS Hour Validation

**Date:** 2025-01-27  
**Script:** `scripts/run_canon_eval_v2.py`  
**Results:** `results/oos_2025_e050/canon_eval_v2.json`  
**Seed:** `SEED_EVAL = 42` (fixed for all runs, Common Random Numbers via shared seed)

---

## Task 1 — Deterministic Reproducibility

B01 ep899 baseline evaluated twice with `env.reset(seed=42)`.

| Run | Trades | Net PnL | Final Equity |
|-----|--------|---------|--------------|
| 1   | 41 767 | −$711.79 | $9 288.60   |
| 2   | 41 767 | −$711.79 | $9 288.60   |

**Result: PASS** — Identical trades, PnL and equity across runs.  
CRN is achieved naturally: same seed → same RNG stream → same slippage draws (minor divergence possible only when configs change balance enough to alter sizing, which in turn changes trade count and RNG call order).

---

## Task 2 — Exit Slippage Double-Count Diagnostic

B01 ep899 with `exit_sl_multiplier=1.5` run under four `exit_slippage_mode` settings:

| Mode | Trades | Gross PnL | Total Costs | **Net PnL** | PF_net | Sharpe |
|------|--------|-----------|-------------|-------------|--------|--------|
| **OFF** (no exit slip) | 41 767 | $14 105 | $14 817 | **−$712** | 0.986 | −0.87 |
| **price_only** | 41 792 | $9 872 | $12 906 | **−$3 034** | 0.935 | −4.33 |
| **cost_only** | 41 764 | $14 123 | $17 819 | **−$3 696** | 0.931 | −0.86 |
| **both** (old default) | 41 792 | $9 872 | $15 512 | **−$5 640** | 0.884 | −4.33 |

### Analysis

| Quantity | Value |
|----------|-------|
| Δ(price_only − OFF) | −$2 322  ← exit slip via worse fill |
| Δ(cost_only − OFF) | −$2 985  ← exit slip via separate cost |
| Δ(both − OFF) | −$4 928  ← sum of both channels |
| price_only + cost_only − OFF | −$5 306  (predicted if additive) |
| both − OFF (actual) | −$4 928  (close; small gap from CRN drift) |

**Conclusion: EXIT SLIPPAGE WAS DOUBLE-COUNTED in `mode=both`.**

- `price_only` adjusts exit fill → gross PnL drops by ~$4 233 but costs decrease by ~$1 911 (no exit cost line) → net impact −$2 322.
- `cost_only` leaves fill untouched → gross PnL unchanged but costs increase by ~$3 003 → net impact −$2 985.
- `both` applies both channels → impact is nearly the **sum** of the individual channels.

**Fix applied:** All canon-set runs (Task 3) use `exit_slippage_mode="price_only"`, which is the physically correct convention: exit slippage adjusts the fill price (reducing gross PnL), and is *not* also recorded as a separate cost.

> **Impact on prior report:** The numbers in `SLIPPAGE_SENSITIVITY_ANALYSIS.md` Tasks 1, 2, 5 used `mode=both`, overstating exit slippage impact by roughly 2×. Corrected (price_only) numbers are in Task 3 below.

---

## Task 3 — Canon Set (Corrected, `mode=price_only`)

### 3a. Main Grid

| Config | Trades | Return % | MDD % | PF_net | WR_net | EV_net | **Net PnL** | Sharpe |
|--------|--------|----------|-------|--------|--------|--------|-------------|--------|
| **ep774 baseline** | 41 683 | −0.79 | 19.49 | 0.998 | 45.7 | −0.002 | **−$80** | −0.07 |
| ep774 exitSL=1.0 | 41 689 | −21.42 | 29.25 | 0.956 | 45.5 | −0.051 | −$2 142 | −2.98 |
| ep774 exitSL=1.2 | 41 690 | −23.09 | 30.11 | 0.952 | 45.5 | −0.055 | −$2 310 | −3.25 |
| ep774 exitSL=1.5 | 41 690 | −25.51 | 31.30 | 0.947 | 45.5 | −0.061 | −$2 551 | −3.65 |
| **ep899 baseline** | 41 767 | −7.11 | 20.06 | 0.986 | 45.4 | −0.017 | **−$712** | −0.87 |
| ep899 exitSL=1.0 | 41 788 | −26.42 | 29.95 | 0.945 | 45.2 | −0.063 | −$2 642 | −3.68 |
| ep899 exitSL=1.2 | 41 789 | −27.98 | 31.21 | 0.941 | 45.2 | −0.067 | −$2 798 | −3.94 |
| ep899 exitSL=1.5 | 41 792 | −30.33 | 33.23 | 0.935 | 45.2 | −0.073 | −$3 034 | −4.33 |

**Key takeaways:**
- **ep774 ≫ ep899** at every slippage level (ep774 loses ~$630 less).
- **Baseline (no exit slip):** ep774 is near break-even (−$80, Sharpe −0.07), ep899 loses −$712.
- **Exit slippage impact** (price_only, corrected):
  - exitSL=1.0 adds ~$2 060–$1 930 in costs → still far from profitable.
  - exitSL=1.5 adds ~$2 470–$2 320 → roughly **$2.4k** impact (vs the ~$5k reported previously under double-counting `mode=both`).
- At all slippage levels the system is net negative.

### 3b. Post-Hoc Hour Filter on exitSL=1.5

Trades from the 2025 eval with exitSL=1.5, filtered by **in-sample** hour EV:

| Config | Filter | Trades | Net PnL | PF_net | Sharpe | MDD % |
|--------|--------|--------|---------|--------|--------|-------|
| ep774 SL=1.5 | ALL hours | 41 690 | −$2 551 | 0.947 | −3.21 | 31.25 |
| ep774 SL=1.5 | excl 04,19,20 | 36 490 | −$1 930 | 0.954 | −2.54 | 26.21 |
| ep774 SL=1.5 | **top 5 EV hours** | 8 729 | **+$36** | 1.003 | +0.08 | 5.83 |
| ep899 SL=1.5 | ALL hours | 41 792 | −$3 034 | 0.935 | −4.05 | 33.21 |
| ep899 SL=1.5 | excl 04,19,20 | 36 572 | −$2 142 | 0.948 | −3.03 | 25.33 |
| ep899 SL=1.5 | **top 5 EV hours** | 8 708 | **+$543** | 1.056 | +1.30 | 3.54 |

The Top-5 EV hour filter on the same 2025 data turns even the slippage-hit equity curves positive — but this is **in-sample filtering** (selected + tested on same period). Task 4 checks if it holds OOS.

---

## Task 4 — Out-of-Sample Hour-Filter Validation

Two independent OOS tests using B01 ep774 (baseline, no exit slippage):

### 4a. 2024 → 2025

Hours selected on 2024 test data (May 27 – Dec 31 2024, 41 659 trades), validated on 2025 test data (Jan 1 – Dec 25 2025, 41 683 trades).

**Hour EV comparison (USD per trade):**

| Hour | 2024 EV | 2025 EV | Sign match? |
|------|---------|---------|-------------|
| 0 | +0.657 | −0.067 | ✗ |
| 1 | +0.420 | +0.074 | ✓ |
| 2 | +0.191 | +0.214 | ✓ |
| 3 | +0.163 | +0.043 | ✓ |
| 4 | +0.173 | −0.032 | ✗ |
| 5 | +0.167 | +0.004 | ✓ |
| 6 | +0.137 | +0.124 | ✓ |
| 7 | +0.314 | −0.057 | ✗ |
| 8 | +0.266 | −0.014 | ✗ |
| 9 | +0.381 | +0.027 | ✓ |
| 10 | +0.057 | −0.000 | ✗ |
| 11 | +0.234 | −0.007 | ✗ |
| 12 | +0.429 | −0.081 | ✗ |
| 13 | +0.066 | −0.064 | ✗ |
| 14 | +0.438 | −0.075 | ✗ |
| 15 | +0.333 | +0.015 | ✓ |
| 16 | +0.063 | −0.021 | ✗ |
| 17 | +0.132 | +0.130 | ✓ |
| 18 | +0.154 | +0.014 | ✓ |
| 19 | −0.135 | −0.053 | ✓ |
| 20 | −0.139 | −0.126 | ✓ |
| 21 | −0.363 | −0.067 | ✓ |
| 22 | −0.253 | +0.068 | ✗ |
| 23 | +0.165 | −0.102 | ✗ |

**Sign agreement: 12/24 (50% — coin flip).**

| Filter | 2025 Trades | 2025 Net PnL | Sharpe | MDD % |
|--------|-------------|-------------|--------|-------|
| ALL | 41 683 | −$80 | −0.09 | 19.45 |
| Top 5 from 2024 [0,14,12,1,9] | 8 764 | **−$209** | −0.62 | 6.75 |
| Excl neg from 2024 [19,20,21,22] | 34 930 | **+$227** | +0.27 | 15.24 |

**Verdict:** Top-5 hour selection does NOT generalize — it picks 2024's best hours which are neutral-to-negative in 2025 (hours 0, 12, 14 flip sign). The more defensive "exclude worst 4 hours" filter manages a tiny +$227 improvement over the −$80 baseline (hours 19, 20, 21 remain negative in 2025; hour 22 flips).

### 4b. H1 2025 → H2 2025

Hours selected on H1 2025 (Jan–Jun, 20 337 trades), validated on H2 2025 (Jul–Dec, 21 346 trades).

**Sign agreement: 7/24 (29% — worse than coin flip).**

| Filter | H2 Trades | H2 Net PnL | Sharpe | MDD % |
|--------|-----------|-----------|--------|-------|
| ALL | 21 346 | −$1 892 | −4.80 | 21.62 |
| Top 5 from H1 [22,1,17,2,10] | 4 400 | **−$157** | −0.54 | 5.83 |
| Excl neg from H1 [4,7,12,21,23] | 16 829 | **−$1 584** | −4.33 | 18.57 |

H2 2025 was broadly negative (−$1 892 across all hours). The Top-5 filter reduces losses significantly (−$157 vs −$1 892) but is still negative — most of the benefit comes from trade reduction (4.4k vs 21.3k trades), not from hour selection being truly predictive. The exclusion filter barely helps (−$1 584 vs −$1 892).

---

## Summary & Recommendations

### Findings

1. **Double-count confirmed.** `mode=both` charged exit slippage twice (via worse fill AND separate cost). Prior `SLIPPAGE_SENSITIVITY_ANALYSIS.md` overstated exit-slip impact by ~2×. Fix: use `exit_slippage_mode="price_only"`.

2. **Corrected exit slippage impact:** ~$2.1k–$2.5k (with SL multiplier 1.0–1.5) on 2025 OOS. Still substantial — enough to turn ep774's near-breakeven (−$80) into a clear loss (−$2.1k to −$2.5k).

3. **ep774 > ep899** at every slippage level. ep774 baseline is near break-even (−$80, Sharpe −0.07); ep899 baseline loses −$712.

4. **Hour filter does NOT generalize.**
   - 2024 → 2025: only 12/24 hours agree in sign (50%). Top-5 selection hurts OOS (−$209 vs −$80 baseline).
   - H1 → H2: only 7/24 hours agree (29%). Hour-EV structure is non-stationary.
   - The "exclude worst hours" variant shows marginal OOS improvement (+$227 on 2024→2025) but relies on just 4 hours (19–22) which happen to stay negative. Fragile.

5. **The in-sample hour filter success** (Task 3b: ep899 SL=1.5 top5EV → +$543) is an **overfitting artifact**. OOS validation shows no reliable hour predictability.

### Corrected Superseded Numbers

| Metric | Old (SLIPPAGE_SENSITIVITY, mode=both) | Corrected (mode=price_only) |
|--------|----------------------------------------|-----------------------------|
| ep899 exitSL=1.5 net | ~−$5 640 | −$3 034 |
| Exit-slip impact (1.5×) | ~$4 900 | ~$2 320 |
| Hour filter OOS validity | Not tested | **FAILS** (coin flip) |

### Action Items

- [x] `exit_slippage_mode` config field added — use `"price_only"` for all future evals.
- [ ] Consider removing hour-filter logic from production pipeline; it does not survive OOS.
- [ ] Focus optimization on reducing base costs (commission, spread) or improving gross alpha rather than post-hoc filtering.
- [ ] Re-evaluate whether exitSL multiplier 1.0× (the minimum realistic exit slip) is survivable with a stronger checkpoint.
