import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from solana_bot.config import get_settings
from solana_bot.core.birdeye_client import BirdEyeClient, OHLCVPoint
from solana_bot.core.models import TokenInfo
from solana_bot.core.rpc_client import RPCClient
from solana_bot.utils.logging import setup_logging


@dataclass
class SeriesPoint:
    ts: int
    close: float
    volume: float
    trades: int


@dataclass
class TokenSeries:
    mint: str
    symbol: str
    liquidity_usd: float
    market_cap: float
    points: list[SeriesPoint]


def parse_ts(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        pass
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def interval_to_seconds(interval: str) -> int:
    if interval.endswith("m"):
        return int(interval[:-1]) * 60
    if interval.endswith("h"):
        return int(interval[:-1]) * 3600
    if interval.endswith("d"):
        return int(interval[:-1]) * 86400
    return 60


async def build_dataset(
    mints: list[str],
    start_ts: int,
    end_ts: int,
    interval: str,
    out_path: Path,
    use_rpc: bool,
) -> None:
    settings = get_settings()
    setup_logging(settings)
    birdeye = BirdEyeClient(settings)
    rpc_client = RPCClient(settings) if use_rpc else None

    try:
        token_series: list[TokenSeries] = []
        for mint in mints:
            overview = await birdeye.get_token_overview(mint)
            symbol = str(overview.get("symbol") or mint[:5])
            liquidity = _get_float(overview, ("liquidity", "liquidityUSD", "liquidity_usd"))
            if liquidity <= 0:
                liquidity = settings.BACKTEST_DEFAULT_LIQUIDITY_USD
            market_cap = _get_float(overview, ("marketcap", "marketCap", "fdv", "mcap"))

            points = await birdeye.get_ohlcv(mint, interval, start_ts, end_ts)
            if not points:
                points = await birdeye.get_price_history(mint, interval, start_ts, end_ts)
            series = [SeriesPoint(pt.ts, pt.close, pt.volume, pt.trades) for pt in points]
            if series:
                token_series.append(TokenSeries(mint, symbol, liquidity, market_cap, series))
            await asyncio.sleep(0.2)

        if not token_series:
            raise RuntimeError("No historical data returned from BirdEye.")

        onchain_meta = {}
        if rpc_client and settings.RPC_URL:
            for token in token_series:
                onchain_meta[token.mint] = await fetch_onchain_metadata(rpc_client, token.mint)

        timeline = sorted({point.ts for token in token_series for point in token.points})
        interval_sec = interval_to_seconds(interval)
        with out_path.open("w", encoding="utf-8") as handle:
            for ts in timeline:
                tokens, prices = build_tick(
                    token_series,
                    ts,
                    interval_sec,
                    settings.BACKTEST_AVG_TRADE_USD,
                    onchain_meta,
                )
                if not tokens:
                    continue
                handle.write(json.dumps({"ts": ts, "tokens": tokens, "prices": prices}) + "\n")
    finally:
        await birdeye.close()
        if rpc_client:
            await rpc_client.close()


def build_tick(
    series_list: list[TokenSeries],
    ts: int,
    interval_sec: int,
    avg_trade_usd: float,
    onchain_meta: dict[str, dict[str, float | int | bool]],
) -> tuple[list[dict], dict[str, float]]:
    tokens: list[dict] = []
    prices: dict[str, float] = {}
    for series in series_list:
        idx = _index_for_ts(series.points, ts)
        if idx is None:
            continue
        point = series.points[idx]
        first_ts = series.points[0].ts
        age_sec = max(0, ts - first_ts)
        volume_m5 = _window_sum(series.points, idx, max(1, int(300 / interval_sec)))
        volume_h1 = _window_sum(series.points, idx, max(1, int(3600 / interval_sec)))
        price_change_m5 = _window_change(series.points, idx, max(1, int(300 / interval_sec)))
        price_change_h1 = _window_change(series.points, idx, max(1, int(3600 / interval_sec)))
        txns_m5 = _window_trades(series.points, idx, max(1, int(300 / interval_sec)), avg_trade_usd)
        txns_h1 = _window_trades(series.points, idx, max(1, int(3600 / interval_sec)), avg_trade_usd)
        buy_ratio = 0.6 if price_change_m5 >= 0 else 0.4
        txns_m5_buys = int(txns_m5 * buy_ratio)
        txns_m5_sells = max(0, txns_m5 - txns_m5_buys)
        txns_h1_buys = int(txns_h1 * buy_ratio)
        txns_h1_sells = max(0, txns_h1 - txns_h1_buys)

        metadata = {
            "price_change_m5": price_change_m5,
            "price_change_h1": price_change_h1,
            "volume_m5": volume_m5,
            "volume_h1": volume_h1,
            "txns_m5_buys": txns_m5_buys,
            "txns_m5_sells": txns_m5_sells,
            "txns_h1_buys": txns_h1_buys,
            "txns_h1_sells": txns_h1_sells,
            "market_cap": series.market_cap,
        }
        metadata.update(onchain_meta.get(series.mint, {}))

        token = TokenInfo(
            mint=series.mint,
            symbol=series.symbol,
            age_sec=age_sec,
            liquidity_usd=series.liquidity_usd,
            volume_usd=volume_h1,
            price=point.close,
            source="birdeye",
            metadata=metadata,
        )
        tokens.append(_token_to_dict(token))
        prices[series.mint] = point.close

    return tokens, prices


def _index_for_ts(points: list[SeriesPoint], ts: int) -> int | None:
    for idx, point in enumerate(points):
        if point.ts == ts:
            return idx
    return None


def _window_sum(points: list[SeriesPoint], idx: int, steps: int) -> float:
    start = max(0, idx - steps + 1)
    return sum(point.volume for point in points[start : idx + 1])


def _window_change(points: list[SeriesPoint], idx: int, steps: int) -> float:
    if idx < steps:
        return 0.0
    prev = points[idx - steps].close
    if prev <= 0:
        return 0.0
    return ((points[idx].close - prev) / prev) * 100.0


def _window_trades(points: list[SeriesPoint], idx: int, steps: int, avg_trade_usd: float) -> int:
    start = max(0, idx - steps + 1)
    total_trades = sum(point.trades for point in points[start : idx + 1])
    if total_trades > 0:
        return total_trades
    total_volume = sum(point.volume for point in points[start : idx + 1])
    if avg_trade_usd <= 0:
        return 0
    return int(total_volume / avg_trade_usd)


def _token_to_dict(token: TokenInfo) -> dict:
    return {
        "mint": token.mint,
        "symbol": token.symbol,
        "age_sec": token.age_sec,
        "liquidity_usd": token.liquidity_usd,
        "volume_usd": token.volume_usd,
        "price": token.price,
        "source": token.source,
        "metadata": token.metadata,
    }


def _get_float(data: dict, keys: Iterable[str]) -> float:
    for key in keys:
        value = data.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


async def fetch_onchain_metadata(rpc_client: RPCClient, mint: str) -> dict[str, float | int | bool]:
    metadata: dict[str, float | int | bool] = {}
    mint_info = await rpc_client.get_mint_info(mint)
    if mint_info:
        metadata["decimals"] = mint_info.decimals
        metadata["mint_authority_active"] = mint_info.mint_authority_active
        metadata["freeze_authority_active"] = mint_info.freeze_authority_active

    supply = await rpc_client.get_token_supply(mint)
    largest = await rpc_client.get_token_largest_accounts(mint)
    supply_ui = _safe_float(
        (supply or {}).get("uiAmountString") or (supply or {}).get("uiAmount")
    )
    if supply_ui and largest:
        top_amounts = [_safe_float(account.get("uiAmount")) for account in largest[:10]]
        top_sum = sum(top_amounts)
        metadata["top10_holding"] = top_sum / supply_ui if supply_ui else 0.0
        metadata["dev_holding"] = top_amounts[0] / supply_ui if top_amounts else 0.0
    return metadata


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_mints(args: argparse.Namespace) -> list[str]:
    mints: list[str] = []
    if args.mints:
        for mint in args.mints.split(","):
            mint = mint.strip()
            if mint:
                mints.append(mint)
    if args.mints_file:
        content = Path(args.mints_file).read_text(encoding="utf-8")
        mints.extend([line.strip() for line in content.splitlines() if line.strip()])
    return list(dict.fromkeys(mints))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build backtest ticks using BirdEye.")
    parser.add_argument("--mints", help="Comma-separated token mint addresses.")
    parser.add_argument("--mints-file", help="Path to file with mint addresses.")
    parser.add_argument("--start", required=True, help="Start timestamp (epoch or ISO).")
    parser.add_argument("--end", required=True, help="End timestamp (epoch or ISO).")
    parser.add_argument("--interval", default=get_settings().BACKTEST_INTERVAL, help="1m,5m,1h,1d")
    parser.add_argument("--out", default=get_settings().BACKTEST_DATA_PATH, help="Output JSONL file.")
    parser.add_argument("--no-rpc", action="store_true", help="Skip RPC enrichment.")
    args = parser.parse_args()

    mints = load_mints(args)
    if not mints:
        raise SystemExit("Provide at least one mint via --mints or --mints-file.")
    start_ts = parse_ts(args.start)
    end_ts = parse_ts(args.end)
    asyncio.run(build_dataset(mints, start_ts, end_ts, args.interval, Path(args.out), not args.no_rpc))


if __name__ == "__main__":
    main()
