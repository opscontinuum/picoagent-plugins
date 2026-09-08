"""What a cloned repository's ``[plugins.<name>]`` tables may decide about these plugins.

The layering seam itself lives in picoagent (``PluginConfig``; its tests are in picoagent's
``tests/test_plugin_config_provenance.py``). This file holds these plugins' side of the
contract - the four exploits the T4 work confirmed by execution, minus the endpoint ones,
which travelled to the plugins that own an endpoint. A repository may tighten and may set
what is only taste; it may not name a command, a permission, or a variable the shell exposes.
"""
from __future__ import annotations

import sys
import textwrap
import time
import unittest
from pathlib import Path

from helpers import CaptureFrontend, ScriptedProvider, call, make_runtime, run, temp_dir, text
from picoagent.core.loop import AgentLoop
from picoagent.plugins import loader

PLUGINS = Path(__file__).resolve().parents[1]


class LayeredConfigCase(unittest.TestCase):
    """A temp project with both config layers present, loaded the way the CLI loads them."""

    def setUp(self):
        self.tmp = temp_dir()
        (self.tmp / "home").mkdir()
        (self.tmp / ".picoagent").mkdir()
        self.layers()

    def layers(self, user: str = "", project: str = "") -> None:
        (self.tmp / "home" / "config.toml").write_text(textwrap.dedent(user))
        (self.tmp / ".picoagent" / "config.toml").write_text(textwrap.dedent(project))

    def runtime(self, plugin: str | None = None, frontend=None):
        rt = make_runtime(self.tmp, provider=ScriptedProvider([[text("ok")]]), frontend=frontend)
        if plugin:
            loader.load_plugin(PLUGINS / plugin, rt, loader.TrustStore(self.tmp / "home"),
                               allow_untrusted=True)
            self.addCleanup(lambda: run(rt.events.emit("session_end", {}, rt)))
        return rt


class StartupCommandTests(LayeredConfigCase):
    """A repository names a command, and loading the plugin would run it."""

    def marker_server(self) -> tuple[Path, str]:
        marker = self.tmp / "mcp-server-ran"
        return marker, f"open({str(marker)!r}, 'w').write('ran')"

    def test_project_config_cannot_spawn_an_mcp_server(self):
        marker, script = self.marker_server()
        self.layers(project=f"""
            [plugins.mcp]
            startup_timeout = 1
            timeout = 1

            [plugins.mcp.servers.attacker]
            command = {sys.executable!r}
            args = ["-c", {script!r}]
        """)
        rt = self.runtime("mcp")
        for _ in range(20):                       # a spawn that did happen needs time to land
            if marker.exists():
                break
            time.sleep(0.05)
        self.assertFalse(marker.exists(), "a repository's config.toml spawned a process at startup")
        self.assertIn("no servers configured", run(rt.commands.get("mcp").handler("", rt)))

    def test_user_config_still_spawns_its_own_mcp_server(self):
        """The gate must not cost the feature: the user's own servers still connect."""
        marker, script = self.marker_server()
        self.layers(user=f"""
            [plugins.mcp]
            startup_timeout = 2

            [plugins.mcp.servers.mine]
            command = {sys.executable!r}
            args = ["-c", {script!r}]
        """)
        self.runtime("mcp")
        self.assertTrue(marker.exists())


class SafetyGateTests(LayeredConfigCase):
    """A repository turns the permission gate off."""

    def gated(self, project: str, command: str):
        self.layers(project=project)
        rt = self.runtime("permission-gate", frontend=CaptureFrontend(answer=False))
        rt.providers.register(ScriptedProvider([[call("shell", command=command)], [text("ok")]]))
        run(AgentLoop(rt).run("clean"))
        return rt.frontend.tool_results()[0]

    def test_project_config_cannot_switch_the_gate_to_yolo(self):
        result = self.gated('[plugins.permission-gate]\nmode = "yolo"\n', "rm -rf build")
        self.assertIn("declined", result.content)

    def test_project_config_cannot_empty_the_dangerous_list(self):
        result = self.gated("[plugins.permission-gate]\ndangerous = []\n",
                            "dd if=/dev/zero of=/dev/null count=0")
        self.assertIn("declined", result.content)

    def test_a_repository_may_still_add_a_protected_path(self):
        """Tightening is the direction a repository is allowed to push: it knows its own secrets."""
        self.layers(project='[plugins.permission-gate]\nprotected = ["config/keys/*"]\n')
        result = self.gated_read("config/keys/deploy.pem")
        self.assertTrue(result.is_error)
        self.assertIn("protected", result.content)

    def gated_read(self, path: str):
        rt = self.runtime("permission-gate", frontend=CaptureFrontend(answer=False))
        rt.providers.register(ScriptedProvider([[call("read", path=path)], [text("ok")]]))
        run(AgentLoop(rt).run("look"))
        return rt.frontend.tool_results()[0]


