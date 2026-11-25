from __future__ import annotations

import ipaddress
import socket
import time
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit
from uuid import uuid4

_MULTICAST_ADDRESS = ("239.255.255.250", 3702)
_MAX_DISCOVERY_SECONDS = 4.0
_MAX_RESPONSES = 64
_MAX_RESPONSE_BYTES = 65_535
_PROBE = b"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <s:Header>
  <a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>
  <a:MessageID>urn:uuid:__MESSAGE_ID__</a:MessageID>
  <a:ReplyTo><a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address></a:ReplyTo>
  <a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
 </s:Header>
 <s:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></s:Body>
</s:Envelope>"""


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _scope_values(root: ET.Element) -> dict[str, str]:
    values = {}
    for element in root.iter():
        if _local_name(element) != "Scopes" or not element.text:
            continue
        for scope in element.text.split():
            parts = urlsplit(scope)
            path = parts.path.strip("/").split("/")
            if len(path) >= 2 and path[0] in {"name", "hardware"}:
                values[path[0]] = unquote(path[-1])
    return values


def _parse_probe_match(payload: bytes, source_ip: str) -> dict[str, str | None] | None:
    normalized_payload = payload.upper()
    if b"<!DOCTYPE" in normalized_payload or b"<!ENTITY" in normalized_payload:
        return None
    try:
        source = ipaddress.ip_address(source_ip)
        if not (source.is_private or source.is_link_local):
            return None
        root = ET.fromstring(payload)
    except (ValueError, ET.ParseError):
        return None

    has_video_transmitter_type = any(
        _local_name(element) == "Types"
        and "networkvideotransmitter" in (element.text or "").lower()
        for element in root.iter()
    )
    if not has_video_transmitter_type:
        return None

    xaddrs = []
    for element in root.iter():
        if _local_name(element) != "XAddrs" or not element.text:
            continue
        for address in element.text.split():
            parsed = urlsplit(address)
            if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username:
                xaddrs.append(address)

    if not xaddrs:
        return None

    scopes = _scope_values(root)
    return {
        "ip": str(source),
        "manufacturer": scopes.get("hardware"),
        "model": scopes.get("name"),
        "onvif_endpoint": xaddrs[0],
        "poe_switch_port": None,
    }


def discover_onvif_cameras(timeout: float = 2.0) -> list[dict[str, str | None]]:
    """Discover ONVIF video transmitters on the local multicast link only."""
    timeout = min(max(float(timeout), 0.25), _MAX_DISCOVERY_SECONDS)
    payload = _PROBE.replace(b"__MESSAGE_ID__", str(uuid4()).encode("ascii"))
    devices: dict[str, dict[str, str | None]] = {}
    discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        discovery_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        discovery_socket.bind(("", 0))
        discovery_socket.sendto(payload, _MULTICAST_ADDRESS)
        deadline = time.monotonic() + timeout
        while len(devices) < _MAX_RESPONSES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            discovery_socket.settimeout(remaining)
            try:
                response, address = discovery_socket.recvfrom(_MAX_RESPONSE_BYTES)
            except socket.timeout:
                break
            device = _parse_probe_match(response, address[0])
            if device is not None:
                devices[device["ip"]] = device
    finally:
        discovery_socket.close()
    return [devices[ip] for ip in sorted(devices)]