import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path

from ai_account_hub.core import provider_discovery as discovery


class ProviderDiscoveryTests(unittest.TestCase):
    def test_invalid_override_warns_and_falls_back_to_installed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed = root / "installed" / "codex.exe"
            installed.parent.mkdir()
            installed.write_bytes(b"binary")
            result = discovery.resolve_target(
                env={"AI_HUB_CODEX_CLI_PATH": str(root / "missing.exe"), "PATH": ""},
                home=root,
                override_names=("AI_HUB_CODEX_CLI_PATH",),
                command_names=("codex",),
                known_candidates=[(installed, "test install")],
            )
            self.assertTrue(result["found"])
            self.assertEqual(result["path"], str(installed.resolve()))
            self.assertEqual(result["source"], "test install")
            self.assertTrue(result["warnings"])

    def test_non_runnable_path_candidate_falls_back_to_healthy_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blocked = root / "blocked.exe"
            healthy = root / "healthy.exe"
            blocked.write_bytes(b"binary")
            healthy.write_bytes(b"binary")
            original_probe = discovery.probe_command
            try:
                discovery.probe_command = lambda path, timeout=4: (
                    (True, "tool 1.2.3") if Path(path) == healthy else (False, "")
                )
                result = discovery.resolve_target(
                    env={"PATH": ""},
                    home=root,
                    override_names=(),
                    command_names=(),
                    known_candidates=[(blocked, "blocked package"), (healthy, "healthy package")],
                    require_runnable=True,
                )
            finally:
                discovery.probe_command = original_probe
            self.assertTrue(result["found"])
            self.assertEqual(result["path"], str(healthy.resolve()))
            self.assertEqual(result["version"], "tool 1.2.3")
            self.assertTrue(any("blocked package" in warning for warning in result["warnings"]))

    def test_rescan_finds_provider_installed_after_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = {"HOME": str(home), "PATH": ""}
            first = discovery.discover_provider_tools(env=env, system="linux", home=home, probe_versions=False)
            self.assertFalse(first["providers"]["antigravity"]["cli"]["found"])

            agy = home / ".local" / "bin" / "agy"
            agy.parent.mkdir(parents=True)
            agy.write_bytes(b"binary")
            second = discovery.discover_provider_tools(env=env, system="linux", home=home, probe_versions=False)
            self.assertTrue(second["providers"]["antigravity"]["cli"]["found"])
            self.assertEqual(second["providers"]["antigravity"]["cli"]["path"], str(agy.resolve()))

    def test_windows_candidates_cover_current_official_user_install_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = {
                "APPDATA": str(home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(home / "AppData" / "Local"),
                "ProgramFiles": str(home / "Program Files"),
                "ProgramFiles(x86)": str(home / "Program Files (x86)"),
                "PATH": "",
            }
            candidates = discovery.windows_provider_candidates(env, home, {})
            claude_paths = {str(path) for path, _source in candidates["claude_cli"]}
            agy_paths = {str(path) for path, _source in candidates["antigravity_cli"]}
            cursor_desktop_paths = {str(path) for path, _source in candidates["cursor_desktop"]}
            cursor_cli_paths = {str(path) for path, _source in candidates["cursor_cli"]}
            cursor_agent_paths = {str(path) for path, _source in candidates["cursor_agent"]}
            self.assertIn(str(home / ".local" / "bin" / "claude.exe"), claude_paths)
            self.assertIn(str(home / "AppData" / "Local" / "agy" / "bin" / "agy.exe"), agy_paths)
            self.assertIn(
                str(home / "AppData" / "Local" / "Programs" / "cursor" / "Cursor.exe"),
                cursor_desktop_paths,
            )
            self.assertIn(
                str(home / "AppData" / "Local" / "Programs" / "cursor" / "resources" / "app" / "bin" / "cursor.cmd"),
                cursor_cli_paths,
            )
            self.assertIn(
                str(home / "AppData" / "Local" / "cursor-agent" / "cursor-agent.cmd"),
                cursor_agent_paths,
            )
            self.assertTrue(cursor_desktop_paths.isdisjoint(cursor_agent_paths))

    def test_macos_and_linux_candidates_cover_native_install_locations(self) -> None:
        home = Path("/Users/tester")
        mac = discovery.posix_provider_candidates("macos", home, {"PATH": ""})
        self.assertIn((Path("/Applications/Codex.app"), "application bundle"), mac["codex_desktop"])
        self.assertIn((home / ".local" / "bin" / "claude", "known-path"), mac["claude_cli"])

        linux_home = Path("/home/tester")
        linux = discovery.posix_provider_candidates("linux", linux_home, {"PATH": ""})
        self.assertIn((linux_home / ".local" / "bin" / "cursor-agent", "known-path"), linux["cursor_agent"])
        self.assertIn((linux_home / ".local" / "bin" / "agy", "known-path"), linux["antigravity_cli"])

    def test_report_is_atomic_fresh_and_does_not_serialize_secret_environment_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            secret = "do-not-write-this-token"
            report = discovery.discover_provider_tools(
                env={"HOME": str(home), "PATH": "", "OPENAI_API_KEY": secret},
                system="linux",
                home=home,
                probe_versions=False,
            )
            target = home / "runtime" / "provider-discovery.json"
            discovery.write_discovery_report(report, target)
            payload = target.read_text(encoding="utf-8")
            self.assertNotIn(secret, payload)
            self.assertFalse(target.with_suffix(".json.tmp").exists())
            loaded = discovery.load_fresh_report(target)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["schemaVersion"], discovery.REPORT_SCHEMA_VERSION)

            stale = json.loads(payload)
            stale["generatedAtUtc"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
            target.write_text(json.dumps(stale), encoding="utf-8")
            self.assertIsNone(discovery.load_fresh_report(target, max_age_seconds=30))

    def test_root_batch_launcher_runs_discovery_before_gui(self) -> None:
        launcher = Path(__file__).resolve().parents[1] / "Start-AI-Account-Hub.bat"
        text = launcher.read_text(encoding="utf-8")
        self.assertIn("provider_discovery.py", text)
        self.assertIn("--write-report --quiet", text)
        self.assertIn("sys.version_info >= (3, 10)", text)
        self.assertIn('set "AI_HUB_DISCOVERY_BOOTSTRAPPED="', text)
        discovery_index = text.index("--write-report --quiet")
        launch_index = text.index('%PYRUN% "%APP%"')
        self.assertLess(discovery_index, launch_index)


if __name__ == "__main__":
    unittest.main()
