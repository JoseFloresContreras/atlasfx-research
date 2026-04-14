# Cost Envelope A/B Testing System

## Overview

This system enables **deterministic regression testing** of the Cost Envelope system by recording agent actions during a baseline run (envelope OFF) and replaying those exact actions with the envelope enabled (envelope ON). This eliminates non-determinism from SAC policy evaluation on CUDA.

## Key Features

- **Action Record/Replay**: Record actions to NPZ file, replay byte-for-byte
- **Deterministic Testing**: Eliminate SAC CUDA non-determinism by recording once, replaying twice
- **Byte-Level Validation**: Compare returns/equity with `pd.testing.assert_frame_equal(check_exact=True, rtol=0, atol=0)`
- **Breach Detection**: Track cost envelope breaches and action modifications
- **Multi-Scenario Support**: Test different cost configurations (c1_s2, c1_s10, breach)
- **Zero Slippage**: Forced to 0.0 for reproducibility

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  run_cost_envelope_ab_experiment.py (Orchestrator)          │
│  ┌────────────────┐          ┌────────────────┐            │
│  │ RECORD (OFF)   │          │ REPLAY (ON)    │            │
│  │ - Load agent   │  ────>   │ - Load actions │            │
│  │ - Generate     │  actions │ - Apply actions│            │
│  │   actions      │   .npz   │ - Enable       │            │
│  │ - Save NPZ     │          │   envelope     │            │
│  └────────────────┘          └────────────────┘            │
│         │                            │                      │
│         v                            v                      │
│  ┌──────────────────────────────────────────────┐          │
│  │ COMPARE: returns, equity, breaches           │          │
│  │ - pd.testing.assert_frame_equal()            │          │
│  │ - Validate steps_executed == max_steps       │          │
│  │ - Check breach_count                         │          │
│  └──────────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

## Usage

### 1. Force Action Test (No Agent Required)

Test with constant action value (e.g., 0.0 for no-trade):

```powershell
python scripts/run_cost_envelope_ab_experiment.py \
    --scenario c1_s2 \
    --max-steps 5000 \
    --episode-length 500000 \
    --seed 42 \
    --force-action 0.0
```

**Expected Results:**
- Total Return: 0.00%
- Final Equity: $30,000.00 (3 sub-accounts × $10,000)
- Breaches: 0
- OFF == ON: IDENTICAL

### 2. SAC Agent Test (EXPERIMENTAL)

**⚠️ IMPORTANT**: The SAC agent must have matching dimensions:
- `state_dim` must match environment observation space
- `action_dim` must match number of symbols

```powershell
python scripts/run_cost_envelope_ab_experiment.py \
    --scenario c1_s2 \
    --max-steps 224559 \
    --episode-length 500000 \
    --seed 42 \
    --agent sac \
    --checkpoint baselines/YOUR_CHECKPOINT.pt \
    --device cpu \
    --deterministic-eval
```

**Dimension Matching Requirements:**
- **Multi-Pair (3 symbols)**: state_dim=282, action_dim=3
- **Single-Pair (1 symbol)**: state_dim=94, action_dim=1

If dimensions mismatch, you'll get a clear error:
```
ValueError: Dimension mismatch:
  Checkpoint: state_dim=94, action_dim=3
  Environment: state_dim=282, action_dim=3
```

### 3. Breach Test

Test scenario with high costs that trigger envelope breaches:

```powershell
python scripts/run_cost_envelope_ab_experiment.py \
    --scenario c1_s10 \
    --max-steps 224559 \
    --episode-length 500000 \
    --seed 42 \
    --expect-breaches \
    --agent sac \
    --checkpoint YOUR_CHECKPOINT.pt
```

## Scenarios

| Scenario | Commission | Spread | Expected Behavior |
|----------|------------|--------|-------------------|
| `c1_s2`  | $2.50/lot  | 0.2 pips | Within envelope (0 breaches) |
| `c2_s1`  | $5.00/lot  | 0.1 pips | Within envelope (0 breaches) |
| `c1_s10` | $2.50/lot  | 1.0 pips | HIGH spread (breaches expected) |
| `breach` | $10.00/lot | 1.0 pips | HIGH costs (breaches expected) |

