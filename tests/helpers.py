"""Test helpers for constructing raw LCM packets."""

from __future__ import annotations

import struct
from typing import Optional, Tuple

from lcm_tools.protocol import LCM2_MAGIC_LONG, LCM2_MAGIC_SHORT


def build_short_packet(
    channel: str,
    payload: bytes,
    seqno: int = 1,
) -> bytes:
    """Construct a raw LCM short-message UDP datagram.

    Format: magic(4B) + seqno(4B) + channel\\0 + payload
    """
    header = struct.pack("!II", LCM2_MAGIC_SHORT, seqno)
    channel_bytes = channel.encode("utf-8") + b"\x00"
    return header + channel_bytes + payload


def build_fragment_packet(
    channel: Optional[str],
    payload: bytes,
    seqno: int = 1,
    fragment_no: int = 0,
    n_fragments: int = 2,
    payload_size: int = 100000,
    fragment_offset: int = 0,
) -> bytes:
    """Construct a raw LCM fragmented-message UDP datagram.

    Format: magic(4B) + seqno(4B) + payload_size(4B) + fragment_offset(4B)
            + fragment_no(2B) + n_fragments(2B)
            + [if fragment_no==0: channel\\0] + payload
    """
    header = struct.pack(
        "!IIIIHH",
        LCM2_MAGIC_LONG,
        seqno,
        payload_size,
        fragment_offset,
        fragment_no,
        n_fragments,
    )
    if fragment_no == 0 and channel is not None:
        channel_bytes = channel.encode("utf-8") + b"\x00"
        return header + channel_bytes + payload
    return header + payload
