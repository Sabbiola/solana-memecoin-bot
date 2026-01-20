from __future__ import annotations


class JitoClient:
    async def submit_bundle(self, txs: list[str]) -> str:
        raise NotImplementedError
