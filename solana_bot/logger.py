"""
Structured logging configuration for the Solana bot.

Provides JSON-based logging with correlation IDs and proper formatting.
"""

import logging
import logging.handlers
import json
import os
from datetime import datetime
from typing import Any, Dict
import uuid

# Create logs directory
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)


class StructuredFormatter(logging.Formatter):
    """
    Custom formatter that outputs structured JSON logs.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        # Base log structure
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # Add correlation ID if present
        if hasattr(record, 'correlation_id'):
            log_data['correlation_id'] = record.correlation_id
        
        # Add extra fields
        if hasattr(record, 'extra_data'):
            log_data.update(record.extra_data)
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)


class HumanReadableFormatter(logging.Formatter):
    """
    Formatter for console output (human-readable).
    """
    
    COLOR_CODES = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'
    }
    
    def format(self, record: logging.LogRecord) -> str:
        # Color for level
        color = self.COLOR_CODES.get(record.levelname, self.COLOR_CODES['RESET'])
        reset = self.COLOR_CODES['RESET']
        
        # Format timestamp
        timestamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
        
        # Base format
        msg = f"{color}[{timestamp}] [{record.levelname:8s}]{reset} {record.getMessage()}"
        
        # Add extra context if present
        if hasattr(record, 'extra_data') and record.extra_data:
            context = " | ".join(f"{k}={v}" for k, v in record.extra_data.items())
            msg += f" ({context})"
        
        # Add exception if present
        if record.exc_info:
            msg += f"\n{self.formatException(record.exc_info)}"
        
        return msg


def setup_logging(level: str = "INFO", enable_console: bool = True, enable_file: bool = True):
    """
    Configure logging system with both file and console handlers.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        enable_console: Enable console output
        enable_file: Enable file output
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Console handler (human-readable)
    if enable_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(HumanReadableFormatter())
        console_handler.setLevel(logging.INFO)
        root_logger.addHandler(console_handler)
    
    # File handlers (structured JSON)
    if enable_file:
        # Main log file with rotation
        main_handler = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "bot.log"),
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5
        )
        main_handler.setFormatter(StructuredFormatter())
        main_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(main_handler)
        
        # Error-only log file
        error_handler = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "errors.log"),
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3
        )
        error_handler.setFormatter(StructuredFormatter())
        error_handler.setLevel(logging.ERROR)
        root_logger.addHandler(error_handler)
        
        # Trade-specific log file
        trade_handler = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "trades.log"),
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=10
        )
        trade_handler.setFormatter(StructuredFormatter())
        trade_handler.addFilter(lambda record: hasattr(record, 'trade_event'))
        root_logger.addHandler(trade_handler)
        
    # Quiet noisy libraries
    logging.getLogger("solana").setLevel(logging.WARNING)
    logging.getLogger("solders").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


class CorrelationLogger:
    """
    Logger wrapper that adds correlation IDs to all log entries.
    
    Usage:
        logger = CorrelationLogger(__name__)
        with logger.correlation_context():
            logger.info("Processing trade", mint="ABC123")
    """
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.correlation_id = None
    
    def _log(self, level: int, msg: str, **kwargs):
        """Internal log method that adds correlation ID and extra data."""
        extra_dict = kwargs.copy()
        
        if self.correlation_id:
            extra_dict['correlation_id'] = self.correlation_id
        
        # Standard logging with extra parameter
        if extra_dict:
            self.logger.log(level, msg, extra={'extra_data': extra_dict})
        else:
            self.logger.log(level, msg)
    
    def debug(self, msg: str, **extra):
        self._log(logging.DEBUG, msg, **extra)
    
    def info(self, msg: str, **extra):
        self._log(logging.INFO, msg, **extra)
    
    def warning(self, msg: str, **extra):
        self._log(logging.WARNING, msg, **extra)
    
    def error(self, msg: str, **extra):
        self._log(logging.ERROR, msg, **extra)
    
    def critical(self, msg: str, **extra):
        self._log(logging.CRITICAL, msg, **extra)
    
    def correlation_context(self):
        """Context manager that generates a correlation ID for all logs within."""
        class CorrelationContext:
            def __init__(context_self, parent):
                context_self.parent = parent
                context_self.old_id = None
            
            def __enter__(context_self):
                context_self.old_id = context_self.parent.correlation_id
                context_self.parent.correlation_id = str(uuid.uuid4())[:8]
                return context_self.parent
            
            def __exit__(context_self, *args):
                context_self.parent.correlation_id = context_self.old_id
        
        return CorrelationContext(self)


