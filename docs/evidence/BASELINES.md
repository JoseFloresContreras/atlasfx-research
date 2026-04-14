# AtlasFX - SAC Baselines

Este documento describe los baselines oficiales de SAC (Soft Actor-Critic) para el proyecto AtlasFX. Todos los baselines se entrenan y evalúan sobre el mismo test set estándar de **449 episodios** con `episode_length=500` barras cada uno.

---

## Baseline v1 — SAC sin TP/SL enforcement (ep799, 2025-12-11)

### Información General
- **Carpeta baseline**: `baselines/sac_baseline_ep799_20251211/`
- **Checkpoint principal**: `checkpoints/checkpoint_ep00799.pt`
- **Fecha de entrenamiento**: 2025-12-11
- **Total episodios entrenados**: 1000
- **Mejor episodio (validation Sharpe)**: 799

### Configuración del Entorno

**Risk Management**:
- **TP/SL ratio**: Sin enforcement (TP libre, agente decide distancia sin restricciones)
- Stop Loss: 2.0× ATR
- Position sizing: USD-centric risk management
- ATR floor: 0.5 pips (protección contra posiciones explosivas)

**Costos de Trading**:
- Comisión: **$2.50 USD/lot/side** ($5.00 USD round-trip por lot)
- Spread: **0.2 pips**
- Slippage: **0 bps**

**Reward Function**:
- Tipo: `pnl_normalized`
- Fórmula base: `reward = (pnl_usd - costs_usd) / balance`
- Penalizaciones adicionales:
  - Clamp penalty (si posición excede máximo): λ = 0.0
  - Risk penalty (capital at risk): λ = 0.0  
  - Action penalty (por trade): λ = 0.0

**Estado y Acción**:
- Dimensión de estado: 94 features
- Dimensión de acción: 3 (posición, SL multiple, TP multiple)
- VAE features: No utilizado

### Métricas de Test Set (449 episodios)

| Métrica | Valor | Percentil 25 | Mediana | Percentil 75 |
|---------|-------|--------------|---------|--------------|
| **Mean Return** | **+16.65%** | - | +8.78% | - |
| **Mean Sharpe** | **+0.312** | - | +0.518 | - |
| **Win Rate** | **71.2%** | - | - | - |
| **Profit Factor** | **5.71** | - | - | - |
| **Max Drawdown** | **4.42%** (avg) | - | - | - |
| **Episodios Positivos** | **300/449 (66.8%)** | - | - | - |

**Rango de Returns**:
- Máximo: +114.26%
- Mínimo: -11.35%

### Rol
**Baseline histórico v1** - Fue la referencia inicial del proyecto pero ha sido superado por v2. Se mantiene para comparaciones históricas y análisis de mejoras.

### Problema Identificado
Análisis posterior reveló que este baseline sufre de **poor TP/SL ratio** (0.53×):
- Losers son 8.7× peores que winners en magnitud absoluta
- Winners: mean +0.0071% (+27.11% en episodios ganadores)
- Losers: mean -0.0616% (-4.40% en episodios perdedores)
- Esto limita el potencial de profit a pesar de alta win rate

---

## Baseline v2 — SAC con TP/SL ratio enforcement (ep699, 2025-12-16)

### Información General
- **Carpeta baseline**: `baselines/sac_baseline_ep699_tp_sl_20251216/`
- **Checkpoint principal**: `checkpoints/checkpoint_ep00699.pt`
- **Alias**: `checkpoints/best_checkpoint.pt` → ep00699
- **Fecha de entrenamiento**: 2025-12-14/15
- **Total episodios entrenados**: 1000
- **Mejor episodio (validation Sharpe)**: 699

### Configuración del Entorno

**Risk Management** (⭐ **CLAVE: TP/SL Ratio Enforcement**):
- **TP/SL ratio enforcement**: 
  - `min_tp_sl_ratio = 1.5` (TP mínimo: 1.5× SL en pips)
  - `max_tp_sl_ratio = 3.0` (TP máximo: 3.0× SL en pips)
  - Implementación: Clip de TP calculado entre `[sl_dist_pips × 1.5, sl_dist_pips × 3.0]`
- Stop Loss: 2.0× ATR (mismo que v1)
- Position sizing: USD-centric risk management (mismo que v1)
- ATR floor: 0.5 pips (mismo que v1)

