"""PumpPortal WebSocket client for real-time Pump.fun token discovery."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Awaitable

import websockets
from websockets.exceptions import ConnectionClosed

from solana_bot.config import Settings


@dataclass
class NewTokenEvent:
    """New token creation event from Pump.fun."""
    mint: str
    name: str
    symbol: str
    uri: str
    creator: str
    bonding_curve: str
    timestamp: int


class PumpPortalClient:
    """WebSocket client for PumpPortal.fun real-time data.
    
    Streams new Pump.fun token creation events in real-time.
    No API key required - free public WebSocket.
    """

    WS_URL = "wss://pumpportal.fun/api/data"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.pumpportal")
        self._running = False
        self._ws = None
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._reconnect_delay = 1.0
        # Price cache: mint -> (price_sol, price_usd, timestamp)
        self._prices: dict[str, tuple[float, float, float]] = {}
        self._subscribed_mints: set[str] = set()
        self._on_price_update: Callable[[str, float], None] | None = None

    async def start(self, on_token: Callable[[NewTokenEvent], Awaitable[None]] | None = None) -> None:
        """Start listening for new token events."""
        self._running = True
        self.logger.info("PumpPortal WebSocket starting...")
        
        while self._running:
            try:
                async with websockets.connect(self.WS_URL) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1.0  # Reset on successful connect
                    self.logger.info("PumpPortal WebSocket connected")
                    
                    # 1. Resubscribe to existing trades (Critical for restarts)
                    if self._subscribed_mints:
                        mints = list(self._subscribed_mints)
                        await ws.send(json.dumps({
                            "method": "subscribeTokenTrade",
                            "keys": mints
                        }))
                        self.logger.info("Resubscribed to trades for %d tokens", len(mints))

                    # 2. Subscribe to new token events
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    self.logger.info("✅ PumpPortal: Subscribed to new tokens stream")
                    
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            tx_type = data.get("txType")
                            
                            # Handle New Token
                            if tx_type == "create":
                                event = self._parse_new_token(data)
                                if event:
                                    self.logger.info("🆕 PUMPPORTAL NEW: %s (%s) mint=%s", event.symbol, event.name, event.mint[:12])
                                    try:
                                        self._queue.put_nowait(event.mint)
                                    except asyncio.QueueFull:
                                        self._queue.get_nowait()
                                        self._queue.put_nowait(event.mint)
                                    except asyncio.QueueEmpty:
                                        pass
                                    
                                    if on_token:
                                        await on_token(event)

                            # Handle Trade
                            elif tx_type == "trade":
                                self._parse_trade(data)
                            
                        except json.JSONDecodeError:
                            pass
                            
            except ConnectionClosed as e:
                self.logger.warning("PumpPortal WebSocket closed: %s", e)
            except Exception as e:
                self.logger.error("PumpPortal error: %s", e)
            
            if self._running:
                self.logger.info("PumpPortal reconnecting in %.1fs...", self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)

    def _parse_new_token(self, data: dict) -> NewTokenEvent | None:
        """Parse incoming WebSocket message into NewTokenEvent."""
        mint = data.get("mint")
        if not mint:
            return None
        
        return NewTokenEvent(
            mint=str(mint),
            name=str(data.get("name", "")),
            symbol=str(data.get("symbol", "")),
            uri=str(data.get("uri", "")),
            creator=str(data.get("traderPublicKey", "")),
            bonding_curve=str(data.get("bondingCurveKey", "")),
            timestamp=int(time.time()),
        )

    def _parse_trade(self, data: dict) -> None:
        """Parse trade event to extract price."""
        mint = data.get("mint")
        if not mint:
            return
        
        sol_amount = float(data.get("solAmount", 0)) / 1e9
        token_amount = float(data.get("tokenAmount", 0))
        
        if token_amount > 0 and sol_amount > 0:
            price_sol = sol_amount / token_amount
            price_usd = price_sol * 200.0 # Approx constant for safety
            
            self._prices[mint] = (price_sol, price_usd, time.time())
            
            # Notify external callback
            if self._on_price_update:
                try:
                    self._on_price_update(mint, price_usd)
                except Exception as e:
                    self.logger.error("Callback error: %s", e)

    async def subscribe_trades(self, mint: str) -> None:
        """Explicitly subscribe to trades for a specific mint."""
        if mint in self._subscribed_mints:
            self.logger.debug("Already subscribed to trades for %s", mint[:12])
            return
            
        self._subscribed_mints.add(mint)
        
        if self._ws and self._running:
            try:
                await self._ws.send(json.dumps({
                    "method": "subscribeTokenTrade",
                    "keys": [mint]
                }))
                self.logger.info("✅ PumpPortal: Subscribed to trades for %s", mint[:12])
            except Exception as e:
                self.logger.error("Failed to subscribe to %s: %s", mint[:12], e)
        else:
            # WebSocket not connected yet - will resubscribe on reconnect (line 63-69)
            self.logger.warning("PumpPortal WS not connected, queued subscription for %s (will subscribe on reconnect)", mint[:12])

    def set_price_callback(self, callback) -> None:
        """Set callback to be called when price updates are received."""
        self._on_price_update = callback

    def get_price(self, mint: str) -> float | None:
        """Get latest price in USD for a token."""
        cached = self._prices.get(mint)
        if cached:
            return cached[1]  # Return USD price
        return None

    def get_price_sol(self, mint: str) -> float | None:
        """Get latest price in SOL for a token."""
        cached = self._prices.get(mint)
        if cached:
            return cached[0]
        return None

    def get_pending_mints(self) -> list[str]:
        """Get all pending mint addresses from queue (non-blocking)."""
        mints = []
        while True:
            try:
                mints.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return mints

    async def stop(self) -> None:
        """Stop the WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        self.logger.info("PumpPortal WebSocket stopped")

