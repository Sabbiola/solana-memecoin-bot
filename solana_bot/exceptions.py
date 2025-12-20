"""
Custom exception classes for the Solana bot.

Provides typed exceptions for better error handling and debugging.
"""

class BotException(Exception):
    """Base exception for all bot-related errors."""
    
    def __init__(self, message: str, **context):
        super().__init__(message)
        self.message = message
        self.context = context
    
    def __str__(self):
        if self.context:
            ctx_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} [{ctx_str}]"
        return self.message


class SwapException(BotException):
    """Raised when swap operations fail."""
    pass


class ValidationException(BotException):
    """Raised when token validation fails."""
    pass


class RiskManagerException(BotException):
    """Raised when risk management operations fail."""
    pass


class DataCollectorException(BotException):
    """Raised when data collection operations fail."""
    pass


class ConfigurationException(BotException):
    """Raised when configuration is invalid."""
    pass


class WalletException(BotException):
    """Raised when wallet operations fail."""
    pass


class NetworkException(BotException):
    """Raised when network/RPC operations fail."""
    pass


class StateException(BotException):
    """Raised when state management operations fail."""
    pass
