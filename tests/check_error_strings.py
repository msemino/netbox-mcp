#!/usr/bin/env python3
"""Check that every tool returns a sentence, in every way NetBox can fail.

The README claims the error strings are what the tools actually return. This is
the check behind that claim: it starts a stub NetBox in each failure mode, calls
all five tools against it, and asserts the returned text.

    python tests/check_error_strings.py

Exit code 0 means every tool, in every mode, returned the expected sentence and
nothing raised. A failure prints the tool, the mode, and what came back instead.
"""
import os
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
STUB = ROOT / "tests" / "stub_netbox.py"
PROBE = ROOT / "tests" / "_probe.py"
PORT = 8099
TIMEOUT = "3"

# mode -> the substring every tool must return in that mode.
EXPECTED = {
    "200": " | ",  # rows of text; the happy path returns data, not a sentence
    "403": "NetBox rejected the token (401/403). Check NETBOX_TOKEN.",
    "500": "NetBox returned HTTP 500 for this query.",
    "refused": "NetBox is unreachable at",
    "stall": f"NetBox did not answer within {TIMEOUT}s.",
}
# list_prefixes / list_vlans / prefix_report format their happy path without " | ".
HAPPY_EXCEPTIONS = {"list_prefixes": "10.20.30.0/24", "list_vlans": "VLAN30",
                    "prefix_report": "10.20.30.0/24"}

# How many tool lines _probe.py must print per mode. Kept in sync with its CALLS list.
EXPECTED_TOOLS = 5


def _wait_for_port(deadline=5.0):
    end = time.time() + deadline
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                return True
        time.sleep(0.05)
    return False


def run_mode(mode):
    env = dict(os.environ,
               NETBOX_TOKEN="stub",
               NETBOX_URL=f"http://127.0.0.1:{PORT}",
               NETBOX_TIMEOUT=TIMEOUT,
               MODE=mode)
    stub = None
    if mode != "refused":
        stub = subprocess.Popen([PY, str(STUB)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _wait_for_port():
            stub.kill()
            raise SystemExit(f"stub did not come up for MODE={mode}")
    try:
        out = subprocess.run([PY, str(PROBE)], env=env, capture_output=True,
                             text=True, timeout=120)
    finally:
        if stub:
            stub.kill()
            stub.wait()
    return out


def main():
    failures = []
    for mode, expected in EXPECTED.items():
        print(f"\n--- NetBox {mode} ---")
        proc = run_mode(mode)
        seen = 0
        for line in proc.stdout.splitlines():
            if "|" not in line:
                continue
            seen += 1
            tool, _, got = line.partition("|")
            tool, got = tool.strip(), got.strip()
            want = HAPPY_EXCEPTIONS.get(tool, expected) if mode == "200" else expected
            ok = want in got and not got.startswith("!! UNCAUGHT")
            print(f"  {'ok  ' if ok else 'FAIL'} {tool:<16} {got[:72]}")
            if not ok:
                failures.append((mode, tool, got))

        # A probe that dies before printing anything leaves the loop above with nothing to
        # iterate, and every assertion passes vacuously. That is how a check reports green
        # while proving nothing, so the count is asserted rather than assumed.
        if seen != EXPECTED_TOOLS:
            detail = (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1]
            print(f"  FAIL probe reported {seen}/{EXPECTED_TOOLS} tools "
                  f"(exit {proc.returncode}) — {detail[:100]}")
            failures.append((mode, f"probe/{seen}-of-{EXPECTED_TOOLS}", detail))

    print()
    if failures:
        for mode, tool, got in failures:
            print(f"FAIL {tool} in mode {mode}: {got}")
        return 1
    print(f"All {EXPECTED_TOOLS} tools return a sentence in all {len(EXPECTED)} modes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