**Costos de Trading** (idénticos a v1):
- Comisión: **$2.50 USD/lot/side** ($5.00 USD round-trip)
- Spread: **0.2 pips**
- Slippage: **0 bps**

**Reward Function** (idéntica a v1):
- Tipo: `pnl_normalized`
- Fórmula: `reward = (pnl_usd - costs_usd) / balance`
- Penalizaciones: Todas en 0 (clamp, risk, action penalties)

**Estado y Acción** (idénticos a v1):
- Dimensión de estado: 94 features
- Dimensión de acción: 3
- VAE features: No utilizado

### Métricas de Test Set (449 episodios)

| Métrica | Valor | vs Baseline v1 | Mejora |
|---------|-------|----------------|--------|
| **Mean Return** | **+38.59%** | +16.65% | **+131.8%** |
| **Median Return** | **+34.39%** | +8.78% | **+291.8%** |
| **Mean Sharpe** | **+1.727** | +0.312 | **+452%** |
| **Median Sharpe** | **+1.811** | +0.518 | **+249%** |
| **Win Rate** | **60.4%** | 71.2% | -10.8 pp |
| **Profit Factor** | **4.40** | 5.71 | -23.0% |
| **Max Drawdown (avg)** | **4.64%** | 4.42% | +4.9% |
| **Episodios Positivos** | **416/449 (92.7%)** | 300/449 (66.8%) | **+25.9 pp** |

### Winner/Loser Analysis

**Baseline v2 (TP/SL enforcement)**:
- Winners: mean +42.08%, median +37.75%
- Losers: mean -5.37%, median -4.49%
- **Asymmetry**: 0.13× (losers controlados, winners 7.7× más grandes)

**Baseline v1 (sin enforcement)**:
- Winners: mean +27.11%, median +21.69%
- Losers: mean -4.40%, median -4.59%
- **Asymmetry**: 0.16× (losers similares, pero winners 36% más pequeños)

### Trade-offs vs v1
- ✅ **Mean return +131.8% mejor** (+38.59% vs +16.65%)
- ✅ **Sharpe +452% mejor** (1.727 vs 0.312)
- ✅ **Consistencia excepcional**: 92.7% vs 66.8% episodios positivos
- ✅ **Winners mucho más grandes**: +42.08% vs +27.11%
- ⚠️ **Win rate ligeramente menor**: 60.4% vs 71.2% (trade quality > quantity)
- ⚠️ **Profit factor menor**: 4.40 vs 5.71 (pero compensado por winners grandes)
- ⚠️ **Menos trades**: 239.6 vs 351.8 por episodio (más selectivo)

### Rol
✅ **BASELINE DE REFERENCIA ACTUAL** - Este es el modelo que todos los experimentos nuevos deben intentar superar.

### ¿Por qué funciona mejor?
El enforcement de TP/SL ratio (1.5×-3.0×) **resuelve el problema fundamental** del baseline v1:
1. Previene que el agente cree TPs demasiado pequeños relativos al SL
2. Asegura risk/reward favorable en cada trade (mínimo 1.5:1)
3. Evita TPs excesivamente grandes que raramente se alcanzan (cap 3:1)
4. Resultado: Winners crecen más (+42% vs +27%), losers controlados (-5.4% vs -4.4%)

---

## Baseline v2 (Seed 2025) — SAC con TP/SL enforcement (ep699, seed 2025)

### Información General
- **Carpeta baseline**: `baselines/sac_baseline_ep699_tp_sl_seed2025_20251217/`
- **Checkpoint principal**: `checkpoints/best_checkpoint.pt`
- **Fecha de entrenamiento**: 2025-12-17
- **Total episodios entrenados**: 1000
- **Mejor episodio (validation Sharpe)**: 699
- **Random Seed**: 2025
- **Status**: 🔬 **EXPERIMENTAL - NOT OFFICIAL BASELINE** (archived for reference)

### Configuración del Entorno

**Idéntica a Baseline v2 (seed 42)**, excepto:
- **Random Seed**: 2025 (vs 42 en v2 oficial)

### Métricas de Test Set (449 episodios)

