# netbox-mcp

**A read-only MCP server over NetBox, served over HTTP for a whole engineering team.**

One process holds the NetBox API token. Engineers point their AI assistant at an HTTP
endpoint and ask questions in plain language — *"which IPs are free in the server
subnet?"*, *"what is 10.20.30.7?"* — against live IPAM data. Nobody installs anything
locally, and the token never leaves the server.

> Running in production on a telecom infrastructure engineering team.
> Five tools, one systemd unit, zero client-side setup.
>
> **Design notes:** [Your MCP server should probably not be a subprocess](docs/why-http-not-stdio.md)
> — why HTTP instead of stdio, what that costs, and why the error strings are prompts.

---

## The problem this solves

Infrastructure teams keep two kinds of knowledge, and they rot in different ways.

**Structured data** — subnets, IP addresses, VLANs — lives in NetBox. It is accurate and
queryable, but only through a web UI or a REST API. When an engineer is mid-incident and
needs the next free IP in a subnet, they open a browser tab, click through IPAM, and
squint.

**Narrative knowledge** — runbooks, incident write-ups, the reason a VLAN exists — lives
in a wiki or a git repository. Engineers now read this through AI assistants, which is
fast and pleasant.

So the assistant can reason about *why* the network looks like it does, but cannot see
*what it currently is*. The obvious fix is to copy IP addresses into the documentation.
That is the wrong fix: **a copied live value goes stale silently.** Six months later the
assistant confidently quotes an address that was reassigned in March.

`netbox-mcp` closes the gap from the other direction. Instead of copying the data into
the docs, it gives the assistant a way to *ask the source of truth directly*, at the
moment the question is asked.

## Why HTTP, and why one server

The common way to ship an MCP server is stdio: the assistant spawns the process locally.
That means every engineer installs Python, clones a repo, and pastes an API token into a
config file on their laptop.

For a team, this is worse than inconvenient — it is a security posture. Now the NetBox
token exists on *N* laptops, rotating it means chasing *N* people, and revoking access
for someone who left means hoping they deleted a dotfile.

Serving MCP over HTTP inverts that:

- **The token lives in exactly one place**, in an `EnvironmentFile` readable only by the
  service user. It never reaches a client.
- **Onboarding is a URL.** A new engineer adds one line to their assistant config.
- **Offboarding is a firewall rule**, not a trust exercise.
- **The tools are versioned centrally.** Fix a query once, everyone gets the fix.

The trade-off is real and worth stating: the endpoint is now a network service, and it
must be treated as one. It listens on an internal network or a private mesh — never the
public internet — and it is read-only.

## Read-only by construction

The `_api` helper issues `GET` and nothing else. There is no code path in this server
that writes to NetBox, so an assistant cannot be talked into deleting a prefix, no matter
how the prompt is phrased.

Pair that with a **read-only NetBox token** and the property holds even if the code is
wrong. Two independent mechanisms, because one of them will eventually be a mistake.

## Tools

| Tool | What it answers |
|---|---|
| `list_prefixes` | Every documented subnet, with VLAN and description. |
| `prefix_report` | For one subnet: how many IPs are documented, and the first free ones. |
| `find_ip` | Search by DNS name, description or address fragment. |
| `subnet_contents` | Every documented IP inside a subnet, in address order. |
| `list_vlans` | Known VLANs, ordered by VLAN ID. |

Tool docstrings are the interface. The assistant reads them to decide which tool to call,
so they are written for a reader who has never seen NetBox — each with a concrete example.

## Failure is a sentence, not a stack trace

When NetBox is down, an unguarded MCP tool hands the assistant a Python traceback, and the
assistant hands the operator a shrug.

Every tool here is wrapped so that transport failures come back as something actionable:

```
NetBox rejected the token (401/403). Check NETBOX_TOKEN.
NetBox is unreachable at http://netbox.internal:8090 (Connection refused).
NetBox did not answer within 20s.
```

The assistant can relay that to a human who knows what to do about it. This matters more
than it sounds: an incident is exactly when your tooling is most likely to be degraded,
and exactly when a confusing error costs the most.

---

## Install

Requires Python 3.10+ and a reachable NetBox instance.

```bash
git clone https://github.com/msemino/netbox-mcp
cd netbox-mcp
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Create a **read-only** API token in NetBox (*Admin → API Tokens*, uncheck *Write enabled*),
then:

```bash
cp .env.example .env      # edit it
chmod 600 .env
set -a && . ./.env && set +a
./venv/bin/python src/server.py
```

The server listens on `:8097` and serves MCP at `/mcp`.

### Run it as a service

```bash
sudo cp deploy/netbox-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now netbox-mcp
journalctl -u netbox-mcp -f
```

### Point an assistant at it

Claude Code:

```bash
claude mcp add --transport http netbox http://netbox-mcp.internal:8097/mcp
```

Or in an MCP client config:

```json
{
  "mcpServers": {
    "netbox": {
      "type": "http",
      "url": "http://netbox-mcp.internal:8097/mcp"
    }
  }
}
```

Then ask it things:

> *Which IPs are free in 10.20.30.0/24?*
> *What is 10.20.30.7, and what VLAN is it on?*
> *List every subnet that mentions "wireless".*

---

## Security notes

- **Never expose this on the public internet.** It is an internal service. Bind it to a
  private network or a mesh VPN.
- **The token is read-only.** Both by NetBox permission and by the absence of any write
  path in the code.
- **No secrets in the repository.** `.env` is gitignored; `.env.example` carries the
  shape, not the values.
- There is **no authentication on the endpoint itself.** Anyone who can reach the port can
  read your IPAM. Treat network reachability as the access control it is, and put the
  service behind a reverse proxy with auth if that assumption does not hold for you.

## Extending it

Adding a tool is one decorated function. Keep three rules and the assistant will use it
correctly:

1. **`GET` only.** The read-only guarantee is the whole security model.
2. **The docstring is the API.** Write it for someone who does not know your schema, and
   include a concrete example argument.
3. **Return text a human could read.** Not JSON. The assistant is going to paraphrase it
   for an operator anyway, and readable output makes hallucinated fields obvious.

## License

MIT — see [LICENSE](LICENSE).
