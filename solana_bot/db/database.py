"""
Database Manager for Trading Bot

Handles all database operations for positions, trades, and stats.
"""

import sqlite3
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from pathlib import Path

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    SQLite database manager for bot data persistence.
    
    Features:
    - Position tracking (open/closed)
    - Trade history
    - Daily performance stats
    - Thread-safe operations
    """
    
    def __init__(self, db_path: str = "bot_data.db"):
        """
        Initialize database manager.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_db()
        logger.info(f"Database initialized: {db_path}")
    
    def _init_db(self):
        """Initialize database with schema"""
        # Read schema file
        schema_path = Path(__file__).parent / "schema.sql"
        
        if not schema_path.exists():
            logger.error(f"Schema file not found: {schema_path}")
            raise FileNotFoundError(f"Schema file not found: {schema_path}")
        
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
        
        # Execute schema
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(schema_sql)
            
            # MIGRATION: Add decimals column if it doesn't exist (for existing databases)
            try:
                conn.execute("ALTER TABLE positions ADD COLUMN decimals INTEGER DEFAULT 6")
                logger.info("✅ Added decimals column to existing database")
            except sqlite3.OperationalError:
                # Column already exists, ignore
                pass
        
        logger.info("Database schema created/verified")
    
    # =========================================================================
    # Position Operations
    # =========================================================================
    
    def save_position(
        self,
        mint: str,
        entry_sol: float,
        entry_signature: str,
        entry_price: Optional[float] = None,
        token_amount: Optional[int] = None,
        dex: Optional[str] = None,
        risk_level: Optional[str] = None,
        profile_name: Optional[str] = None,
        decimals: int = 6  # Token decimals (default 6)
    ) -> int:
        """
        Save new position to database.
        
        Args:
            mint: Token mint address
            entry_sol: SOL amount invested
            entry_signature: Transaction signature
            entry_price: Entry price (optional)
            token_amount: Token amount received (optional)
            dex: DEX used (optional)
            risk_level: Risk level from rugcheck (optional)
            profile_name: Risk profile name (optional)
            decimals: Token decimals (default 6)
        
        Returns:
            Position ID
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO positions (
                    mint, entry_sol, entry_signature, entry_price, 
                    token_amount, dex, risk_level, profile_name, decimals
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (mint, entry_sol, entry_signature, entry_price,
                 token_amount, dex, risk_level, profile_name, decimals)
            )
            position_id = cursor.lastrowid
        
        logger.info(f"Position saved: {mint[:20]}... decimals={decimals} (ID: {position_id})")
        return position_id
    
    def update_position(
        self,
        mint: str,
        current_value: Optional[float] = None,
        unrealized_pnl: Optional[float] = None,
        token_amount: Optional[int] = None
    ):
        """
        Update position with current values.
        
        Args:
            mint: Token mint address
            current_value: Current position value in SOL
            unrealized_pnl: Unrealized profit/loss
            token_amount: Current token amount
        """
        updates = []
        params = []
        
        if current_value is not None:
            updates.append("current_value = ?")
            params.append(current_value)
        
        if unrealized_pnl is not None:
            updates.append("unrealized_pnl = ?")
            params.append(unrealized_pnl)
        
        if token_amount is not None:
            updates.append("token_amount = ?")
            params.append(token_amount)
        
        if not updates:
            return
        
        params.append(mint)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE positions SET {', '.join(updates)} WHERE mint = ?",
                params
            )
    
    def close_position(
        self,
        mint: str,
        close_signature: str,
        close_reason: str = "MANUAL"
    ):
        """
        Close position.
        
        Args:
            mint: Token mint address
            close_signature: Sell transaction signature
            close_reason: Reason for close (TAKE_PROFIT, STOP_LOSS, MANUAL, etc.)
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE positions 
                SET status = 'CLOSED', 
                    closed_at = ?,
                    close_signature = ?,
                    close_reason = ?
                WHERE mint = ?
                """,
                (datetime.now(), close_signature, close_reason, mint)
            )
        
        logger.info(f"Position closed: {mint[:20]}... (Reason: {close_reason})")
    
    def get_open_positions(self) -> List[Dict[str, Any]]:
        """
        Get all open positions.
        
        Returns:
            List of position dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM positions WHERE status = 'OPEN' ORDER BY created_at DESC"
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_position(self, mint: str) -> Optional[Dict[str, Any]]:
        """
        Get position by mint address.
        
        Args:
            mint: Token mint address
        
        Returns:
            Position dictionary or None
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM positions WHERE mint = ?",
                (mint,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    # =========================================================================
    # Trade Operations
    # =========================================================================
    
    def save_trade(
        self,
        action: str,
        mint: str,
        amount_sol: float,
        signature: str,
        position_id: Optional[int] = None,
        token_amount: Optional[int] = None,
        price: Optional[float] = None,
        dex: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None
    ) -> int:
        """
        Save trade to database.
        
        Args:
            action: BUY or SELL
            mint: Token mint address
            amount_sol: SOL amount
            signature: Transaction signature
            position_id: Associated position ID (optional)
            token_amount: Token amount (optional)
            price: Price (optional)
            dex: DEX used (optional)
            success: Trade success status
            error_message: Error message if failed (optional)
        
        Returns:
            Trade ID
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (
                    position_id, action, mint, amount_sol, token_amount,
                    price, signature, dex, success, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (position_id, action, mint, amount_sol, token_amount,
                 price, signature, dex, success, error_message)
            )
            trade_id = cursor.lastrowid
        
        logger.info(f"Trade saved: {action} {mint[:20]}... (ID: {trade_id})")
        return trade_id
    
    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get recent trades.
        
        Args:
            limit: Maximum number of trades to return
        
        Returns:
            List of trade dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # Stats Operations
    # =========================================================================
    
    def update_daily_stats(
        self,
        total_trades: int = 0,
        successful_trades: int = 0,
        failed_trades: int = 0,
        volume_sol: float = 0,
        realized_pnl: float = 0
    ):
        """
        Update daily statistics.
        
        Args:
            total_trades: Number of trades today
            successful_trades: Number of successful trades
            failed_trades: Number of failed trades
            volume_sol: Trading volume in SOL
            realized_pnl: Realized profit/loss
        """
        today = date.today().isoformat()
        win_rate = (successful_trades / total_trades * 100) if total_trades > 0 else 0
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO daily_stats (
                    date, total_trades, successful_trades, failed_trades,
                    total_volume_sol, realized_pnl, win_rate
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_trades = total_trades + ?,
                    successful_trades = successful_trades + ?,
                    failed_trades = failed_trades + ?,
                    total_volume_sol = total_volume_sol + ?,
                    realized_pnl = realized_pnl + ?,
                    win_rate = (successful_trades * 100.0 / total_trades)
                """,
                (today, total_trades, successful_trades, failed_trades,
                 volume_sol, realized_pnl, win_rate,
                 total_trades, successful_trades, failed_trades,
                 volume_sol, realized_pnl)
            )
    
    def get_stats(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Get daily stats for last N days.
        
        Args:
            days: Number of days to retrieve
        
        Returns:
            List of daily stat dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM daily_stats 
                ORDER BY date DESC 
                LIMIT ?
                """,
                (days,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # Utility Operations
    # =========================================================================
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get overall bot summary.
        
        Returns:
            Summary dictionary with key metrics
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Open positions
            open_count = conn.execute(
                "SELECT COUNT(*) as count FROM positions WHERE status = 'OPEN'"
            ).fetchone()['count']
            
            # Total trades
            total_trades = conn.execute(
                "SELECT COUNT(*) as count FROM trades"
            ).fetchone()['count']
            
            # Win rate
            successful = conn.execute(
                "SELECT COUNT(*) as count FROM trades WHERE success = 1"
            ).fetchone()['count']
            
            win_rate = (successful / total_trades * 100) if total_trades > 0 else 0
            
            # Total volume
            volume = conn.execute(
                "SELECT SUM(amount_sol) as total FROM trades"
            ).fetchone()['total'] or 0
            
            return {
                "open_positions": open_count,
                "total_trades": total_trades,
                "successful_trades": successful,
                "win_rate": win_rate,
                "total_volume_sol": volume
            }
    
    def close(self):
        """Close database connection (no-op for context manager pattern)"""
        # SQLite connections are managed via context managers
        # This is for API compatibility
        pass
