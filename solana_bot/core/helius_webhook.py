from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from solana_bot.config import Settings


class HeliusWebhook:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.helius_webhook")
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._server:
            return
        self._loop = asyncio.get_running_loop()
        address = (self.settings.HELIUS_WEBHOOK_HOST, self.settings.HELIUS_WEBHOOK_PORT)
        handler = self._make_handler()
        self._server = ThreadingHTTPServer(address, handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.logger.info("Helius webhook listening on %s:%s", *address)

    async def stop(self) -> None:
        if not self._server:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self.logger.info("Helius webhook stopped")

    async def drain_mints(self) -> list[str]:
        mints: list[str] = []
        while True:
            try:
                mints.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return mints

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        settings = self.settings
        logger = self.logger
        loop = self._loop
        queue = self.queue

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
                if self.path != settings.HELIUS_WEBHOOK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length)
                if settings.HELIUS_WEBHOOK_SECRET:
                    signature = self.headers.get("X-Helius-Signature", "")
                    expected = hmac.new(
                        settings.HELIUS_WEBHOOK_SECRET.encode("utf-8"),
                        body,
                        hashlib.sha256,
                    ).hexdigest()
                    if not hmac.compare_digest(signature, expected):
                        self.send_response(401)
                        self.end_headers()
                        return

                try:
                    payload = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return

                mints = _extract_mints(payload)
                if mints and loop is not None:
                    for mint in mints:
                        loop.call_soon_threadsafe(queue.put_nowait, mint)
                logger.debug("Helius webhook received %d mints", len(mints))
                self.send_response(200)
                self.end_headers()

            def log_message(self, fmt: str, *args: Any) -> None:
                return

        return Handler


def _extract_mints(payload: Any) -> list[str]:
    items: list[Any]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("events") or payload.get("data") or payload.get("transactions") or [payload]
    else:
        return []

    mints: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("tokenAddress", "mint", "mintAddress"):
            value = item.get(key)
            if isinstance(value, str):
                mints.add(value)
        for key in ("tokenTransfers", "tokenBalanceChanges"):
            transfers = item.get(key)
            if isinstance(transfers, list):
                for transfer in transfers:
                    if isinstance(transfer, dict):
                        mint = transfer.get("mint")
                        if isinstance(mint, str):
                            mints.add(mint)
    return list(mints)
