"""Réseau sortant : repli en IPv4 quand l'IPv6 du réseau ne passe pas.

Une box peut annoncer l'IPv6 (adresse et passerelle attribuées) sans que
rien ne sorte réellement. Les navigateurs et curl essaient alors l'IPv4 en
parallèle et ne voient rien ; Python, lui, attend l'échec de chaque adresse
IPv6 avant de passer à l'IPv4 : 8 à 40 s perdues à CHAQUE connexion vers
Mistral, Eurostat, OpenAlex… Cas vécu : des vérifications passées de
~5 s à plus de 20 s.

configure() teste une fois l'IPv6 au démarrage (≤ PROBE_TIMEOUT_S) et, s'il
ne passe pas, force l'IPv4 pour toutes les requêtes (requests → urllib3).
FORCE_IPV4 dans .env : auto (défaut), 1 (toujours IPv4), 0 (système)."""

import socket

import urllib3.util.connection

PROBE_HOST = "api.mistral.ai"  # le service dont dépend tout le pipeline
PROBE_TIMEOUT_S = 1.5

_system_family = urllib3.util.connection.allowed_gai_family


def ipv6_works(host: str = PROBE_HOST, timeout: float = PROBE_TIMEOUT_S):
    """True / False si une connexion IPv6 vers host aboutit ou non ; None si
    la question ne se pose pas (pas d'adresse IPv6, pas d'IPv6 local)."""
    try:
        infos = socket.getaddrinfo(host, 443, socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return None
    family, kind, proto, _, addr = infos[0]
    try:
        s = socket.socket(family, kind, proto)
    except OSError:
        return None
    s.settimeout(timeout)
    try:
        s.connect(addr)
        return True
    except OSError:
        return False
    finally:
        s.close()


def force_ipv4(on: bool = True):
    urllib3.util.connection.allowed_gai_family = (lambda: socket.AF_INET) if on else _system_family


def configure(mode: str = "auto") -> str:
    """Applique FORCE_IPV4 ; retourne « ipv4 » si l'IPv4 est forcé, sinon « system »."""
    mode = (mode or "auto").strip().lower()
    if mode in ("0", "false", "non", "no"):
        force_ipv4(False)
        return "system"
    if mode in ("1", "true", "oui", "yes") or ipv6_works() is False:
        force_ipv4(True)
        return "ipv4"
    return "system"
