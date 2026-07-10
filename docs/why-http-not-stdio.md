# Your MCP server should probably not be a subprocess

Every MCP quickstart teaches you the same shape. You add a block to a JSON config, your
assistant spawns a subprocess, and the two talk over stdin and stdout. It works on the
first try, which is exactly why almost nobody questions it.

stdio is the right default for a single developer on a single laptop. It quietly becomes
the wrong one the moment a second person needs the same server — and the reason has
nothing to do with performance.

I hit this building `netbox-mcp`, a read-only MCP server over [NetBox](https://netbox.dev),
the IPAM/DCIM system of record for a network infrastructure team. Engineers wanted to ask
their AI assistant *"which addresses are free in this subnet"* and get an answer from the
live source of truth instead of a stale copy pasted into a wiki page. The tool itself is
unremarkable: five read-only queries wrapped in FastMCP. The interesting decision was the
transport.

---

## What stdio actually asks of you

The stdio config block is short, so it reads as cheap. Expand what it means when seven
engineers each add it to their assistant:

You now have seven copies of the server. Seven Python environments to keep at the same
version. When you fix a bug in a tool description — and you will, because tool descriptions
are prompts and prompts get iterated — you have shipped that fix to nobody. The engineer
who hasn't pulled is running last month's semantics against this month's data, and the
failure is silent: the model gets an answer, it's just the wrong shape of answer.

More seriously, you have seven copies of the API token. It sits in plaintext, in a config
file, in a home directory, on a laptop that leaves the building. Rotating it is a message
in a group chat and a hope. When someone leaves the company, revoking their access to the
system of record means either rotating the shared token for everyone, or trusting that they
deleted a file. Neither of those is a control. They're both a feeling.

And you have no idea what anyone is asking. The server writes its logs to the same laptop
that spawned it. There is no central record that the tool was used, that it was slow, that
it started returning 403s two days before anyone reported it.

None of this is a flaw in stdio. stdio is doing precisely what it was designed to do: give
one user one process with one user's credentials. The mismatch is that a system of record
isn't one user's resource.

## What HTTP gives back

One process, on one host, with one token, in one place.

```
claude mcp add --transport http netbox http://netbox-mcp.internal:9004/mcp
```

That's the entire client-side install. Nobody clones a repo, nobody creates a virtualenv,
nobody is asked to keep a secret. Token rotation is `systemctl restart` and everyone gets
it at once. Version skew is not mitigated, it's impossible. Access is granted and revoked
by whether a machine can reach a host, which is a control you can actually audit.

The client config, notably, contains no secret at all. That's not a small thing. The most
common way credentials leak is not an attacker — it's a developer pasting a config file
into a ticket to explain why their tool isn't working.

## Now the honest part

Serving over HTTP doesn't make the security problem disappear. It moves it, and you have to
be willing to say where it went.

stdio hands you a security property for free, and it's easy not to notice you were relying
on it: **the transport is the user boundary**. The operating system already authenticated
the person. The process runs as them. Nobody else can talk to that pipe.

Bind an MCP server to a TCP port and you have thrown that away. The MCP specification has an
authorization framework, and for anything with real blast radius you should use it. I didn't.
I made **reachability the access control**: the service binds to an internal interface,
reachable only from inside the network. If you can open a TCP connection to it, you can query
it.

That is a real control, not a fig leaf — it's the same one that protects most internal
Prometheus endpoints on earth. But it is *coarse*. It has exactly two states, and everyone
who is inside has identical rights. Which brings me to the thing that makes the whole design
hold together.

**Read-only is not a preference here. It's the load-bearing assumption.** So I enforced it
twice, in two places that fail independently:

- **By construction.** The internal `_api` helper issues `GET` and nothing else. There is no
  code path in this server that can write to NetBox. You would have to add one on purpose.
- **By credential.** The token it authenticates with is provisioned read-only in NetBox. Even
  if the code were wrong, the API refuses.

Two mechanisms for one property looks redundant until you ask what each one protects against.
The first fails when a well-meaning colleague adds a "quick" write helper six months from now.
The second fails when someone regenerates the token and NetBox hands them default scopes.
Neither is a hypothetical; both are Tuesday. Defense in depth isn't paranoia about attackers.
It's an admission that *you* will eventually make one of these mistakes, and a decision that
the system should survive it.

I also gave up per-user attribution. The server logs record what was asked, not who asked it.
For read-only IPAM lookups, I'll take that trade. **If this server could write, I would stop
here and implement OAuth before shipping** — not because writes are scarier in the abstract,
but because an unattributable write to a system of record is an incident you cannot
investigate afterward. That's the line. It's worth knowing where yours is before you're
standing on the wrong side of it.

## Centralizing means centralizing the failure too

With stdio, a broken server breaks one laptop. Over HTTP, it breaks everyone at once. You
don't get to hand-wave that away; you design for it.

What makes this specifically dangerous with an LLM in the loop is the failure *mode*. A tool
that raises an exception hands the model a stack trace. The model, being helpful, will
summarize it — and then, being helpful, will often answer the original question anyway from
its training data. Ask which VLANs exist on a switch, let the tool fail, and you may well get
a fluent, plausible, entirely fabricated list of VLAN IDs. The tool didn't lie. It just
declined to say anything the model could use, so the model filled the silence.

So every tool call is wrapped:

```python
def _guard(fn):
    """Turn transport failures into a sentence the model can act on."""
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
    return wrapper
```

Every branch returns a plain sentence stating what failed and, where possible, what to check.
Given `NetBox is unreachable at ...`, assistants reliably do the right thing: they report that
they cannot reach the source of truth, and they decline to guess. That last part is the whole
point of writing the error messages carefully.

**Error strings in an MCP server are not logs. They are prompts.** They are consumed by a
language model, not read by a human, and they are the only thing standing between a network
partition and a confidently invented subnet.

## So: which one?

Reach for **stdio** when the server is a personal tool — when it touches local files, drives
something on the user's own machine, or authenticates with credentials that belong to that
individual. The subprocess boundary is doing real work for you there, and running a network
service to talk to your own filesystem is silly.

Reach for **HTTP** when the server fronts a shared system of record: one service credential,
many humans, and an answer that must be the same for all of them. Then accept the bill that
comes with it — you now own authentication, or you own an explicit, written-down decision not
to have it.

The thing worth internalizing is that this was never a transport question. `stdio` versus
`streamable-http` is one line in `mcp.run()`. What actually changed was where the secret
lives, who can be offboarded, and what happens to seven engineers when one host goes down.
Those are operational questions wearing a protocol costume, and MCP being new doesn't grant
anyone an exemption from them.

Most of the MCP conversation right now is about consuming servers. The moment you write one
that other people depend on, you're not integrating an AI feature. You're running a service.

---

*`netbox-mcp` is open source (MIT): [github.com/msemino/netbox-mcp](https://github.com/msemino/netbox-mcp)
— five read-only tools, one systemd unit, and a README that argues with itself about security.
It runs in production for an infrastructure engineering team.*
