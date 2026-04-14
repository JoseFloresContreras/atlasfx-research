# AtlasFX — Informe Técnico para Revisión de Experto

**Proyecto:** AtlasFX MVP — Agente de RL (SAC) para trading algorítmico en Forex  
**Fecha:** 24 de febrero de 2026  
**Autores:** Equipo AtlasFX  
**Propósito:** Solicitar evaluación experta sobre el estado del proyecto, viabilidad de la estrategia, y recomendaciones para los próximos pasos.

---

## Tabla de Contenidos

1. [Resumen Ejecutivo](#1-resumen-ejecutivo)
2. [Objetivo del Proyecto](#2-objetivo-del-proyecto)
3. [Arquitectura Técnica](#3-arquitectura-técnica)
4. [Datos y Features](#4-datos-y-features)
5. [Historial de Experimentos (E046–E051)](#5-historial-de-experimentos-e046e051)
6. [Resultados del Campeón (B01 V2b s42)](#6-resultados-del-campeón-b01-v2b-s42)
7. [Stress Test de Costos (E051)](#7-stress-test-de-costos-e051)
8. [Análisis por Subperiodo (2025)](#8-análisis-por-subperiodo-2025)
9. [Diagnóstico del Problema Central](#9-diagnóstico-del-problema-central)
10. [Plan Propuesto de Próximos Pasos](#10-plan-propuesto-de-próximos-pasos)
11. [Preguntas para el Experto](#11-preguntas-para-el-experto)

---

## 1. Resumen Ejecutivo

Hemos desarrollado un agente de Reinforcement Learning (SAC — Soft Actor-Critic) que opera USDJPY en barras de 1 minuto. Después de corregir un bug crítico de look-ahead bias y de implementar mecanismos anti-overtrading, logramos un agente que:

- **En 2024 OOS (May-Dic):** Sharpe +12.20, Return +95.6%, MDD 2.2%
- **En 2025 OOS (año completo):** Sharpe +4.62, Return +41.6%, MDD 5.3%

Sin embargo, el stress test de costos (E051) reveló que:

- **El margen de costo es extremadamente estrecho:** con el doble de slippage (0.20 pips) el Sharpe cae a +1.46. Con triple slippage o spreads retail (0.5 pips), la estrategia muere.
- **El rendimiento está concentrado en H1 2025** (Sharpe 8.6, +37% return), mientras que H2 2025 apenas es positivo (Sharpe 0.9, +3.4%).
- **El agente tradea ~160 veces/día**, re-entrando el 97% de las veces en la misma barra en que cierra. El edge neto por trade es solo $0.41 sobre $0.31 de costos.

**El problema central es que el agente aprendió a ser un scalper always-in con un edge bruto real pero extremadamente sensible a la fricción de ejecución.**

---

## 2. Objetivo del Proyecto

Construir un sistema de trading algorítmico para forex usando Deep RL, capaz de:
- Operar de manera autónoma en pares FX mayores
- Generar retornos ajustados al riesgo positivos fuera de muestra (OOS)
- Ser desplegable en producción con costos realistas

**Hardware:** Windows 11, Intel i9-14900HX (32 cores), 64 GB RAM, RTX 4090 Laptop (16 GB VRAM)  
**Stack:** Python 3.12, PyTorch, entorno custom `trading_env3.py`

---

## 3. Arquitectura Técnica

### 3.1 Agente SAC (Soft Actor-Critic)

**Actor (Política Estocástica):**
- MLP backbone: `[obs_dim → 256 → ReLU → 256 → ReLU]`
- Dos cabezas de salida: `mean_layer` (μ) y `log_std_layer` (log σ, clampeado a [-20, 2])
- Muestreo vía reparameterization trick: `action_pre_tanh ~ N(μ, σ²)`, luego `action = tanh(action_pre_tanh)` → rango [-1, 1]
- Corrección de log-prob por squashing: `log_prob -= log(1 - action² + ε)`

**Critic (Twin Q-Networks):**
- 2 redes independientes: `[obs_dim + action_dim → 256 → ReLU → 256 → ReLU → 1]`
- 2 target networks con soft update (τ = 0.005)

**Automatic entropy tuning:**
- Target entropy = `-action_dim`
- α aprendible vía gradiente descendente

**Parámetros totales:** ~522K por modelo

### 3.2 Espacio de Acciones

Cada paso (1 minuto), el agente emite 3 acciones continuas por par:

| Componente | Rango | Significado |
|---|---|---|
| `target_pos_frac` | [-1, 1] | Dirección × convicción. +1 = máximo long, -1 = máximo short, 0 = sin posición |
| `sl_dist_ATR` | [0.1, 5.0] | Distancia de stop-loss como múltiplo del ATR |
| `tp_dist_ATR` | [0.1, 10.0] | Distancia de take-profit como múltiplo del ATR |

### 3.3 Mecanismos Anti-Overtrading

Implementados después de E046, donde el agente sin restricciones generaba >200K trades destructivos:

| Guard | Parámetro | Efecto |
|---|---|---|
| **Dead Zone** | `position_dead_zone` = 0.10 | Si `|target_pos_frac| < 0.10`, la acción se ignora (no tradea) |
| **Min Hold Period** | `min_hold_period` = 20 bars | Si la posición existe y tiene < 20 barras, se bloquean cambios |
| **Action Penalty** | `action_penalty` = 0.0002 | Penalización en reward por cada trade ejecutado |
| **Loss Penalty** | `loss_penalty_factor` = 1.5 | Las pérdidas se penalizan 1.5× en el reward |

### 3.4 Flujo de Ejecución (_execute_action)

1. Dead zone filter → 2. Min hold check → 3. Risk budget: `balance × 2% × conviction` → 4. SL/TP en pips (basado en ATR, con floors) → 5. Position sizing: `max_lots = risk / (SL_pips × pip_value)` → 6. Caps: max leverage (20×), max notional, max concentration → 7. Asignar SL/TP prices → 8. Ejecutar al precio open[T]

### 3.5 Modelo de Costos

```
commission = lots × 2.5 USD/lot/lado (5 USD round-turn por lote)
spread     = lots × spread_pips × 10 USD/pip/lot
slippage   = lots × |N(mean, std)| × 10 USD/pip/lot  (half-normal, siempre adverso)
```

**Costos baseline (producción ECN):**

| Componente | Valor |
|---|---|
| Comisión | 2.5 USD/lot/lado |
| Spread | 0.2 pips |
| Slippage mean | 0.10 pips |
| Slippage std | 0.05 pips |
| Pip value | 10 USD/pip/lot |
| Lot size | 100,000 unidades |

### 3.6 Función de Reward

```
reward = (pnl_neto_usd / balance)
       - 0.01 × clamped_flag
       - 0.05 × (capital_at_risk / balance)  
       - 0.0002 × trades_executed

Si reward < 0:  reward *= 1.5  (loss penalty asymétrica)
```

El PnL neto ya incluye la deducción de costos — no hay doble penalización.

### 3.7 Entrenamiento

| Parámetro | Valor |
|---|---|
| Episodios | 2,000 |
| Longitud episodio | 500 barras (~8.3 horas de mercado) |
| Batch size | 256 |
| Replay buffer | 1,000,000 transiciones |
| Warmup steps | 10,000 (acciones random) |
| Update frequency | Cada step después de warmup |
| Eval interval | Cada 25 episodios |
| Balance inicial | $10,000 USD |

---

## 4. Datos y Features

### 4.1 Splits de Datos

| Dataset | Filas | Días | Periodo |
|---|---|---|---|
| **Train** | 1,047,939 | ~874 | 2021-01-03 → 2023-10-18 |
| **Validation** | 224,558 | ~190 | 2023-10-18 → 2024-05-27 |
| **Test 2024 (OOS)** | 224,559 | 156 | 2024-05-27 → 2024-12-31 |
| **Test 2025 (OOS)** | 373,487 | 260 | 2025-01-01 → 2025-12-25 |

- Gap train→test: 221 días (zero overlap)
- 7 pares disponibles: EURUSD, USDJPY, USDCHF, GBPUSD, AUDUSD, NZDUSD, USDCAD
- Resolución: 1 minuto, OHLCV

### 4.2 Features (94 dimensiones por par)

**86 features de mercado:**

| Grupo | Cantidad | Detalle |
|---|---|---|
| Sesiones de trading | 4 | Sydney, Tokyo, London, NY (flags binarios) |
| Tiempo cíclico | 4 | minute_of_day (sin/cos), day_of_week (sin/cos) |
| Indicadores técnicos por par (×7 pares) | 56 | ATR(14), RSI(14), volatility_ratio(14/60), VWAP deviation, OFI rolling mean(14), bipower_var_continuous(30), bipower_var_jump(30), return_skewness(60), return_kurtosis(60) — 8 features × 7 pares |
| Currency Strength Index | 8 | CSI para AUD, CAD, CHF, EUR, GBP, JPY, NZD, USD |
| Correlaciones cross-pair | 7 | Rolling 30-bar correlations entre 7 combinaciones de pares |
| OFI rolling mean | 7 | Order Flow Imbalance por par |

**8 features de estado del agente:**
1. `pos_frac` — posición notional / balance
2. `sl_norm_atr` — |entry - SL| / ATR
3. `tp_norm_atr` — |TP - entry| / ATR
4. `executed_fraction` — última acción ejecutada [pos]
5. `sl_ATR` — última acción ejecutada [SL]
6. `tp_ATR` — última acción ejecutada [TP]
7. `clamped_flag` — 1 si la acción fue clampeada
8. `position_open_flag` — 1 si hay posición abierta

**Garantía causal:** Todos los features de mercado usan datos de `T-1`. El agente observa `features[T-1]` y ejecuta al `open[T]`. Un bug previo (pre-E046) exponía datos de T, creando look-ahead bias.

### 4.3 Normalización

- Z-score: `(x - μ_train) / σ_train` calculado SOLO con datos de training
- Excluidos de normalización: RSI, ATR, correlaciones, session flags, volatility_ratio, timestamps
- No se re-estima en test/production

---

## 5. Historial de Experimentos (E046–E051)

### Contexto: El Bug de Look-Ahead Bias

Entre E045 y E046 descubrimos que `_get_observation()` exponía `close[T]`, `high[T]`, `low[T]` en el momento de decidir la acción para la barra T. Dado que el agente ejecuta al `open[T]`, esto le daba conocimiento casi perfecto del movimiento futuro de 1 barra. El Sharpe aparente de E045 (14-26) era 100% atribuible a este leak.

**Fix:** Laguear todos los features a T-1 (commit `43fd728`). **Todos los resultados pre-E046 quedaron invalidados.**

### E046 — Reentrenamiento Limpio Sin Guards (❌ CATASTRÓFICO)

**Objetivo:** Determinar si existe algún edge genuino post-fix, sin ninguna restricción.

| Resultado | Valor |
|---|---|
| Pares probados | EURUSD, USDJPY, USDCHF (3 de 7) |
| Mejor Sharpe | -31.93 |
| Peor Sharpe | -87.16 |
| Trades por modelo | 200K-250K en 156 días |
| Return | -92% a -98% |

**Diagnóstico:** Sin el information leak, el agente de Gaussian noise de SAC interpreta cada oscilación como señal y tradea cada barra. El 82-90% de las pérdidas eran por costos de transacción. El edge bruto existía pero era aniquilado por la frecuencia.

**Acción:** Diseño de 3 guards anti-overtrading: dead_zone, min_hold_period, action_penalty.

---

### E047 — Primer Test de Guards (⬜ ABORTADO)

Prueba inicial de guards con parámetros conservadores. Abortado antes de completar — reemplazado por el diseño más enfocado de E048.

---

### E048 — 3 Configs × 2 Pares (⚡ BREAKTHROUGH)

**Objetivo:** Encontrar el balance óptimo entre guards y señal. 3 variantes de config en USDJPY y USDCHF.

| Config | gamma | lr | dead_zone | min_hold | action_penalty |
|---|---|---|---|---|---|
| V1 "Balanced" | 0.99 | 3e-4 | 0.05 | 5 | 0.0001 |
| **V2 "Patient"** | **0.995** | **1e-4** | **0.08** | **10** | **0.0002** |
| V3 "Compact" | 0.99 | 3e-4 | 0.05 | 5 | 0.0001 |

**Resultados OOS 2024 (156 días):**

| Config | Par | Sharpe | Return | MDD | Trades |
|---|---|---|---|---|---|
| V1 | USDJPY | -3.22 | -15.0% | 15.8% | 47,831 |
| **V2** | **USDJPY** | **+4.52** | **+31.2%** | **8.1%** | **33,005** |
| V1 | USDCHF | -28.90 | -52.2% | 52.3% | 44,957 |

**Hito:** V2 "Patient" = primer resultado positivo post-fix de bias. La clave fue: lr más baja (1e-4), gamma más alto (0.995), guards más agresivos. Redujo trades de ~48K a ~33K, convirtiendo un Sharpe negativo en +4.52.

---

### E049 — Cross-Seed + V2a/V3 Expansion (✅ NUEVO MEJOR: Sharpe 9.69)

**Objetivo:** Validar robustez de V2 con múltiples seeds y probar V2a (aún más paciente).

| Config | gamma | lr | dead_zone | min_hold | action_penalty |
|---|---|---|---|---|---|
| V2 "Patient" | 0.995 | 1e-4 | 0.08 | 10 | 0.0002 |
| **V2a "Ultra-Patient"** | **0.997** | **1e-4** | **0.10** | **15** | **0.0002** |
| V3 "Compact" (128×128) | 0.99 | 3e-4 | 0.05 | 5 | 0.0001 |

**Resultados OOS 2024:**

| Config | Par | Seed | Sharpe | Return | MDD | Trades |
|---|---|---|---|---|---|---|
| V2 | USDJPY | 42 | +4.99 | +31.2% | 8.1% | 33,005 |
| V2 | USDJPY | 137 | +6.01 | +37.0% | 5.5% | 32,915 |
| V2 | USDJPY | 2024 | +7.31 | +47.9% | 4.8% | 32,993 |
| **V2a** | **USDJPY** | **42** | **+9.69** | **+68.3%** | **4.3%** | **27,686** |
| V2 | EURUSD | 42 | +0.87 | +4.7% | 8.1% | 28,849 |
| V2 | GBPUSD | 42 | -8.00 | -20.8% | 20.9% | 31,971 |
| V2 | USDCAD | 42 | -16.81 | -21.0% | 21.2% | 31,541 |
| V3 | USDJPY | 42 | -5.14 | -23.0% | — | 46,991 |

**Hallazgos clave:**
- V2 USDJPY robustamente positivo en 3 seeds: Sharpe 5.0-7.3 (CV 19%)
- V2a reduce trades a 27.7K y mejora Sharpe a 9.69 — más paciencia = mejor
- Todos los pares tienen PF > 1.0 (edge bruto existe), pero el discriminador es el % de costos sobre PnL bruto: USDJPY ~42%, EURUSD ~48%, GBP/CAD ~64%
- V3 Compact (128×128) no funciona — la capacidad de red importa

---

### E050 — Validación Full Matrix + 2025 OOS (✅ CHAMPION: V2b Sharpe 12.20/4.62)

**Objetivo:** (1) V2a cross-seed USDJPY, (2) V2b horizon extension (gamma=0.999, min_hold=20), (3) Expansión a USDCHF/EURUSD, (4) Validación OOS 2025 completa.

**Duración:** 12.6 horas de entrenamiento + 47 min de evaluación.

| Config | gamma | lr | dead_zone | min_hold | Diferencia clave |
|---|---|---|---|---|---|
| V2a "Ultra-Patient" | 0.997 | 1e-4 | 0.10 | 15 | — |
| **V2b "Extended Horizon"** | **0.999** | **1e-4** | **0.10** | **20** | gamma más alto, min_hold más largo |

**Matriz de Experimentos (8 planeados, 6 ejecutados, 4 convergieron):**

| ID | Par | Config | Seed | Estado |
|---|---|---|---|---|
| A01 | USDJPY | V2a | 137 | ✅ Convergió |
| A02 | USDJPY | V2a | 2024 | ✅ Convergió |
| **B01** | **USDJPY** | **V2b** | **42** | **✅ Convergió — CHAMPION** |
| C01 | USDCHF | V2a | 42 | ❌ Falló |
| C02 | EURUSD | V2a | 42 | ❌ Falló |
| D03 | USDJPY | V2b | 137 | ✅ Convergió |

**Resultados OOS 2024 (May-Dic 2024, 156 días):**

| ID | Config | Sharpe | Return | MDD | PF | Trades | EV/trade |
|---|---|---|---|---|---|---|---|
| A01 | V2a s137 | +9.82 | +69.8% | 4.2% | 1.45 | 27,652 | $0.56 |
| A02 | V2a s2024 | +6.34 | +41.1% | 5.1% | 1.35 | 27,776 | $0.41 |
| **B01** | **V2b s42** | **+12.20** | **+95.6%** | **2.2%** | **1.50** | **25,018** | **$0.72** |
| D03 | V2b s137 | +5.32 | +33.5% | 9.2% | 1.31 | 25,443 | $0.38 |
| C01 | USDCHF | -12.12 | -26.9% | 27.5% | 1.25 | 24,747 | — |
| C02 | EURUSD | -0.45 | -2.8% | 13.3% | 1.39 | 24,423 | — |

**Resultados OOS 2025 (año completo, 260 días) — LA PRUEBA REAL:**

| ID | Config | Sh. 2024 | **Sh. 2025** | **Ret. 2025** | **MDD 2025** | Trades | WR% | PF | Decay |
|---|---|---|---|---|---|---|---|---|---|
| A01 | V2a s137 | +9.82 | **+3.61** | **+29.3%** | **8.2%** | 45,711 | 48.5% | 1.32 | -63% |
| A02 | V2a s2024 | +6.34 | -3.25 | -22.0% | 25.6% | 46,585 | 45.8% | 1.16 | ☠️ |
| **B01** | **V2b s42** | **+12.20** | **+4.62** | **+41.6%** | **5.3%** | **41,748** | **48.2%** | **1.32** | **-62%** |
| D03 | V2b s137 | +5.32 | -1.24 | -9.5% | 16.0% | 42,368 | 45.6% | 1.19 | ☠️ |
| C01 | USDCHF | -12.12 | -9.48 | -39.7% | 39.7% | 43,412 | — | — | — |
| C02 | EURUSD | -0.45 | -5.96 | -37.5% | 38.9% | 45,060 | — | — | — |

**Observaciones E050:**
- Solo 2 de 6 modelos sobreviven en 2025 OOS: B01 y A01
- V2b (gamma=0.999, min_hold=20) más robusto que V2a en ambos períodos
- Seed 42 consistentemente el mejor — seed 2024 y 137 muestran fragilidad
- USDCHF y EURUSD fallan completamente en todas las configuraciones post-fix
- Decay de Sharpe 2024→2025 ~62%: el Sharpe "real" es ~4-5, no ~12

**Auditoría de bias (B01):** Limpia — features lagged T-1, trades en open[T], ATR lagged T-1, zero overlap train/test, sin z-scoring snooping. PnL autocorrelation +0.071. Win rate 48.4%.

---

### E051 — Stress Test de Costos (Más detalle en secciones 7 y 8)

Evaluación del impacto de costos elevados en B01 y A01 sobre 2025. Reveló que el margen de costo es extremadamente estrecho.

---

## 6. Resultados del Campeón (B01 V2b s42)

### 6.1 Perfil del Modelo

| Atributo | Valor |
|---|---|
| Config | V2b "Extended Horizon" |
| gamma | 0.999 |
| hidden_dims | [256, 256] |
| dead_zone | 0.10 |
| min_hold_period | 20 bars |
| action_penalty | 0.0002 |
| loss_penalty_factor | 1.5 |
| lr | 1e-4 |
| Seed | 42 |
| Best checkpoint | Episodio 899 |
| Val Sharpe | +10.26 |

### 6.2 Performance OOS Consolidada

| Métrica | 2024 (156 días) | 2025 (260 días) |
|---|---|---|
| **Sharpe** | +12.20 | +4.62 |
| **Return** | +95.6% | +41.6% |
| **Max Drawdown** | 2.2% | 5.3% |
| Profit Factor | 1.50 | 1.32 |
| Win Rate | 50.6% | 48.2% |
| Total Trades | 25,018 | 41,748 |
| Trades/día | ~160 | ~160 |
| EV/trade | $0.72 | $0.41 |
| Sortino | — | 5.61 |
| Calmar | 88.92 | 7.57 |

### 6.3 Anatomía de los Trades (2025)

| Métrica | Valor |
|---|---|
| **Total trades** | 41,748 |
| **Trades/día** | 160.6 |
| **Mediana de hold time** | 7 minutos |
| **P25 hold time** | 3 minutos |
| **P75 hold time** | 15 minutos |
| **P90 hold time** | 20 minutos |
| **Trades ≤3 min** | 12,981 (31.1%) |
| **Trades ≤7 min** | 22,247 (53.3%) |
| **Trades ≥20 min** | 7,055 (16.9%) |

| Exit Reason | Count | % |
|---|---|---|
| SL hit | 19,347 | 46.3% |
| TP hit | 16,238 | 38.9% |
| Reverse signal | 6,163 | 14.8% |

### 6.4 Economía por Trade (2025)

| Componente | Valor |
|---|---|
| **PnL bruto total** | $30,210 |
| Comisión total | $5,517 |
| Slippage total | $7,510 |
| **Costos totales** | **$13,028** |
| **PnL neto total** | **$17,183** |
| **Costos como % del bruto** | **43.1%** |
| PnL bruto/trade | $0.72 |
| **PnL neto/trade** | **$0.41** |
| Costo/trade | $0.31 |

| | Avg PnL | Count | % |
|---|---|---|---|
| Trades ganadores | +$3.49 | 20,120 | 48.2% |
| Trades perdedores | -$2.45 | 21,600 | 51.7% |
| Payoff ratio | 1.42× | — | — |

### 6.5 Sizing y Riesgo

| Métrica | Valor |
|---|---|
| Notional promedio | $1,586 |
| Unidades promedio | 5,152 |
| Leverage promedio | 0.12× |
| SL promedio | 5.46 pips |
| TP promedio | 8.19 pips |
| TP/SL ratio | 1.50 |
| MAE promedio | -$1.66 |
| MFE promedio | +$2.49 |
| MFE/MAE | 1.51× |

### 6.6 Dato Crítico: Gap Entre Trades

| Gap (barras) | Count | % |
|---|---|---|
| **Gap = 0 (reentry instantánea)** | **40,441** | **96.9%** |
| Gap = 1 | 1,225 | 2.9% |
| Gap ≤ 5 | 41,727 | 100.0% |

**El agente está SIEMPRE posicionado.** En el 96.9% de las ocasiones, abre un nuevo trade en la misma barra en que cierra el anterior. El min_hold_period=20 no impide esto porque la mayoría de trades se cierran antes por SL/TP (mediana 7 barras), y no hay cooldown entre posiciones.

---

## 7. Stress Test de Costos (E051)

### 7.1 Escenarios Probados

| Escenario | Spread (pips) | Slippage (pips) | Contexto |
|---|---|---|---|
| **Baseline** | 0.2 | 0.10 ± 0.05 | ECN/institucional |
| **2x slippage** | 0.2 | 0.20 ± 0.10 | Llenado adverso moderado |
| **3x slippage** | 0.2 | 0.30 ± 0.15 | Llenado adverso severo |
| **Retail spread** | 0.5 | 0.10 ± 0.05 | Broker retail típico |
| **Retail worst-case** | 0.5 | 0.30 ± 0.15 | Peor caso retail |

### 7.2 Resultados B01 V2b s42 (Champion)

| Escenario | Sharpe | Return | MDD | PF | WR% | Veredicto |
|---|---|---|---|---|---|---|
| **Baseline** | **+4.62** | **+41.6%** | **5.3%** | 1.32 | 48.2% | ✅ FUERTE |
| **2x slippage** | **+1.46** | **+11.4%** | **12.8%** | 1.32 | 48.2% | ⚠️ MARGINAL |
| 3x slippage | -1.67 | -12.1% | 22.3% | 1.33 | 48.2% | ❌ MUERTO |
| Retail spread | -2.10 | -15.0% | 23.8% | 1.33 | 48.2% | ❌ MUERTO |
| Retail worst | -8.47 | -47.6% | 49.1% | 1.33 | 48.2% | ❌ MUERTO |

### 7.3 Resultados A01 V2a s137 (Backup)

| Escenario | Sharpe | Return | MDD | PF | WR% | Veredicto |
|---|---|---|---|---|---|---|
| **Baseline** | **+3.61** | **+29.3%** | **8.2%** | 1.32 | 48.5% | ✅ FUERTE |
| 2x slippage | -0.02 | -0.4% | 19.9% | 1.32 | 48.5% | ❌ MUERTO |
| 3x slippage | -3.67 | -23.4% | 31.1% | 1.32 | 48.6% | ❌ MUERTO |
| Retail spread | -4.19 | -26.2% | 32.6% | 1.32 | 48.6% | ❌ MUERTO |
| Retail worst | -11.52 | -56.5% | 57.0% | 1.32 | 48.6% | ❌ MUERTO |

### 7.4 Observación Clave

**Win rate, profit factor y TP/SL ratio NO varían** entre escenarios (el agente usa la misma política fija). Lo único que cambia es el costo por trade. El edge bruto es constante (~$0.72/trade bruto); los costos determinan si el neto es positivo o negativo.

**Breakeven de slippage para B01:** ~0.22-0.25 pips mean (interpolando entre 2x rentable y 3x muerto).

---

## 8. Análisis por Subperiodo (2025)

### 8.1 B01 V2b s42 (Champion)

| Periodo | Return | MDD | Sharpe (aprox) |
|---|---|---|---|
| Q1 (Ene-Mar) | +14.4% | -1.4% | 9.47 |
| Q2 (Abr-Jun) | +19.7% | -1.9% | 8.31 |
| **Q3 (Jul-Sep)** | **+1.0%** | **-2.9%** | **0.79** |
| **Q4 (Oct-Dic)** | **+2.3%** | **-5.3%** | **1.03** |
| **H1 (Ene-Jun)** | **+36.9%** | **-1.9%** | **8.64** |
| **H2 (Jul-Dic)** | **+3.4%** | **-5.3%** | **0.93** |

### 8.2 A01 V2a s137 (Backup)

| Periodo | Return | MDD | Sharpe (aprox) |
|---|---|---|---|
| Q1 (Ene-Mar) | +16.3% | -1.5% | 9.34 |
| Q2 (Abr-Jun) | +16.2% | -2.3% | 6.75 |
| **Q3 (Jul-Sep)** | **-2.4%** | **-4.1%** | **-1.84** |
| **Q4 (Oct-Dic)** | **-2.0%** | **-5.8%** | **-0.97** |
| **H1 (Ene-Jun)** | **+35.1%** | **-2.3%** | **7.27** |
| **H2 (Jul-Dic)** | **-4.3%** | **-8.2%** | **-1.28** |

### 8.3 Interpretación

- **Ambos modelos son fuertemente rentables en H1 2025** (Sharpe 7-9)
- **H2 2025 muestra degradación severa**: B01 marginalmente positivo (Sharpe 0.9), A01 negativo (-1.3)
- B01 (V2b, min_hold=20) sobrevive H2 con +3.4%; A01 (V2a, min_hold=15) pierde -4.3%
- La transición Q2→Q3 es el punto de inflexión: algo cambió en el régimen de USDJPY a partir de julio 2025
- **Implicación:** El edge puede ser régimen-dependiente y no estacionario

---

## 9. Diagnóstico del Problema Central

### 9.1 El Problema en una Frase

> **El agente aprendió a ser un scalper always-in con edge bruto real pero margen de costos tan fino que cualquier aumento en fricción de ejecución destruye la rentabilidad.**

### 9.2 Métricas del Problema

| Dimensión | Evidencia |
|---|---|
| **Frecuencia excesiva** | 160 trades/día, 97% instant reentry, siempre posicionado |
| **Edge fino** | $0.41 neto / $0.72 bruto = 43% se va en costos |
| **Fragilidad a costos** | Muere en 3x slippage (0.30 pips) o retail spread (0.5 pips) |
| **Min hold inefectivo** | P50 hold = 7 min (SL/TP cierra antes de los 20 bars), no hay cooldown post-cierre |
| **Performance temporal** | H1 2025 = 91% del return anual; H2 apenas breakeven |
| **Seed fragility** | Solo 2 de 4 USDJPY seeds sobreviven en 2025 (seed 42 y parcialmente 137) |
| **Par único** | Solo USDJPY funciona. EURUSD, USDCHF, GBPUSD, AUDUSD, NZDUSD, USDCAD fracasan |

### 9.3 ¿Por Qué el Agente Aprendió Esto?

1. **El min_hold_period no crea cooldown entre trades** — solo bloquea modificaciones mientras la posición está abierta. Una vez que SL/TP cierra la posición (en ~7 barras), el agente puede reabrir instantáneamente.
2. **SAC Gaussian noise + señal genuina** — el agente descubrió que explotar un edge bruto repetidamente (aunque pequeño) maximiza el reward acumulado. Como el reward normaliza por balance, cada $0.41 neto/trade contribuye positivamente a la señal de aprendizaje.
3. **Costos durante entrenamiento son idénticos a baseline eval** — el agente no fue penalizado por costos extra, así que no aprendió a ser selectivo. La frecuencia de trading es óptima para LOS costos con los que entrenó, pero no deja margen.

### 9.4 ¿Es Real el Edge?

**Sí, pero con caveats importantes:**
- PF 1.32, WR 48.2%, payoff ratio 1.42× son consistentes entre 2024 y 2025
- El edge NO proviene de look-ahead bias (auditoría completa limpia)
- El decay 62% entre 2024→2025 es significativo pero el edge sigue positivo
- La concentración en H1 2025 sugiere que parte del edge es régimen-dependiente

---

## 10. Plan Propuesto de Próximos Pasos

Proponemos un enfoque multi-flanco, priorizando por ratio de impacto/esfuerzo:

### FLANCO 1: Filtrado Post-Hoc (rápido, sin reentrenar)

**Tiempo estimado:** ~1-2 horas  
**Pregunta:** ¿Existe un subconjunto de trades de alta convicción que sobreviva costos elevados?

Acciones:
- Re-evaluar B01 2025 con `dead_zone` elevado: 0.25, 0.35, 0.50 (solo tomar trades con señal fuerte)
- Filtrar trades por hora del día (análisis de sesión: ¿el edge solo existe en ciertas sesiones?)
- Si existe un subset con PF > 1.5 y menor frecuencia, esto validaría el camino sin reentrenar

### FLANCO 2: Reentrenamiento Estructural (E052)

**Tiempo estimado:** ~14 horas (7h training + 2h eval)  
**Objetivo:** Cambiar la estructura del agente de "always-in scalper" a "trader selectivo de swing corto"

Intervenciones propuestas:
1. **Cooldown obligatorio post-cierre:** Después de cerrar posición, bloquear N barras (e.g., 20-30) antes de permitir reabrir. Esto ataca directamente el 96.9% de instant reentry.
2. **Entrenar con 2x costos:** El agente aprende a ser selectivo porque necesita compensar costos más altos
3. **Explorar dead_zone más agresivo** (0.20-0.30) para filtrar señales débiles durante entrenamiento

### FLANCO 3: Análisis de Régimen H1 vs H2

**Tiempo estimado:** ~1-2 horas  
**Pregunta:** ¿Qué cambió en USDJPY a partir de julio 2025 que debilitó el edge?

Acciones:
- Comparar volatilidad (ATR), distribución de returns, correlación con otros pares entre H1 y H2
- Sharpe rolling con ventana de 30-60 días para identificar cuándo exactamente se apaga el edge
- ¿Cambió el comportamiento del agente (señal) o el mercado (régimen)?

### FLANCO 4: Validación en MT4/MT5

**Tiempo estimado:** ~1-2 semanas de desarrollo  
**Prerequisito:** Resolver margen de costos (Flancos 1-2) primero

Acciones:
- Bridge Python↔MT4/MT5
- Cuenta demo ECN (ICMarkets raw, Pepperstone, similar)
- Forward test de 1-3 meses

**Nota:** Consideramos esto **prematuro** hasta resolver el problema de frecuencia/costos. No tiene sentido invertir 2 semanas de desarrollo para descubrir que la estrategia muere con costos reales.

### Orden Sugerido

| Paso | Acción | Tiempo | Decisión Go/No-Go |
|---|---|---|---|
| 1 | Filtrado post-hoc | ~2h | ¿Existe subset rentable con costos 2x? |
| 2 | Análisis de régimen H1/H2 | ~2h | ¿El edge es persistente o temporal? |
| 3 | E052 con cooldown + 2x costos | ~14h | Solo si pasos 1-2 sugieren edge filtrable/persistente |
| 4 | MT4/MT5 demo | ~2 semanas | Solo si E052 sobrevive stress test con retail spread |

---

## 11. Preguntas para el Experto

Agradeceríamos opinión experta sobre los siguientes puntos:

### Arquitectura y Enfoque
1. ¿Es SAC la elección correcta para este tipo de trading? ¿Habría ventajas en cambiar a PPO, TD3, o un enfoque no-RL (e.g., supervised learning con labels de retorno)?
2. ¿El feature set de 94 dimensiones es excesivo o insuficiente? ¿Hay features canónicos para FX intraday que deberíamos agregar? (Nos falta orderbook data, volumen real, VIX/MOVE como proxy de risk-on/off, etc.)
3. ¿La arquitectura MLP [256,256] es adecuada o deberíamos explorar LSTM/Transformer para capturar dependencias temporales? Actualmente el agente solo ve el snapshot actual (T-1), sin memoria explícita de barras anteriores (excepto los rolling indicators pre-computados).

### Estrategia y Costos
4. ¿Es viable una estrategia de scalping RL en FX con 160 trades/día en producción ECN? ¿Cuáles serían los costos realistas de ejecución a esperar?
5. ¿El enfoque de cooldown obligatorio es la mejor forma de reducir frecuencia, o hay alternativas más elegantes? (e.g., frame-skipping, agregación temporal a 5-min/15-min bars).
6. ¿Qué opinión le merece la dependencia de un solo par (USDJPY)? ¿Es común en quant trading tener una estrategia single-instrument?

### Validación y Producción
7. El decay de Sharpe 2024→2025 de 62% — ¿es típico para estrategias intraday o es una señal de alerta?
8. La concentración del performance en H1 2025 — ¿sugiere overfitting al régimen de entrenamiento o es normal para estrategias momentum/mean-reversion?
9. ¿Qué pasos de validación adicionales recomendaría antes de considerar un forward test en cuenta demo?
10. ¿Tiene experiencia o sugerencias sobre la integración Python↔MT4/MT5 para trading algorítmico en forex?

---

## Apéndice: Evolución de Configuraciones

| Config | Experimento | gamma | lr | dead_zone | min_hold | action_pen. | loss_pen. | Mejor Sharpe OOS 2024 |
|---|---|---|---|---|---|---|---|---|
| Sin guards | E046 | 0.99 | 3e-4 | 0 | 0 | 0 | 1.0 | -87 (catástrofe) |
| V1 "Balanced" | E048 | 0.99 | 3e-4 | 0.05 | 5 | 0.0001 | 1.2 | -3.22 |
| V2 "Patient" | E048-49 | 0.995 | 1e-4 | 0.08 | 10 | 0.0002 | 1.5 | +7.31 |
| V2a "Ultra-Patient" | E049-50 | 0.997 | 1e-4 | 0.10 | 15 | 0.0002 | 1.5 | +9.82 |
| **V2b "Extended Horizon"** | **E050** | **0.999** | **1e-4** | **0.10** | **20** | **0.0002** | **1.5** | **+12.20** |

**Patrón claro:** Más paciencia (gamma↑, min_hold↑, dead_zone↑, lr↓) = mejor performance. El límite natural de este enfoque es el Flanco 2 propuesto (cooldown + costos elevados).

---

*Informe generado el 24 de febrero de 2026. Todos los datos provienen de backtests sobre datos históricos con walk-forward OOS. No representan garantía de rendimiento futuro.*
