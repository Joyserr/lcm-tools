"""Real-time statistics collection for LCM channels.

Provides a thread-safe, memory-bounded statistics collector that
tracks per-channel message frequency, bandwidth, message sizes,
and cumulative data transfer.

Frequency and bandwidth are computed using a sliding window over
the most recent timestamps, giving a smoothed, up-to-date rate
without storing unbounded history.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from lcm_tools.protocol import PacketInfo

# Default sliding-window capacity (number of samples kept per channel)
_DEFAULT_WINDOW: int = 2000


@dataclass
class ChannelStats:
    """Per-channel statistics with a sliding time window."""

    channel: str
    msg_count: int = 0
    total_bytes: int = 0
    _timestamps: deque = field(default_factory=lambda: deque(maxlen=_DEFAULT_WINDOW))
    _sizes: deque = field(default_factory=lambda: deque(maxlen=_DEFAULT_WINDOW))

    @property
    def frequency_hz(self) -> float:
        """Message rate in Hz over the current window."""
        n = len(self._timestamps)
        if n < 2:
            return 0.0
        dt = self._timestamps[-1] - self._timestamps[0]
        return (n - 1) / dt if dt > 0.0 else 0.0

    @property
    def bandwidth_kbps(self) -> float:
        """Bandwidth in KB/s over the current window."""
        n = len(self._timestamps)
        if n < 2:
            return 0.0
        dt = self._timestamps[-1] - self._timestamps[0]
        if dt <= 0.0:
            return 0.0
        total = sum(self._sizes)
        return (total / 1024.0) / dt

    @property
    def avg_msg_size(self) -> float:
        """Average message size in bytes over the current window."""
        if not self._sizes:
            return 0.0
        return sum(self._sizes) / len(self._sizes)

    @property
    def total_kb(self) -> float:
        """Total data transferred in KB."""
        return self.total_bytes / 1024.0

    def record(self, size: int, ts: Optional[float] = None) -> None:
        """Record a single message."""
        if ts is None:
            ts = time.monotonic()
        self.msg_count += 1
        self.total_bytes += size
        self._timestamps.append(ts)
        self._sizes.append(size)


class StatsCollector:
    """Thread-safe multi-channel statistics aggregator.

    Safe to call ``on_packet`` from a listener thread while
    reading stats from the main thread.
    """

    def __init__(self, channel_filter: Optional[str] = None) -> None:
        """
        Args:
            channel_filter: If not None, only collect stats for channels
                whose name contains this substring.
        """
        self._lock = threading.Lock()
        self._stats: Dict[str, ChannelStats] = {}
        self._filter = channel_filter

    def on_packet(self, pkt: PacketInfo) -> None:
        """Process an incoming LCM packet for statistics."""
        # Skip mid-fragments (no channel info)
        if not pkt.has_channel:
            return

        channel = pkt.channel
        assert channel is not None

        # Apply filter
        if self._filter and self._filter not in channel:
            return

        with self._lock:
            if channel not in self._stats:
                self._stats[channel] = ChannelStats(channel=channel)
            self._stats[channel].record(pkt.packet_size)

    def get_stats(self) -> List[ChannelStats]:
        """Return a snapshot of all channel statistics, sorted by name."""
        with self._lock:
            return sorted(self._stats.values(), key=lambda s: s.channel)

    def get_channel_stats(self, channel: str) -> Optional[ChannelStats]:
        """Return stats for a specific channel, or None."""
        with self._lock:
            return self._stats.get(channel)

    @property
    def total_channels(self) -> int:
        with self._lock:
            return len(self._stats)

    @property
    def total_messages(self) -> int:
        with self._lock:
            return sum(s.msg_count for s in self._stats.values())

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return sum(s.total_bytes for s in self._stats.values())

    def snapshot(self) -> "StatsSnapshot":
        """Return an immutable point-in-time snapshot of all stats."""
        with self._lock:
            channels = [
                _ChannelSnapshot(
                    channel=s.channel,
                    msg_count=s.msg_count,
                    total_bytes=s.total_bytes,
                    frequency_hz=s.frequency_hz,
                    bandwidth_kbps=s.bandwidth_kbps,
                    avg_msg_size=s.avg_msg_size,
                )
                for s in sorted(self._stats.values(), key=lambda x: x.channel)
            ]
        return StatsSnapshot(
            channels=channels,
            total_channels=len(channels),
            total_messages=sum(c.msg_count for c in channels),
            total_bytes=sum(c.total_bytes for c in channels),
        )


@dataclass(frozen=True)
class _ChannelSnapshot:
    channel: str
    msg_count: int
    total_bytes: int
    frequency_hz: float
    bandwidth_kbps: float
    avg_msg_size: float


@dataclass(frozen=True)
class StatsSnapshot:
    """Immutable point-in-time snapshot of all channel statistics."""

    channels: List[_ChannelSnapshot]
    total_channels: int
    total_messages: int
    total_bytes: int

    @property
    def total_bandwidth_kbps(self) -> float:
        return sum(c.bandwidth_kbps for c in self.channels)