## Output Structure

```
reports/runtime_cost_monitoring/
└── EXP_COST_ENVELOPE_AB_<scenario>_<agent>_<timestamp>/
    ├── OFF/                              # Baseline run (envelope disabled)
    │   ├── summary.json                  # Metrics: ROI, equity, MaxDD, trades
    │   ├── returns_series_continuous.parquet
    │   ├── equity_curve_continuous.parquet
    │   └── run_config.json               # Full environment config
    ├── ON/                               # Replay run (envelope enabled)
    │   ├── summary.json
    │   ├── returns_series_continuous.parquet
    │   ├── equity_curve_continuous.parquet
    │   ├── cost_breaches.jsonl           # Breach details (if any)
    │   └── run_config.json
    ├── actions.npz                       # Recorded actions array
    └── ab_summary.json                   # Comparison summary
```

## Validation Checks

The system performs the following checks:

1. **Steps Executed**: Verify `steps_executed == max_steps_requested`
2. **Breach Count**: 
   - `c1_s2`: Expect 0 breaches
   - `c1_s10`: Expect >0 breaches (if `--expect-breaches`)
3. **Returns Equality**: `OFF.returns == ON.returns` (byte-level)
4. **Equity Equality**: `OFF.equity == ON.equity` (byte-level)

## Metrics Calculated

From `summary.json`:

```json
{
  "initial_equity": 30000.0,           // Portfolio initial balance (sum of sub-accounts)
  "final_equity": 30000.0,             // Portfolio final balance
  "total_return": 0.0,                 // (final / initial) - 1.0
  "max_drawdown": 0.0,                 // Maximum percentage drawdown
  "total_trades": 0,                   // Total executed trades
  "steps_executed": 5000,              // Actual steps run
  "max_steps_requested": 5000,         // CLI --max-steps value
  "done_reason": "max_steps",          // Why episode ended
  "equity_aggregation_method": "sum_subaccounts"  // How portfolio equity is calculated
}
```

## Known Limitations

### 1. SAC Checkpoint Compatibility

**Current Issue**: Most existing SAC checkpoints have dimension mismatches with the MultiPairPortfolioEnv.

**Workaround**: Use `--force-action` for deterministic baseline tests (no agent required).

**Future Fix**: 
- Train SAC agents specifically for MultiPairPortfolioEnv (state_dim=282, action_dim=3)
- Or: Dynamically adapt environment to match checkpoint dimensions

### 2. Observation Space Difference

The checkpoint in `baselines/atlasfx_multipair_v1_20251219/eurusd/` has:
- `state_dim=94` (single sub-env observation)
- `action_dim=3` (multi-pair actions)

But MultiPairPortfolioEnv stacks observations:
- `state_dim=94 * 3 = 282` (concatenated observations from 3 sub-envs)

**Impact**: Cannot directly load single-pair trained agents into multi-pair environment.

### 3. Recommended Testing Approach

**For Production Validation**:
1. Use `--force-action 0.0` for no-trade invariant test (baseline sanity check)
2. Train new SAC agent with MultiPairPortfolioEnv for full agent-based tests
3. Run full dataset test (224,559 steps) for comprehensive coverage

**For Quick Development Iterations**:
1. Use `--max-steps 5000` (5k steps ≈ 3.5 days)
2. Focus on specific scenarios (c1_s2 for no-breach, c1_s10 for breach)
3. Validate OFF == ON equality

## Examples

### Example 1: Quick No-Trade Test

```powershell
# Test with force_action=0.0 (5k steps)
python scripts/run_cost_envelope_ab_experiment.py \
    --scenario c1_s2 \
    --max-steps 5000 \
    --episode-length 500000 \
    --seed 42 \
    --force-action 0.0
```

**Expected Output:**
```
✅ PASS: OFF executed full 5,000 steps
✅ PASS: ON executed full 5,000 steps
✅ PASS: 0 breaches (as expected for c1_s2)
✅ PASS: Returns series EXACTLY identical
✅ PASS: Equity curves EXACTLY identical

Final Metrics:
  Initial Equity: $30,000.00
  Final Equity: $30,000.00
  Total Return: 0.00%
  Max Drawdown: 0.00%
  Total Trades: 0
```

