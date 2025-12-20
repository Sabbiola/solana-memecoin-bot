# 🚀 Solana Memecoin Trading Bot

**Autonomous trading bot for Solana memecoins with multi-phase entry strategy, real-time rugcheck, and advanced position management.**

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Trading Strategy](#trading-strategy)
6. [Token Discovery](#token-discovery)
7. [Safety & Rugcheck](#safety--rugcheck)
8. [Position Management](#position-management)
9. [Convex Strategy (Advanced)](#convex-strategy-advanced)
10. [Modules Reference](#modules-reference)
11. [API & Integrations](#api--integrations)
12. [Monitoring & Logging](#monitoring--logging)

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    SOLANA MEMECOIN BOT                          │
├─────────────────────────────────────────────────────────────────┤
│  Token Discovery → Filtering → Rugcheck → Entry → Monitoring   │
│       ↓               ↓           ↓        ↓          ↓        │
│  Webhook/Scanner → Validator → Rugchecker → Trader → Exit      │
└─────────────────────────────────────────────────────────────────┘
```

### Key Features

- **Multi-Source Token Discovery**: Helius webhooks + DexScreener + Pump.fun scanner
- **Phased Entry (Convex)**: SCOUT → CONFIRM → CONVICTION → MOONBAG
- **Real-Time Rugcheck**: Mint/freeze authority, holder concentration, LP analysis
- **Dynamic Position Management**: Trailing stops, EAS (Exit Acceleration Score), partial exits
- **Event-Driven Architecture**: Webhook-based dev wallet and LP monitoring
- **Paper Trading Mode**: Safe testing without real funds
- **Telegram Integration**: Alerts, commands, force-sell buttons

---

## Architecture

```
solana_bot/
├── main.py                    # Entry point
├── config/                    # Configuration management
│   ├── __init__.py           # All settings, env vars, risk limits
│   └── risk_config.py        # Risk management profiles
│
├── core/                      # Core trading logic (42 modules)
│   ├── bot.py                # Main orchestrator (2400+ lines)
│   ├── trader.py             # Buy/sell execution
│   ├── validator.py          # Token phase detection, pool checks
│   ├── rugcheck.py           # Safety analysis
│   ├── convex_state_machine.py # Phased entry strategy
│   ├── token_scanner.py      # Token discovery
│   ├── helius_webhook.py     # Real-time token events
│   ├── jupiter_client.py     # DEX aggregator
│   ├── price_feed.py         # Price quotes
│   └── ...                   # 30+ more modules
│
├── paper_trading/            # Simulated trading
├── telegram_notifier.py      # Alerts and commands
└── utils/                    # Helpers
```

---

## Installation

```bash
# Clone and enter directory
cd Solana4

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your keys
```

### Required Environment Variables

```bash
# Core
SOLANA_PRIVATE_KEY=<base58 private key>
RPC_URL=https://mainnet.helius-rpc.com/?api-key=<key>

# Telegram (optional but recommended)
TELEGRAM_BOT_TOKEN=<bot token>
TELEGRAM_CHAT_ID=<chat id>

# Trading Mode
PAPER_TRADING_MODE=True   # Start with paper trading!

# Convex Strategy
CONVEX_MODE_ENABLED=True
CONVEX_SCOUT_SIZE_SOL=0.01
CONVEX_CONFIRM_SIZE_SOL=0.04
```

### Run

```bash
# Paper trading (default)
python -m solana_bot.main

# Live trading (after testing)
PAPER_TRADING_MODE=False python -m solana_bot.main
```

---

## Configuration

### Risk Limits (`config/__init__.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_POSITIONS` | 3 | Max concurrent positions |
| `MAX_DAILY_LOSS_SOL` | 1.0 | Max daily loss |
| `MAX_DAILY_TRADES` | 50 | Trading limit |
| `MIN_RESERVE_SOL` | 0.05 | Keep for fees |

> ❗ **CRITICAL**: `STOP_LOSS_PCT` and `TAKE_PROFIT_PCT` are **NOT used in Convex Mode**.
> - Stop-loss is **phase-specific** (see Position Management)
> - Take-profit **does not exist** (only partial exits + trailing)
> - Using global SL/TP breaks the convex edge

### Strategy Profiles

**EARLY Strategy** (Pump.fun bonding curve):
- Entry: 0.001-0.005 SOL
- Target: +100% / SL: -80%
- Token age: < 2 hours

**STABLE Strategy** (Migrated DEX):
- Entry: 0.01-0.05 SOL
- Target: +30% / SL: -10%
- Token age: > 1 hour, liquidity > $5k

---

## Trading Strategy

### Entry Flow

```
Token Detected
     ↓
┌─────────────────┐
│ Phase Detection │  BONDING_CURVE / PUMPSWAP / RAYDIUM / JUPITER
└────────┬────────┘
         ↓
┌─────────────────┐
│ Pool Quality    │  Min liquidity: 0.1 SOL (BC) / 0.5 SOL (DEX)
└────────┬────────┘
         ↓
┌─────────────────┐
│  Entry Scorer   │  Score 0-100 based on momentum, age, liquidity
└────────┬────────┘
         ↓
┌─────────────────┐
│   Rugcheck      │  Authority, holders, LP analysis
└────────┬────────┘
         ↓
   BUY EXECUTION
```

### Exit Flow

```
Position Open
     ↓
┌─────────────────┐
│ EAS Tracking    │  Dynamic stop-loss adjustment
│ (Exit Accel.)   │
└────────┬────────┘
         ↓
┌─────────────────┐
│ Stop-Loss Check │  -80% initial, moves to break-even
└────────┬────────┘
         ↓
┌─────────────────┐
│ Trailing Stop   │  10% from peak
└────────┬────────┘
         ↓
┌─────────────────┐
│ Timeout         │  Force sell after 15 min
└────────┬────────┘
         ↓
   SELL EXECUTION
```

---

## Token Discovery

### Sources

1. **Helius Webhook** (`helius_webhook.py`)
   - Real-time new token creations on Pump.fun
   - Whale wallet activity detection
   - Event-driven dev/LP monitoring

2. **Token Scanner** (`token_scanner.py`)
   - DexScreener API polling
   - Jupiter quotable tokens
   - Configurable filters

3. **Pump.fun Scanner** (`pumpfun_scanner.py`)
   - Direct bonding curve monitoring

### Filters Applied

| Filter | Threshold | Purpose |
|--------|-----------|---------|
| Min Liquidity | $100+ | Tradeable |
| Max Age | < 24h | Fresh tokens |
| Volume/MCap Ratio | > 0.01 | Active trading |
| Price Change 5m | -50% to +500% | Avoid pumped/dead |

---

## Safety & Rugcheck (Phase-Aware and Non-Suicidal)

### Philosophy

Rugcheck is **phase-aware**:
- SCOUT is permissive (avoid missing winners), but rejects obvious traps
- CONFIRM is strict
- CONVICTION is very strict + event-driven dev monitoring

### Hard Reject (NO-GO) Flags (all phases)

Reject immediately if any of these are true:

- **Cannot sell / honeypot-like behavior** (if detectable via quote simulation)
- **Freeze authority active** on DEX-traded tokens (hard reject)
- Severe rugcheck "CRITICAL" flags (known scam patterns)
- LP / pool anomalies consistent with fake migration or malicious setup

### Phase-Specific Thresholds

#### SCOUT (permissive, survival-first)

- `risk_score ≤ 65`
- `dev_holding ≤ 35%`
- `top10 ≤ 75%`
- `mint authority`: **WARN allowed**
- `freeze authority`: **prefer revoked**; if active → generally reject unless you explicitly allow Pump.fun-only exceptions

> SCOUT should avoid obvious scams, not over-filter winners.

---

#### CONFIRM (strict selection gate)

- `risk_score < 50`
- `dev_holding < 25%`
- `top10 < 65%`
- `mint authority`: **MUST be revoked**
- `freeze authority`: **MUST be revoked**

If fails → exit all. No negotiation.

---

#### CONVICTION / MOONBAG (very strict)

- `risk_score < 40`
- `dev_holding < 15%`
- `top10 < 60%`
- LP quality preferred (locked/burned if available)

**Dev monitoring becomes event-driven:**
- Any dev sell / suspicious transfer event → CRITICAL exit

---

### Rugcheck Analysis Module

The `Rugchecker` class performs comprehensive safety analysis:

```python
result = await rugchecker.check(mint, early_mode=True)
# Returns RugcheckResult with:
# - is_safe: bool
# - risk_score: 0-100 (lower = safer)
# - risk_level: "LOW" / "MEDIUM" / "HIGH" / "CRITICAL"
```

---

## Position Management (v12.3 Convex-Coherent)

### Goals (what we optimize for)

- **Cut dead tokens fast** (SCOUT is a cost, not a profit center)
- **Increase commitment only after selection**
- **Capture convex outliers** (+200%+) without "top calling"
- **Events override models**
- **Indicators modulate risk; PRICE decides exits**

### Position Lifecycle

```
SCOUT (micro) → CONFIRM (add) → CONVICTION (full) → MOONBAG (tail)
```

### Risk Controls by Phase (do NOT use one SL for everything)

#### SCOUT (0.005–0.01 SOL)

**Purpose:** Pay little to test if the token is alive.

> ❗ **CRITICAL RULE**: SCOUT positions are **NEVER managed for profit**

**Rules:**
- No trailing
- No take profit
- No partial exits
- No runner logic
- No "scalping" or optimization
- **Primary exit = selection timeout**
- Optional safety stop only for extreme dumps

**Exits:**
- `SCOUT_TIMEOUT`: exit if Selection Score doesn't confirm within `180s` (default)
- `CRITICAL_EVENT`: exit immediately

**Stop-loss (optional):**
- `-12% to -18%` max (only if you want a hard floor)

> ⚠️ Avoid -80%: it turns SCOUT into a slow bleed and breaks expectancy.

---

#### CONFIRM (add 0.03–0.05 SOL)

**Purpose:** token proved it's alive → scale up **only now**.

**Rules:**
- Activate monitoring stack (events + execution state)
- Tight risk: if CONFIRM fails, don't "hope"

**Exits:**
- `INVALIDATION`: loss of structure (no HH, breaks HL, absorption fails)
- `CRITICAL_EVENT`: exit immediately

**Hard stop-loss:**
- `-10% to -18%` depending on liquidity and execution quality
  (Use tighter stops when liquidity is deeper; wider only when thin.)

---

#### CONVICTION (max total 0.08–0.15 SOL)

**Purpose:** run the v12.3 stack to capture +200%+.

**Active Systems:**
- **Event Bus:** CRITICAL/MAJOR root-cause exits
- **Dynamic EAS Risk Level (with hysteresis):** risk signal, not exit
- **Runner State Machine:** NORMAL → PRE_RUNNER → RUNNER → PARABOLIC
- **Partial Exits:** crystallize gains under risk transitions
- **Narrative Phase:** INFLOW vs DISTRIBUTION
- **Composite trailing:** state × momentum × risk × narrative

**Hard stop-loss:**
- EARLY-like conviction: `-20% to -25% max`
- Mature/Jupiter conviction: `-8% to -12%`

> The system captures upside via partials + trailing, not via massive SL.

---

#### MOONBAG (tail only, typically 10–30%)

**Purpose:** keep convex exposure with strict protection.

**Rules:**
- No re-adds
- Trailing tightened aggressively when narrative ends
- Events still override everything

---

### Runner States (gradual protection)

```
NORMAL (<+30%):        any signal can exit (non-critical)
PRE_RUNNER (+30–80%):  need 2/3 signals
RUNNER (+80–200%):     need 2/3 signals, retail alone ignored
PARABOLIC (+200%+):    need 3/4 signals
```

### Dynamic Risk Level (Structural Edge Assessment)

**Terminology clarification:**
- EAS = **Execution-Aware Asymmetry** (structural edge / risk input)
- NOT "Exit Acceleration Score" (mentally dangerous)
- Think: **Structural Edge Score** or **Execution Edge Ratio**

Risk level is **not an exit trigger**, it **modulates** trailing and partial exits.

**Hysteresis bands:**
- LOW → MEDIUM activate at `EAS < 1.15`
- MEDIUM → LOW deactivate at `EAS > 1.25`
- MEDIUM → HIGH activate at `EAS < 0.92`
- HIGH → MEDIUM deactivate at `EAS > 1.02`

### Partial Exit Manager (crystallize gains)

Triggered **only** on stable risk transitions (avoid flapping):
- MEDIUM risk: **sell 20%** (once)
- HIGH risk: **sell 35%** (once)
- PARABOLIC + HIGH: **sell 25%** additional (once)

Max realized: **60%**, tail rides with trailing.

### Composite Trailing Formula (deterministic)

```
trailing = base
  × runner_state_multiplier
  × momentum_multiplier
  × eas_risk_multiplier
  × narrative_multiplier
  × regret_policy_multiplier   # updated weekly, never intra-trade
```

### Execution State Machine (Pro Addition)

**3 execution states** (modulate risk/exits independently of token signals):

```
EXEC_OK:        normal operation
EXEC_DEGRADED:  slippage ↑, route instability, impact creep
EXEC_FAILING:   tx failures spike, chain congestion
```

**Actions by state:**
- **DEGRADED**: take partials earlier, tighten trailing, reduce add sizes
- **FAILING**: emit MAJOR event (root cause: `EXECUTION_DEGRADATION`)

**Purpose:** Protect when Solana/DEX infrastructure degrades, not just token.

---

### Event Bus Rules

Internal event system for reactive position management:

**Event Priority:**
- **CRITICAL**: 1 event → immediate exit
- **MAJOR**: 2 distinct root causes → immediate exit
- Models do not override events

---

## Convex Strategy (Advanced)

The Convex strategy implements phased entry with progressive commitment:

```
┌─────────┐    ┌──────────┐    ┌────────────┐    ┌────────────┐
│  SCOUT  │ →  │  CONFIRM │ →  │ CONVICTION │ →  │  MOONBAG   │
│ 0.01 SOL│    │ +0.04 SOL│    │  Full v12  │    │  Tail Only │
└─────────┘    └──────────┘    └────────────┘    └────────────┘
     ↓              ↓                ↓                 ↓
  45-60s         If alive:        EAS + Runner     Let ride or
  baseline    add size + re-rug   Protection        trail out
```

### Selection Signals (5 metrics)

1. **tx_rate_accel**: Transaction rate acceleration (≥1.8x = point)
2. **wallet_influx_accel**: New buyers acceleration (≥1.6x = point)
3. **hh_confirmed**: Higher highs confirmed (boolean)
4. **curve_slope_accel**: Price curve steepening (≥1.5x = point)
5. **sell_absorption**: Dips bought aggressively (boolean)

**Score ≥2 for 2 consecutive windows** → Confirm entry

### Anti-Fake Filter (Quality Gate)

**Before CONFIRM, require at least ONE:**
- ≥5 unique wallets buying > 0.1 SOL each
- OR ≥1 single buy > 0.5 SOL

**Purpose:** Reject tx spam, wallet recycling, micro-buy wash trading common on Pump.fun.

### Configuration

```bash
CONVEX_MODE_ENABLED=True
CONVEX_SCOUT_SIZE_SOL=0.01      # Entry size
CONVEX_CONFIRM_SIZE_SOL=0.04   # Add size on confirmation
CONVEX_MAX_TOTAL_SOL=0.15      # Max position size
CONVEX_SCOUT_TIMEOUT_SEC=180   # 3 min to prove life
CONVEX_SELECTION_THRESHOLD=2   # Min signals to confirm
```

---

## Modules Reference

### Core Trading

| Module | Purpose |
|--------|---------|
| `bot.py` | Main orchestrator, position management |
| `trader.py` | Buy/sell execution (Pump + Jupiter) |
| `jupiter_client.py` | DEX aggregator integration |
| `trading_mode_manager.py` | Paper/Live mode switching |

### Token Analysis

| Module | Purpose |
|--------|---------|
| `validator.py` | Phase detection, pool quality |
| `rugcheck.py` | Safety analysis |
| `entry_scorer.py` | Opportunity scoring |
| `token_scanner.py` | Token discovery |
| `volume_analyzer.py` | Volume metrics |

### Position Management

| Module | Purpose |
|--------|---------|
| `dynamic_trailing.py` | Stop-loss and trailing |
| `dynamic_eas_tracker.py` | Exit acceleration |
| `partial_exit_manager.py` | Staged exits |
| `runner_protection.py` | Protect big gains |
| `narrative_analyzer.py` | Trade phase detection |

### Event System

| Module | Purpose |
|--------|---------|
| `event_bus.py` | Internal event dispatch |
| `helius_webhook.py` | External event reception |
| `dev_tracker.py` | Dev wallet monitoring |
| `lp_monitor.py` | LP integrity checking |

### Infrastructure

| Module | Purpose |
|--------|---------|
| `rpc_client.py` | Solana RPC wrapper |
| `rpc_cache.py` | TTL cache + credit limiter |
| `price_feed.py` | Real-time prices |
| `helius_sender.py` | Transaction submission |
| `jito_client.py` | MEV protection |
| `tx_confirmer.py` | Transaction confirmation |

---

## API & Integrations

### External Services

| Service | Purpose | Required |
|---------|---------|----------|
| **Helius** | RPC + Webhooks + DAS | ✅ Yes |
| **Jupiter** | DEX Aggregation | ✅ Yes |
| **DexScreener** | Market data | ✅ Yes |
| **Jito** | MEV protection | Optional |
| **Telegram** | Alerts | Optional |
| **Birdeye** | Historical data | Optional |

### Telegram Commands

```
/status      - Bot status and balance
/positions   - Active positions
/sell <mint> - Force sell position
/stop        - Pause trading
/resume      - Resume trading
/config      - Show configuration
```

---

## RPC Budget Policy (Helius Credit Survival Guide)

### Core Rule

**Do not use the chain as a database.**
Use it for **events**, and keep **state locally**.

### Allowed Polling vs Event-Driven

#### Must be Event-Driven (webhooks)

- Dev wallet activity (sell, transfers)
- LP changes / liquidity removal
- Large transfers (only in conviction)
- Execution instability (internal events from your trader)

#### Allowed Polling (limited)

- Price quotes (Jupiter) — frequent but controlled
- Batch account reads via `getMultipleAccounts` — infrequent (30–60s)

### Forbidden in loops

Never poll these in a loop:

- `getTokenSupply`
- `getTokenLargestAccounts`
- holder stats repeatedly

These are **one-shot snapshots** (CONFIRM/CONVICTION only).

---

### State-Based Scheduling (default targets)

#### SCOUT

- Price quotes: every **3–5s**
- No holders/supply/largest
- No account polling except minimal required
- Total target: **≤ 0.05 req/sec per token**

#### CONFIRM

- Price quotes: every **1–2s**
- Batch accounts: every **30–60s** (`getMultipleAccounts`)
- Rugcheck: **once**
- Total target: **≤ 0.2 req/sec**

#### CONVICTION / MOONBAG

- Price quotes: **1s**
- Batch accounts: **30s**
- EAS recalculation: **30–60s**
- Narrative update: **30–60s** (prefer from webhooks)
- Total target: **≤ 0.8–1.0 req/sec** (single active position recommended)

---

### Batching Rules (mandatory)

- Use `getMultipleAccounts()` for all per-tick reads
- Never call `getAccountInfo` repeatedly per account
- Combine: pool accounts + your ATA + dev ATA (if needed) into one batch read

### Caching Rules (TTL)

- Mint metadata / decimals / authorities: cache **24h**
- Supply / top holders: cache **30–60m** or one-shot
- Any repeated expensive call without TTL is a bug

### Credit Safety Guard (enforced)

Implement a global limiter:

- if usage exceeds daily/hourly budget:
  - drop non-critical updates (narrative sampling)
  - keep only quotes + critical event handling
  - pause new SCOUT opens if necessary

### Regime Filter (prevent token graveyard bleed)

> **Pump.fun is graveyard, SCOUT is cost, not profit.**

Rules:
- Maximum **3 concurrent scouts**
- Kill dead tokens within **180s** (no CONFIRM → exit)
- **Zero RPC calls** after exit (no monitoring dead tokens)

### Anti-Overtrading Rule (Pro Addition)

> **If 3 consecutive SCOUT entries fail (timeout/invalidate), activate 10-15 min cooldown.**

**Purpose:** Prevent algorithmic revenge trading during dead market periods.

**Exception:** CRITICAL whale signal or major narrative shift can override.

---

## Monitoring & Logging

### Log Files

```
logs/
├── bot.log              # Main bot logs
├── trade_metrics.json   # Trade performance
├── trade_metrics.jsonl  # Detailed trade JSONL
└── paper_trades.log     # Paper trading log
```

### Trade Metrics

```python
from solana_bot.core.trade_metrics_logger import get_metrics_logger

logger = get_metrics_logger()
logger.print_report(days=7)

# KPIs available:
# - scout_success_rate_pct
# - dead_rate_pct
# - runner_capture_rate_pct
# - efficiency_top10_pct
# - avg_pnl_per_trade_sol
```

### RPC Credit Monitoring

```python
from solana_bot.core.rpc_cache import get_rpc_cache, get_credit_limiter

# Check cache efficiency
get_rpc_cache().print_stats()

# Check credit usage
get_credit_limiter().print_status()
```

---

## Implementation Checklist

Use this checklist to verify the bot is fully operational according to v12.3/convex spec:

### Core Architecture

- [ ] **Separate terminology:**
  - EAS = Execution-Aware Asymmetry (structural edge / risk input)
  - RiskLevel = LOW/MEDIUM/HIGH (hysteresis)
  - RunnerState = NORMAL/PRE/RUNNER/PARABOLIC
- [ ] **Convex state machine wired:** `SCAN → SCOUT → CONFIRM → CONVICTION → MOONBAG`

### Selection & Entry

- [ ] **Selection Score implemented** (5 signals)
  - [ ] baseline captured (first 45–60s)
  - [ ] score ≥2 for **2 consecutive windows** required for CONFIRM
  - [ ] SCOUT timeout = 180s (exit if not selected)
- [ ] **Rugcheck phase thresholds implemented**
  - [ ] SCOUT permissive
  - [ ] CONFIRM strict (mint+freeze revoked required)
  - [ ] CONVICTION very strict
  - [ ] NO-GO flags hard reject

### Event System

- [ ] **Event Bus with root-cause grouping**
  - [ ] CRITICAL: 1 event → immediate exit
  - [ ] MAJOR: 2 distinct root causes → immediate exit
  - [ ] Models do not override events
- [ ] **Dev + LP monitoring moved to webhooks** (no polling loops)

### RPC Optimization

- [ ] **RPC Budget Policy enforced**
  - [ ] no loops for supply/largest/holders
  - [ ] batching via getMultipleAccounts
  - [ ] TTL cache on static calls
  - [ ] state-based scheduling (SCOUT/CONFIRM/CONVICTION)
  - [ ] global credit limiter (degrade gracefully)
- [ ] **Regime filter implemented**
  - [ ] max N concurrent scouts (e.g. 3)
  - [ ] kill dead tokens within 180s
  - [ ] zero calls after exit

### Position Management

- [ ] **Partial Exit Manager implemented** (stable transitions only)
  - [ ] MEDIUM risk: sell 20% once
  - [ ] HIGH risk: sell 35% once
  - [ ] PARABOLIC+HIGH: sell extra 25% once
- [ ] **Composite trailing implemented** with clamped min/max bounds
- [ ] **Phase-specific stop-loss**
  - [ ] SCOUT: -12% to -18% (or timeout only)
  - [ ] CONFIRM: -10% to -18%
  - [ ] CONVICTION: -8% to -25% (depending on phase)

### Logging & Analytics

- [ ] **Logging (JSONL) includes**
  - [ ] selection signals + outcomes
  - [ ] risk transitions + runner states
  - [ ] partial exits + slippage estimates
  - [ ] MFE/MAE/realized + exit reason
  - [ ] event root causes encountered
  - [ ] RPC usage stats (cache hit rate, req/sec)

### Testing Protocol

- [ ] **Paper test protocol**
  - [ ] run 5–7 days
  - [ ] review 50+ trades
  - [ ] report: scout_success_rate, dead_rate, P(reach RUNNER/PARABOLIC), tail regret, event false positives
- [ ] **Live micro protocol**
  - [ ] start 0.01 SOL
  - [ ] scale only after 100+ trades and stable execution

### Key Metrics to Track

> **👑 KING METRIC:** `P(reach PARABOLIC | entry)`
>
> ❗ **If this metric doesn't improve over time, THE ENTIRE SYSTEM MUST BE REVIEWED**, even if you're profitable.
>
> Why: Short-term profit can come from luck or market mania. Only consistent PARABOLIC capture proves you have real outlier edge.

**Secondary metrics:**
- `scout_success_rate_pct` — % of scouts that reach CONFIRM
- `dead_rate_pct` — % of scouts that timeout (graveyard)
- `runner_capture_rate_pct` — % that reach +80% or more  
- `efficiency_top10_pct` — how much profit comes from top 10% of trades
- `avg_pnl_per_trade_sol` — blended expectancy

**What kills the edge:**
- Optimizing for winrate (vs outlier capture)
- Tightening filters too much (miss runners)
- Managing SCOUT for profit (slow bleed)

---

## License

MIT License - Use at your own risk. Trading cryptocurrencies involves significant risk of loss.

---

## Support

For issues or questions, review logs in `logs/` directory first.

Common issues:
- **"No tokens found"**: Check DexScreener API / webhook connectivity
- **"Buy failed"**: Check SOL balance and RPC status
- **"Credit limit"**: Reduce polling or upgrade Helius plan
