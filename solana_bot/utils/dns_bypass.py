"""
DNS Monkey-Patch for Telecom Italia DNS Hijacking

This module patches socket.getaddrinfo to force resolution of quote-api.jup.ag
to a known CloudFlare IP, bypassing ISP DNS hijacking.

Usage:
    import dns_bypass  # Must be first import!

NOTE: Import this BEFORE any network libraries (aiohttp, requests, etc.)
"""

import socket
import logging

logger = logging.getLogger(__name__)

# Store original function
_original_getaddrinfo = socket.getaddrinfo

# DNS mapping for hijacked domains
DNS_MAPPING = {
    "quote-api.jup.ag": "104.18.43.59",  # CloudFlare IP
}


def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """
    Patched getaddrinfo that forces resolution for known hijacked domains.
    
    Args:
        host: Hostname to resolve
        port: Port number
        family, type, proto, flags: Standard socket args
    
    Returns:
        Address info tuple
    """
    # Check if this is a known hijacked domain
    if host in DNS_MAPPING:
        forced_ip = DNS_MAPPING[host]
        logger.debug(f"DNS bypass: {host} → {forced_ip}")
        # Return address info for forced IP
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (forced_ip, port))]
    
    # Use original function for other domains
    return _original_getaddrinfo(host, port, family, type, proto, flags)


def install():
    """Install the DNS bypass monkeypatch."""
    socket.getaddrinfo = patched_getaddrinfo
    logger.info("✅ DNS Monkey-Patch installed for Jupiter API")
    print("[SYSTEM] DNS Monkey-Patch applied for Jupiter API bypass")


def uninstall():
    """Restore original getaddrinfo (for testing)."""
    socket.getaddrinfo = _original_getaddrinfo
    logger.info("DNS Monkey-Patch uninstalled")


# Auto-install on import
install()