class TradeLogger:
    """
    Specialized logger for trade events.
    
    Logs are structured for easy analytics parsing.
    
    Usage:
        trade_logger = TradeLogger()
        trade_logger.log_buy(mint="ABC", amount_sol=0.1, signature="xyz...")
        trade_logger.log_sell(mint="ABC", pnl_pct=15.5, reason="TRAILING_STOP")
    """
    
    def __init__(self):
        self.logger = logging.getLogger("trades")
        self._ensure_trade_handler()
    
    def _ensure_trade_handler(self):
        """Ensure trades.log handler exists"""
        if not os.path.exists(LOG_DIR):
            os.makedirs(LOG_DIR, exist_ok=True)
        
        # Check if handler exists
        for handler in self.logger.handlers:
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                if "trades.log" in handler.baseFilename:
                    return
        
        # Add handler
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "trades.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=10
        )
        handler.setFormatter(StructuredFormatter())
        self.logger.addHandler(handler)
    
    def log_buy(
        self,
        mint: str,
        amount_sol: float,
        signature: str,
        wallet_name: str = "",
        dex: str = "",
        profile: str = "",
        token_amount: int = 0
    ):
        """Log buy trade event"""
        self.logger.info("BUY", extra={'extra_data': {
            'trade_event': True,
            'event_type': 'BUY',
            'mint': mint,
            'amount_sol': amount_sol,
            'signature': signature,
            'wallet_name': wallet_name,
            'dex': dex,
            'profile': profile,
            'token_amount': token_amount,
            'timestamp': datetime.now().isoformat()
        }})
    
    def log_sell(
        self,
        mint: str,
        amount_sol: float,
        signature: str,
        reason: str,
        pnl_sol: float = 0.0,
        pnl_pct: float = 0.0,
        hold_time_seconds: float = 0.0,
        token_amount: int = 0
    ):
        """Log sell trade event"""
        self.logger.info("SELL", extra={'extra_data': {
            'trade_event': True,
            'event_type': 'SELL',
            'mint': mint,
            'amount_sol': amount_sol,
            'signature': signature,
            'reason': reason,
            'pnl_sol': pnl_sol,
            'pnl_pct': pnl_pct,
            'hold_time_seconds': hold_time_seconds,
            'token_amount': token_amount,
            'timestamp': datetime.now().isoformat()
        }})
    
    def log_position_update(
        self,
        mint: str,
        current_value: float,
        entry_sol: float,
        pnl_pct: float,
        action: str = "HOLD"
    ):
        """Log position monitoring update"""
        self.logger.debug("POSITION_UPDATE", extra={'extra_data': {
            'trade_event': True,
            'event_type': 'POSITION_UPDATE',
            'mint': mint,
            'current_value': current_value,
            'entry_sol': entry_sol,
            'pnl_pct': pnl_pct,
            'action': action,
            'timestamp': datetime.now().isoformat()
        }})


class PerformanceLogger:
    """
    Logger for performance metrics and timing.
    
    Usage:
        perf = PerformanceLogger()
        with perf.measure("rpc_call"):
            await client.get_balance()
        # Logs: "rpc_call completed in 0.123s"
    """
    
    def __init__(self):
        self.logger = logging.getLogger("performance")
        self._metrics: Dict[str, list] = {}
    
    def measure(self, operation: str):
        """Context manager to measure operation time"""
        import time
        
        class Timer:
            def __init__(timer_self, perf_logger, op_name):
                timer_self.perf = perf_logger
                timer_self.op = op_name
                timer_self.start = None
            
            def __enter__(timer_self):
                timer_self.start = time.time()
                return timer_self
            
            def __exit__(timer_self, *args):
                elapsed = time.time() - timer_self.start
                timer_self.perf._record(timer_self.op, elapsed)
        
        return Timer(self, operation)
    
    def _record(self, operation: str, elapsed: float):
        """Record timing metric"""
        if operation not in self._metrics:
            self._metrics[operation] = []
        
        self._metrics[operation].append(elapsed)
        
        # Log if slow (> 1s)
        if elapsed > 1.0:
            self.logger.warning(f"{operation} slow: {elapsed:.2f}s")
        else:
            self.logger.debug(f"{operation}: {elapsed:.3f}s")
    
    def get_stats(self, operation: str) -> Dict[str, float]:
        """Get statistics for an operation"""
        times = self._metrics.get(operation, [])
        
        if not times:
            return {}
        
        return {
            'count': len(times),
            'total': sum(times),
            'avg': sum(times) / len(times),
            'min': min(times),
            'max': max(times)
        }
    
    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get all operation statistics"""
        return {op: self.get_stats(op) for op in self._metrics}


class MetricsCollector:
    """
    Collect and aggregate metrics for analytics.
    
    Usage:
        metrics = MetricsCollector()
        metrics.increment("buys_total")
        metrics.record("rpc_latency", 0.123)
        metrics.get_summary()
    """
    
    def __init__(self):
        self._counters: Dict[str, int] = {}
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, list] = {}
    
    def increment(self, name: str, value: int = 1):
        """Increment counter"""
        self._counters[name] = self._counters.get(name, 0) + value
    
    def set_gauge(self, name: str, value: float):
        """Set gauge value"""
        self._gauges[name] = value
    
    def record(self, name: str, value: float):
        """Record histogram value"""
        if name not in self._histograms:
            self._histograms[name] = []
        self._histograms[name].append(value)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get metrics summary"""
        summary = {
            'counters': self._counters.copy(),
            'gauges': self._gauges.copy(),
            'histograms': {}
        }
        
        for name, values in self._histograms.items():
            if values:
                summary['histograms'][name] = {
                    'count': len(values),
                    'sum': sum(values),
                    'avg': sum(values) / len(values),
                    'min': min(values),
                    'max': max(values)
                }
        
        return summary
    
    def reset(self):
        """Reset all metrics"""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()


# Global instances
trade_logger = TradeLogger()
perf_logger = PerformanceLogger()
metrics = MetricsCollector()


# Initialize logging on module import
setup_logging()
