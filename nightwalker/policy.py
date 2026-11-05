from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


class PolicyDenied(PermissionError):
    pass


@dataclass(frozen=True)
class AgentManifest:
    capability: str
    permissions: frozenset[str]
    allowed_domains: frozenset[str] = frozenset()


DEFAULT_MANIFESTS = {
    "navigation": AgentManifest("navigation", frozenset({"browser.navigate", "browser.read_title"})),
    "interaction": AgentManifest("interaction", frozenset({"browser.click", "browser.type", "browser.scroll"})),
    "extraction": AgentManifest("extraction", frozenset({"browser.read_dom", "browser.read_text", "evidence.write"})),
    "recovery": AgentManifest("recovery", frozenset({"browser.read_status", "browser.reload"})),
}


class PolicyEngine:
    def authorize(self, manifest: AgentManifest, permission: str) -> None:
        if permission not in manifest.permissions:
            raise PolicyDenied(f"Permission denied: {manifest.capability} lacks {permission}")

    def validate_url(self, url: str, allow_private_networks: bool = False) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise PolicyDenied("Only absolute HTTP(S) URLs are permitted")
        host = parsed.hostname.lower()
        if host in {"localhost", "localhost.localdomain"} and not allow_private_networks:
            raise PolicyDenied("Localhost navigation is disabled by policy")
        try:
            addresses = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)}
        except socket.gaierror as exc:
            raise PolicyDenied("Hostname could not be resolved") from exc
        if not allow_private_networks and any(
            addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
            for addr in addresses
        ):
            raise PolicyDenied(
                "Private, loopback, link-local, and reserved network targets are blocked"
            )
