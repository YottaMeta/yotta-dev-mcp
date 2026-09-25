#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Broad-coverage contract tests for mcp_doctor."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import dev_mcp_doctor


class McpDoctorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.cwd = self.root / "cwd"
        self.home.mkdir()
        self.cwd.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def doctor(self, environ=None, **kwargs):
        return dev_mcp_doctor.mcp_doctor(
            home=self.home,
            cwd=self.cwd,
            environ=environ or {},
            include_unverified=False,
            **kwargs
        )

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def coverage(self, result, host):
        return next(item for item in result["coverage"] if item["host"] == host)

    def test_workbuddy_json_is_discovered(self):
        config = self.write(
            self.home / ".workbuddy" / "mcp.json",
            json.dumps({"mcpServers": {"yotta-memory": {"command": "python"}}}),
        )
        result = self.doctor()
        entry = self.coverage(result, "workbuddy")
        self.assertEqual(entry["status"], "checked")
        self.assertEqual(entry["configs"][0]["path"], str(config))
        self.assertEqual(entry["configs"][0]["servers"], ["yotta-memory"])
        self.assertEqual(result["checked_configs"], 1)
        self.assertEqual(result["issues"], [])

    def test_alternate_home_root_is_scanned(self):
        alt = self.root / "alt-home"
        self.write(
            self.home / ".workbuddy" / "mcp.json",
            json.dumps({"mcpServers": {"primary": {}}}),
        )
        self.write(
            alt / ".workbuddy" / "mcp.json",
            json.dumps({"mcpServers": {"alternate": {}}}),
        )
        result = self.doctor(environ={"USERPROFILE": str(alt)})
        entry = self.coverage(result, "workbuddy")
        paths = [item["path"] for item in entry["configs"]]
        servers = {name for item in entry["configs"] for name in item["servers"]}
        self.assertEqual(len(paths), 2)
        self.assertEqual(servers, {"primary", "alternate"})

    def test_codex_toml_is_discovered(self):
        config = self.write(
            self.home / ".codex" / "config.toml",
            "[mcp_servers]\n"
            "[mcp_servers.mempalace]\n"
            'command = "python"\n'
            '[mcp_servers."quoted name"]\n'
            'command = "python"\n',
        )
        result = self.doctor()
        entry = self.coverage(result, "codex")
        self.assertEqual(entry["status"], "checked")
        self.assertEqual(entry["configs"][0]["format"], "toml")
        self.assertEqual(entry["configs"][0]["servers"], ["mempalace", "quoted name"])
        self.assertEqual(entry["configs"][0]["path"], str(config))

    def test_opencode_jsonc_is_discovered_and_values_are_not_returned(self):
        xdg = self.root / "xdg"
        config = self.write(
            xdg / "opencode" / "opencode.jsonc",
            "{\n"
            "  // comment with a URL http://example.com/a//b\n"
            '  "mcp": {\n'
            '    "demo": {"command": "python", "env": {"TOKEN": "secret-value"}},\n'
            "  },\n"
            "}\n",
        )
        result = self.doctor(environ={"XDG_CONFIG_HOME": str(xdg)})
        entry = self.coverage(result, "opencode")
        self.assertEqual(entry["status"], "checked")
        self.assertEqual(entry["configs"][0]["format"], "jsonc")
        self.assertEqual(entry["configs"][0]["key"], "mcp")
        self.assertEqual(entry["configs"][0]["servers"], ["demo"])
        self.assertEqual(entry["configs"][0]["path"], str(config))
        self.assertNotIn("secret-value", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("python", json.dumps(result, ensure_ascii=False))

    def test_unsupported_yaml_is_an_explicit_gap(self):
        self.write(self.home / ".config" / "goose" / "config.yaml", "extensions: {}\n")
        result = self.doctor()
        entry = self.coverage(result, "goose")
        self.assertEqual(entry["status"], "unsupported")
        self.assertIn("yaml", entry["reason"])
        self.assertTrue(result["coverage_gaps"])
        self.assertFalse(result["summary"]["all_clear"])

    def test_missing_hosts_are_not_errors(self):
        result = self.doctor()
        self.assertEqual(result["issues"], [])
        self.assertGreater(result["summary"]["missing_hosts"], 0)
        self.assertTrue(result["summary"]["all_clear"])

    def test_unverified_hosts_are_reported_and_block_all_clear(self):
        result = dev_mcp_doctor.mcp_doctor(
            home=self.home, cwd=self.cwd, environ={}, include_unverified=True
        )
        self.assertTrue(result["unknown_hosts"])
        self.assertTrue(result["coverage_gaps"])
        self.assertFalse(result["summary"]["all_clear"])

    def test_explicit_scope_is_marked_partial(self):
        config = self.write(
            self.root / "explicit.json",
            json.dumps({"mcpServers": {"explicit": {}}}),
        )
        result = dev_mcp_doctor.mcp_doctor(
            home=self.home,
            cwd=self.cwd,
            environ={},
            config_paths=[str(config)],
            include_unverified=False,
        )
        self.assertEqual(result["scope"], "explicit")
        self.assertTrue(result["default_coverage_skipped"])
        self.assertFalse(result["summary"]["all_clear"])
        self.assertEqual(result["mcp_configs"][0]["servers"], ["explicit"])

    def test_workbuddy_connectors_glob_excludes_marketplace(self):
        self.write(
            self.home / ".workbuddy" / "connectors" / "default" / "mcp.json",
            json.dumps({"mcpServers": {"default": {}}}),
        )
        self.write(
            self.home / ".workbuddy" / "connectors" / "other" / "mcp.json",
            json.dumps({"mcpServers": {"other": {}}}),
        )
        self.write(
            self.home / ".workbuddy" / "connectors-marketplace" / "catalog" / "mcp.json",
            json.dumps({"mcpServers": {"catalog": {}}}),
        )
        result = self.doctor()
        entry = self.coverage(result, "workbuddy")
        paths = [item["path"] for item in entry["configs"]]
        self.assertEqual(len(paths), 2)
        self.assertFalse(any("connectors-marketplace" in path for path in paths))

    def test_skill_dirs_cover_workbuddy_and_custom_env(self):
        self.write(
            self.home / ".workbuddy" / "skills" / "demo" / "SKILL.md",
            "---\nname: demo\nversion: 1.0.0\n---\n# Demo\n",
        )
        custom = self.root / "custom-skills"
        self.write(
            custom / "custom" / "SKILL.md",
            "---\nname: custom\nversion: 1.0.0\n---\n# Custom\n",
        )
        result = self.doctor(environ={"YOTTA_DEV_MCP_SKILL_DIRS": str(custom)})
        names = {item["name"] for item in result["skills"]}
        self.assertIn("demo", names)
        self.assertIn("custom", names)
        workbuddy = next(item for item in result["skills_coverage"] if item["host"] == "workbuddy")
        self.assertEqual(workbuddy["status"], "checked")
        self.assertGreaterEqual(workbuddy["count"], 1)

    def test_custom_registry_extension_is_discovered(self):
        registry = self.write(
            self.root / "registry.json",
            json.dumps({
                "hosts": [{
                    "id": "myclient",
                    "label": "My Client",
                    "candidates": ["{home}/.myclient/mcp.json"],
                    "formats": ["json"],
                    "server_keys": ["mcpServers"],
                }]
            }),
        )
        self.write(
            self.home / ".myclient" / "mcp.json",
            json.dumps({"mcpServers": {"mine": {}}}),
        )
        result = self.doctor(environ={"YOTTA_DEV_MCP_CONFIG_REGISTRY": str(registry)})
        entry = self.coverage(result, "myclient")
        self.assertEqual(entry["status"], "checked")
        self.assertEqual(entry["configs"][0]["servers"], ["mine"])

    def test_malformed_and_oversized_configs_are_reported_without_content(self):
        broken = self.write(self.root / "broken.json", "{not json secret-value")
        big = self.write(self.root / "big.json", '{"mcpServers": {}}')
        result = dev_mcp_doctor.mcp_doctor(
            home=self.home,
            cwd=self.cwd,
            environ={},
            config_paths=[str(broken), str(big)],
            include_unverified=False,
            max_config_bytes=10,
        )
        joined = "\n".join(result["issues"])
        self.assertIn(str(broken), joined)
        self.assertIn(str(big), joined)
        self.assertNotIn("secret-value", json.dumps(result, ensure_ascii=False))

    def test_cli_mcp_doctor_returns_coverage_json(self):
        config = self.write(
            self.home / ".workbuddy" / "mcp.json",
            json.dumps({"mcpServers": {"cli": {}}}),
        )
        script = Path(__file__).resolve().parent / "dev_engine.py"
        proc = subprocess.run(
            [sys.executable, str(script), "mcp-doctor", "--config", str(config)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home)),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("coverage", payload)
        self.assertEqual(payload["scope"], "explicit")


if __name__ == "__main__":
    unittest.main()
