
import asyncio
import sys
import signal
import os
import platform
import logging
import threading

# Ensure we can import from the package
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solana_bot.core.bot import UniversalCopyTradeV7

# CRITICAL: Apply DNS Monkeypatch for Telecom Italia bypass
# Must be imported before other network modules
try:
    from solana_bot.utils import dns_bypass
    logger = logging.getLogger(__name__)
    logger.info("✅ DNS Bypass module loaded successfully")
except ImportError:
    logging.getLogger(__name__).warning("⚠️ Could not load DNS bypass module")

logger = logging.getLogger(__name__)

# Global bot instance for signal handling
bot_instance = None
shutdown_event = asyncio.Event()
dashboard_thread = None

def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    print(f"\n🛑 [SHUTDOWN] Received signal {sig}...")
    shutdown_event.set()

def start_dashboard_server():
    """Start dashboard server in a separate thread."""
    try:
        import uvicorn
        from dashboard_config import DASHBOARD_HOST, DASHBOARD_PORT
        
        # Import the app
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from dashboard_server import app
        
        logger.info(f"📊 Starting Dashboard on http://localhost:{DASHBOARD_PORT}")
        
        # Run uvicorn in this thread
        uvicorn.run(
            app,
            host=DASHBOARD_HOST,
            port=DASHBOARD_PORT,
            log_level="warning",  # Reduce noise
            access_log=False
        )
    except ImportError as e:
        logger.warning(f"⚠️ Dashboard not available: {e}")
    except Exception as e:
        logger.error(f"❌ Dashboard error: {e}")

async def main():
    global bot_instance, dashboard_thread
    
    # Windows UTF-8 fix
    if platform.system() == "Windows":
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')

    # Setup signal handlers (improved)
    loop = asyncio.get_running_loop()
    
    def handle_shutdown(sig):
        """Handle shutdown signals."""
        print(f"\n🛑 [SHUTDOWN] Received signal {sig}...")
        shutdown_event.set()
        # Force stop bot
        if bot_instance:
            bot_instance.is_running = False
            bot_instance.stop_listening = True
    
    # Add signal handlers (not supported on Windows - use fallback)
    if platform.system() != "Windows":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: handle_shutdown(s))
    else:
        # Windows fallback: use old-style signal handler
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    # Start dashboard server in background thread
    dashboard_thread = threading.Thread(target=start_dashboard_server, daemon=True)
    dashboard_thread.start()
    logger.info("📊 Dashboard server started in background")

    # Create bot
    bot_instance = UniversalCopyTradeV7()
    
    try:
        # Start bot in background
        bot_task = asyncio.create_task(bot_instance.start())
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
        # Graceful shutdown
        logger.info("Initiating graceful shutdown...")
        bot_instance.is_running = False
        bot_instance.stop_listening = True
        
        # Cancel bot task
        bot_task.cancel()
        
        try:
            await asyncio.wait_for(bot_task, timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        
        # Cancel all remaining tasks
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        # Close resources
        if bot_instance.session and not bot_instance.session.closed:
            await bot_instance.session.close()
            await asyncio.sleep(0.1)
        
        if bot_instance.client:
            await bot_instance.client.close()
        
        logger.info("Shutdown complete")
    
    except KeyboardInterrupt:
        print("\n👋 Keyboard interrupt - shutting down...")
        if bot_instance:
            bot_instance.is_running = False
            bot_instance.stop_listening = True

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║         🚀 SOLANA COPY TRADE BOT + DASHBOARD 🚀              ║
╠══════════════════════════════════════════════════════════════╣
║  Dashboard: http://localhost:8088                            ║
╚══════════════════════════════════════════════════════════════╝
    """)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Bot stopped by user.")
    except Exception as e:
        print(f"🔥 Fatal Error: {e}")
        import traceback
        traceback.print_exc()

