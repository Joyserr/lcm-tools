"""UDP multicast socket management for LCM traffic capture.

Creates and manages a UDP socket that joins the LCM multicast group,
receives raw datagrams, parses them via the protocol module, and
dispatches PacketInfo objects to registered callbacks.
"""

from __future__ import annotations

import select
import socket
import struct
import sys
import threading
from typing import Callable, Optional

from lcm_tools.protocol import (
    DEFAULT_MC_ADDR,
    DEFAULT_MC_PORT,
    PacketInfo,
    parse_lcm_packet,
)

# 4 MB receive buffer — helps avoid packet loss under burst traffic
_RCVBUF_SIZE: int = 4 * 1024 * 1024

PacketCallback = Callable[[PacketInfo], None]


def create_multicast_socket(
    mc_addr: str = DEFAULT_MC_ADDR,
    mc_port: int = DEFAULT_MC_PORT,
    interface: Optional[str] = None,
) -> socket.socket:
    """Create a UDP socket and join the given multicast group.

    Args:
        mc_addr: Multicast group address (e.g. "239.255.76.67").
        mc_port: Multicast port (e.g. 7667).
        interface: Local interface IP to bind to.  ``None`` means
            ``INADDR_ANY`` (let the OS choose).

    Returns:
        A configured, non-blocking UDP socket ready to receive
        multicast datagrams.

    Raises:
        OSError: If the socket cannot be created or the group cannot
            be joined (e.g. no route to multicast address).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # macOS / FreeBSD require SO_REUSEPORT in addition to SO_REUSEADDR
    if sys.platform == "darwin" or "freebsd" in sys.platform:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    sock.bind(("", mc_port))

    # Build the IP_ADD_MEMBERSHIP request
    group_bin = socket.inet_aton(mc_addr)
    if interface:
        iface_bin = socket.inet_aton(interface)
    else:
        iface_bin = struct.pack("=I", socket.INADDR_ANY)

    mreq = group_bin + iface_bin
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    # Enlarge receive buffer
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, _RCVBUF_SIZE)
    except OSError:
        pass  # OS may refuse; proceed with default

    sock.setblocking(False)
    return sock


def drop_multicast_membership(
    sock: socket.socket,
    mc_addr: str = DEFAULT_MC_ADDR,
    interface: Optional[str] = None,
) -> None:
    """Leave the multicast group before closing the socket."""
    group_bin = socket.inet_aton(mc_addr)
    iface_bin = (
        socket.inet_aton(interface)
        if interface
        else struct.pack("=I", socket.INADDR_ANY)
    )
    mreq = group_bin + iface_bin
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, mreq)
    except OSError:
        pass


def listen_packets(
    sock: socket.socket,
    callback: PacketCallback,
    stop_event: threading.Event,
    timeout: float = 0.5,
) -> None:
    """Read packets from *sock* and invoke *callback* until *stop_event* is set.

    This function is designed to run in a dedicated thread.

    Args:
        sock: A non-blocking multicast socket from :func:`create_multicast_socket`.
        callback: Called for every successfully parsed LCM packet.
        stop_event: Set this event to request a clean shutdown.
        timeout: ``select`` timeout in seconds; controls how quickly the
            function checks *stop_event*.
    """
    while not stop_event.is_set():
        readable, _, _ = select.select([sock], [], [], timeout)
        if not readable:
            continue

        try:
            data, sender_addr = sock.recvfrom(65536)
        except OSError:
            # Socket may have been closed during shutdown
            break

        pkt = parse_lcm_packet(data, sender_addr)
        if pkt is not None:
            callback(pkt)


def run_listener(
    callback: PacketCallback,
    mc_addr: str = DEFAULT_MC_ADDR,
    mc_port: int = DEFAULT_MC_PORT,
    interface: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
) -> threading.Event:
    """Convenience: start a background listener thread.

    Returns the ``threading.Event`` that can be set to stop the listener.
    """
    if stop_event is None:
        stop_event = threading.Event()

    sock = create_multicast_socket(mc_addr, mc_port, interface)

    def _worker() -> None:
        try:
            listen_packets(sock, callback, stop_event)
        finally:
            drop_multicast_membership(sock, mc_addr, interface)
            sock.close()

    t = threading.Thread(target=_worker, daemon=True, name="lcm-listener")
    t.start()
    return stop_event
