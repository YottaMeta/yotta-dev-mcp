#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Protocol and end-to-end tests for the yotta-dev-mcp stdio server."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yotta_dev_mcp as server


class YottaDevMcpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "main.py").write_text(
            "def helper(value):\n"
            "    return value + 1\n\n"
            "if __name__ == '__main__':\n"
            "    print(helper(1))\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, name, arguments):
        return server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })

    def test_legacy_initialize(self):
        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        })
        result = response["result"]
        self.assertEqual(result["serverInfo"]["name"], "yotta-dev-mcp")
        self.assertEqual(result["serverInfo"]["version"], server.VERSION)
        self.assertIn("tools", result["capabilities"])

    def test_modern_discover(self):
        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "server/discover",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                },
            },
        })
        result = response["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"], ["2026-07-28"])
        self.assertIn("tools", result["capabilities"])

    def test_modern_ping(self):
        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 21,
            "method": "ping",
            "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}},
        })
        self.assertEqual(response["result"]["resultType"], "complete")

    def test_tools_list_has_seventeen_contracts(self):
        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list",
        })
        tools = response["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(names, [
            "repo_map",
            "system_model",
            "architecture_review",
            "impact_analysis",
            "verify_change",
            "self_test",
            "find_code",
            "compress_output",
            "review_code",
            "review_diff",
            "mcp_doctor",
            "scan_secrets",
            "scan_dependencies",
            "check_publish_readiness",
            "run_checks",
            "scaffold_skill",
            "workflow_state",
        ])
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIs(tool["inputSchema"].get("additionalProperties"), False)

    def test_repo_map_tool(self):
        response = self.call("repo_map", {"path": str(self.root)})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertIn("main.py", payload["entrypoints"])

    def test_system_model_tool(self):
        response = self.call("system_model", {"path": str(self.root)})
        self.assertFalse(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "UNKNOWN")
        self.assertIn("model", payload)
        self.assertEqual(
            payload["unknowns"][0]["kind"],
            "contract-missing",
        )

    def test_system_model_rejects_missing_path(self):
        response = self.call("system_model", {"path": str(self.root / "nope")})
        self.assertTrue(response["result"]["isError"])

    def test_architecture_review_tool(self):
        (self.root / ".yotta").mkdir()
        (self.root / ".yotta" / "architecture.json").write_text(json.dumps({
            "version": 1,
            "layers": [
                {"id": "core", "paths": ["core/**"], "risk": "high"},
                {"id": "boot", "paths": ["main.py"]},
            ],
            "rules": [
                {"id": "core-no-boot", "type": "forbid-dependency", "from": "core",
                 "to": "boot", "severity": "high", "claim": "core must not import boot"},
            ],
        }), encoding="utf-8")
        (self.root / "core").mkdir()
        (self.root / "core" / "leak.py").write_text(
            "from main import helper\n", encoding="utf-8"
        )
        response = self.call("architecture_review", {"path": str(self.root)})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(payload["violations"][0]["rule"], "core-no-boot")
        self.assertEqual(payload["violations"][0]["evidence"][0]["path"], "core/leak.py")

    def test_impact_analysis_tool(self):
        (self.root / "core").mkdir()
        (self.root / "core" / "util.py").write_text(
            "def helper(value):\n    return value + 1\n", encoding="utf-8"
        )
        (self.root / "core" / "use.py").write_text(
            "from core.util import helper\n", encoding="utf-8"
        )
        response = self.call("impact_analysis", {
            "path": str(self.root),
            "changed_files": ["core/util.py"],
        })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertFalse(response["result"]["isError"])
        self.assertIn("core/use.py", payload["direct_consumers"])
        nodes = {item["path"]: item for item in payload["cone"]["nodes"]}
        self.assertEqual(nodes["core/use.py"]["depth"], 1)

    def test_impact_analysis_requires_change_input(self):
        response = self.call("impact_analysis", {"path": str(self.root)})
        self.assertTrue(response["result"]["isError"])

    def test_verify_change_tool(self):
        (self.root / ".yotta").mkdir()
        (self.root / ".yotta" / "architecture.json").write_text(json.dumps({
            "version": 1,
            "layers": [
                {"id": "core", "paths": ["core/**"], "risk": "high"},
                {"id": "boot", "paths": ["main.py"]},
            ],
        }), encoding="utf-8")
        (self.root / "core").mkdir()
        (self.root / "core" / "util.py").write_text(
            "def helper(value):\n    return value + 1\n", encoding="utf-8"
        )
        response = self.call("verify_change", {
            "path": str(self.root),
            "changed_files": ["core/util.py"],
        })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(payload["status"], "PASS", payload["ledger"])
        self.assertEqual(
            [item["id"] for item in payload["ledger"]],
            ["L0-contract", "L0-syntax", "L1-architecture"],
        )

    def test_verify_change_requires_change_input(self):
        response = self.call("verify_change", {"path": str(self.root)})
        self.assertTrue(response["result"]["isError"])

    def test_self_test_tool_installed_mode(self):
        skill = self.root / "installed"
        (skill / "assets").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: yotta-dev-mcp\n"
            "description: test install\n"
            "version: 0.2.0\n"
            "license: MIT\n"
            "---\n\n"
            "# 元开\n",
            encoding="utf-8",
        )
        (skill / "assets" / "banner.png").write_bytes(b"png")
        response = self.call("self_test", {
            "path": str(skill),
            "mode": "installed",
        })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(payload["status"], "PASS", payload["checks"])
        self.assertEqual(payload["mode"], "installed")

    def test_find_code_tool(self):
        response = self.call("find_code", {"path": str(self.root), "query": "helper"})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertGreaterEqual(len(payload["matches"]), 1)

    def test_compress_output_tool(self):
        response = self.call("compress_output", {
            "text": "\n".join(["noise"] * 20 + ["ERROR boom"] + ["tail"] * 20),
            "max_chars": 180,
        })
        self.assertFalse(response["result"]["isError"])
        self.assertIn("ERROR boom", response["result"]["content"][0]["text"])

    def test_unknown_tool_is_error(self):
        response = self.call("nope", {})
        self.assertTrue(response["result"]["isError"])

    def test_scan_secrets_tool(self):
        secret = self.root / "secret.env"
        secret.write_text("TOKEN=abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8")
        response = self.call("scan_secrets", {"path": str(self.root)})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertGreaterEqual(len(payload["findings"]), 1)
        self.assertFalse(response["result"]["isError"])

    def test_workflow_state_tool(self):
        workflow = self.root / ".workflow"
        workflow.mkdir()
        for name in ("STATE.md", "TASKS.md", "DECISIONS.md", "ROADMAP.md"):
            (workflow / name).write_text("# " + name + "\n", encoding="utf-8")
        response = self.call("workflow_state", {"root": str(self.root)})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertFalse(response["result"]["isError"])

    def test_run_checks_requires_explicit_execution(self):
        (self.root / "test_sample.py").write_text(
            "import unittest\n\n"
            "class SampleTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        blocked = self.call("run_checks", {"kind": "python-unittest", "cwd": str(self.root)})
        self.assertTrue(blocked["result"]["isError"])
        allowed = self.call("run_checks", {
            "kind": "python-unittest", "cwd": str(self.root), "allow_execute": True,
        })
        self.assertFalse(allowed["result"]["isError"])

    def test_stdio_roundtrip(self):
        script = Path(__file__).with_name("yotta_dev_mcp.py")
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        stdin = "".join(json.dumps(item) + "\n" for item in messages)
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["result"]["serverInfo"]["name"], "yotta-dev-mcp")
        self.assertEqual(len(json.loads(lines[1])["result"]["tools"]), 17)


if __name__ == "__main__":
    unittest.main(verbosity=2)
