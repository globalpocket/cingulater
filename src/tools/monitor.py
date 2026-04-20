#!/usr/bin/env python3
import asyncio
import json
import os

# プロジェクトルートを PYTHONPATH に追加
import sys
import time
from datetime import datetime

import redis.asyncio as aioredis
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.config import get_settings

console = Console()

async def fetch_heartbeats(redis_client):
    """Redis から全コンポーネントのハートビートを取得"""
    keys = await redis_client.keys("brownie:heartbeat:*")
    heartbeats = []
    for key in keys:
        data = await redis_client.get(key)
        if data:
            hb = json.loads(data)
            hb["key"] = key.decode("utf-8")
            heartbeats.append(hb)
    return heartbeats

def generate_table(heartbeats):
    """ハートビート情報をテーブル化"""
    table = Table(box=box.DOUBLE_EDGE, expand=True)
    table.add_column("Component", style="cyan", no_wrap=True)
    table.add_column("Task ID", style="magenta")
    table.add_column("Last Seen", justify="right")
    table.add_column("Status", justify="center")

    now = time.time()
    for hb in sorted(heartbeats, key=lambda x: x.get("component", "")):
        last_seen_ts = hb.get("timestamp", 0)
        diff = now - last_seen_ts
        
        # 5分以上経過で警告
        status = "[green]ONLINE[/green]"
        if diff > 300:
            status = "[red]STALLED[/red]"
        elif diff > 60:
            status = "[yellow]AWAY[/yellow]"

        last_seen_str = datetime.fromtimestamp(last_seen_ts).strftime("%H:%M:%S")
        
        table.add_row(
            hb.get("component", "Unknown"),
            hb.get("task_id", "-"),
            f"{last_seen_str} ({int(diff)}s ago)",
            status
        )
    return table

async def main():
    settings = get_settings()
    redis_client = aioredis.Redis(
        host=settings.redis.host,
        port=settings.redis.port,
        password=settings.redis.password,
        db=settings.redis.db
    )

    console.clear()
    with Live(auto_refresh=False, console=console) as live:
        while True:
            try:
                heartbeats = await fetch_heartbeats(redis_client)
                
                layout = Layout()
                layout.split_column(
                    Layout(name="header", size=3),
                    Layout(name="body"),
                    Layout(name="footer", size=3)
                )
                
                # Header
                header_text = (
                    f"[bold blue]BROWNIE SYSTEM MONITOR[/bold blue] | "
                    f"Build: [green]{settings.build_id}[/green]"
                )
                layout["header"].update(Panel(header_text, box=box.ROUNDED))
                
                # Body
                layout["body"].update(generate_table(heartbeats))
                
                # Footer
                update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                footer_text = f"Last Update: {update_time} | Press Ctrl+C to exit"
                layout["footer"].update(Panel(footer_text, style="dim"))

                live.update(layout, refresh=True)
                await asyncio.sleep(2)
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
