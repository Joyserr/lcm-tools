"""Rich-based real-time echo display for ``lcm topic echo``.

Supports three display modes:
1. Default — Rich Panel with channel, seq, size, fingerprint, hex dump
2. Raw — compact plain-text output for piping/scripting
3. Decoded — attempt to decode payload via an lcm-gen generated class
"""

from __future__ import annotations

import importlib
import time
from datetime import datetime
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from lcm_tools.protocol import PacketInfo, extract_fingerprint, fingerprint_to_hex

# TYPE_CHECKING import for TypeRegistry (avoid circular imports)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lcm_tools.core.lcm_type_builder import TypeRegistry

_console = Console()


def echo_packet_default(pkt: PacketInfo, msg_index: int) -> None:
    """Display a single packet using Rich Panel (default mode)."""
    fp = extract_fingerprint(pkt.payload)
    fp_str = fingerprint_to_hex(fp) if fp is not None else "N/A"

    ts_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]

    # Build payload preview (first 128 bytes as hex, wrapped)
    preview_bytes = pkt.payload[:128]
    hex_lines = [
        preview_bytes[i : i + 16].hex(" ")
        for i in range(0, len(preview_bytes), 16)
    ]
    hex_preview = "\n".join(hex_lines)
    if len(pkt.payload) > 128:
        hex_preview += f"\n... ({len(pkt.payload) - 128} more bytes)"

    body = Text.from_markup(
        f"[bold]channel:[/bold]    [cyan]{pkt.channel}[/cyan]\n"
        f"[bold]seq:[/bold]        {pkt.seqno}\n"
        f"[bold]size:[/bold]       {len(pkt.payload)} bytes "
        f"(packet: {pkt.packet_size} B)\n"
        f"[bold]time:[/bold]       {ts_str}\n"
        f"[bold]fingerprint:[/bold] {fp_str}\n"
        f"[bold]sender:[/bold]     {pkt.sender_addr[0]}:{pkt.sender_addr[1]}\n"
        f"\n[dim]payload (hex):[/dim]\n{hex_preview}"
    )

    panel = Panel(
        body,
        title=f"[bold green]Message #{msg_index}[/bold green]",
        border_style="blue",
        expand=False,
    )
    _console.print(panel)


def echo_packet_raw(pkt: PacketInfo, msg_index: int) -> None:
    """Display a packet in compact raw text mode."""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    fp = extract_fingerprint(pkt.payload)
    fp_str = fingerprint_to_hex(fp) if fp is not None else "N/A"
    _console.print(
        f"[{ts}] #{msg_index} {pkt.channel}  "
        f"seq={pkt.seqno}  size={len(pkt.payload)}B  "
        f"fp={fp_str}  from={pkt.sender_addr[0]}:{pkt.sender_addr[1]}"
    )


def _format_value(value: Any, indent: int = 1) -> str:
    """Recursively format a field value, expanding nested LCM structs.

    Args:
        value: The field value to format.
        indent: Current indentation level (each level = 2 spaces).

    Returns:
        A formatted string representation of the value.
    """
    prefix = "  " * indent

    # Handle nested LCM struct objects (have __slots__ or custom attributes)
    if hasattr(value, "__slots__") or (
        hasattr(value, "__dict__")
        and not isinstance(value, (list, tuple, dict, str, bytes))
        and not hasattr(value, "__len__")
    ):
        nested_fields = _extract_fields(value)
        if nested_fields:
            lines = []
            for k, v in nested_fields:
                formatted_v = _format_value(v, indent + 1)
                lines.append(f"{prefix}  {k}: {formatted_v}")
            return "\n" + "\n".join(lines)

    # Handle lists / tuples (e.g., arrays of structs or primitives)
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        # Check if elements are nested structs
        first = value[0]
        if hasattr(first, "__slots__") or (
            hasattr(first, "__dict__")
            and not isinstance(first, (list, tuple, dict, str, bytes))
            and not hasattr(first, "__len__")
        ):
            lines = []
            for i, item in enumerate(value):
                nested_fields = _extract_fields(item)
                if nested_fields:
                    lines.append(f"{prefix}  [{i}]:")
                    for k, v in nested_fields:
                        formatted_v = _format_value(v, indent + 2)
                        lines.append(f"{prefix}    {k}: {formatted_v}")
                else:
                    lines.append(f"{prefix}  [{i}]: {item}")
            return "\n" + "\n".join(lines)
        return repr(value)

    # Handle dicts
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for k, v in value.items():
            formatted_v = _format_value(v, indent + 1)
            lines.append(f"{prefix}  {k}: {formatted_v}")
        return "\n" + "\n".join(lines)

    # Primitive types
    return repr(value)


def _extract_fields(obj: Any) -> list[tuple[str, Any]]:
    """Extract (name, value) pairs from an LCM struct-like object."""
    # Prefer __slots__ if available (lcm-gen generated classes use __slots__)
    if hasattr(obj, "__slots__"):
        return [
            (k, getattr(obj, k))
            for k in obj.__slots__
            if not k.startswith("_")
        ]
    # Fall back to dir(), filtering out callables and private attrs
    return [
        (k, getattr(obj, k))
        for k in dir(obj)
        if not k.startswith("_") and not callable(getattr(obj, k))
    ]


def echo_packet_decoded(
    pkt: PacketInfo, msg_index: int, decode_cls: Any
) -> None:
    """Display a decoded LCM message with recursive nested struct expansion."""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    try:
        msg = decode_cls.decode(pkt.payload)
        fields = _extract_fields(msg)
        body_lines = []
        for k, v in fields:
            formatted_v = _format_value(v, indent=1)
            body_lines.append(f"  {k}: {formatted_v}")
        body = "\n".join(body_lines)
    except Exception as exc:
        body = f"[decode error: {exc}]\nRaw hex: {pkt.payload[:64].hex(' ')}"

    _console.print(
        Panel(
            body,
            title=f"[bold green]#{msg_index}[/bold green] "
            f"[cyan]{pkt.channel}[/cyan] [{ts}]",
            border_style="blue",
            expand=False,
        )
    )


def echo_packet_auto_decode(
    pkt: PacketInfo, msg_index: int, registry: "TypeRegistry"
) -> None:
    """Auto-decode a packet using fingerprint matching from a TypeRegistry.

    If the fingerprint matches a registered type, decode and display.
    Otherwise, fall back to default display with a hint.
    """
    fp = extract_fingerprint(pkt.payload)
    decode_cls = None
    if fp is not None:
        decode_cls = registry.find_by_fingerprint(fp)

    if decode_cls is not None:
        echo_packet_decoded(pkt, msg_index, decode_cls)
    else:
        # Fall back to default display with fingerprint info
        echo_packet_default(pkt, msg_index)
        if fp is not None:
            _console.print(
                f"  [dim](no matching type for fingerprint "
                f"{fingerprint_to_hex(fp)})[/dim]"
            )


def load_decode_class(type_path: str) -> Any:
    """Dynamically import an lcm-gen generated class.

    Args:
        type_path: Dotted path like ``exlcm.example_t``.

    Returns:
        The class object (must have a ``decode(data)`` classmethod).

    Raises:
        ImportError: If the module cannot be imported.
        AttributeError: If the class is not found in the module.
    """
    parts = type_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Expected 'module.ClassName' format, got: {type_path!r}"
        )
    module_path, class_name = parts
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)
