#!/usr/bin/env python3
"""A stub NetBox that can be told how to fail.

The README claims the error strings are what the tools actually return, checked
against a NetBox that answers 200, 403, 500, refuses the connection, and
accepts-then-stalls. This is that NetBox. It serves fictional IPAM data, so it
also doubles as a safe target for demos and screen recordings.

Run:
    python tests/stub_netbox.py                 # answers 200 with fixture data
    MODE=403 python tests/stub_netbox.py        # rejects the token
    MODE=500 python tests/stub_netbox.py        # server error
    MODE=stall python tests/stub_netbox.py      # accepts, never answers
    (for "refused": do not run it at all)

Then point the MCP server at it:
    NETBOX_URL=http://127.0.0.1:8099 NETBOX_TOKEN=stub python src/server.py
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

MODE = os.environ.get("MODE", "200")
PORT = int(os.environ.get("STUB_PORT", "8099"))

# Fictional IPAM. RFC 5737 / RFC 1918 documentation ranges only — nothing real.
PREFIXES = [
    {"id": 1, "prefix": "10.20.30.0/24", "description": "Server subnet",
     "vlan": {"vid": 30}},
    {"id": 2, "prefix": "10.20.40.0/24", "description": "Wireless clients",
     "vlan": {"vid": 40}},
    {"id": 3, "prefix": "192.0.2.0/24", "description": "Lab, documentation range",
     "vlan": None},
]

IPS = [
    {"address": "10.20.30.7/24", "dns_name": "exchange-01.example.net",
     "description": "Mail node"},
    {"address": "10.20.30.11/24", "dns_name": "wlc-01.example.net",
     "description": "Wireless controller"},
    {"address": "10.20.30.42/24", "dns_name": "netbox.example.net",
     "description": "Source of truth"},
]

VLANS = [
    {"vid": 30, "name": "servers"},
    {"vid": 40, "name": "wireless"},
    {"vid": 99, "name": "management"},
]

AVAILABLE = [{"address": f"10.20.30.{n}/24"} for n in (2, 3, 4)]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if MODE == "stall":
            # Accept the connection and never answer: exercises the read timeout,
            # which is a different code path from a refused connection.
            time.sleep(3600)
            return
        if MODE == "403":
            return self._send(403, {"detail": "Invalid token."})
        if MODE == "500":
            return self._send(500, {"detail": "Server error."})

        path = self.path
        if "/available-ips/" in path:
            return self._send(200, AVAILABLE)
        if path.startswith("/api/ipam/prefixes/"):
            return self._send(200, {"count": len(PREFIXES), "results": PREFIXES})
        if path.startswith("/api/ipam/ip-addresses/"):
            return self._send(200, {"count": len(IPS), "results": IPS})
        if path.startswith("/api/ipam/vlans/"):
            return self._send(200, {"count": len(VLANS), "results": VLANS})
        return self._send(404, {"detail": "Not found."})

    def log_message(self, fmt, *args):
        # One line per request, so a recording shows the tool actually calling out.
        print(f"stub[{MODE}] {fmt % args}", flush=True)


if __name__ == "__main__":
    print(f"stub NetBox on :{PORT} in MODE={MODE}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
