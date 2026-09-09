# picoagent-plugins

The plugins that prove picoagent's architecture: each one implements a capability the
minimal core deliberately does not ship, through the same registries and events any
third-party plugin uses. They lived in picoagent's `examples/plugins/` until the examples
outgrew the harness they were examples of; each directory here kept its history
(`git subtree split`), and later plugins land here directly - the ingestion pair arrived
from picoagent PRs #16/#17 when the destination changed under them.

| Plugin | Gives the agent |
|---|---|
| [`mcp`](mcp/) | MCP servers as tools: stdio transport, handshake, tool translation, per-server env allowlist |
| [`permission-gate`](permission-gate/) | a confirmation prompt before destructive commands and protected-path writes |
| [`credential-guard`](credential-guard/) | a credential store, a tool-call guard over key files, and shell-env narrowing on top of core's allowlist |
| [`rules`](rules/) | per-file rules delivered when the agent touches a matching file, each rule approved before it reaches the prompt |
| [`agents`](agents/) | a child-agent tool (one level deep, gated through the parent's bus) plus delegation, background-run, schedule and automation skills |
| [`compaction`](compaction/) | context compaction as a `context`-event rewrite |
| [`complete`](complete/) | shell-style completion for slash commands |
| [`tdd-guard`](tdd-guard/) | test-integrity mechanisms: confirm weakening edits, prove changed tests run red against pre-change code |
| [`sdlc-evidence`](sdlc-evidence/) | probe the ASD STIG's machine-checkable process artifacts; evidence status, never determinations |
| [`secure-dev-policy`](secure-dev-policy/) | classify secure-dev obligations correctly (post M-26-05); check SBOMs against the CISA 2026 minimum elements |
| [`doc-ooxml`](doc-ooxml/) | read `.docx`/`.xlsx` as evidence with the standard library alone: quotations verified against headings and cells, template italics told from content, empty sections named |
| [`doc-pdf`](doc-pdf/) | read a PDF as evidence: verify a quotation resolves to a real page before citing it, pin the edition by hash, distinguish italic guidance from content (needs `pymupdf`, the collection's one `python_dep`) |

`tdd-guard`, `sdlc-evidence` and `secure-dev-policy` each carry the primary-source
research they operationalise under
`<plugin>/reference/` - what a finding is based on ships with the mechanism that enforces
it, and each reference states plainly what it verified and what it could not.

## Installing

`picoagent plugin add git:` installs a repository whose root is one plugin, so a collection
like this one is enabled **by path**: clone it, then name the plugins you want in
`~/.picoagent/config.toml`:

    [plugins]
    enabled = [
        "/path/to/picoagent-plugins/permission-gate",
        "/path/to/picoagent-plugins/mcp",
    ]

Each directory faces the trust prompt on its own, exactly like any other plugin.
`picoagent -e /path/to/picoagent-plugins/<name>` loads one for a single run while developing.

## Tests

The tests run against a picoagent checkout - its core is the runtime under test. Keep one as
a sibling directory named `picoagent`, or point `PICOAGENT_ROOT` at one:

    git clone https://github.com/opscontinuum/picoagent ../picoagent
    python3 -m unittest discover -s tests

303 tests, offline, a few seconds. The fake MCP server lives in `tests/fake_mcp.py`.

## Running the live MCP tests

`tests/test_mcp_live.py` is the one opt-in file here. The rest of the MCP suite runs against
`tests/fake_mcp.py`, a server written from the same reading of the specification as the
client. That proves the two agree. It cannot prove either one matches the protocol, because a
misreading would be a mistake both sides share and every test would agree with it. This file closes
that gap by talking to a server nobody here wrote: `@modelcontextprotocol/server-everything`, the
protocol's own reference server.

```bash
export PICOAGENT_E2E_MCP=1
python -m unittest discover -s tests -v
```

| Variable | Default | Purpose |
|---|---|---|
| `PICOAGENT_E2E_MCP` | unset | the switch; unset, `0` or `false` skips every test in the file |
| `PICOAGENT_E2E_MCP_IMAGE` | `node:22-alpine` | the container the server runs in |
| `PICOAGENT_E2E_MCP_PACKAGE` | `@modelcontextprotocol/server-everything` | the server `npx` runs |
| `PICOAGENT_E2E_MCP_STARTUP_TIMEOUT` | `300` | seconds for the handshake and `tools/list` |
| `PICOAGENT_E2E_MCP_TIMEOUT` | `60` | seconds for one `tools/call` |

The startup budget is high because `--rm` throws the container away, so every run starts on a cold
npm cache and `npx` fetches the package again. A warm image and a fast link finish the whole file in
about 12 seconds; the ceiling is there to fail a stuck start rather than to pace a healthy one.

Three things can be missing, and each skips with its own fix: the switch is off, Docker is not
installed, or the daemon is not reachable. "You did not opt in", "Docker is not installed" and "the
daemon is not running" send you to three different places, so they are never reported as one flat
"not available".

The assertions are on protocol facts and side effects: the handshake completed and recorded a
negotiated revision, `tools/list` came back and its tools were registered, `everything_echo` ran and
returned content the server produced, and the built-in `read` is still `ReadTool` afterwards. The
count of tools the reference server ships is its business and moves with its version, so nothing
asserts on it. Against version 2.0.0 the connection reports:

```
everything: mcp-servers/everything 2.0.0 (protocol 2024-11-05, running), 13 tools
```

Every container is stamped with a `picoagent-e2e-mcp=<run>` label, unique per connection. That is
the one addition these tests make to the command a user would write, and it is what lets
`ShutdownTests` assert that `session_end` left nothing running instead of assuming it. Each test
also force-removes its own label on the way out, so a failed assertion still leaves the machine
clean.

### Why the server runs in a container

The server is started as `docker run -i --rm ...`, which is a stdio command like any other, so a
stdio-only client reaches any containerised server without gaining a transport. It also sidesteps a
failure that costs an afternoon. On a WSL machine with no Linux Node installed, `npx` resolves
through interop to the Windows binary under `/mnt/c`, which starts the server under `CMD.EXE` in a
UNC path it cannot use. The child comes up, the pipe is there, and the `initialize` handshake never
completes. It looks exactly like a client bug and it is not one. If you point these tests at a
server on the host instead, check which `npx` you actually got first.
