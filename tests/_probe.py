#!/usr/bin/env python3
"""Call all five tools once and print exactly what each returns.

Used by check_error_strings.py, which runs it against the stub in every
failure mode. Kept separate because src/server.py reads its configuration at
import time, so each mode needs a fresh process.
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("nbserver", ROOT / "src" / "server.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


def _callable(name):
    """@mcp.tool() may return the function or a Tool wrapper; handle both."""
    obj = getattr(srv, name)
    return obj if callable(obj) else obj.fn


CALLS = [
    ("list_prefixes", ()),
    ("list_vlans", ()),
    ("find_ip", ("exchange",)),
    ("prefix_report", ("10.20.30.0/24",)),
    ("subnet_contents", ("10.20.30.0/24",)),
]

for name, args in CALLS:
    try:
        out = _callable(name)(*args)
    except Exception as exc:  # noqa: BLE001 - the point is to see what escapes
        out = f"!! UNCAUGHT {type(exc).__name__}: {exc}"
    first = str(out).splitlines()[0] if str(out).strip() else "(empty)"
    print(f"{name:>17} | {first}")
    sys.stdout.flush()