| Métrica | Seed 2025 | Baseline v2 (seed 42) | Delta |
|---------|-----------|----------------------|-------|
| **Mean Return** | **+40.13%** | +38.59% | **+1.54pp** |
| **Median Return** | **+36.81%** | +34.39% | **+2.42pp** |
| **Mean Sharpe** | **+2.030** | +1.727 | **+0.303** |
| **Median Sharpe** | **+2.072** | +1.811 | **+0.261** |
| **Win Rate** | **61.9%** | 60.4% | **+1.5pp** |
| **Profit Factor** | **6.30** | 4.40 | **+1.90** |
| **Max Drawdown (avg)** | **3.22%** | 4.64% | **-1.42pp** ✅ |
| **Episodios Positivos** | **428/449 (95.3%)** | 416/449 (92.7%) | **+2.6pp** |
| **Trades/Episode** | 248 | 240 | +8 |

### Contexto: Multi-Seed Experiment

**Experiment Date**: December 17, 2025  
**Seeds Tested**: 5 (42, 1337, 2024, 2025, 7777)  
**Training**: 1000 episodes each, identical config except seed

**Seed 2025 Ranking**:
- 🥇 **1st place** by mean return (+40.13%)
- 🥇 **1st place** by Sharpe ratio (2.030)
- 🥇 **1st place** by positive episodes (95.3%)
- 🥇 **1st place** by profit factor (6.30)

**Reproducibility Assessment**:
- Multi-seed aggregate: 33.17% ± 4.62pp
- Coefficient of variation: 13.9%
- Seed 2025 at **+1.43σ above mean** (lucky seed)

### Rol

🔬 **EXPERIMENTAL - ARCHIVED FOR REFERENCE**

This baseline is **NOT replacing official Baseline v2** because:
1. **Selection bias**: Chosen from 5 seeds post-hoc (cherry-picking)
2. **Overfitting risk**: Best seed may be lucky, not representative
3. **Reproducibility principle**: Official baseline should use fixed seed (42 convention)
4. **Small improvement**: +1.54pp not significant enough to justify change

**Use Cases**:
- Reference for "best case" performance with TP/SL enforcement
- Validation that baseline v2 approach is reproducible across seeds
- Future multi-seed ensemble experiments

**Recommendation**: Continue using **Baseline v2 (seed 42)** as official reference

---

## Cómo Usar Estos Baselines

### Para Nuevos Experimentos

**Cualquier experimento serio debe**:

1. **Entrenar un nuevo modelo** (SAC, PPO, u otro algoritmo)

2. **Evaluar en el MISMO test set estándar**:
   ```bash
   python scripts/eval_sac_full_testset.py \
     --checkpoint <path_to_new_checkpoint> \
     --output-dir reports/<experiment_name>_test \
     --episode-length 500
   ```
   Esto generará 449 episodios comparables con los baselines.

3. **Comparar métricas mínimas requeridas**:
   - ✅ Mean return (%)
   - ✅ Median return (%)
   - ✅ Mean Sharpe ratio
   - ✅ Win rate (%)
   - ✅ Profit factor
   - ✅ Max drawdown (%)
   - ✅ % de episodios positivos

4. **Especificar baseline de comparación**:
   - **Recomendado**: Comparar contra **Baseline v2** (ep699 TP/SL enforcement)
   - Alternativa: Comparar contra v1 solo si se busca medir mejora histórica
   - **Incluir ambos** en análisis completo

### Para Cargar un Baseline

**Baseline v1**:
```python
from atlasfx.agents.sac import SACAgent

checkpoint_path = "baselines/sac_baseline_ep799_20251211/checkpoints/checkpoint_ep00799.pt"
agent = SACAgent.load(checkpoint_path)
```

**Baseline v2 (recomendado)**:
```python
from atlasfx.agents.sac import SACAgent

checkpoint_path = "baselines/sac_baseline_ep699_tp_sl_20251216/checkpoints/best_checkpoint.pt"
agent = SACAgent.load(checkpoint_path)
```

### Archivos de Referencia

**Baseline v1**:
- Test metrics: `baselines/sac_baseline_ep799_20251211/test_results/full_testset/episode_metrics.csv`
- Training history: `baselines/sac_baseline_ep799_20251211/training_history/training_progress.csv`
- Metadata: `baselines/sac_baseline_ep799_20251211/BASELINE_INFO.json`

