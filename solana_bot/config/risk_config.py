"""
Risk Configuration Manager

Provides configurable risk parameters via YAML/JSON file.
Hot-reload support for live configuration updates.
"""

import os
import json
import yaml
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from pathlib import Path
import threading
import time

logger = logging.getLogger(__name__)


@dataclass
class PositionLimits:
    """Position size limits"""
    max_position_sol: float = 0.1
    min_position_sol: float = 0.01
    max_positions: int = 5
    max_exposure_sol: float = 0.5  # Total capital at risk


@dataclass
class StopLossConfig:
    """Stop loss configuration"""
    hard_stop_pct: float = 50.0  # -50% = sell everything
    trailing_stop_pct: float = 20.0  # -20% from peak
    break_even_trigger_pct: float = 5.0  # +5% triggers break-even stop
    break_even_sell_pct: float = 50.0  # Sell 50% at break-even


@dataclass
class TakeProfitConfig:
    """Take profit configuration"""
    enabled: bool = False  # Disabled = moonbag only (no fixed TP)
    target_1_pct: float = 50.0  # +50%
    target_1_sell_pct: float = 25.0  # Sell 25% at target 1
    target_2_pct: float = 100.0  # +100%
    target_2_sell_pct: float = 50.0  # Sell 50% at target 2
    moon_bag_pct: float = 25.0  # Keep 25% for moon shot


@dataclass 
class TokenFilters:
    """Token filtering rules"""
    min_liquidity_usd: float = 1000.0
    max_risk_level: str = "HIGH"  # LOW, MEDIUM, HIGH, CRITICAL
    blocked_tokens: List[str] = field(default_factory=list)
    allowed_dexes: List[str] = field(default_factory=lambda: ["raydium", "orca", "jupiter"])
    max_market_cap_usd: float = 1_000_000.0  # Only small caps


@dataclass
class JitoConfig:
    """Jito MEV protection config"""
    enabled: bool = True
    region: str = "frankfurt"
    min_trade_sol_for_jito: float = 0.1
    default_tip_lamports: int = 10000
    high_priority_tip_lamports: int = 50000


@dataclass
class TelegramConfig:
    """Telegram notification config"""
    enabled: bool = True
    notify_buys: bool = True
    notify_sells: bool = True
    notify_stop_loss: bool = True
    notify_errors: bool = True
    chat_id: str = ""
    admin_id: str = ""


