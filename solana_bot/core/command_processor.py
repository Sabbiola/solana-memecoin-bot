"""
Command Processor Module

Handles commands from various sources:
- Dashboard (web UI via JSON file)
- Telegram (via callbacks)
- API (future)

This keeps the main bot.py cleaner and provides a unified command interface.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Awaitable
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class CommandType(Enum):
    """Types of commands that can be processed."""
    SELL = "SELL"
    STOP = "STOP"
    RESUME = "RESUME"
    STATUS = "STATUS"
    CLEAR_ALL = "CLEAR_ALL"


class CommandSource(Enum):
    """Source of the command."""
    DASHBOARD = "DASHBOARD"
    TELEGRAM = "TELEGRAM"
    API = "API"


@dataclass
class Command:
    """Represents a command to be processed."""
    command_type: CommandType
    source: CommandSource
    mint: Optional[str] = None
    reason: str = ""
    percentage: float = 100.0
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class CommandProcessor:
    """
    Unified command processor for the trading bot.
    
    Features:
    - Reads commands from dashboard JSON file
    - Provides callback interface for Telegram
    - Queues and processes commands asynchronously
    - Logs all command activity
    
    Usage:
        processor = CommandProcessor(
            command_file="dashboard_commands.json",
            on_sell=bot.sell_position,
            on_stop=bot.stop_trading,
            get_positions=lambda: bot.active_positions
        )
        await processor.start()
    """
    
    def __init__(
        self,
        command_file: str = "dashboard_commands.json",
        on_sell: Callable[[str, str, float], Awaitable[Optional[str]]] = None,
        on_stop: Callable[[], Awaitable[None]] = None,
        on_resume: Callable[[], Awaitable[None]] = None,
        get_positions: Callable[[], Dict[str, Any]] = None,
        remove_position: Callable[[str], Awaitable[None]] = None,
        poll_interval: float = 3.0
    ):
        """
        Initialize command processor.
        
        Args:
            command_file: Path to dashboard commands JSON file
            on_sell: Callback for sell commands (mint, reason, pct) -> signature
            on_stop: Callback for stop trading
            on_resume: Callback for resume trading
            get_positions: Callback to get active positions dict
            remove_position: Callback to remove position from tracking
            poll_interval: Seconds between command file checks
        """
        self.command_file = Path(command_file)
        self.on_sell = on_sell
        self.on_stop = on_stop
        self.on_resume = on_resume
        self.get_positions = get_positions
        self.remove_position = remove_position
        self.poll_interval = poll_interval
        
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._command_queue: asyncio.Queue = asyncio.Queue()
    
    async def start(self):
        """Start the command processor."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("📊 CommandProcessor started")
    
    async def stop(self):
        """Stop the command processor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("📊 CommandProcessor stopped")
    
    async def _run(self):
        """Main processing loop."""
        while self._running:
            try:
                # Check file-based commands (dashboard)
                await self._check_file_commands()
                
                # Process any queued commands
                while not self._command_queue.empty():
                    cmd = await self._command_queue.get()
                    await self._process_command(cmd)
                
                await asyncio.sleep(self.poll_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"CommandProcessor error: {e}")
                await asyncio.sleep(5)
    
    async def _check_file_commands(self):
        """Check dashboard_commands.json for new commands."""
        if not self.command_file.exists():
            return
        
        try:
            with open(self.command_file, 'r') as f:
                commands = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return
        
        if not commands:
            return
        
        # Process unprocessed commands
        updated = False
        for cmd_data in commands:
            if cmd_data.get('processed', False):
                continue
            
            cmd_type = cmd_data.get('type', '').upper()
            
            if cmd_type == 'SELL':
                cmd = Command(
                    command_type=CommandType.SELL,
                    source=CommandSource.DASHBOARD,
                    mint=cmd_data.get('mint', ''),
                    reason=cmd_data.get('reason', 'DASHBOARD_MANUAL'),
                    percentage=cmd_data.get('percentage', 100.0)
                )
                await self._process_command(cmd)
            
            elif cmd_type == 'STOP':
                cmd = Command(
                    command_type=CommandType.STOP,
                    source=CommandSource.DASHBOARD
                )
                await self._process_command(cmd)
            
            cmd_data['processed'] = True
            updated = True
        
        # Save updated commands
        if updated:
            with open(self.command_file, 'w') as f:
                json.dump(commands, f, indent=2)
    
    async def _process_command(self, cmd: Command):
        """Process a single command."""
        logger.info(f"📊 Processing {cmd.command_type.value} from {cmd.source.value}")
        
        if cmd.command_type == CommandType.SELL:
            await self._handle_sell(cmd)
        elif cmd.command_type == CommandType.STOP:
            await self._handle_stop(cmd)
        elif cmd.command_type == CommandType.RESUME:
            await self._handle_resume(cmd)
    
    async def _handle_sell(self, cmd: Command):
        """Handle a sell command."""
        if not self.on_sell:
            logger.warning("No sell handler configured")
            return
        
        mint = cmd.mint
        if not mint:
            logger.warning("SELL command missing mint")
            return
        
        # Check if position exists
        positions = self.get_positions() if self.get_positions else {}
        if mint not in positions:
            logger.warning(f"⚠️ Position {mint[:8]}... not found for {cmd.source.value} sell")
            return
        
        logger.info(f"🔴 Executing {cmd.source.value} sell for {mint[:8]}...")
        
        try:
            signature = await self.on_sell(mint, cmd.reason, cmd.percentage)
            
            if signature:
                logger.info(f"✅ {cmd.source.value} sell executed: {signature[:16]}...")
                
                # Remove from tracking
                if self.remove_position:
                    await self.remove_position(mint)
            else:
                logger.warning(f"⚠️ {cmd.source.value} sell failed for {mint[:8]}...")
                
        except Exception as e:
            logger.error(f"Sell error: {e}")
    
    async def _handle_stop(self, cmd: Command):
        """Handle a stop trading command."""
        if self.on_stop:
            await self.on_stop()
            logger.info(f"⏹️ Trading stopped via {cmd.source.value}")
    
    async def _handle_resume(self, cmd: Command):
        """Handle a resume trading command."""
        if self.on_resume:
            await self.on_resume()
            logger.info(f"▶️ Trading resumed via {cmd.source.value}")
    
    def queue_command(self, cmd: Command):
        """Queue a command for processing (for Telegram/API use)."""
        self._command_queue.put_nowait(cmd)
    
    def queue_sell(self, mint: str, source: CommandSource = CommandSource.TELEGRAM, reason: str = ""):
        """Convenience method to queue a sell command."""
        cmd = Command(
            command_type=CommandType.SELL,
            source=source,
            mint=mint,
            reason=reason or f"{source.value}_MANUAL"
        )
        self.queue_command(cmd)
