"""``lcm topic echo`` — echo messages received on a channel.

Listens on the LCM multicast group and prints every message matching
the given channel name (or pattern).

Supports four display modes:
- **default**: Rich panel with hex dump and metadata
- **--raw**: compact one-line-per-message text
- **--type module.Class**: decode payload with an lcm-gen generated class
- **--lcm-file path.lcm**: auto-decode from .lcm file definitions
"""

from __future__ import annotations

import queue
import re
import signal
import sys
import threading
from typing import Any, List, Optional

import typer
from rich.console import Console

from lcm_tools.display.echo_display import (
    echo_packet_auto_decode,
    echo_packet_decoded,
    echo_packet_default,
    echo_packet_raw,
    load_decode_class,
)
from lcm_tools.listener import run_listener
from lcm_tools.protocol import DEFAULT_MC_ADDR, DEFAULT_MC_PORT, PacketInfo

_console = Console()


def echo(
    channel: str = typer.Argument(
        ...,
        help="Channel name to listen on. Use a regex pattern to match "
        "multiple channels (e.g. 'CAM.*').",
    ),
    count: Optional[int] = typer.Option(
        None,
        "--count",
        "-n",
        help="Stop after receiving this many messages.",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        "-t",
        help="Stop after this many seconds with no matching messages.",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Compact raw-text output (suitable for piping).",
    ),
    type_path: Optional[str] = typer.Option(
        None,
        "--type",
        help="lcm-gen type for decoding, e.g. 'exlcm.example_t'. "
        "With --lcm-file, use just the struct name (e.g. 'example_t').",
    ),
    lcm_files: Optional[List[str]] = typer.Option(
        None,
        "--lcm-file",
        "-f",
        help="Path to .lcm file or directory containing .lcm files. "
        "Can be specified multiple times. Enables auto-decode without lcm-gen.",
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
    """Echo messages on an LCM channel (like ``ros2 topic echo``)."""
    # Compile channel filter
    try:
        pattern = re.compile(channel)
    except re.error as exc:
        _console.print(f"[red]Invalid regex pattern:[/red] {exc}")
        raise typer.Exit(code=1)

    # Build TypeRegistry from --lcm-file if provided
    type_registry: Any = None
    if lcm_files:
        try:
            from lcm_tools.core.lcm_type_builder import TypeRegistry

            type_registry = TypeRegistry()
            type_registry.register_paths(lcm_files)
            n_types = len(type_registry.all_types)
            _console.print(
                f"[green]Loaded {n_types} type(s) from "
                f"{len(lcm_files)} LCM file path(s).[/green]"
            )
        except Exception as exc:
            _console.print(f"[red]Failed to load LCM files:[/red] {exc}")
            raise typer.Exit(code=1)

    # Resolve decode class
    decode_cls: Any = None
    if type_path:
        if type_registry is not None:
            # Look up from registry (--lcm-file + --type)
            decode_cls = type_registry.find_by_name(type_path)
            if decode_cls is None:
                available = ", ".join(sorted(type_registry.all_types.keys()))
                _console.print(
                    f"[red]Type '{type_path}' not found in LCM files.[/red]\n"
                    f"Available: {available}"
                )
                raise typer.Exit(code=1)
        else:
            # Traditional: import from PYTHONPATH
            try:
                decode_cls = load_decode_class(type_path)
            except Exception as exc:
                _console.print(f"[red]Failed to load type '{type_path}':[/red] {exc}")
                raise typer.Exit(code=1)

    # Thread-safe queue bridging the listener thread → main display thread
    pkt_queue: "queue.Queue[Optional[PacketInfo]]" = queue.Queue(maxsize=5000)

    def _on_packet(pkt: PacketInfo) -> None:
        if pkt.has_channel and pattern.search(pkt.channel):  # type: ignore[arg-type]
            try:
                pkt_queue.put_nowait(pkt)
            except queue.Full:
                pass  # drop oldest if producer outpaces display

    stop_event = run_listener(
        _on_packet,
        mc_addr=lcm_url,
        mc_port=lcm_port,
    )

    _console.print(
        f"[bold]Listening on '{channel}' ...[/bold]  "
        f"(multicast: {lcm_url}:{lcm_port}, Ctrl+C to stop)"
    )

    received = 0
    import time

    last_match_time = time.monotonic()

    try:
        while True:
            try:
                pkt = pkt_queue.get(timeout=0.3)
            except queue.Empty:
                if timeout and (time.monotonic() - last_match_time) >= timeout:
                    break
                continue

            if pkt is None:
                break

            received += 1
            last_match_time = time.monotonic()

            if raw:
                echo_packet_raw(pkt, received)
            elif decode_cls:
                echo_packet_decoded(pkt, received, decode_cls)
            elif type_registry is not None:
                echo_packet_auto_decode(pkt, received, type_registry)
            else:
                echo_packet_default(pkt, received)

            if count and received >= count:
                break

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        _console.print(f"\n[dim]Received {received} message(s).[/dim]")
