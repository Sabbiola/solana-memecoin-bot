from __future__ import annotations

import logging
from pathlib import Path

from solana_bot.config import Settings


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for important bot events."""
    
    # Readable "Terminal" Palette (High Contrast)
    GREY = "\x1b[90m"            # Bright Black (Grey)
    NEON_GREEN = "\x1b[92m"      # Bright Green (Standard)
    NEON_CYAN = "\x1b[96m"       # Bright Cyan (Standard)
    NEON_RED = "\x1b[91m"        # Bright Red (Standard)
    MAGENTA = "\x1b[95m"         # Bright Magenta (Standard)
    YELLOW = "\x1b[93m"          # Bright Yellow
    WHITE = "\x1b[97m"           # Bright White
    RESET = "\x1b[0m"

    # Simplified timestamp (HH:MM:ss)
    FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
    DATE_FMT = "%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        # Base color based on level
        if record.levelno >= logging.ERROR:
            color = self.NEON_RED
        elif record.levelno >= logging.WARNING:
            color = self.YELLOW
        else:
            color = self.GREY

        # Keyword highlighting (override base color)
        msg = str(record.msg)
        
        # 1. POSITIVE / ACTION
        if "PASS" in msg or "ENTRY" in msg or "BUY" in msg:
            color = self.NEON_GREEN
        # 2. DISCOVERY
        elif "NEW TOKEN" in msg or "🔭" in msg:
            color = self.NEON_CYAN
        # 3. EXIT / PROFIT
        elif "SELL" in msg or "EXIT" in msg or "💰" in msg or "🚀" in msg:
            color = self.MAGENTA
        # 4. REJECTION / NOISE (Dim them down)
        elif "REJECT" in msg or "🚫" in msg or "🔇" in msg or "👴" in msg or "🤏" in msg:
            color = self.GREY

        # Format the actual message
        # We manually format time to use DATE_FMT
        record.asctime = self.formatTime(record, self.DATE_FMT)
        
        formatter = logging.Formatter(f"{color}%(asctime)s %(message)s{self.RESET}", datefmt=self.DATE_FMT)
        return formatter.format(record)


def setup_logging(settings: Settings) -> None:
    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "bot.log"

    # File Handler (Plain text, no colors)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    # Console Handler (Colored)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter())

    # Root Logger
    logger = logging.getLogger()
    logger.setLevel(settings.LOG_LEVEL)
    
    # Remove existing handlers to avoid duplicates on reload
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Silence noisy HTTP libraries - only show WARNING and above
    for noisy in ("httpx", "httpcore", "hpack", "asyncio", "urllib3", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