### Example 2: Full Dataset Test

```powershell
# Full test set (224,559 steps ≈ 156 days)
python scripts/run_cost_envelope_ab_experiment.py \
    --scenario c1_s2 \
    --max-steps 224559 \
    --episode-length 500000 \
    --seed 42 \
    --force-action 0.0
```

### Example 3: Breach Test

```powershell
# High spread scenario (expect breaches)
python scripts/run_cost_envelope_ab_experiment.py \
    --scenario c1_s10 \
    --max-steps 224559 \
    --episode-length 500000 \
    --seed 42 \
    --expect-breaches \
    --force-action 0.0
```

## Testing Script

Use the provided PowerShell script for quick testing:

```powershell
# Quick test (5k steps)
.\test_ab_quick.ps1

# With SAC agent (requires compatible checkpoint)
.\test_ab_with_sac.ps1
```

## Troubleshooting

### Problem: Episode Terminates Early

```
❌ FAIL: OFF executed 4,635 < 224,559 requested
   Done reason: terminated
```

**Solution**: Add `--allow-early-stop` flag or investigate margin call.

### Problem: Dimension Mismatch

```
ValueError: Dimension mismatch:
  Checkpoint: state_dim=94, action_dim=3
  Environment: state_dim=282, action_dim=3
```

**Solution**: 
- Use `--symbols SYMBOL` to match checkpoint (single-pair)
- Or train new checkpoint with MultiPairPortfolioEnv

### Problem: Non-Identical Results

```
❌ Returns series differ:
  AssertionError: DataFrame.iloc[:, 0] are different
```

**Possible Causes:**
- Slippage not set to 0.0
- Random seed mismatch
- Cost envelope configuration changed between runs

**Solution**: Verify `slippage_bps=0.0` in ProductionTradingConfig.

## Technical Details

### Equity Calculation (Multi-Account Portfolio)

The MultiPairPortfolioEnv sums equity across sub-environments:

```python
portfolio_equity = sum(sub_env.equity for sub_env in sub_envs)
initial_equity = equity_curve[0]  # First observation (e.g., 30,000 for 3×10k)
total_return = (final_equity / initial_equity) - 1.0
```

### Action Recording

Actions are saved as NumPy array:

```python
# Record
actions_list.append(action.copy())  # Shape: (action_dim,)
np.savez(actions_path, actions=np.array(actions_list), timestamps=np.array(step_timestamps))

# Replay
npz = np.load(actions_path)
actions = npz["actions"]  # Shape: (num_steps, action_dim)
for t in range(len(actions)):
    action = actions[t]
    obs, reward, terminated, truncated, info = env.step(action)
```

### Deterministic Replay Requirements

1. **Fixed Seed**: `np.random.seed(seed)` and `env.reset(seed=seed)`
2. **Validation Mode**: `validation_mode=True` forces `start_step=0` (no random start)
3. **Zero Slippage**: `slippage_bps=0.0` for reproducibility
4. **Deterministic Policy**: `--deterministic-eval` for SAC (uses mean, not sample)

## Future Improvements

1. **Auto-Adapt Environment**: Detect checkpoint dimensions and create matching environment
2. **Multi-Pair SAC Training**: Train agents specifically for MultiPairPortfolioEnv
3. **Breach Visualization**: Generate plots showing breach locations and action modifications
4. **Performance Profiling**: Add timing metrics for record/replay operations
5. **Parallel Testing**: Run multiple scenarios in parallel

## References

- **Script**: `scripts/run_cost_envelope_ab_experiment.py`
- **Core Module**: `scripts/record_replay_actions_multipair.py`
- **Test Suite**: `tests/test_cost_envelope_record_replay.py`
- **Environment**: `src/atlasfx/environments/trading_env_multipair.py`
- **Cost Envelope**: `src/atlasfx/runtime_cost_monitoring/envelope.py`
