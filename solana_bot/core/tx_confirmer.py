from __future__ import annotations


class TxConfirmer:
    async def confirm(self, signature: str) -> bool:
        raise NotImplementedError
