#!/usr/bin/env python3
"""
netbox-mcp — a read-only MCP server over NetBox, served over HTTP for a whole team.

One process holds the NetBox API token. Engineers point their AI assistant at the
HTTP endpoint and query live IPAM data; nobody installs anything locally and the
token never leaves the server.

Configuration is entirely by environment:
    NETBOX_URL    base URL of the NetBox instance (e.g. http://netbox.internal:8090)
    NETBOX_TOKEN  a NetBox API token — create it read-only
    HOST          bind address (default 0.0.0.0)
    PORT          bind port    (default 8097)

Run:
    NETBOX_URL=... NETBOX_TOKEN=... python src/server.py
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

NETBOX_URL = os.environ.get("NETBOX_URL", "http://localhost:8080").rstrip("/")
NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8097"))
TIMEOUT = int(os.environ.get("NETBOX_TIMEOUT", "20"))

if not NETBOX_TOKEN:
    raise SystemExit("NETBOX_TOKEN is required. Create a read-only token in NetBox.")

mcp = FastMCP("netbox", host=HOST, port=PORT)


def _api(path: str):
    """GET {NETBOX_URL}/api{path} and return the decoded JSON body.

    Only ever issues GET requests: this server is read-only by construction,
    not by convention.
    """
    req = urllib.request.Request(
        f"{NETBOX_URL}/api{path}",
        headers={"Authorization": f"Token {NETBOX_TOKEN}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def _guard(fn):
    """Turn transport failures into a sentence the model can act on.

    An MCP tool that raises gives the assistant a stack trace. An MCP tool that
    explains why it failed lets the assistant tell the operator what to check.
    """

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return "NetBox rejected the token (401/403). Check NETBOX_TOKEN."
            return f"NetBox returned HTTP {exc.code} for this query."
        except urllib.error.URLError as exc:
            return f"NetBox is unreachable at {NETBOX_URL} ({exc.reason})."
        except TimeoutError:
            return f"NetBox did not answer within {TIMEOUT}s."

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@mcp.tool()
@_guard
def list_prefixes() -> str:
    """List every documented prefix/subnet with its VLAN and description."""
    data = _api("/ipam/prefixes/?limit=500")
    rows = []
    for p in data.get("results", []):
        vlan = p.get("vlan")
        tag = f" VLAN{vlan['vid']}" if vlan else ""
        rows.append(f"{p['prefix']}{tag} - {p.get('description', '')}")
    return "\n".join(rows) or "No prefixes documented."


@mcp.tool()
@_guard
def prefix_report(prefix: str) -> str:
    """Report on one subnet: how many IPs are documented and which are free.

    Example: prefix_report("10.20.30.0/24")
    """
    quoted = urllib.parse.quote(prefix)
    matches = _api(f"/ipam/prefixes/?prefix={quoted}").get("results", [])
    if not matches:
        return f"No prefix {prefix} in NetBox."

    pid = matches[0]["id"]
    used = _api(f"/ipam/ip-addresses/?parent={quoted}&limit=1").get("count", 0)
    available = _api(f"/ipam/prefixes/{pid}/available-ips/?limit=10")
    free = [ip["address"].split("/")[0] for ip in available] if isinstance(available, list) else []

    return (
        f"{prefix} ({matches[0].get('description', '')})\n"
        f"Documented IPs: {used}\n"
        f"First free IPs: {', '.join(free) or 'none available'}"
    )


@mcp.tool()
@_guard
def find_ip(query: str) -> str:
    """Search IPs by DNS name, description or address fragment.

    Example: find_ip("exchange"), find_ip("wlc"), find_ip(".80.7")
    """
    quoted = urllib.parse.quote(query)
    data = _api(f"/ipam/ip-addresses/?q={quoted}&limit=50")
    rows = [
        f"{ip['address'].split('/')[0]} | {ip.get('dns_name', '')} | {ip.get('description', '')}"
        for ip in data.get("results", [])
    ]
    return "\n".join(rows) or f"Nothing matches '{query}'."


@mcp.tool()
@_guard
def subnet_contents(prefix: str) -> str:
    """List every documented IP inside a subnet, in address order.

    Example: subnet_contents("10.20.30.0/24")
    """
    quoted = urllib.parse.quote(prefix)
    data = _api(f"/ipam/ip-addresses/?parent={quoted}&limit=300")
    results = sorted(data.get("results", []), key=lambda x: x["address"])
    rows = [
        f"{ip['address'].split('/')[0]} | {ip.get('dns_name', '')} | {ip.get('description', '')}"
        for ip in results
    ]
    return "\n".join(rows) or f"No IPs documented in {prefix}."


@mcp.tool()
@_guard
def list_vlans() -> str:
    """List known VLANs, ordered by VLAN ID."""
    data = _api("/ipam/vlans/?limit=500&ordering=vid")
    rows = [f"VLAN{v['vid']} {v['name']}" for v in data.get("results", [])]
    return "\n".join(rows) or "No VLANs documented."


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