**Baseline v2**:
- Test metrics: `baselines/sac_baseline_ep699_tp_sl_20251216/test_results/episode_metrics.csv`
- Training history: `baselines/sac_baseline_ep699_tp_sl_20251216/training_progress.csv`
- Metadata: `baselines/sac_baseline_ep699_tp_sl_20251216/BASELINE_INFO.json`
- Analysis: `baselines/sac_baseline_ep699_tp_sl_20251216/analysis/comparison_vs_ep799.md`
- README: `baselines/sac_baseline_ep699_tp_sl_20251216/README.md`

---

## Criterios para un Nuevo Baseline

Para que un modelo se convierta en el **Baseline v3** (o superior), debe:

1. **Superar significativamente a v2** en métricas clave:
   - Mean return > +38.59%
   - Mean Sharpe > +1.727
   - Mantener o mejorar consistencia (≥92.7% episodios positivos)

2. **Mantener o mejorar control de riesgo**:
   - Max drawdown ≤ 4.64% (o justificar por qué es aceptable mayor DD)
   - Win rate razonable (≥55%)

3. **Ser reproducible**:
   - Código de configuración documentado
   - Checkpoints preservados
   - Test set evaluation completo

4. **Incluir análisis comparativo**:
   - Comparación detallada vs v2 (y opcionalmente vs v1)
   - Explicación de qué cambios generaron la mejora
   - Trade-offs identificados y justificados

5. **Seguir estructura estándar**:
   - Usar `baselines/sac_baseline_<description>_<date>/`
   - Incluir BASELINE_INFO.json, README.md
   - Copiar checkpoints, test results, analysis

---

## Resumen Ejecutivo

| Baseline | Mean Return | Sharpe | Win Rate | Episodios Positivos | Status |
|----------|-------------|--------|----------|---------------------|--------|
| **v1 (ep799)** | +16.65% | +0.312 | 71.2% | 66.8% | Histórico |
| **v2 (ep699, seed 42)** | **+38.59%** | **+1.727** | 60.4% | **92.7%** | ✅ **ACTUAL** |
| v2 (ep699, seed 2025) | +40.13% | +2.030 | 61.9% | 95.3% | 🔬 Experimental |

**Recomendación**: Usar **Baseline v2 (seed 42)** como referencia oficial para todos los experimentos nuevos.

---

## ❌ Rejected Baselines

### E018 — Portfolio 1-Agent 3-Pairs (Rejected Dec 28, 2025)

**Approach**: Single SAC agent managing EURUSD + GBPUSD + USDJPY simultaneously via MultiPairPortfolioEnv

**Test Results (449 episodes, best_checkpoint @ Ep 599)**:
- Mean Return: **-17.91%** ❌
- Mean Sharpe: **-2.04** ❌
- Max Drawdown: **28.30%** ❌
- Positive Episodes: **0.0% (0/449)** ❌

**Why Rejected**:
1. **Catastrophic generalization failure**: Training +32.20% (Ep 599) → Test -17.91% (avg) = -50pp gap
2. **Zero wins**: 0 positive episodes in 449 tests = statistically impossible for trained model
3. **Severe overfitting**: Agent memorized training data, learned no transferable patterns
4. **55.87pp worse than E016**: Multi-Pair V1 (+37.96%) vastly superior
5. **Not production-viable**: Negative Sharpe means worse than holding cash

**Comparison vs E016 (Multi-Pair V1)**:
- E016: +37.96% return, +4.84 Sharpe, 100% positive episodes ✅
- E018: -17.91% return, -2.04 Sharpe, 0% positive episodes ❌
- Gap: **-55.87pp return**, **-6.88 Sharpe**

**Decision**: Abandoned. Multi-agent approach (E016) proven superior for multi-symbol portfolios.

**Full Analysis**: See `docs/E018_PORTFOLIO_CONCLUSION.md` and `docs/E018_BEST_EXECUTIVE_SUMMARY.md`

---

**Última actualización**: 2025-12-28  
**Baseline actual**: v2 (ep699 TP/SL enforcement, seed 42)  
**Target a superar**: +38.59% mean return, +1.727 Sharpe, 92.7% consistencia  
**Best reference seed**: seed 2025 (+40.13% return, +2.030 Sharpe) - experimental only
