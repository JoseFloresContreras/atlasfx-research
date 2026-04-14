#!/usr/bin/env python3
"""
Canon Evaluation v2 — Deterministic, CRN, Double-Count Diagnostic, Hour Validation.

Task 1: Deterministic eval (seed_eval=42) + CRN via same-seed slippage streams
Task 2: Double-count diagnostic (price_only vs cost_only vs both)
Task 3: Canon set (B01 ep774/ep899 × baseline/exitSL 1.0/1.2/1.5 + hour filter)
Task 4: OOS hour validation (2024→2025 + H1 2025→H2 2025)
"""
import gc
import json
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from atlasfx.environments.trading_env import ProductionTradingConfig, ProductionTradingEnv
from atlasfx.models.sac import SAC

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

SEED_EVAL = 42
DATA_2025 = Path("data/1min_forex_data_test_2025.parquet")
DATA_2024 = Path("data/1min_forex_data_test.parquet")
INITIAL_BALANCE = 10_000.0
RESULTS_PATH = Path("results/oos_2025_e050/canon_eval_v2.json")

B01_ep774 = Path("models/e050_B01_usdjpy_V2b_s42/checkpoint_ep00774.pt")
B01_ep899 = Path("models/e050_B01_usdjpy_V2b_s42/checkpoint_ep00899.pt")


# ── Helpers ────────────────────────────────────────────────────────────────


def load_data(path: Path) -> pd.DataFrame:
    log.info(f"Loading {path}...")
    return pd.read_parquet(path)


def make_env(df, config_overrides: dict | None = None) -> ProductionTradingEnv:
    """Create eval environment with base config + overrides."""
    base = dict(
        initial_balance=INITIAL_BALANCE,
        max_risk_per_trade_pct=0.02,
        episode_length=len(df),
        commission_per_lot=2.5,
        pip_value_per_lot=10.0,
        spread_pips=0.2,
        slippage_pips_mean=0.10,
        slippage_pips_std=0.05,
        slippage_half_normal=True,
        allow_positive_slippage=False,
        action_penalty=0.0002,
        position_dead_zone=0.10,
        max_leverage=20.0,
        max_position_lots=50.0,
        min_hold_period=20,
    )
    if config_overrides:
        base.update(config_overrides)
    config = ProductionTradingConfig(**base)
    symbol = "usdjpy-pair"
    price_cols = {
        symbol: {
            "open": f"{symbol} | open",
            "high": f"{symbol} | high",
            "low": f"{symbol} | low",
            "close": f"{symbol} | close",
        }
    }
    return ProductionTradingEnv(data=df, symbols=[symbol], price_cols=price_cols, config=config)


def load_agent(checkpoint_path: Path, state_dim: int, action_dim: int,
               device: torch.device) -> SAC:
    agent = SAC(state_dim=state_dim, action_dim=action_dim,
                hidden_dims=[256, 256], device=device)
    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    agent.load_state_dict(ckpt["agent_state_dict"])
    agent.eval()
    return agent


def run_continuous(env: ProductionTradingEnv, agent: SAC, device: torch.device,
                   seed: int = SEED_EVAL):
    """Run continuous evaluation and return metrics dict + trades DataFrame."""
    obs, _ = env.reset(seed=seed)
    done = False
    total_reward = 0.0
    steps = 0

    while not done:
        obs_t = torch.FloatTensor(obs).unsqueeze(0).to(device)
        with torch.no_grad():
            action = agent.select_action(obs_t, deterministic=True)
        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated
        total_reward += reward
        steps += 1

    # Equity curve & MDD
    eq_arr = np.array(env.metrics_tracker.equity_curve)
    rm = np.maximum.accumulate(eq_arr)
    dd = (eq_arr - rm) / rm
    mdd_pct = abs(dd.min()) * 100

    # Metrics
    metrics = env.metrics_tracker.compute_all_metrics()

    # Trades
    trades_df = None
    if hasattr(env, "trade_history") and env.trade_history:
        trades_df = pd.DataFrame([asdict(t) for t in env.trade_history])

    return {
        "steps": steps,
        "total_reward": round(total_reward, 2),
        "final_equity": round(eq_arr[-1], 2),
        "total_return_pct": round((eq_arr[-1] / INITIAL_BALANCE - 1) * 100, 2),
        "max_drawdown_pct": round(mdd_pct, 2),
        "equity_curve": eq_arr,
        **metrics,
    }, trades_df


