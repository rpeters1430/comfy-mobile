"""Wake only the administrator-configured PC; never accept destinations from a phone."""
import ipaddress
import math
import re
import socket
import threading
import time

COOLDOWN = 30


class WakeError(ValueError):
    pass


class WakePC:
    def __init__(self, mac, broadcast):
        self.mac = mac.replace(':', '').replace('-', '')
        self.broadcast = broadcast
        self.lock = threading.Lock()
        self.last = None
        try:
            address = ipaddress.IPv4Address(broadcast)
            self.enabled = bool(re.fullmatch(r'[0-9a-fA-F]{12}', self.mac)) and not (
                address.is_loopback or address.is_multicast or address.is_unspecified)
        except ValueError:
            self.enabled = False

    def status(self):
        with self.lock:
            return {'enabled': self.enabled, 'cooldown': max(0, math.ceil(COOLDOWN - (time.monotonic() - self.last))) if self.last is not None else 0,
                    'reason': '' if self.enabled else 'Set WOL_MAC and WOL_BROADCAST on the NAS to enable PC wake.'}

    def send(self):
        with self.lock:
            if not self.enabled: raise WakeError('PC wake is not configured')
            now = time.monotonic()
            if self.last is not None and now - self.last < COOLDOWN:
                raise WakeError('Wait 30 seconds between wake requests')
            packet = b'\xff' * 6 + bytes.fromhex(self.mac) * 16
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.settimeout(2)
                sock.sendto(packet, (self.broadcast, 9))
            self.last = now
            return {'sent': True, 'cooldown': COOLDOWN}
