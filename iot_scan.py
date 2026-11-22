#!/usr/bin/env python3
"""Find smart-home devices on the local network and say how to control them.

Nothing here touches a cloud: it uses the ARP table, a TCP port scan, SSDP,
mDNS and the discovery broadcasts of the common local protocols.

    .\\venv\\Scripts\\python.exe iot_scan.py
    .\\venv\\Scripts\\python.exe iot_scan.py --subnet 192.168.1.0/24 --out iot_devices.json

The report tells you which devices Olive can drive directly (Yeelight, WiZ,
Shelly, Tasmota, ESPHome, Hue, Kasa, Chromecast), which need one-time keys
(Tuya) and which speak Matter (commission once, then fully local).

Exit code is 0 when at least one controllable or identifiable device was found.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import socket
import struct
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

# Ports that identify local smart-home hardware.
PORT_NAMES = {
    80: "http",
    81: "http-alt",
    443: "https",
    1883: "mqtt",
    6053: "esphome",
    6668: "tuya",
    8008: "cast",
    8009: "cast",
    8123: "home-assistant",
    8443: "https-alt",
    8883: "mqtt-tls",
    9999: "kasa",
    55443: "yeelight",
}

# A small OUI table for the vendors that show up in home networks. Unknown
# prefixes are left blank on purpose: guessing is worse than saying nothing.
OUI_VENDORS = {
    "00:17:88": "Philips Hue",
    "00:55:da": "Philips Hue",
    "ec:b5:fa": "Philips Hue",
    "24:0a:c4": "Espressif",
    "24:6f:28": "Espressif",
    "30:ae:a4": "Espressif",
    "3c:61:05": "Espressif",
    "3c:71:bf": "Espressif",
    "48:e7:29": "Espressif",
    "4c:11:ae": "Espressif",
    "54:5a:a6": "Espressif",
    "5c:cf:7f": "Espressif",
    "68:c6:3a": "Espressif",
    "70:03:9f": "Espressif",
    "7c:9e:bd": "Espressif",
    "84:cc:a8": "Espressif",
    "84:f3:eb": "Espressif",
    "8c:aa:b5": "Espressif",
    "a4:cf:12": "Espressif",
    "c4:4f:33": "Espressif",
    "cc:50:e3": "Espressif",
    "dc:4f:22": "Espressif",
    "ec:fa:bc": "Espressif",
    "10:d5:61": "Tuya",
    "18:69:d8": "Tuya",
    "18:de:50": "Tuya",
    "50:02:91": "Tuya",
    "5c:e5:0c": "Tuya",
    "68:57:2d": "Tuya",
    "70:3a:cb": "Tuya",
    "84:e3:42": "Tuya",
    "d8:1d:72": "Tuya",
    "50:c7:bf": "TP-Link",
    "60:32:b1": "TP-Link",
    "98:da:c4": "TP-Link",
    "a8:57:4e": "TP-Link",
    "30:de:4b": "TP-Link",
    "d0:73:d5": "LIFX",
    "78:11:dc": "Yeelight",
    "04:cf:8c": "Yeelight",
    "7c:49:eb": "Yeelight",
    "f0:b4:29": "Yeelight",
    "44:4f:8e": "WiZ",
    "34:ea:34": "Shelly",
    "44:17:93": "Shelly",
    "c8:c9:a3": "Shelly",
    "e8:68:e7": "Shelly",
    "b0:be:76": "Sonoff",
}

# protocols Olive can drive with no cloud at all
LOCAL_PROTOCOLS = {
    "yeelight", "wiz", "shelly", "tasmota", "esphome", "hue", "lifx", "kasa",
    "wled", "chromecast", "matter", "homekit",
}


@dataclass
class Device:
    ip: str
    mac: str = ""
    vendor: str = ""
    name: str = ""
    protocols: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    detail: str = ""

    def add(self, protocol: str, detail: str = "") -> None:
        if protocol and protocol not in self.protocols:
            self.protocols.append(protocol)
        if detail and detail not in self.detail:
            self.detail = (self.detail + "; " + detail).strip("; ")

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["controllable"] = any(p in LOCAL_PROTOCOLS for p in self.protocols)
        return data


# ---------------------------------------------------------------- network info
def local_subnets() -> list[str]:
    """Every non-loopback IPv4 interface as ``a.b.c.d/prefix``."""
    try:
        import ifaddr  # shipped with zeroconf

        found: list[str] = []
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if isinstance(ip.ip, str) and not ip.ip.startswith("127."):
                    found.append(f"{ip.ip}/{ip.network_prefix}")
        if found:
            return found
    except Exception:
        pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return [ip + "/24"]
    except Exception:
        return ["192.168.1.0/24"]


def hosts_in(cidr: str) -> list[str]:
    import ipaddress

    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except Exception:
        return []
    if network.num_addresses > 1024:
        network = ipaddress.ip_network(cidr.rsplit(".", 1)[0] + ".0/24", strict=False)
    return [str(host) for host in network.hosts()]


def arp_table() -> dict[str, str]:
    """IP -> MAC from the OS ARP cache (already-seen neighbours)."""
    table: dict[str, str] = {}
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True,
                             timeout=10, errors="replace").stdout
    except Exception:
        return table
    for line in out.splitlines():
        hit = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})", line)
        if hit:
            table[hit.group(1)] = hit.group(2).replace("-", ":").lower()
    return table


def vendor_for(mac: str) -> str:
    if not mac:
        return ""
    return OUI_VENDORS.get(mac[:8].lower(), "")


# ------------------------------------------------------------------ TCP scan
async def _probe_port(ip: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def tcp_scan(hosts: Iterable[str], ports: Iterable[int],
                   timeout: float = 0.35, concurrency: int = 256) -> dict[str, list[int]]:
    ports = list(ports)
    semaphore = asyncio.Semaphore(concurrency)
    found: dict[str, list[int]] = {}

    async def check(ip: str, port: int) -> None:
        async with semaphore:
            if await _probe_port(ip, port, timeout):
                found.setdefault(ip, []).append(port)

    await asyncio.gather(*(check(ip, port) for ip in hosts for port in ports))
    return found


# --------------------------------------------------------------------- SSDP
SSDP_TARGETS = [
    ("ssdp:all", 1900),
    ("urn:schemas-upnp-org:device:basic:1", 1900),
    ("wifi_bulb", 1982),        # Yeelight
    ("urn:schemas-upnp-org:device:MediaRenderer:1", 1900),
]


def ssdp_scan(seconds: float = 3.0) -> dict[str, dict[str, str]]:
    """Ask UPnP/Yeelight devices to identify themselves."""
    results: dict[str, dict[str, str]] = {}
    for target, port in SSDP_TARGETS:
        deadline = time.monotonic() + seconds / len(SSDP_TARGETS) + 0.5
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.4)
            message = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: 239.255.255.250:{port}\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 2\r\n"
                f"ST: {target}\r\n\r\n"
            ).encode()
            for _ in range(3):
                for address in ("239.255.255.250", "255.255.255.255"):
                    try:
                        sock.sendto(message, (address, port))
                    except OSError:
                        pass
            while time.monotonic() < deadline:
                try:
                    data, (ip, _port) = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                text = data.decode("utf-8", "replace")
                headers = {}
                for line in text.splitlines()[1:]:
                    if ":" in line:
                        key, value = line.split(":", 1)
                        headers[key.strip().lower()] = value.strip()
                if headers:
                    headers["_raw"] = text[:400]
                    results.setdefault(ip, {}).update(headers)
            sock.close()
        except Exception:
            continue
    return results


# ---------------------------------------------------------------------- mDNS
MDNS_TYPES = {
    "_matter._tcp.local.": "matter",
    "_matterc._udp.local.": "matter",
    "_shelly._tcp.local.": "shelly",
    "_esphomelib._tcp.local.": "esphome",
    "_yeelight._tcp.local.": "yeelight",
    "_hap._tcp.local.": "homekit",
    "_googlecast._tcp.local.": "chromecast",
    "_wled._tcp.local.": "wled",
    "_hue._tcp.local.": "hue",
}


def mdns_scan(seconds: float = 4.0) -> dict[str, dict[str, str]]:
    try:
        from zeroconf import ServiceBrowser, ServiceState, Zeroconf
    except Exception:
        return {}
    results: dict[str, dict[str, str]] = {}
    zeroconf = Zeroconf()

    def make_handler(protocol: str):
        def handler(zc, service_type, name, state_change):
            if state_change is not ServiceState.Added:
                return
            try:
                info = zc.get_service_info(service_type, name, timeout=1500)
            except Exception:
                return
            if not info or not info.addresses:
                return
            ip = socket.inet_ntoa(info.addresses[0])
            entry = results.setdefault(ip, {})
            entry["mdns"] = protocol
            entry.setdefault("name", (info.name or "").split(".")[0])
            if info.properties:
                props = {}
                for key, value in info.properties.items():
                    try:
                        props[key.decode()] = (value or b"").decode("utf-8", "replace")
                    except Exception:
                        pass
                if props:
                    entry["props"] = json.dumps(props)[:300]
        return handler

    browsers = []
    try:
        for service_type, protocol in MDNS_TYPES.items():
            browsers.append(ServiceBrowser(zeroconf, service_type, make_handler(protocol)))
        time.sleep(seconds)
    finally:
        for browser in browsers:
            try:
                browser.cancel()
            except Exception:
                pass
        zeroconf.close()
    return results


# ------------------------------------------------------------- UDP discovery
def _udp_probe(port: int, payload: bytes, seconds: float = 2.0,
               replies: int = 8) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.4)
        for address in ("255.255.255.255", "239.255.255.250"):
            try:
                sock.sendto(payload, (address, port))
            except OSError:
                pass
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and len(found) < replies:
            try:
                data, (ip, _port) = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            found[ip] = data
        sock.close()
    except Exception:
        pass
    return found


def wiz_scan() -> dict[str, dict[str, Any]]:
    """WiZ bulbs answer an unauthenticated getPilot on UDP 38899."""
    payload = json.dumps({"method": "getPilot", "params": {}}).encode()
    found = {}
    for ip, data in _udp_probe(38899, payload).items():
        try:
            parsed = json.loads(data.decode("utf-8", "replace"))
        except Exception:
            continue
        found[ip] = parsed.get("result") or {}
    return found


def lifx_scan() -> list[str]:
    """LIFX answers a binary GetService packet on UDP 56700."""
    packet = bytearray(36)
    struct.pack_into("<HHI", packet, 0, 36, 1024, 2)  # size, protocol, GetService
    struct.pack_into("<I", packet, 4, 1)              # tagged
    replies = _udp_probe(56700, bytes(packet), seconds=1.5)
    return list(replies)


def kasa_scan() -> list[str]:
    """TP-Link Kasa answers its encrypted discovery probe on UDP 9999."""
    payload = b'{"system":{"get_sysinfo":{}}}'
    replies = _udp_probe(9999, payload, seconds=1.5)
    return list(replies)


# ------------------------------------------------------------ HTTP fingerprints
async def http_fingerprint(session, ip: str, port: int = 80) -> dict[str, str]:
    base = f"http://{ip}" if port == 80 else f"http://{ip}:{port}"
    hints: dict[str, str] = {}
    checks = (
        ("/shelly", "shelly"),
        ("/rpc/Shelly.GetDeviceInfo", "shelly"),
        ("/cm?cmnd=status", "tasmota"),
        ("/description.xml", "hue"),
        ("/", "web"),
    )
    for path, kind in checks:
        try:
            async with session.get(base + path,
                                   timeout=__import__("aiohttp").ClientTimeout(total=2.5),
                                   allow_redirects=True) as response:
                text = (await response.text(errors="replace"))[:1200]
        except Exception:
            continue
        low = text.lower()
        if kind == "shelly" and ("shelly" in low or '"device"' in low):
            hints["/shelly"] = text[:200]
            hints["protocol"] = "shelly"
            break
        if kind == "tasmota" and ("tasmota" in low or '"status"' in low):
            hints["protocol"] = "tasmota"
            hints["detail"] = text[:200]
            break
        if kind == "hue" and "<manufacturer>philips" in low:
            hints["protocol"] = "hue"
            hints["detail"] = "Philips Hue bridge"
            break
        if kind == "web":
            title = re.search(r"<title>([^<]{1,80})</title>", text, re.IGNORECASE)
            if title:
                hints["name"] = title.group(1).strip()
            if "esphome" in low:
                hints["protocol"] = "esphome"
            elif "wled" in low:
                hints["protocol"] = "wled"
            elif "tasmota" in low:
                hints["protocol"] = "tasmota"
            elif "shelly" in low:
                hints["protocol"] = "shelly"
            elif "yeelight" in low:
                hints["protocol"] = "yeelight"
    return hints


# -------------------------------------------------------------------- report
def classify(device: Device, ssdp: dict, mdns: dict, extra: dict[str, Any]) -> None:
    ports = set(device.ports)
    if ports & {8008, 8009}:
        device.add("chromecast", "Google Cast")
    if 55443 in ports:
        device.add("yeelight", "TCP 55443")
    if 6668 in ports:
        device.add("tuya", "TCP 6668 (needs device id + local key)")
    if 9999 in ports:
        device.add("kasa", "TP-Link Kasa discovery")
    if 6053 in ports:
        device.add("esphome", "TCP 6053")
    if 1883 in ports:
        device.add("mqtt", "TCP 1883")

    # SSDP
    server = (ssdp.get("server") or "").lower()
    raw = (ssdp.get("_raw") or "").lower()
    if "yeelight" in server or "yeelight" in raw:
        device.add("yeelight", "SSDP wifi_bulb")
    if "philips" in server or "hue" in server:
        device.add("hue", "SSDP")
    if "shelly" in server:
        device.add("shelly", "SSDP")
    if "tasmota" in server:
        device.add("tasmota", "SSDP")

    # mDNS
    protocol = (mdns.get("mdns") or "").lower()
    if protocol:
        device.add(protocol, "mDNS")
        if protocol == "chromecast":
            device.name = device.name or mdns.get("name", "")

    # Active UDP / HTTP probes (an absent result is None or False)
    if extra.get("wiz") is not None:
        device.add("wiz", "UDP 38899")
        state = extra["wiz"] or {}
        if state.get("state") is not None:
            device.detail = (device.detail + f"; state={state.get('state')}").strip("; ")
    if extra.get("lifx"):
        device.add("lifx", "UDP 56700")
    if extra.get("kasa"):
        device.add("kasa", "UDP 9999")
    if "http" in extra:
        hints = extra["http"]
        if hints.get("protocol"):
            device.add(hints["protocol"], "HTTP fingerprint")
        if hints.get("name"):
            device.name = device.name or hints["name"]
        if hints.get("detail"):
            device.add(hints["protocol"] or "web", hints["detail"][:80])


async def main() -> int:
    parser = argparse.ArgumentParser(description="Discover local IoT devices")
    parser.add_argument("--subnet", action="append", default=[],
                        help="CIDR to scan (repeatable); default: local interfaces")
    parser.add_argument("--out", default="iot_devices.json")
    parser.add_argument("--timeout", type=float, default=0.35)
    args = parser.parse_args()

    subnets = args.subnet or local_subnets()
    hosts: list[str] = []
    for cidr in subnets:
        hosts.extend(hosts_in(cidr))
    hosts = list(dict.fromkeys(hosts))

    print(f"scanning {len(hosts)} hosts on {', '.join(subnets)} ...")
    started = time.monotonic()

    arp = arp_table()
    # Probe the neighbours we know first, then the whole subnet.
    known = [ip for ip in arp if ip in hosts]
    ordered = known + [ip for ip in hosts if ip not in known]

    print("• TCP ports ...")
    open_ports = await tcp_scan(ordered, PORT_NAMES, timeout=args.timeout)

    print("• SSDP ...")
    ssdp = await asyncio.to_thread(ssdp_scan)
    print("• mDNS ...")
    mdns = await asyncio.to_thread(mdns_scan)
    print("• WiZ / LIFX / Kasa broadcasts ...")
    wiz = await asyncio.to_thread(wiz_scan)
    lifx = await asyncio.to_thread(lifx_scan)
    kasa = await asyncio.to_thread(kasa_scan)

    ips = set(open_ports) | set(ssdp) | set(mdns) | set(wiz) | set(lifx) | set(kasa)
    # Report neighbours even when no port answered, but only real hosts in the
    # scanned subnet: the ARP cache also holds multicast and broadcast rows.
    scanned = set(hosts)
    ips |= {ip for ip in arp if ip in scanned}

    devices: dict[str, Device] = {}
    for ip in sorted(ips, key=lambda value: tuple(int(p) for p in value.split("."))):
        mac = arp.get(ip, "")
        device = Device(ip=ip, mac=mac, vendor=vendor_for(mac),
                        ports=sorted(open_ports.get(ip, [])))
        classify(device, ssdp.get(ip, {}), mdns.get(ip, {}), {
            "wiz": wiz.get(ip),
            "lifx": ip in lifx,
            "kasa": ip in kasa,
        })
        if any(p in PORT_NAMES for p in device.ports) or device.protocols:
            devices[ip] = device

    # HTTP fingerprints for devices that look like hubs or ESP boards.
    import aiohttp

    async with aiohttp.ClientSession() as session:
        candidates = [d for d in devices.values()
                      if 80 in d.ports and not ({"chromecast"} & set(d.protocols))]
        results = await asyncio.gather(
            *(http_fingerprint(session, d.ip) for d in candidates))
    for device, hints in zip(candidates, results):
        if hints:
            classify(device, {}, {}, {"http": hints})

    report = [d.as_dict() for d in devices.values()]
    report.sort(key=lambda item: (not item["controllable"], item["ip"]))
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"scanned_at": time.time(), "subnets": subnets,
                   "devices": report}, handle, indent=1)

    print(f"\nfound {len(report)} device(s) in {time.monotonic() - started:.1f}s\n")
    for device in report:
        labels = ", ".join(device["protocols"]) or "unknown"
        vendor = device["vendor"] or "?"
        print(f"  {device['ip']:<15} {vendor:<14} {labels}")
        if device["name"]:
            print(f"    name: {device['name']}")
        if device["ports"]:
            names = [f"{port}/{PORT_NAMES.get(port, '?')}" for port in device["ports"]]
            print(f"    ports: {', '.join(names)}")
        if device["detail"]:
            print(f"    detail: {device['detail'][:160]}")
    print(f"\nJSON: {args.out}")

    controllable = [d for d in report if d["controllable"]]
    unknown = [d for d in report if not d["controllable"] and d["protocols"]]
    if controllable:
        print("\nDirectly controllable without a cloud: " +
              ", ".join(f"{d['ip']} ({', '.join(d['protocols'])})" for d in controllable))
    if unknown:
        print("Present but not directly driveable yet: " +
              ", ".join(f"{d['ip']} ({', '.join(d['protocols'])})" for d in unknown))
    return 0 if report else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
