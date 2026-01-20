from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from solana_bot.config import Settings
from solana_bot.core.rpc_cache import get_credit_limiter


@dataclass(frozen=True)
class MintInfo:
    decimals: int
    supply: int
    mint_authority_active: bool
    freeze_authority_active: bool


class RPCClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=settings.API_TIMEOUT_SEC)
        self.logger = logging.getLogger("solana_bot.rpc")
        self._limiter = get_credit_limiter()

    async def close(self) -> None:
        await self.client.aclose()

    async def _post(self, method: str, params: list[Any]) -> dict[str, Any] | None:
        if not self.settings.RPC_URL:
            return None
        
        # Check rate limit before making request
        if self._limiter.should_throttle():
            self.logger.warning("RPC credit limit reached, throttling %s request", method)
            return None
        
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            response = await self.client.post(self.settings.RPC_URL, json=payload)
            self._limiter.record(cost=1)  # Track usage after request
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            self.logger.debug("RPC %s failed: %s", method, exc)
            return None

        if isinstance(data, dict) and data.get("error"):
            self.logger.debug("RPC %s error: %s", method, data["error"])
            return None
        return data.get("result") if isinstance(data, dict) else None

    async def get_multiple_accounts(self, pubkeys: list[str]) -> list[dict]:
        result = await self._post("getMultipleAccounts", [pubkeys, {"encoding": "base64"}])
        if not result:
            return []
        return result.get("value") or []

    async def get_account_info(self, pubkey: str) -> dict[str, Any] | None:
        result = await self._post("getAccountInfo", [pubkey, {"encoding": "base64"}])
        if not result:
            return None
        return result.get("value")

    async def get_token_supply(self, mint: str) -> dict[str, Any] | None:
        result = await self._post("getTokenSupply", [mint])
        if not result:
            return None
        return result.get("value")

    async def get_token_largest_accounts(self, mint: str) -> list[dict[str, Any]]:
        result = await self._post("getTokenLargestAccounts", [mint])
        if not result:
            return []
        return result.get("value") or []

    async def get_mint_info(self, mint: str) -> MintInfo | None:
        account = await self.get_account_info(mint)
        if not account:
            return None
        data = account.get("data")
        if not data:
            return None
        encoded = data[0] if isinstance(data, list) else data
        try:
            raw = base64.b64decode(encoded)
        except Exception:
            return None
        return parse_mint_account(raw)


def parse_mint_account(raw: bytes) -> MintInfo | None:
    if len(raw) < 82:
        return None
    mint_auth_option = int.from_bytes(raw[0:4], "little")
    supply = int.from_bytes(raw[36:44], "little")
    decimals = raw[44]
    freeze_auth_option = int.from_bytes(raw[46:50], "little")
    return MintInfo(
        decimals=decimals,
        supply=supply,
        mint_authority_active=mint_auth_option == 1,
        freeze_authority_active=freeze_auth_option == 1,
    )
