"""Unit tests for lcm_tools.protocol module."""

from __future__ import annotations

import struct

import pytest

from lcm_tools.protocol import (
    LCM2_MAGIC_LONG,
    LCM2_MAGIC_SHORT,
    PacketInfo,
    extract_fingerprint,
    fingerprint_to_hex,
    parse_lcm_packet,
)

from tests.helpers import build_fragment_packet, build_short_packet


# ---------------------------------------------------------------------------
# Short (non-fragmented) message tests
# ---------------------------------------------------------------------------


class TestShortMessage:
    def test_valid_short_message(self) -> None:
        payload = b"\x01\x02\x03\x04\x05\x06\x07\x08payload"
        raw = build_short_packet("EXAMPLE", payload, seqno=42)
        pkt = parse_lcm_packet(raw, ("10.0.0.1", 9999))

        assert pkt is not None
        assert pkt.channel == "EXAMPLE"
        assert pkt.seqno == 42
        assert pkt.payload == payload
        assert pkt.is_fragment is False
        assert pkt.n_fragments == 1
        assert pkt.sender_addr == ("10.0.0.1", 9999)

    def test_empty_payload(self) -> None:
        raw = build_short_packet("CH", b"")
        pkt = parse_lcm_packet(raw)
        assert pkt is not None
        assert pkt.channel == "CH"
        assert pkt.payload == b""

    def test_long_channel_name(self) -> None:
        channel = "A" * 200
        raw = build_short_packet(channel, b"data")
        pkt = parse_lcm_packet(raw)
        assert pkt is not None
        assert pkt.channel == channel

    def test_channel_name_exceeds_max(self) -> None:
        channel = "X" * 300  # > _MAX_CHANNEL_LEN (256)
        raw = build_short_packet(channel, b"data")
        pkt = parse_lcm_packet(raw)
        assert pkt is None

    def test_unicode_channel(self) -> None:
        channel = "tëst_chännél"
        raw = build_short_packet(channel, b"payload")
        pkt = parse_lcm_packet(raw)
        assert pkt is not None
        assert pkt.channel == channel


# ---------------------------------------------------------------------------
# Fragmented message tests
# ---------------------------------------------------------------------------


class TestFragmentedMessage:
    def test_first_fragment_with_channel(self) -> None:
        payload = b"first_fragment_payload"
        raw = build_fragment_packet(
            "BIG_MSG", payload, seqno=7, fragment_no=0, n_fragments=5
        )
        pkt = parse_lcm_packet(raw)

        assert pkt is not None
        assert pkt.channel == "BIG_MSG"
        assert pkt.seqno == 7
        assert pkt.fragment_no == 0
        assert pkt.n_fragments == 5
        assert pkt.is_fragment is True
        assert pkt.is_first_fragment is True
        assert pkt.has_channel is True

    def test_subsequent_fragment_no_channel(self) -> None:
        payload = b"middle_data"
        raw = build_fragment_packet(
            None, payload, seqno=7, fragment_no=2, n_fragments=5
        )
        pkt = parse_lcm_packet(raw)

        assert pkt is not None
        assert pkt.channel is None
        assert pkt.is_fragment is True
        assert pkt.is_first_fragment is False
        assert pkt.has_channel is False
        assert pkt.payload == payload

    def test_fragment_header_too_short(self) -> None:
        # Only 12 bytes (less than the 20-byte minimum for long header)
        raw = struct.pack("!II", LCM2_MAGIC_LONG, 1) + b"short"
        pkt = parse_lcm_packet(raw)
        assert pkt is None


# ---------------------------------------------------------------------------
# Error handling / malformed packets
# ---------------------------------------------------------------------------


class TestMalformedPackets:
    def test_empty_data(self) -> None:
        assert parse_lcm_packet(b"") is None

    def test_too_short(self) -> None:
        assert parse_lcm_packet(b"\x00\x01\x02") is None

    def test_wrong_magic(self) -> None:
        raw = struct.pack("!II", 0xDEADBEEF, 1) + b"EXAMPLE\x00data"
        assert parse_lcm_packet(raw) is None

    def test_missing_null_terminator(self) -> None:
        # Short message with no null terminator after channel
        raw = struct.pack("!II", LCM2_MAGIC_SHORT, 1) + b"NO_NULL"
        assert parse_lcm_packet(raw) is None

    def test_invalid_utf8_channel(self) -> None:
        raw = struct.pack("!II", LCM2_MAGIC_SHORT, 1) + b"\xff\xfe\x00data"
        assert parse_lcm_packet(raw) is None


# ---------------------------------------------------------------------------
# Fingerprint tests
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_extract_fingerprint(self) -> None:
        payload = struct.pack(">Q", 0x123456789ABCDEF0) + b"rest"
        fp = extract_fingerprint(payload)
        assert fp == 0x123456789ABCDEF0

    def test_short_payload(self) -> None:
        assert extract_fingerprint(b"\x01\x02\x03") is None

    def test_exactly_8_bytes(self) -> None:
        payload = struct.pack(">Q", 42)
        assert extract_fingerprint(payload) == 42

    def test_fingerprint_to_hex(self) -> None:
        assert fingerprint_to_hex(0x0000000000000001) == "0x0000000000000001"
        assert fingerprint_to_hex(0) == "0x0000000000000000"
