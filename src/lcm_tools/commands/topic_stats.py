"""``lcm topic stats`` — real-time per-channel statistics monitor.

Joins the LCM multicast group and displays a continuously updating
table of message frequency, bandwidth, message sizes, and cumulative
data transfer for each observed channel.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import typer
from rich.console import Console
from rich.live import Live

from lcm_tools.core.stats import StatsCollector
from lcm_tools.display.stats_display import build_stats_table
from lcm_tools.listener import run_listener
from lcm_tools.protocol import DEFAULT_MC_ADDR, DEFAULT_MC_PORT

_console = Console()


def stats(
    channel: Optional[str] = typer.Argument(
        None,
        help="Only monitor channels whose name contains this string. "
        "Leave empty to monitor all channels.",
    ),
    duration: Optional[float] = typer.Option(
        None,
        "--duration",
        "-d",
        help="Stop after this many seconds. "
        "Default: run until Ctrl+C.",
    ),
    lcm_url: str = typer.Option(
        DEFAULT_MC_ADDR,
        "--lcm-url",
        help="LCM multicast address.",
    ),
    lcm_port: int = typer.Option(
        DEFAULT_MC_PORT,
        "--lcm-port",
        help="LCM multicast port.",
    ),
) -> None:
    """Show real-time channel statistics (like ``ros2 topic hz``)."""
    collector = StatsCollector(channel_filter=channel)

    filter_label = f"matching '{channel}'" if channel else "all"
    _console.print(
        f"[bold]Collecting stats for {filter_label} channels ...[/bold]  "
        f"(multicast: {lcm_url}:{lcm_port}, Ctrl+C to stop)"
    )

    stop_event = run_listener(
        collector.on_packet,
        mc_addr=lcm_url,
        mc_port=lcm_port,
    )

    start_time = time.monotonic()

    try:
        with Live(
            build_stats_table(collector.snapshot()),
            console=_console,
            refresh_per_second=2,
        ) as live:
            while True:
                time.sleep(0.5)
                live.update(build_stats_table(collector.snapshot()))

                if duration and (time.monotonic() - start_time) >= duration:
                    break

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()

    # Print final snapshot
    _console.print("\n[bold]Final Statistics:[/bold]")
    _console.print(build_stats_table(collector.snapshot()))
