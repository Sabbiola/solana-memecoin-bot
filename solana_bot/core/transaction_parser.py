"""
Transaction Parser for Solana DEX trades

Parses WebSocket transaction logs to extract trade signals.
"""

import re
import logging
from typing import Optional, List
from solana_bot.models.trade_signal import TradeSignal
from solana_bot.constants import (
    PUMP_PROGRAM,
    PUMPSWAP_PROGRAM,
    RAYDIUM_AMM_PROGRAM,
    JUPITER_PROGRAM
)

logger = logging.getLogger(__name__)


class TransactionParser:
    """
    Parse Solana transaction logs to extract trading signals.
    
    Supports:
    - Pump.fun bonding curve
    - PumpSwap AMM
    - Raydium swaps
    - Jupiter aggregator
    """
    
    # Program ID to DEX name mapping (as strings for log matching)
    DEX_PROGRAMS = {
        str(PUMP_PROGRAM): "BONDING_CURVE",
        str(PUMPSWAP_PROGRAM): "PUMPSWAP",
        str(RAYDIUM_AMM_PROGRAM): "RAYDIUM",
        str(JUPITER_PROGRAM): "JUPITER"
    }
    
    def parse_transaction(self, message: dict) -> Optional[TradeSignal]:
        """
        Parse a WebSocket transaction notification.
        
        Args:
            message: WebSocket message (logsNotification)
        
        Returns:
            TradeSignal if trade detected, None otherwise
        """
        try:
            # Extract params from notification
            params = message.get("params", {})
            result = params.get("result", {})
            
            if not result:
                return None
            
            # Get transaction signature
            signature = result.get("signature", "")
            
            # Get logs
            value = result.get("value", {})
            logs = value.get("logs", [])
            
            if not logs:
                return None
            
            # Get context
            context = result.get("context", {})
            slot = context.get("slot", 0)
            
            # Identify DEX from logs
            dex = self._identify_dex(logs)
            if not dex:
                logger.debug(f"No supported DEX found in transaction {signature[:16]}")
                return None
            
            # Extract trade info based on DEX
            if dex == "BONDING_CURVE":
                return self._parse_bonding_curve(logs, signature, slot)
            elif dex == "PUMPSWAP":
                return self._parse_pumpswap(logs, signature, slot)
            elif dex == "RAYDIUM":
                return self._parse_raydium(logs, signature, slot)
            elif dex == "JUPITER":
                return self._parse_jupiter(logs, signature, slot)
            
            return None
            
        except Exception as e:
            logger.error(f"Error parsing transaction: {e}", exc_info=True)
            return None
    
    def _identify_dex(self, logs: List[str]) -> Optional[str]:
        """Identify which DEX was used from logs."""
        log_text = " ".join(logs)
        
        for program_id, dex_name in self.DEX_PROGRAMS.items():
            if program_id in log_text:
                return dex_name
        
        return None
    
    def _parse_bonding_curve(
        self,
        logs: List[str],
        signature: str,
        slot: int
    ) -> Optional[TradeSignal]:
        """Parse Pump.fun bonding curve transaction."""
        try:
            # Look for buy/sell patterns in logs
            log_text = " ".join(logs)
            
            # Pump.fun specific patterns
            # Example: "Program log: Instruction: Buy"
            # Example: "Program log: Instruction: Sell"
            
            action = None
            if "Instruction: Buy" in log_text or "buy" in log_text.lower():
                action = "BUY"
            elif "Instruction: Sell" in log_text or "sell" in log_text.lower():
                action = "SELL"
            
            if not action:
                return None
            
            # Extract token mint (usually in account mentions)
            # Pattern: accounts like "mint: [address]"
            mint = self._extract_mint_from_logs(logs)
            if not mint:
                return None
            
            # Extract SOL amount (harder - need to parse transfer logs)
            amount_sol = self._extract_sol_amount(logs)
            
            # Extract wallet (first signer usually)
            wallet = self._extract_wallet(logs)
            if not wallet:
                wallet = "UNKNOWN"
            
            return TradeSignal(
                wallet=wallet,
                mint=mint,
                action=action,
                amount_sol=amount_sol,
                dex="BONDING_CURVE",
                signature=signature,
                slot=slot
            )
            
        except Exception as e:
            logger.error(f"Error parsing bonding curve: {e}")
            return None
    
    def _parse_pumpswap(
        self,
        logs: List[str],
        signature: str,
        slot: int
    ) -> Optional[TradeSignal]:
        """Parse PumpSwap AMM transaction."""
        # Similar logic to bonding curve
        return self._parse_bonding_curve(logs, signature, slot)
    
    def _parse_raydium(
        self,
        logs: List[str],
        signature: str,
        slot: int
    ) -> Optional[TradeSignal]:
        """Parse Raydium swap transaction."""
        try:
            log_text = " ".join(logs)
            
            # Raydium swap patterns
            action = "BUY" if "swap" in log_text.lower() else None
            
            if not action:
                return None
            
            mint = self._extract_mint_from_logs(logs)
            if not mint:
                return None
            
            amount_sol = self._extract_sol_amount(logs)
            wallet = self._extract_wallet(logs) or "UNKNOWN"
            
            return TradeSignal(
                wallet=wallet,
                mint=mint,
                action=action,
                amount_sol=amount_sol,
                dex="RAYDIUM",
                signature=signature,
                slot=slot
            )
            
        except Exception as e:
            logger.error(f"Error parsing Raydium: {e}")
            return None
    
    def _parse_jupiter(
        self,
        logs: List[str],
        signature: str,
        slot: int
    ) -> Optional[TradeSignal]:
        """Parse Jupiter aggregator transaction."""
        try:
            log_text = " ".join(logs)
            
            # Jupiter route patterns
            action = "BUY" if "route" in log_text.lower() else None
            
            if not action:
                return None
            
            mint = self._extract_mint_from_logs(logs)
            if not mint:
                return None
            
            amount_sol = self._extract_sol_amount(logs)
            wallet = self._extract_wallet(logs) or "UNKNOWN"
            
            return TradeSignal(
                wallet=wallet,
                mint=mint,
                action=action,
                amount_sol=amount_sol,
                dex="JUPITER",
                signature=signature,
                slot=slot
            )
            
        except Exception as e:
            logger.error(f"Error parsing Jupiter: {e}")
            return None
    
    # -------------------------------------------------------------------------
    # Helper methods
    # -------------------------------------------------------------------------
    
    def _extract_mint_from_logs(self, logs: List[str]) -> Optional[str]:
        """Extract token mint address from logs."""
        # Look for base58 addresses (common mint pattern)
        # Solana addresses are 32-44 characters, base58
        mint_pattern = r'[1-9A-HJ-NP-Za-km-z]{32,44}'
        
        for log in logs:
            # Skip common system addresses
            if any(skip in log for skip in ["11111111111111", "TokenkegQfeZyiNwAJbN"]):
                continue
            
            matches = re.findall(mint_pattern, log)
            if matches:
                # Return first non-system address
                for match in matches:
                    if len(match) >= 32 and not match.startswith("1111"):
                        return match
        
        return None
    
    def _extract_sol_amount(self, logs: List[str]) -> float:
        """Extract SOL amount from transfer logs."""
        # Pattern: "Transfer: X lamports" or similar
        for log in logs:
            # Look for lamports transfers
            if "lamports" in log.lower():
                # Extract number
                numbers = re.findall(r'\d+', log)
                if numbers:
                    lamports = int(numbers[0])
                    # Convert lamports to SOL
                    return lamports / 1e9
        
        # Default estimate if not found
        return 0.01
    
    def _extract_wallet(self, logs: List[str]) -> Optional[str]:
        """Extract wallet address (signer) from logs."""
        # Look for "Instruction: [wallet]" or similar patterns
        for log in logs:
            if "invoke" in log.lower() and "[" in log:
                # Extract address in brackets
                match = re.search(r'\[(\w+)\]', log)
                if match:
                    return match.group(1)
        
        return None
