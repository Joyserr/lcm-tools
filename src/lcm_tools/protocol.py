"""LCM UDP Multicast wire protocol parser.

Reference: https://lcm-proj.github.io/lcm/content/udp-multicast-protocol.html

Two packet formats:
- Small message (short header, 8 bytes): magic=0x4c433032 + seqno + channel\0 + payload
- Fragmented message (long header, 20 bytes): magic=0x4c433033 + seqno + payload_size
  + fragment_offset + fragment_no(2B) + n_fragments(2B) + [if frag==0: channel\0] + payload
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional, Tuple

# LCM protocol constants
LCM2_MAGIC_SHORT: int = 0x4C433032  # "LC02" - small single-packet messages
LCM2_MAGIC_LONG: int = 0x4C433033  # "LC03" - fragmented messages

DEFAULT_MC_ADDR: str = "239.255.76.67"
DEFAULT_MC_PORT: int = 7667

# Maximum channel name length (sanity check)
_MAX_CHANNEL_LEN: int = 256


@dataclass
class PacketInfo:
    """Parsed information from a single LCM UDP datagram."""

    channel: Optional[str] = None
    seqno: int = 0
    payload: bytes = b""
    fragment_no: int = 0
    n_fragments: int = 1
    packet_size: int = 0
    sender_addr: Tuple[str, int] = ("", 0)
    is_fragment: bool = False

    @property
    def is_first_fragment(self) -> bool:
        return self.is_fragment and self.fragment_no == 0

    @property
    def has_channel(self) -> bool:
        """True if this packet contains a channel name."""
        return self.channel is not None


def parse_lcm_packet(
    data: bytes, sender_addr: Tuple[str, int] = ("", 0)
) -> Optional[PacketInfo]:
    """Parse a raw UDP datagram as an LCM packet.

    Args:
        data: Raw bytes received from the UDP socket.
        sender_addr: (ip, port) of the sender.

    Returns:
        PacketInfo on success, None if the packet is malformed or unrecognised.
    """
    if len(data) < 8:
        return None

    magic, seqno = struct.unpack("!II", data[:8])

    if magic == LCM2_MAGIC_SHORT:
        return _parse_short_message(data, seqno, sender_addr)
    elif magic == LCM2_MAGIC_LONG:
        return _parse_fragmented_message(data, seqno, sender_addr)
    else:
        return None


def _parse_short_message(
    data: bytes, seqno: int, sender_addr: Tuple[str, int]
) -> Optional[PacketInfo]:
    """Parse a small (non-fragmented) LCM message."""
    # Header: 8 bytes already read; channel name starts at offset 8
    null_pos = data.find(b"\x00", 8)
    if null_pos == -1 or null_pos - 8 > _MAX_CHANNEL_LEN:
        return None

    try:
        channel = data[8:null_pos].decode("utf-8")
    except UnicodeDecodeError:
        return None

    payload = data[null_pos + 1 :]

    return PacketInfo(
        channel=channel,
        seqno=seqno,
        payload=payload,
        fragment_no=0,
        n_fragments=1,
        packet_size=len(data),
        sender_addr=sender_addr,
        is_fragment=False,
    )


def _parse_fragmented_message(
    data: bytes, seqno: int, sender_addr: Tuple[str, int]
) -> Optional[PacketInfo]:
    """Parse a fragmented LCM message (first fragment has channel name)."""
    if len(data) < 20:
        return None

    _payload_size, _frag_offset, frag_no, n_frags = struct.unpack(
        "!IIHH", data[8:20]
    )

    if frag_no == 0:
        # First fragment: contains channel name
        null_pos = data.find(b"\x00", 20)
        if null_pos == -1 or null_pos - 20 > _MAX_CHANNEL_LEN:
            return None

        try:
            channel = data[20:null_pos].decode("utf-8")
        except UnicodeDecodeError:
            return None

        payload = data[null_pos + 1 :]

        return PacketInfo(
            channel=channel,
            seqno=seqno,
            payload=payload,
            fragment_no=0,
            n_fragments=n_frags,
            packet_size=len(data),
            sender_addr=sender_addr,
            is_fragment=True,
        )
    else:
        # Subsequent fragments: no channel name, payload only
        payload = data[20:]
        return PacketInfo(
            channel=None,
            seqno=seqno,
            payload=payload,
            fragment_no=frag_no,
            n_fragments=n_frags,
            packet_size=len(data),
            sender_addr=sender_addr,
            is_fragment=True,
        )


def extract_fingerprint(payload: bytes) -> Optional[int]:
    """Extract the LCM type fingerprint from a message payload.

    The fingerprint is the first 8 bytes of the payload, encoded as a
    big-endian unsigned 64-bit integer.

    Args:
        payload: The message payload bytes.

    Returns:
        The fingerprint as an integer, or None if the payload is too short.
    """
    if len(payload) < 8:
        return None
    return struct.unpack(">Q", payload[:8])[0]


def fingerprint_to_hex(fp: int) -> str:
    """Format a fingerprint integer as a hex string."""
    return f"0x{fp:016x}"
