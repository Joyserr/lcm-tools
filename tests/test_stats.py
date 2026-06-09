"""Unit tests for lcm_tools.core.stats module."""

from __future__ import annotations

import time

import pytest

from lcm_tools.core.stats import ChannelStats, StatsCollector
from lcm_tools.protocol import PacketInfo


def _make_packet(channel: str, size: int = 100) -> PacketInfo:
    return PacketInfo(
        channel=channel,
        seqno=1,
        payload=b"\x00" * size,
        packet_size=size + 8 + len(channel) + 1,  # payload + header + channel\0
        sender_addr=("10.0.0.1", 5000),
    )


class TestChannelStats:
    def test_single_message(self) -> None:
        stats = ChannelStats(channel="TEST")
        stats.record(100)
        assert stats.msg_count == 1
        assert stats.total_bytes == 100
        assert stats.frequency_hz == 0.0  # need >= 2 samples
        assert stats.avg_msg_size == 100.0

    def test_frequency_calcation(self) -> None:
        stats = ChannelStats(channel="TEST")
        # Record 11 messages over 1 second => ~10 Hz
        for i in range(11):
            stats.record(50, ts=i * 0.1)
        assert 9.5 < stats.frequency_hz < 10.5

    def test_bandwidth_calculation(self) -> None:
        stats = ChannelStats(channel="TEST")
        # 11 messages of 1024 bytes each over 1 second => 11 * 1024 / 1024 = 11 KB/s
        for i in range(11):
            stats.record(1024, ts=i * 0.1)
        assert 10.5 < stats.bandwidth_kbps < 11.5

    def test_avg_msg_size(self) -> None:
        stats = ChannelStats(channel="TEST")
        stats.record(100)
        stats.record(200)
        stats.record(300)
        assert stats.avg_msg_size == pytest.approx(200.0)

    def test_total_kb(self) -> None:
        stats = ChannelStats(channel="TEST")
        stats.record(1024)
        stats.record(1024)
        assert stats.total_kb == pytest.approx(2.0)

    def test_sliding_window(self) -> None:
        stats = ChannelStats(channel="TEST")
        # Fill beyond the window capacity
        for i in range(3000):
            stats.record(10, ts=i * 0.001)
        # Window should only contain the last 2000 entries
        assert len(stats._timestamps) <= 2000


class TestStatsCollector:
    def test_basic_collection(self) -> None:
        collector = StatsCollector()
        collector.on_packet(_make_packet("A"))
        collector.on_packet(_make_packet("B"))
        collector.on_packet(_make_packet("A"))

        all_stats = collector.get_stats()
        assert len(all_stats) == 2
        assert collector.total_channels == 2
        assert collector.total_messages == 3

    def test_channel_filter(self) -> None:
        collector = StatsCollector(channel_filter="CAM")
        collector.on_packet(_make_packet("CAMERA_LEFT"))
        collector.on_packet(_make_packet("LIDAR"))
        collector.on_packet(_make_packet("CAMERA_RIGHT"))

        all_stats = collector.get_stats()
        assert len(all_stats) == 2
        names = {s.channel for s in all_stats}
        assert names == {"CAMERA_LEFT", "CAMERA_RIGHT"}

    def test_skip_fragments_without_channel(self) -> None:
        collector = StatsCollector()
        fragment = PacketInfo(
            channel=None,  # mid-fragment
            seqno=1,
            payload=b"\x00" * 100,
            packet_size=120,
            is_fragment=True,
            fragment_no=2,
        )
        collector.on_packet(fragment)
        assert collector.total_messages == 0

    def test_snapshot(self) -> None:
        collector = StatsCollector()
        for i in range(10):
            collector.on_packet(_make_packet("X", size=50))

        snap = collector.snapshot()
        assert snap.total_channels == 1
        assert snap.total_messages == 10
        assert len(snap.channels) == 1
        assert snap.channels[0].channel == "X"
        assert snap.channels[0].msg_count == 10

    def test_get_channel_stats_specific(self) -> None:
        collector = StatsCollector()
        collector.on_packet(_make_packet("A"))
        collector.on_packet(_make_packet("A"))

        s = collector.get_channel_stats("A")
        assert s is not None
        assert s.msg_count == 2

        assert collector.get_channel_stats("NONEXISTENT") is None