def summarize(metrics: dict, trades_df: pd.DataFrame | None, label: str) -> dict:
    """Extract key metrics into a compact summary dict."""
    n = int(metrics.get("total_trades", 0))
    result = {
        "label": label,
        "trades": n,
        "final_equity": metrics.get("final_equity", 0),
        "return_pct": round(metrics.get("total_return_pct", 0), 2),
        "mdd_pct": round(metrics.get("max_drawdown_pct", 0), 2),
        "pf_gross": round(metrics.get("profit_factor", 0), 3),
        "pf_net": round(metrics.get("profit_factor_net", 0), 3),
        "wr_gross": round(metrics.get("win_rate_pct", 0), 1),
        "wr_net": round(metrics.get("win_rate_net_pct", 0), 1),
        "ev_gross": round(metrics.get("expected_value_per_trade", 0), 4),
        "ev_net": round(metrics.get("expected_value_net_per_trade", 0), 4),
        "total_costs": round(metrics.get("total_costs_usd", 0), 2),
        "pnl_gross": round(metrics.get("total_pnl_gross_usd", 0), 2),
        "pnl_net": round(metrics.get("total_pnl_net_usd", 0), 2),
    }

    # Daily Sharpe
    eq_arr = metrics.get("equity_curve")
    if eq_arr is not None and len(eq_arr) > 1440:
        BARS_PER_DAY = 1440
        n_days = len(eq_arr) // BARS_PER_DAY
        daily_eq = eq_arr[::BARS_PER_DAY][:n_days + 1]
        daily_returns = np.diff(daily_eq) / daily_eq[:-1]
        if len(daily_returns) > 1 and np.std(daily_returns) > 1e-9:
            result["sharpe_daily"] = round(
                (np.mean(daily_returns) / np.std(daily_returns)) * np.sqrt(252), 2)
        else:
            result["sharpe_daily"] = 0.0
        result["n_days"] = len(daily_returns)
    else:
        result["sharpe_daily"] = 0.0
        result["n_days"] = 0

    return result


def run_eval(df, checkpoint_path, label, config_overrides=None, seed=SEED_EVAL,
             save_trades=False):
    """Full eval pipeline: create env, load agent, run, summarize."""
    device = torch.device("cpu")
    env = make_env(df, config_overrides)
    agent = load_agent(checkpoint_path, env.state_dim, env.action_dim, device)
    t0 = time.time()
    metrics, trades_df = run_continuous(env, agent, device, seed=seed)
    elapsed = time.time() - t0
    log.info(f"  {label}: {elapsed:.0f}s, {metrics.get('total_trades', 0)} trades, "
             f"net=${metrics.get('total_pnl_net_usd', 0):.0f}")
    summary = summarize(metrics, trades_df, label)

    # Cleanup
    del env, agent
    gc.collect()

    if save_trades:
        return summary, trades_df
    return summary, None


def print_comparison(results: list[dict], title: str):
    print(f"\n{'='*120}")
    print(f"  {title}")
    print(f"{'='*120}")
    hdr = (f"{'Label':<45} {'Trades':>6} {'Ret%':>7} {'Sh_d':>6} {'MDD%':>6} "
           f"{'PFg':>6} {'PFn':>6} {'WRn':>6} {'EVn':>8} {'Net$':>8} {'Costs$':>8} {'Gross$':>8}")
    print(hdr)
    print("-" * 130)
    for r in results:
        print(
            f"{r['label']:<45} "
            f"{r['trades']:>6} "
            f"{r['return_pct']:>7.2f} "
            f"{r.get('sharpe_daily', 0):>6.2f} "
            f"{r['mdd_pct']:>5.2f}% "
            f"{r['pf_gross']:>6.3f} "
            f"{r['pf_net']:>6.3f} "
            f"{r['wr_net']:>5.1f}% "
            f"{r['ev_net']:>8.4f} "
            f"{r['pnl_net']:>8.0f} "
            f"{r['total_costs']:>8.0f} "
            f"{r['pnl_gross']:>8.0f}"
        )
    print()


