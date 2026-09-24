"""Repli IPv4 (server/network.py) — sans réseau.

Lancer : python tests/test_network.py   (ou python -m pytest tests)"""

import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import urllib3.util.connection as uc  # noqa: E402

from server import network  # noqa: E402


def test_forced_modes_need_no_probe():
    probe = network.ipv6_works
    network.ipv6_works = lambda *a, **k: (_ for _ in ()).throw(AssertionError("test IPv6 inutile"))
    try:
        assert network.configure("1") == "ipv4" and uc.allowed_gai_family() == socket.AF_INET
        assert network.configure("0") == "system" and uc.allowed_gai_family is network._system_family
    finally:
        network.ipv6_works = probe
        network.force_ipv4(False)


def test_auto_falls_back_to_ipv4_only_when_ipv6_is_broken():
    probe = network.ipv6_works
    try:
        for works, expected in ((False, "ipv4"), (True, "system"), (None, "system")):
            network.ipv6_works = lambda *a, _w=works, **k: _w
            network.force_ipv4(False)
            assert network.configure("auto") == expected
            assert (uc.allowed_gai_family() == socket.AF_INET) == (expected == "ipv4")
    finally:
        network.ipv6_works = probe
        network.force_ipv4(False)


def test_probe_without_ipv6_address():
    assert network.ipv6_works("hote-inexistant.invalid", timeout=0.1) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
