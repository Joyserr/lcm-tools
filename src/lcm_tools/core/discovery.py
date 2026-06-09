"""Channel and node discovery by sniffing LCM multicast traffic.

LCM has no centralized registry or daemon, so there is no built-in
mechanism to discover active channels or publishers.  This module
implements passive discovery by listening to all multicast traffic
and recording metadata about each observed channel and sender.

Nodes are identified by their UDP source address (IP:port), since
LCM does not have a native "node name" concept like ROS2.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from lcm_tools.protocol import PacketInfo, extract_fingerprint


@dataclass
class ChannelInfo:
    """Metadata about a single observed LCM channel."""

    name: str
    first_seen: float = 0.0
    last_seen: float = 0.0
    msg_count: int = 0
    total_bytes: int = 0
    fingerprint: Optional[int] = None
    publishers: Set[str] = field(default_factory=set)

    @property
    def avg_msg_size(self) -> float:
        return self.total_bytes / self.msg_count if self.msg_count > 0 else 0.0


@dataclass
class NodeInfo:
    """A publisher node identified by its UDP source address."""

    address: str  # "ip:port"
    channels: Set[str] = field(default_factory=set)
    msg_count: int = 0
    total_bytes: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0


class ChannelDiscovery:
    """Passive discovery of LCM channels and publisher nodes.

    Thread-safe: can be fed packets from a listener thread while
    being queried from the main thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._channels: Dict[str, ChannelInfo] = {}
        self._nodes: Dict[str, NodeInfo] = {}  # key = "ip:port"

    def on_packet(self, pkt: PacketInfo) -> None:
        """Process a received LCM packet for discovery purposes."""
        now = time.time()
        sender_key = f"{pkt.sender_addr[0]}:{pkt.sender_addr[1]}"

        with self._lock:
            # Update node info
            if sender_key not in self._nodes:
                self._nodes[sender_key] = NodeInfo(
                    address=sender_key, first_seen=now
                )
            node = self._nodes[sender_key]
            node.msg_count += 1
            node.total_bytes += pkt.packet_size
            node.last_seen = now

            # Only process packets that have a channel (skip mid-fragments)
            if not pkt.has_channel:
                return

            channel = pkt.channel
            assert channel is not None
            node.channels.add(channel)

            # Update channel info
            if channel not in self._channels:
                self._channels[channel] = ChannelInfo(
                    name=channel, first_seen=now
                )
            ch_info = self._channels[channel]
            ch_info.last_seen = now
            ch_info.msg_count += 1
            ch_info.total_bytes += pkt.packet_size
            ch_info.publishers.add(sender_key)

            # Try to extract fingerprint from first message
            if ch_info.fingerprint is None and pkt.payload:
                fp = extract_fingerprint(pkt.payload)
                if fp is not None:
                    ch_info.fingerprint = fp

    def get_active_channels(
        self, stale_after: float = 10.0
    ) -> List[ChannelInfo]:
        """Return channels that have been active within *stale_after* seconds."""
        now = time.time()
        with self._lock:
            return [
                ch
                for ch in sorted(self._channels.values(), key=lambda c: c.name)
                if now - ch.last_seen < stale_after
            ]

    def get_all_channels(self) -> List[ChannelInfo]:
        """Return all ever-observed channels."""
        with self._lock:
            return sorted(self._channels.values(), key=lambda c: c.name)

    def get_nodes(self, stale_after: float = 10.0) -> List[NodeInfo]:
        """Return nodes that have been active within *stale_after* seconds."""
        now = time.time()
        with self._lock:
            return [
                node
                for node in sorted(self._nodes.values(), key=lambda n: n.address)
                if now - node.last_seen < stale_after
            ]

    def get_all_nodes(self) -> List[NodeInfo]:
        """Return all ever-observed nodes."""
        with self._lock:
            return sorted(self._nodes.values(), key=lambda n: n.address)
