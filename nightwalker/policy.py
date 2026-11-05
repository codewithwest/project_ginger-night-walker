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
    "navigation": AgentManifest(
        "navigation", frozenset({"browser.navigate", "browser.read_title"})
    ),
    "interaction": AgentManifest(
        "interaction", frozenset({"browser.click", "browser.type", "browser.scroll"})
    ),
    "extraction": AgentManifest(
        "extraction", frozenset({"browser.read_dom", "browser.read_text", "evidence.write"})
    ),
    "recovery": AgentManifest("recovery", frozenset({"browser.read_status", "browser.reload"})),
}


class PolicyEngine:
    BLOCKED_HOSTS = {
        "localhost", "localhost.localdomain", "metadata.google.internal",
        "metadata", "instance-data",
    }

    def authorize(self, manifest: AgentManifest, permission: str) -> None:
        if permission not in manifest.permissions:
            raise PolicyDenied(f"Permission denied: {manifest.capability} lacks {permission}")

    def validate_url(self, url: str, allow_private_networks: bool = False) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise PolicyDenied("Only absolute HTTP(S) URLs are permitted")
        if parsed.username or parsed.password:
            raise PolicyDenied("URLs containing embedded credentials are not permitted")
        try:
            port = parsed.port
        except ValueError as exc:
            raise PolicyDenied("Invalid URL port") from exc
        if port not in {None, 80, 443}:
            raise PolicyDenied("Only standard HTTP and HTTPS ports are permitted")
        host = parsed.hostname.lower().rstrip(".")
        if not allow_private_networks and (
            host in self.BLOCKED_HOSTS or host.endswith((".localhost", ".local", ".internal"))
        ):
            raise PolicyDenied("Local and internal hostnames are blocked by policy")
        try:
            addresses = {
                ipaddress.ip_address(info[4][0].split("%", 1)[0])
                for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            }
        except (socket.gaierror, ValueError) as exc:
            raise PolicyDenied("Hostname could not be resolved") from exc
        if not addresses:
            raise PolicyDenied("Hostname did not resolve to an address")
        if not allow_private_networks and any(not address.is_global for address in addresses):
            raise PolicyDenied(
                "Private, loopback, link-local, reserved, and non-global targets are blocked"
            )
