"""``lcm topic list`` — discover and list active LCM channels.

Joins the LCM multicast group, listens for a configurable duration,
and prints a table of all observed channels with their message counts,
total sizes, and publisher addresses.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import typer
from rich.console import Console

from lcm_tools.core.discovery import ChannelDiscovery
from lcm_tools.display.stats_display import build_channel_table
from lcm_tools.listener import run_listener
from lcm_tools.protocol import DEFAULT_MC_ADDR, DEFAULT_MC_PORT

_console = Console()


def list_channels(
    duration: float = typer.Option(
        5.0,
        "--duration",
        "-d",
        help="How many seconds to listen for channel activity.",
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
    """List active LCM channels (like ``ros2 topic list``)."""
    discovery = ChannelDiscovery()

    _console.print(
        f"[bold]Discovering channels for {duration}s ...[/bold]  "
        f"(multicast: {lcm_url}:{lcm_port})"
    )

    stop_event = run_listener(discovery.on_packet, mc_addr=lcm_url, mc_port=lcm_port)

    try:
        time.sleep(duration)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()

    channels = discovery.get_active_channels(stale_after=duration + 2.0)
    if not channels:
        _console.print("[yellow]No active channels found.[/yellow]")
        _console.print(
            "[dim]Hint: make sure a publisher is running and your "
            "multicast routing is configured.[/dim]"
        )
        raise typer.Exit(code=0)

    _console.print(build_channel_table(channels))
