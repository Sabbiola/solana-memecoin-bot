import json
import time
import sys
from pathlib import Path
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.console import Console
from rich import box
from datetime import datetime

SNAPSHOT_PATH = Path("logs/positions.json")
LOG_FILE = Path("logs/bot.log")

def get_positions_table(data):
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("Symbol", style="cyan", no_wrap=True)
    table.add_column("State", style="magenta")
    table.add_column("Size (SOL)", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("PnL %", justify="right")
    table.add_column("Value", justify="right")
    table.add_column("Age", justify="right")

    positions = data.get("open_positions", [])
    now = data.get("ts", time.time())

    if not positions:
        # Empty row
        table.add_row("-", "-", "-", "-", "-", "-", "-", "-")
        return table

    for p in positions:
        sym = p.get('symbol', '???')
        state = p.get('state', '???')
        entry = p.get('entry_price') or 0.0
        curr = p.get('last_price') or 0.0
        size = p.get('size_sol') or 0.0
        pnl_pct = p.get('pnl_pct', 0.0)
        opened_at = p.get('opened_at', now)
        age_sec = int(now - opened_at)
        
        # Color PnL
        if pnl_pct > 0:
            pnl_style = "green"
            pnl_str = f"+{pnl_pct*100:.1f}%"
        elif pnl_pct < -0.10:
            pnl_style = "bold red"
            pnl_str = f"{pnl_pct*100:.1f}%"
        else:
            pnl_style = "red"
            pnl_str = f"{pnl_pct*100:.1f}%"
            
        val_sol = size * (1 + pnl_pct)

        table.add_row(
            sym,
            state,
            f"{size:.3f}",
            f"{entry:.8f}",
            f"{curr:.8f}",
            f"[{pnl_style}]{pnl_str}[/{pnl_style}]",
            f"{val_sol:.3f}",
            f"{age_sec}s"
        )
    return table

def make_layout():
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3)
    )
    return layout

def main():
    console = Console()
    layout = make_layout()
    
    layout["header"].update(Panel("🚀 ANTIGRAVITY SOLANA BOT - LIVE MONITOR 🚀", style="bold white on blue"))
    layout["footer"].update(Panel("Press Ctrl+C to exit", style="dim"))

    with Live(layout, refresh_per_second=1, screen=True) as live:
        while True:
            try:
                if SNAPSHOT_PATH.exists():
                    try:
                        text = SNAPSHOT_PATH.read_text(encoding='utf-8')
                        if text.strip():
                            data = json.loads(text)
                            ts = data.get("ts", 0)
                            lag = time.time() - ts
                            
                            status = f"Last Update: {datetime.fromtimestamp(ts).strftime('%H:%M:%S')} (Lag: {lag:.1f}s)"
                            if lag > 10:
                                status += " [bold red]⚠️  LAG COMPILING[/bold red]"
                            
                            layout["header"].update(Panel(f"🚀 ANTIGRAVITY BOT | {status}", style="bold white on blue"))
                            layout["main"].update(Panel(get_positions_table(data), title="Open Positions", border_style="green"))
                    except json.JSONDecodeError:
                        pass # writing
                else:
                    layout["main"].update(Panel("Waiting for bot data...", title="Status", border_style="yellow"))
                
                time.sleep(1)
            except KeyboardInterrupt:
                break
            except Exception:
                time.sleep(1)

if __name__ == "__main__":
    main()