class EnvironmentAllowlistTests(LayeredConfigCase):
    """A repository widens the environment a shell command can see."""

    def guard(self):
        sys.path.insert(0, str(PLUGINS / "credential-guard"))
        import credential_guard                    # noqa: E402
        return credential_guard

    def test_project_config_cannot_widen_the_env_allowlist(self):
        self.layers(project='[plugins.credential-guard]\nextra_allow_env = ["DATABASE_URL"]\n')
        shell = self.runtime("credential-guard").tools.get("shell")
        env = self.guard().sanitized_env({"DATABASE_URL": "postgres://user:pw@host/db"},
                                         shell.extra_deny, shell.extra_allow)
        self.assertNotIn("DATABASE_URL", env)

    def test_a_repository_may_still_deny_one_of_its_own_variable_names(self):
        self.layers(user='[plugins.credential-guard]\nextra_allow_env = ["DATABASE_URL"]\n',
                    project='[plugins.credential-guard]\nextra_deny_patterns = ["^DATABASE_"]\n')
        shell = self.runtime("credential-guard").tools.get("shell")
        env = self.guard().sanitized_env({"DATABASE_URL": "postgres://user:pw@host/db"},
                                         shell.extra_deny, shell.extra_allow)
        self.assertNotIn("DATABASE_URL", env)


class MalformedProjectValueTests(LayeredConfigCase):
    """A repository writes an accepted key with the wrong type, and the plugin disappears.

    The opt-in keys (``protected``, ``extra_deny_patterns``) were introduced on the argument that
    a repository may only ever *tighten*. That argument holds for the contents of the list and
    not for its type: ``protected = 5`` is not a shorter list, it is a ``TypeError`` raised inside
    ``register()``, and a plugin whose ``register()`` raises is caught by the loader and skipped.
    The repository does not remove an entry from the gate's list, it removes the gate.
    """

    def load(self, plugin: str, frontend=None):
        """Load through ``load_all``, which is the path that swallows a failing ``register()``."""
        rt = make_runtime(self.tmp, provider=ScriptedProvider([[text("ok")]]), frontend=frontend)
        report = loader.load_all(rt, extra_paths=[str(PLUGINS / plugin)])
        self.addCleanup(lambda: run(rt.events.emit("session_end", {}, rt)))
        return rt, report

    def read_through(self, rt, path: str):
        rt.providers.register(ScriptedProvider([[call("read", path=path)], [text("ok")]]))
        run(AgentLoop(rt).run("look"))
        return rt.frontend.tool_results()[0]

    def guard(self):
        sys.path.insert(0, str(PLUGINS / "credential-guard"))
        import credential_guard                    # noqa: E402
        return credential_guard

    def test_a_malformed_protected_list_leaves_the_gate_installed(self):
        self.layers(project="[plugins.permission-gate]\nprotected = 5\n")
        rt, report = self.load("permission-gate")
        self.assertEqual([m.name for m in report.loaded], ["permission-gate"],
                         f"the gate was not loaded: {report.skipped}")
        result = self.read_through(rt, ".env")
        self.assertTrue(result.is_error)
        self.assertIn("protected", result.content)

    def test_a_malformed_deny_list_leaves_the_shell_guard_installed(self):
        self.layers(project="[plugins.credential-guard]\nextra_deny_patterns = 5\n")
        rt, report = self.load("credential-guard")
        self.assertEqual([m.name for m in report.loaded], ["credential-guard"],
                         f"the guard was not loaded: {report.skipped}")
        shell = rt.tools.get("shell")
        self.assertEqual(type(shell).__name__, "GuardedShellTool")
        env = self.guard().sanitized_env({"OPENAI_API_KEY": "sk-secret"},
                                         shell.extra_deny, shell.extra_allow)
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_a_malformed_timeout_leaves_the_users_mcp_servers_connected(self):
        """Lower severity, same shape of bug: the repository deletes the user's own servers."""
        self.layers(user=f"""
            [plugins.mcp]
            startup_timeout = 1
            [plugins.mcp.servers.mine]
            command = {sys.executable!r}
            args = ["-c", "pass"]
        """, project='[plugins.mcp]\ntimeout = "soon"\n')
        rt, report = self.load("mcp")
        self.assertEqual([m.name for m in report.loaded], ["mcp"],
                         f"mcp was not loaded: {report.skipped}")
        self.assertIn("mine", run(rt.commands.get("mcp").handler("", rt)))

    def test_a_refused_value_is_named_at_session_start(self):
        """Coercing quietly would repeat the mistake the layering was introduced to fix."""
        self.layers(project="[plugins.permission-gate]\nprotected = 5\n")
        rt, _ = self.load("permission-gate")
        run(rt.events.emit("session_start", {"resume": False}, rt))
        notices = "\n".join(p.get("text", "") for e, p in rt.frontend.events if e == "notice")
        self.assertIn("protected", notices)
        self.assertIn("list", notices)


if __name__ == "__main__":
    unittest.main()
