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

    def test_tools_list_has_six_contracts(self):
        response = server.handle_message({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list",
        })
        tools = response["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(names, [
            "repo_map",
            "find_code",
            "compress_output",
            "review_code",
            "review_diff",
            "mcp_doctor",
        ])
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_repo_map_tool(self):
        response = self.call("repo_map", {"path": str(self.root)})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertIn("main.py", payload["entrypoints"])

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
        self.assertEqual(len(json.loads(lines[1])["result"]["tools"]), 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