# ── Persistence ────────────────────────────────────────────────────────────


def save_results(all_results):
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(all_results, f, indent=2, default=str)


def load_results() -> dict:
    if RESULTS_PATH.exists():
        with open(RESULTS_PATH) as f:
            return json.load(f)
    return {}


def find_cached(results_list: list, label: str) -> dict | None:
    for r in results_list:
        if r.get("label") == label:
            return r
    return None


def run_eval_cached(df, results_list, checkpoint_path, label,
                    config_overrides=None, seed=SEED_EVAL, save_trades=False):
    cached = find_cached(results_list, label)
    if cached:
        log.info(f"  [CACHED] {label}")
        return cached, None
    summary, trades_df = run_eval(df, checkpoint_path, label, config_overrides,
                                  seed=seed, save_trades=save_trades)
    results_list.append(summary)
    return summary, trades_df


# ── Post-hoc hour analysis ────────────────────────────────────────────────


def add_hour_info(trades_df: pd.DataFrame, data_df: pd.DataFrame) -> pd.DataFrame:
    """Add entry_dt and hour_utc columns to trades DataFrame."""
    trades_df = trades_df.copy()
    time_col = "start_time" if "start_time" in data_df.columns else "timestamp"
    trades_df["entry_ts_ms"] = data_df[time_col].iloc[trades_df["entry_time"].values].values
    trades_df["entry_dt"] = pd.to_datetime(trades_df["entry_ts_ms"], unit="ms", utc=True)
    trades_df["hour_utc"] = trades_df["entry_dt"].dt.hour
    return trades_df


def compute_hour_ev(trades_df: pd.DataFrame) -> dict[int, float]:
    """Return {hour: ev_net} for each hour."""
    trades_df = trades_df.copy()
    trades_df["net_pnl"] = (trades_df["pnl_usd"]
                            - trades_df["commission_usd"]
                            - trades_df["slippage_usd"])
    result = {}
    for h in range(24):
        mask = trades_df["hour_utc"] == h
        h_trades = trades_df[mask]
        n = len(h_trades)
        if n > 0:
            result[h] = h_trades["net_pnl"].mean()
        else:
            result[h] = 0.0
    return result


def hourly_metrics(trades_df: pd.DataFrame, hours: list[int], label: str) -> dict:
    """Compute metrics for trades in specified hours."""
    filtered = trades_df[trades_df["hour_utc"].isin(hours)]
    return compute_filtered_metrics(filtered, label)