@dataclass
class RiskConfig:
    """Complete risk configuration"""
    # Version for compatibility
    version: str = "1.0"
    
    # Sub-configs
    position_limits: PositionLimits = field(default_factory=PositionLimits)
    stop_loss: StopLossConfig = field(default_factory=StopLossConfig)
    take_profit: TakeProfitConfig = field(default_factory=TakeProfitConfig)
    token_filters: TokenFilters = field(default_factory=TokenFilters)
    jito: JitoConfig = field(default_factory=JitoConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    
    # Global switches
    trading_enabled: bool = True
    paper_trading: bool = False  # Simulate trades without executing
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RiskConfig":
        """Create from dictionary"""
        return cls(
            version=data.get("version", "1.0"),
            position_limits=PositionLimits(**data.get("position_limits", {})),
            stop_loss=StopLossConfig(**data.get("stop_loss", {})),
            take_profit=TakeProfitConfig(**data.get("take_profit", {})),
            token_filters=TokenFilters(**data.get("token_filters", {})),
            jito=JitoConfig(**data.get("jito", {})),
            telegram=TelegramConfig(**data.get("telegram", {})),
            trading_enabled=data.get("trading_enabled", True),
            paper_trading=data.get("paper_trading", False)
        )


class RiskConfigManager:
    """
    Risk configuration manager with hot-reload support.
    
    Features:
    - Load from YAML or JSON
    - Save configuration
    - Hot-reload on file change
    - Validation
    - Default fallback
    
    Usage:
        config_manager = RiskConfigManager("config/risk.yaml")
        config = config_manager.get_config()
        
        # Access values
        max_pos = config.position_limits.max_position_sol
        
        # Update and save
        config.trading_enabled = False
        config_manager.save_config(config)
    """
    
    DEFAULT_CONFIG_PATH = "config/risk_config.yaml"
    
    def __init__(self, config_path: Optional[str] = None, auto_reload: bool = False):
        """
        Initialize config manager.
        
        Args:
            config_path: Path to config file
            auto_reload: Enable automatic reload on file change
        """
        self.config_path = Path(config_path or self.DEFAULT_CONFIG_PATH)
        self._config: Optional[RiskConfig] = None
        self._last_modified: float = 0
        self._auto_reload = auto_reload
        self._reload_thread: Optional[threading.Thread] = None
        self._running = False
        
        # Ensure config directory exists
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load or create config
        self._load_or_create()
        
        # Start auto-reload if enabled
        if auto_reload:
            self._start_auto_reload()
    
    def _load_or_create(self):
        """Load existing config or create default"""
        if self.config_path.exists():
            self._config = self._load_from_file()
            logger.info(f"Risk config loaded from {self.config_path}")
        else:
            self._config = RiskConfig()
            self.save_config(self._config)
            logger.info(f"Default risk config created at {self.config_path}")
    
    def _load_from_file(self) -> RiskConfig:
        """Load config from file"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                if self.config_path.suffix in ['.yaml', '.yml']:
                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)
            
            self._last_modified = self.config_path.stat().st_mtime
            return RiskConfig.from_dict(data or {})
        
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return RiskConfig()
    
    def save_config(self, config: Optional[RiskConfig] = None):
        """Save config to file"""
        config = config or self._config
        
        try:
            data = config.to_dict()
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                if self.config_path.suffix in ['.yaml', '.yml']:
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                else:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            
            self._last_modified = self.config_path.stat().st_mtime
            logger.info(f"Risk config saved to {self.config_path}")
        
        except Exception as e:
            logger.error(f"Error saving config: {e}")
    
    def get_config(self) -> RiskConfig:
        """Get current config"""
        if self._config is None:
            self._config = RiskConfig()
        return self._config
    
    def reload(self) -> bool:
        """Reload config from file"""
        if self.config_path.exists():
            new_config = self._load_from_file()
            self._config = new_config
            logger.info("Risk config reloaded")
            return True
        return False
    
    def validate(self) -> List[str]:
        """Validate current config, return list of errors"""
        errors = []
        config = self.get_config()
        
        # Position limits
        if config.position_limits.max_position_sol <= 0:
            errors.append("max_position_sol must be > 0")
        
        if config.position_limits.min_position_sol <= 0:
            errors.append("min_position_sol must be > 0")
        
        if config.position_limits.min_position_sol > config.position_limits.max_position_sol:
            errors.append("min_position_sol must be <= max_position_sol")
        
        # Stop loss
        if config.stop_loss.hard_stop_pct <= 0 or config.stop_loss.hard_stop_pct > 100:
            errors.append("hard_stop_pct must be between 0 and 100")
        
        if config.stop_loss.trailing_stop_pct <= 0 or config.stop_loss.trailing_stop_pct > 100:
            errors.append("trailing_stop_pct must be between 0 and 100")
        
        # Take profit
        if config.take_profit.target_1_pct <= 0:
            errors.append("target_1_pct must be > 0")
        
        return errors
    
    def _start_auto_reload(self):
        """Start auto-reload thread"""
        self._running = True
        self._reload_thread = threading.Thread(target=self._auto_reload_loop, daemon=True)
        self._reload_thread.start()
        logger.info("Auto-reload enabled for risk config")
    
    def _auto_reload_loop(self):
        """Auto-reload loop"""
        while self._running:
            try:
                if self.config_path.exists():
                    mtime = self.config_path.stat().st_mtime
                    if mtime > self._last_modified:
                        self.reload()
            except Exception as e:
                logger.debug(f"Auto-reload check error: {e}")
            
            time.sleep(5)  # Check every 5 seconds
    
    def stop(self):
        """Stop auto-reload"""
        self._running = False
        if self._reload_thread:
            self._reload_thread.join(timeout=1)


# Global config manager instance
_config_manager: Optional[RiskConfigManager] = None


def get_risk_config() -> RiskConfig:
    """Get global risk config"""
    global _config_manager
    if _config_manager is None:
        _config_manager = RiskConfigManager()
    return _config_manager.get_config()


def get_config_manager() -> RiskConfigManager:
    """Get global config manager"""
    global _config_manager
    if _config_manager is None:
        _config_manager = RiskConfigManager()
    return _config_manager
