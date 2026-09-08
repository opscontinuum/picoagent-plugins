# picoagent-plugins

The plugins that prove picoagent's architecture: each one implements a capability the
minimal core deliberately does not ship, through the same registries and events any
third-party plugin uses. They lived in picoagent's `examples/plugins/` until the examples
outgrew the harness they were examples of; each directory here kept its history
(`git subtree split`).

| Plugin | Gives the agent |
|---|---|
| [`mcp`](mcp/) | MCP servers as tools: stdio transport, handshake, tool translation, per-server env allowlist |
| [`permission-gate`](permission-gate/) | a confirmation prompt before destructive commands and protected-path writes |
| [`credential-guard`](credential-guard/) | a credential store, a tool-call guard over key files, and shell-env narrowing on top of core's allowlist |
| [`rules`](rules/) | per-file rules delivered when the agent touches a matching file, each rule approved before it reaches the prompt |
| [`agents`](agents/) | a child-agent tool (one level deep, gated through the parent's bus) plus delegation, background-run, schedule and automation skills |
| [`compaction`](compaction/) | context compaction as a `context`-event rewrite |
| [`complete`](complete/) | shell-style completion for slash commands |

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

212 tests, offline, a few seconds. The fake MCP server lives in `tests/fake_mcp.py`. One
opt-in file talks to the outside world and skips by default: `tests/test_mcp_live.py` drives
the protocol's reference server in a container (`PICOAGENT_E2E_MCP=1`; Docker required).