def compute_filtered_metrics(trades_df: pd.DataFrame, label: str) -> dict:
    """Compute net metrics from a filtered trades DataFrame."""
    n = len(trades_df)
    if n == 0:
        return {"label": label, "trades": 0, "net_pnl": 0, "gross_pnl": 0,
                "total_costs": 0, "pf_net": 0, "wr_net": 0, "ev_net": 0,
                "sharpe_daily": 0, "mdd_pct": 0, "trades_per_day": 0}

    trades_df = trades_df.copy()
    trades_df["net_pnl"] = (trades_df["pnl_usd"]
                            - trades_df["commission_usd"]
                            - trades_df["slippage_usd"])

    gross_pnl = trades_df["pnl_usd"].sum()
    net_pnl = trades_df["net_pnl"].sum()
    total_costs = trades_df["commission_usd"].sum() + trades_df["slippage_usd"].sum()

    # Net metrics
    net_wins = (trades_df["net_pnl"] > 0).sum()
    wr_net = net_wins / n * 100
    np_ = trades_df.loc[trades_df["net_pnl"] > 0, "net_pnl"].sum()
    nl = abs(trades_df.loc[trades_df["net_pnl"] < 0, "net_pnl"].sum())
    pf_net = np_ / nl if nl > 1e-9 else float("inf")
    ev_net = trades_df["net_pnl"].mean()

    # Equity curve from net PnL
    eq = np.zeros(n + 1)
    eq[0] = INITIAL_BALANCE
    for i, pnl in enumerate(trades_df["net_pnl"].values):
        eq[i + 1] = eq[i] + pnl
    rm = np.maximum.accumulate(eq)
    dd = (eq - rm) / rm
    mdd_pct = abs(dd.min()) * 100

    # Daily Sharpe
    if "entry_dt" in trades_df.columns:
        trades_df["entry_date"] = trades_df["entry_dt"].dt.date
    else:
        trades_df["entry_date"] = trades_df["entry_time"] // 1440
    daily_pnl = trades_df.groupby("entry_date")["net_pnl"].sum()
    n_days = len(daily_pnl)
    sharpe_daily = 0.0
    if n_days > 1 and daily_pnl.std() > 1e-9:
        sharpe_daily = (daily_pnl.mean() / daily_pnl.std()) * np.sqrt(252)

    return {
        "label": label,
        "trades": n,
        "net_pnl": round(net_pnl, 2),
        "gross_pnl": round(gross_pnl, 2),
        "total_costs": round(total_costs, 2),
        "pf_net": round(pf_net, 3),
        "wr_net": round(wr_net, 1),
        "ev_net": round(ev_net, 4),
        "sharpe_daily": round(sharpe_daily, 2),
        "mdd_pct": round(mdd_pct, 2),
        "trades_per_day": round(n / n_days if n_days > 0 else 0, 1),
        "n_days": n_days,
    }


def print_filter_table(results: list[dict], title: str):
    print(f"\n{'='*100}")
    print(f"  {title}")
    print(f"{'='*100}")
    hdr = f"{'Label':<35} {'Trades':>6} {'Net$':>8} {'PFn':>6} {'WRn':>6} {'EVn':>8} {'Sh_d':>7} {'MDD%':>6} {'Tr/d':>5}"
    print(hdr)
    print("-" * 100)
    for r in results:
        print(
            f"{r['label']:<35} "
            f"{r['trades']:>6} "
            f"{r['net_pnl']:>8.0f} "
            f"{r['pf_net']:>6.3f} "
            f"{r['wr_net']:>5.1f}% "
            f"{r['ev_net']:>8.4f} "
            f"{r.get('sharpe_daily', 0):>7.2f} "
            f"{r['mdd_pct']:>5.2f}% "
            f"{r.get('trades_per_day', 0):>5.1f}"
        )
    print()


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════


