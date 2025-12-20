-- Database schema for Solana Trading Bot
-- Tracks positions, trades, and bot performance

-- Positions table
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL UNIQUE,
    entry_sol REAL NOT NULL,
    entry_signature TEXT,
    entry_price REAL,
    token_amount INTEGER,
    decimals INTEGER DEFAULT 6,  -- Token decimals for accurate valuation
    current_value REAL,
    unrealized_pnl REAL,
    status TEXT DEFAULT 'OPEN' CHECK(status IN ('OPEN', 'CLOSED', 'FAILED')),
    dex TEXT,
    risk_level TEXT,
    profile_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    close_signature TEXT,
    close_reason TEXT
);

-- Trades table (granular trade history)
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER,
    action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
    mint TEXT NOT NULL,
    amount_sol REAL NOT NULL,
    token_amount INTEGER,
    price REAL,
    signature TEXT,
    dex TEXT,
    success BOOLEAN DEFAULT 1,
    error_message TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (position_id) REFERENCES positions(id)
);

-- Performance tracking
CREATE TABLE IF NOT EXISTS daily_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    total_trades INTEGER DEFAULT 0,
    successful_trades INTEGER DEFAULT 0,
    failed_trades INTEGER DEFAULT 0,
    total_volume_sol REAL DEFAULT 0,
    realized_pnl REAL DEFAULT 0,
    win_rate REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_positions_mint ON positions(mint);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_created ON positions(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_position ON trades(position_id);
CREATE INDEX IF NOT EXISTS idx_trades_mint ON trades(mint);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date);
