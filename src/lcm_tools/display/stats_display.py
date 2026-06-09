"""Rich Live table display for ``lcm topic stats`` and ``lcm topic list``.

Uses ``rich.live.Live`` to render a continuously updating table of
per-channel statistics in the terminal.
"""

from __future__ import annotations

from typing import List

from rich.console import Console
from rich.live import Live
from rich.table import Table

from lcm_tools.core.discovery import ChannelInfo, NodeInfo
from lcm_tools.core.stats import StatsSnapshot, _ChannelSnapshot

_console = Console()


def build_stats_table(snap: StatsSnapshot) -> Table:
    """Build a Rich Table from a stats snapshot."""
    table = Table(
        title="LCM Channel Statistics",
        show_lines=False,
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Channel", style="cyan", min_width=18)
    table.add_column("Messages", justify="right", style="bold")
    table.add_column("Rate (Hz)", justify="right", style="green")
    table.add_column("BW (KB/s)", justify="right", style="yellow")
    table.add_column("Avg Size (B)", justify="right")
    table.add_column("Total (KB)", justify="right", style="blue")

    for ch in snap.channels:
        table.add_row(
            ch.channel,
            str(ch.msg_count),
            f"{ch.frequency_hz:.1f}",
            f"{ch.bandwidth_kbps:.2f}",
            f"{ch.avg_msg_size:.0f}",
            f"{ch.total_bytes / 1024:.1f}",
        )

    # Summary row
    table.add_section()
    table.add_row(
        f"[bold]{snap.total_channels} channels[/bold]",
        f"[bold]{snap.total_messages}[/bold]",
        "",
        f"[bold]{snap.total_bandwidth_kbps:.2f}[/bold]",
        "",
        f"[bold]{snap.total_bytes / 1024:.1f}[/bold]",
    )
    return table


def build_channel_table(channels: List[ChannelInfo]) -> Table:
    """Build a Rich Table listing discovered channels."""
    table = Table(
        title=f"Active LCM Channels ({len(channels)} found)",
        show_lines=False,
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Channel", style="cyan", min_width=18)
    table.add_column("Messages", justify="right")
    table.add_column("Total Size", justify="right", style="blue")
    table.add_column("Publishers", justify="right", style="dim")

    for ch in channels:
        table.add_row(
            ch.name,
            str(ch.msg_count),
            f"{ch.total_bytes / 1024:.1f} KB",
            ", ".join(sorted(ch.publishers)) if ch.publishers else "-",
        )
    return table


def build_node_table(nodes: List[NodeInfo]) -> Table:
    """Build a Rich Table listing discovered publisher nodes."""
    table = Table(
        title=f"LCM Publisher Nodes ({len(nodes)} found)",
        show_lines=False,
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Node (IP:port)", style="cyan", min_width=22)
    table.add_column("Channels", style="green")
    table.add_column("Messages", justify="right")
    table.add_column("Total Size", justify="right", style="blue")

    for node in nodes:
        channels_str = ", ".join(sorted(node.channels)) or "-"
        table.add_row(
            node.address,
            channels_str,
            str(node.msg_count),
            f"{node.total_bytes / 1024:.1f} KB",
        )
    return table