def main():
    all_results = load_results()
    df_2025 = load_data(DATA_2025)

    # ──────────────────────────────────────────────────────────────────────
    # TASK 1: Deterministic reproducibility check
    # ──────────────────────────────────────────────────────────────────────
    log.info("=" * 80)
    log.info("TASK 1: Deterministic Reproducibility Check (seed=%d)", SEED_EVAL)
    log.info("=" * 80)

    repro = all_results.get("task1_reproducibility", [])
    if len(repro) < 2:
        # Run B01 ep899 baseline TWICE with same seed
        r1, _ = run_eval(df_2025, B01_ep899, "B01 ep899 baseline — run1", seed=SEED_EVAL)
        r2, _ = run_eval(df_2025, B01_ep899, "B01 ep899 baseline — run2", seed=SEED_EVAL)
        repro = [r1, r2]
        all_results["task1_reproducibility"] = repro
        save_results(all_results)

    # Check determinism
    r1, r2 = repro[0], repro[1]
    is_deterministic = (
        r1["trades"] == r2["trades"]
        and r1["pnl_net"] == r2["pnl_net"]
        and r1["pnl_gross"] == r2["pnl_gross"]
        and r1["total_costs"] == r2["total_costs"]
    )
    print(f"\n  Determinism check: {'✅ PASS' if is_deterministic else '❌ FAIL'}")
    print(f"    Run 1: trades={r1['trades']}, net=${r1['pnl_net']:.2f}, "
          f"gross=${r1['pnl_gross']:.2f}, costs=${r1['total_costs']:.2f}")
    print(f"    Run 2: trades={r2['trades']}, net=${r2['pnl_net']:.2f}, "
          f"gross=${r2['pnl_gross']:.2f}, costs=${r2['total_costs']:.2f}")
    all_results["task1_deterministic"] = is_deterministic

    # ──────────────────────────────────────────────────────────────────────
    # TASK 2: Double-count diagnostic
    # ──────────────────────────────────────────────────────────────────────
    log.info("=" * 80)
    log.info("TASK 2: Exit Slippage Double-Count Diagnostic")
    log.info("=" * 80)

    task2 = all_results.get("task2_doublecount", [])

    EXIT_SLIP_BASE = {
        "exit_slippage_enabled": True,
        "exit_slippage_mult_sl": 1.5,
        "exit_slippage_mult_tp": 1.0,
        "exit_slippage_mult_reverse": 1.0,
        "exit_slippage_mult_agent_close": 1.0,
    }

    # 2a: No exit slippage (reference)
    run_eval_cached(df_2025, task2, B01_ep899, "exit_slip=OFF")
    all_results["task2_doublecount"] = task2; save_results(all_results)

    # 2b: price_only — exit slip adjusts price, NOT recorded as cost
    run_eval_cached(df_2025, task2, B01_ep899, "exit_slip=price_only",
                    {**EXIT_SLIP_BASE, "exit_slippage_mode": "price_only"})
    all_results["task2_doublecount"] = task2; save_results(all_results)

    # 2c: cost_only — exit slip recorded as cost, price NOT adjusted
    run_eval_cached(df_2025, task2, B01_ep899, "exit_slip=cost_only",
                    {**EXIT_SLIP_BASE, "exit_slippage_mode": "cost_only"})
    all_results["task2_doublecount"] = task2; save_results(all_results)

    # 2d: both — current behavior (potential double-count)
    run_eval_cached(df_2025, task2, B01_ep899, "exit_slip=both (current)",
                    {**EXIT_SLIP_BASE, "exit_slippage_mode": "both"})
    all_results["task2_doublecount"] = task2; save_results(all_results)

    print_comparison(task2, "TASK 2: EXIT SLIPPAGE DOUBLE-COUNT DIAGNOSTIC")

    # Analysis: check if both < min(price_only, cost_only)
    off = find_cached(task2, "exit_slip=OFF")
    po = find_cached(task2, "exit_slip=price_only")
    co = find_cached(task2, "exit_slip=cost_only")
    both = find_cached(task2, "exit_slip=both (current)")

    if off and po and co and both:
        delta_price = off["pnl_net"] - po["pnl_net"]
        delta_cost = off["pnl_net"] - co["pnl_net"]
        delta_both = off["pnl_net"] - both["pnl_net"]
        print(f"  Exit slippage impact:")
        print(f"    price_only  : Δ net PnL = ${delta_price:>8.2f}")
        print(f"    cost_only   : Δ net PnL = ${delta_cost:>8.2f}")
        print(f"    both        : Δ net PnL = ${delta_both:>8.2f}")
        print(f"    Expected if no double-count: both ≈ price_only ≈ cost_only")
        print(f"    Expected if double-count   : both ≈ price_only + cost_only - OFF")

        # Predicted double-count value
        predicted_both_dc = po["pnl_net"] + co["pnl_net"] - off["pnl_net"]
        print(f"\n    Predicted both (if double-count): ${predicted_both_dc:.2f}")
        print(f"    Actual both                     : ${both['pnl_net']:.2f}")
        print(f"    Match: {'YES — DOUBLE-COUNT CONFIRMED' if abs(predicted_both_dc - both['pnl_net']) < 50 else 'NO'}")

    # ──────────────────────────────────────────────────────────────────────
    # TASK 3: Canon Set
    # ──────────────────────────────────────────────────────────────────────
    log.info("=" * 80)
    log.info("TASK 3: Canon Set — B01 ep774 & ep899 × Slippage Grid")
    log.info("=" * 80)

    task3 = all_results.get("task3_canon", [])

    slip_configs = {
        "baseline": {},
        "exitSL=1.0": {
            "exit_slippage_enabled": True,
            "exit_slippage_mult_sl": 1.0,
            "exit_slippage_mult_tp": 1.0,
            "exit_slippage_mult_reverse": 1.0,
            "exit_slippage_mult_agent_close": 1.0,
            "exit_slippage_mode": "price_only",
        },
        "exitSL=1.2": {
            "exit_slippage_enabled": True,
            "exit_slippage_mult_sl": 1.2,
            "exit_slippage_mult_tp": 1.0,
            "exit_slippage_mult_reverse": 1.0,
            "exit_slippage_mult_agent_close": 1.0,
            "exit_slippage_mode": "price_only",
        },
        "exitSL=1.5": {
            "exit_slippage_enabled": True,
            "exit_slippage_mult_sl": 1.5,
            "exit_slippage_mult_tp": 1.0,
            "exit_slippage_mult_reverse": 1.0,
            "exit_slippage_mult_agent_close": 1.0,
            "exit_slippage_mode": "price_only",
        },
    }

    # Save trades for exitSL=1.5 (needed for hour filter)
    ep774_slip15_trades = None
    ep899_slip15_trades = None

    for ckpt_label, ckpt_path in [("ep774", B01_ep774), ("ep899", B01_ep899)]:
        for slip_label, slip_cfg in slip_configs.items():
            full_label = f"B01 {ckpt_label} {slip_label}"
            need_trades = (slip_label == "exitSL=1.5")
            s, trades = run_eval_cached(df_2025, task3, ckpt_path, full_label,
                                        slip_cfg, save_trades=need_trades)
            all_results["task3_canon"] = task3; save_results(all_results)
            if trades is not None:
                if ckpt_label == "ep774":
                    ep774_slip15_trades = trades
                else:
                    ep899_slip15_trades = trades

    print_comparison(task3, "TASK 3: CANON SET (exit_slippage_mode=price_only for all exit slip)")

    # ── Post-hoc hour filter on exitSL=1.5 results ──
    log.info("Applying post-hoc hour filter (excl 04,19,20) on exitSL=1.5 trades...")
    task3_filtered = all_results.get("task3_filtered", [])

    for ckpt_label, trades_df in [("ep774", ep774_slip15_trades), ("ep899", ep899_slip15_trades)]:
        if trades_df is not None and not find_cached(task3_filtered, f"B01 {ckpt_label} exitSL=1.5 excl04,19,20"):
            trades_h = add_hour_info(trades_df, df_2025)
            # All trades
            all_m = compute_filtered_metrics(trades_h, f"B01 {ckpt_label} exitSL=1.5 ALL")
            task3_filtered.append(all_m)
            # Excl 04, 19, 20
            filtered = trades_h[~trades_h["hour_utc"].isin([4, 19, 20])]
            filt_m = compute_filtered_metrics(filtered, f"B01 {ckpt_label} exitSL=1.5 excl04,19,20")
            task3_filtered.append(filt_m)
            # Top 5 EV hours
            top5 = trades_h[trades_h["hour_utc"].isin([1, 2, 7, 17, 18])]
            top5_m = compute_filtered_metrics(top5, f"B01 {ckpt_label} exitSL=1.5 top5EV")
            task3_filtered.append(top5_m)

    if task3_filtered:
        all_results["task3_filtered"] = task3_filtered; save_results(all_results)
        print_filter_table(task3_filtered, "TASK 3b: POST-HOC HOUR FILTER ON exitSL=1.5")

    # ──────────────────────────────────────────────────────────────────────
    # TASK 4: OOS Hour Validation
    # ──────────────────────────────────────────────────────────────────────
    log.info("=" * 80)
    log.info("TASK 4: OOS Hour Validation")
    log.info("=" * 80)

    # ── 4A: Run B01 ep774 on 2024 test data ──
    task4 = all_results.get("task4_oos_hours", {})
    df_2024 = load_data(DATA_2024)

    # Get 2024 trades
    trades_2024_path = Path("results/oos_2025_e050/B01_ep774_2024test_trades.parquet")
    if not trades_2024_path.exists():
        log.info("Running B01 ep774 on 2024 test data for hour validation...")
        _, trades_2024 = run_eval(df_2024, B01_ep774, "B01 ep774 on 2024 test",
                                  seed=SEED_EVAL, save_trades=True)
        if trades_2024 is not None:
            trades_2024.to_parquet(trades_2024_path)
            log.info(f"  Saved {len(trades_2024)} trades to {trades_2024_path}")
    else:
        log.info(f"  Loading cached 2024 trades from {trades_2024_path}")
        trades_2024 = pd.read_parquet(trades_2024_path)

    # Get 2025 trades (from canon set ep774 baseline, or re-run)
    trades_2025_path = Path("results/oos_2025_e050/B01_ep774_2025test_trades.parquet")
    if not trades_2025_path.exists():
        log.info("Running B01 ep774 on 2025 test data for hour validation...")
        _, trades_2025 = run_eval(df_2025, B01_ep774, "B01 ep774 on 2025 test",
                                  seed=SEED_EVAL, save_trades=True)
        if trades_2025 is not None:
            trades_2025.to_parquet(trades_2025_path)
            log.info(f"  Saved {len(trades_2025)} trades to {trades_2025_path}")
    else:
        log.info(f"  Loading cached 2025 trades from {trades_2025_path}")
        trades_2025 = pd.read_parquet(trades_2025_path)

    # Add hour info
    trades_2024_h = add_hour_info(trades_2024, df_2024)
    trades_2025_h = add_hour_info(trades_2025, df_2025)

    # ── 4A: Select hours from 2024, test on 2025 ──
    log.info("4A: Selecting best hours from 2024, testing on 2025...")
    ev_2024 = compute_hour_ev(trades_2024_h)
    ev_2025 = compute_hour_ev(trades_2025_h)

    # Print per-hour comparison
    print(f"\n{'='*80}")
    print("  PER-HOUR EV NET: 2024 vs 2025")
    print(f"{'='*80}")
    print(f"  {'Hour':>4}  {'EV 2024':>10}  {'EV 2025':>10}  {'Agree?':>8}")
    print(f"  {'-'*40}")
    for h in range(24):
        e24 = ev_2024.get(h, 0)
        e25 = ev_2025.get(h, 0)
        agree = "✓" if (e24 > 0 and e25 > 0) or (e24 <= 0 and e25 <= 0) else "✗"
        print(f"  {h:>4}  {e24:>10.4f}  {e25:>10.4f}  {agree:>8}")

    # Select top hours from 2024 (positive EV)
    top_hours_2024 = sorted([h for h, ev in ev_2024.items() if ev > 0],
                            key=lambda h: ev_2024[h], reverse=True)
    top5_2024 = top_hours_2024[:5]
    negative_2024 = [h for h, ev in ev_2024.items() if ev <= 0]

    print(f"\n  Top 5 hours from 2024: {top5_2024}")
    print(f"  Negative hours from 2024: {negative_2024}")

    # Test on 2025
    oos_results_4a = []
    oos_results_4a.append(compute_filtered_metrics(trades_2025_h, "2025 ALL"))
    top5_mask = trades_2025_h["hour_utc"].isin(top5_2024)
    oos_results_4a.append(compute_filtered_metrics(trades_2025_h[top5_mask],
                                                    f"2025 Top5 from 2024 {top5_2024}"))
    excl_neg = trades_2025_h[~trades_2025_h["hour_utc"].isin(negative_2024)]
    oos_results_4a.append(compute_filtered_metrics(excl_neg,
                                                    f"2025 Excl neg from 2024 {negative_2024}"))

    print_filter_table(oos_results_4a, "TASK 4A: 2024 HOUR SELECTION → 2025 OOS TEST")
    task4["oos_2024_to_2025"] = oos_results_4a
    task4["top5_hours_2024"] = top5_2024
    task4["negative_hours_2024"] = negative_2024
    task4["ev_per_hour_2024"] = {str(k): round(v, 4) for k, v in ev_2024.items()}
    task4["ev_per_hour_2025"] = {str(k): round(v, 4) for k, v in ev_2025.items()}

    # ── 4B: H1 2025 → H2 2025 ──
    log.info("4B: H1 2025 → H2 2025 split...")
    mid_date = pd.Timestamp("2025-07-01", tz="UTC")
    h1_mask = trades_2025_h["entry_dt"] < mid_date
    h2_mask = ~h1_mask
    trades_h1 = trades_2025_h[h1_mask]
    trades_h2 = trades_2025_h[h2_mask]

    log.info(f"  H1 trades: {len(trades_h1)}, H2 trades: {len(trades_h2)}")

    # Select from H1
    ev_h1 = compute_hour_ev(trades_h1)
    ev_h2 = compute_hour_ev(trades_h2)

    print(f"\n{'='*80}")
    print("  PER-HOUR EV NET: H1 2025 vs H2 2025")
    print(f"{'='*80}")
    print(f"  {'Hour':>4}  {'EV H1':>10}  {'EV H2':>10}  {'Agree?':>8}")
    print(f"  {'-'*40}")
    for h in range(24):
        e_h1 = ev_h1.get(h, 0)
        e_h2 = ev_h2.get(h, 0)
        agree = "✓" if (e_h1 > 0 and e_h2 > 0) or (e_h1 <= 0 and e_h2 <= 0) else "✗"
        print(f"  {h:>4}  {e_h1:>10.4f}  {e_h2:>10.4f}  {agree:>8}")

    top5_h1 = sorted([h for h, ev in ev_h1.items() if ev > 0],
                     key=lambda h: ev_h1[h], reverse=True)[:5]
    negative_h1 = [h for h, ev in ev_h1.items() if ev <= 0]

    print(f"\n  Top 5 hours from H1: {top5_h1}")
    print(f"  Negative hours from H1: {negative_h1}")

    oos_results_4b = []
    oos_results_4b.append(compute_filtered_metrics(trades_h2, "H2 ALL"))
    top5_mask_h2 = trades_h2["hour_utc"].isin(top5_h1)
    oos_results_4b.append(compute_filtered_metrics(trades_h2[top5_mask_h2],
                                                    f"H2 Top5 from H1 {top5_h1}"))
    excl_neg_h2 = trades_h2[~trades_h2["hour_utc"].isin(negative_h1)]
    oos_results_4b.append(compute_filtered_metrics(excl_neg_h2,
                                                    f"H2 Excl neg from H1 {negative_h1}"))

    print_filter_table(oos_results_4b, "TASK 4B: H1 2025 HOUR SELECTION → H2 2025 OOS TEST")
    task4["oos_h1_to_h2"] = oos_results_4b
    task4["top5_hours_h1"] = top5_h1
    task4["negative_hours_h1"] = negative_h1
    task4["ev_per_hour_h1"] = {str(k): round(v, 4) for k, v in ev_h1.items()}
    task4["ev_per_hour_h2"] = {str(k): round(v, 4) for k, v in ev_h2.items()}

    all_results["task4_oos_hours"] = task4
    save_results(all_results)

    # ──────────────────────────────────────────────────────────────────────
    # Final save
    # ──────────────────────────────────────────────────────────────────────
    save_results(all_results)
    log.info(f"✓ All results saved to {RESULTS_PATH}")
    print("\n\n✅ Canon Eval v2 complete. All results saved.")


if __name__ == "__main__":
    main()
