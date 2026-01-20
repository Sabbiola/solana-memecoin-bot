# Solana Memecoin Trading Bot

Autonomous trading bot scaffold for Solana memecoins with a convex, phased entry
strategy, phase-aware rugcheck, and event-driven position management.

## Overview

The current codebase is a runnable scaffold that mirrors the architecture described
in the spec. It supports paper trading with real token discovery + quote integrations,
plus a backtest harness for replaying historical ticks.

## Architecture

```
solana_bot/
├── main.py
├── config/
├── core/
├── paper_trading/
├── utils/
└── v15/
```

Key modules:
- `core/bot.py`: main orchestrator, state machine, exits
- `core/rugcheck.py`: phase-aware safety rules
- `core/convex_state_machine.py`: SCOUT -> CONFIRM -> CONVICTION -> MOONBAG
- `core/event_bus.py`: CRITICAL and MAJOR event exit logic
- `core/partial_exit_manager.py`: staged exits
- `core/dynamic_trailing.py`: composite trailing logic

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env
```

## Run (paper trading with live discovery)

```bash
python -m solana_bot.main

# supervised runtime (capital guard)
python -m solana_bot.v15.main
```

## Configuration

Edit `.env` to set risk limits, convex sizing, and simulation parameters. The scaffold
uses defaults aligned with the spec and adds live discovery + quotes for paper trading.

### Helius webhook (optional)

Start the bot and point your Helius webhook to:

```
http://<host>:<port>/webhook
```

Set `HELIUS_WEBHOOK_SECRET` if you enable signatures in Helius.

### Backtest replay

Provide a JSONL file where each line is a tick:

```
{"ts": 1700000000, "tokens": [...], "prices": {"<mint>": 0.0012}}
```

Run:

```bash
python -m solana_bot.backtest.main --data backtest/data.jsonl
python -m solana_bot.backtest.main --data backtest/sample.jsonl
```

### Build historical ticks (BirdEye)

Set `BIRDEYE_API_KEY` in `.env`, create a `backtest/mints.txt` with one mint per line,
then build ticks:

```bash
python -m solana_bot.backtest.build_birdeye --mints-file backtest/mints.txt --start 2024-10-01 --end 2024-10-02 --interval 1m --out backtest/data.jsonl
python -m solana_bot.backtest.main --data backtest/data.jsonl
```

## Notes

- Live trading integrations are stubbed in `core/jito_client.py`.
- Paper trading uses a slippage model and logs metrics to
  `logs/trade_metrics.jsonl`.
- The event bus enforces immediate exit on CRITICAL events and two distinct MAJOR events.
