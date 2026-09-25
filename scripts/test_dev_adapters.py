#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contract tests for the optional yotta-dev-mcp adapter layer."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dev_adapters


def write_executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if os.name != "nt":
        os.chmod(str(path), 0o755)


class AdapterProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_has_first_batch_metadata(self):
        catalog = dev_adapters.adapter_catalog()
        by_id = {item["id"]: item for item in catalog}
        self.assertEqual(
            sorted(by_id),
            ["dependency-cruiser", "import-linter", "repomix"],
        )
        self.assertEqual(by_id["import-linter"]["license"], "BSD-2-Clause")
        self.assertEqual(by_id["dependency-cruiser"]["license"], "MIT")
        self.assertEqual(by_id["repomix"]["license"], "MIT")

    def test_list_reports_missing_tools_without_running(self):
        with mock.patch.object(
            dev_adapters, "_run_process", side_effect=AssertionError("must not run")
        ):
            result = dev_adapters.run_adapter(str(self.root), action="list")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["action"], "list")
        self.assertEqual(len(result["adapters"]), 3)
        for item in result["adapters"]:
            self.assertFalse(item["available"])
            self.assertFalse(item["ready"])
            self.assertEqual(item["reason"], "adapter-not-installed")
        self.assertTrue(result["unknowns"])

    def test_list_finds_project_local_tools_and_configs(self):
        (self.root / ".importlinter").write_text("[importlinter]\nroot_package = app\n",
                                                  encoding="utf-8")
        (self.root / ".dependency-cruiser.js").write_text(
            "module.exports = { forbidden: [] };\n", encoding="utf-8"
        )
        (self.root / "node_modules" / ".bin").mkdir(parents=True)
        write_executable(self.root / "node_modules" / ".bin" / "depcruise", "#!/bin/sh\n")
        write_executable(self.root / "node_modules" / ".bin" / "depcruise.cmd", "@echo off\n")
        write_executable(self.root / "node_modules" / ".bin" / "repomix", "#!/bin/sh\n")
        write_executable(self.root / "node_modules" / ".bin" / "repomix.cmd", "@echo off\n")
        if os.name == "nt":
            write_executable(self.root / ".venv" / "Scripts" / "lint-imports.exe", "MZ")
        else:
            write_executable(self.root / ".venv" / "bin" / "lint-imports", "#!/bin/sh\n")

        result = dev_adapters.run_adapter(str(self.root), action="list")
        by_id = {item["id"]: item for item in result["adapters"]}
        self.assertTrue(by_id["import-linter"]["available"])
        self.assertTrue(by_id["import-linter"]["ready"])
        self.assertEqual(by_id["import-linter"]["config"], ".importlinter")
        self.assertTrue(by_id["dependency-cruiser"]["available"])
        self.assertTrue(by_id["dependency-cruiser"]["ready"])
        self.assertEqual(by_id["dependency-cruiser"]["config"], ".dependency-cruiser.js")
        self.assertTrue(by_id["repomix"]["available"])
        self.assertTrue(by_id["repomix"]["ready"])

    def test_list_marks_missing_config_unready(self):
        (self.root / "node_modules" / ".bin").mkdir(parents=True)
        write_executable(self.root / "node_modules" / ".bin" / "depcruise", "#!/bin/sh\n")
        write_executable(self.root / "node_modules" / ".bin" / "depcruise.cmd", "@echo off\n")
        result = dev_adapters.run_adapter(str(self.root), action="list")
        item = {entry["id"]: entry for entry in result["adapters"]}["dependency-cruiser"]
        self.assertTrue(item["available"])
        self.assertFalse(item["ready"])
        self.assertEqual(item["reason"], "adapter-config-missing")


class AdapterRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".importlinter").write_text("[importlinter]\nroot_package = app\n",
                                                  encoding="utf-8")
        (self.root / ".dependency-cruiser.js").write_text(
            "module.exports = { forbidden: [] };\n", encoding="utf-8"
        )
        (self.root / "node_modules" / ".bin").mkdir(parents=True)
        write_executable(self.root / "node_modules" / ".bin" / "depcruise", "#!/bin/sh\n")
        write_executable(self.root / "node_modules" / ".bin" / "depcruise.cmd", "@echo off\n")
        write_executable(self.root / "node_modules" / ".bin" / "repomix", "#!/bin/sh\n")
        write_executable(self.root / "node_modules" / ".bin" / "repomix.cmd", "@echo off\n")
        if os.name == "nt":
            write_executable(self.root / ".venv" / "Scripts" / "lint-imports.exe", "MZ")
        else:
            write_executable(self.root / ".venv" / "bin" / "lint-imports", "#!/bin/sh\n")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def runner(code=0, stdout="", stderr="", version="2.15"):
        def run(argv, cwd, timeout):
            del cwd, timeout
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, version + "\n", "")
            return subprocess.CompletedProcess(argv, code, stdout, stderr)
        return run

    def test_run_requires_explicit_execute(self):
        with mock.patch.object(
            dev_adapters, "_run_process", side_effect=AssertionError("must not run")
        ):
            result = dev_adapters.run_adapter(
                str(self.root), action="run", adapter="import-linter"
            )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "allow-execute-false")
        self.assertIn("allow_execute=true", result["next_step"])

    def test_run_rejects_unknown_adapter(self):
        with self.assertRaises(ValueError):
            dev_adapters.run_adapter(
                str(self.root), action="run", adapter="nope", allow_execute=True
            )

    def test_run_rejects_target_escape(self):
        with self.assertRaises(ValueError):
            dev_adapters.run_adapter(
                str(self.root), action="run", adapter="repomix",
                allow_execute=True, target="../outside",
            )

    def test_import_linter_failure_normalizes_contract_findings(self):
        output = (
            "=============\n"
            "Import Linter\n"
            "=============\n\n"
            "Layered architecture BROKEN\n"
            "pkg.ui is not allowed to import pkg.core\n"
        )
        with mock.patch.object(
            dev_adapters, "_run_process", side_effect=self.runner(code=1, stdout=output)
        ):
            result = dev_adapters.run_adapter(
                str(self.root), action="run", adapter="import-linter",
                allow_execute=True,
            )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["command"]["exit_code"], 1)
        self.assertTrue(result["command"]["output_hash"].startswith("sha256:"))
        self.assertEqual(result["adapter"]["version"], "2.15")
        finding = result["findings"][0]
        self.assertEqual(finding["rule"], "Layered architecture")
        self.assertEqual(finding["path"], "pkg.ui")
        self.assertEqual(finding["target"], "pkg.core")
        self.assertEqual(finding["severity"], "high")

    def test_import_linter_success_is_pass(self):
        output = "Layered architecture KEPT\nContracts: 1 kept, 0 broken.\n"
        with mock.patch.object(
            dev_adapters, "_run_process", side_effect=self.runner(code=0, stdout=output)
        ):
            result = dev_adapters.run_adapter(
                str(self.root), action="run", adapter="import-linter",
                allow_execute=True,
            )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["findings"], [])

    def test_import_linter_strips_trailing_punctuation(self):
        output = (
            "core must not import ui BROKEN\n"
            "app.core is not allowed to import app.ui:\n"
        )
        with mock.patch.object(
            dev_adapters, "_run_process", side_effect=self.runner(code=1, stdout=output)
        ):
            result = dev_adapters.run_adapter(
                str(self.root), action="run", adapter="import-linter",
                allow_execute=True,
            )
        self.assertEqual(result["findings"][0]["path"], "app.core")
        self.assertEqual(result["findings"][0]["target"], "app.ui")

    def test_dependency_cruiser_parses_json_violations(self):
        output = json.dumps({
            "summary": {
                "violations": [{
                    "from": "src/ui.js",
                    "to": "src/core.js",
                    "comment": "ui must not import core",
                    "rule": {"name": "ui-no-core", "severity": "error"},
                }],
                "error": 1,
                "warn": 0,
                "info": 0,
            }
        })
        with mock.patch.object(
            dev_adapters, "_run_process", side_effect=self.runner(code=1, stdout=output)
        ):
            result = dev_adapters.run_adapter(
                str(self.root), action="run", adapter="dependency-cruiser",
                allow_execute=True,
            )
        self.assertEqual(result["status"], "FAIL")
        finding = result["findings"][0]
        self.assertEqual(finding["rule"], "ui-no-core")
        self.assertEqual(finding["path"], "src/ui.js")
        self.assertEqual(finding["target"], "src/core.js")
        self.assertEqual(finding["severity"], "high")

    def test_dependency_cruiser_advisory_only_is_pass(self):
        output = json.dumps({
            "summary": {
                "violations": [{
                    "from": "src/ui.js",
                    "to": "src/core.js",
                    "comment": "advisory",
                    "rule": {"name": "advisory-rule", "severity": "warn"},
                }],
                "error": 0,
                "warn": 1,
                "info": 0,
            }
        })
        with mock.patch.object(
            dev_adapters, "_run_process", side_effect=self.runner(code=0, stdout=output)
        ):
            result = dev_adapters.run_adapter(
                str(self.root), action="run", adapter="dependency-cruiser",
                allow_execute=True,
            )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["findings"][0]["severity"], "medium")

    def test_dependency_cruiser_invalid_json_is_unknown(self):
        with mock.patch.object(
            dev_adapters, "_run_process", side_effect=self.runner(code=0, stdout="not json")
        ):
            result = dev_adapters.run_adapter(
                str(self.root), action="run", adapter="dependency-cruiser",
                allow_execute=True,
            )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "adapter-output-invalid")

    def test_repomix_returns_bounded_content(self):
        output = "x" * 5000
        with mock.patch.object(
            dev_adapters, "_run_process", side_effect=self.runner(code=0, stdout=output)
        ):
            result = dev_adapters.run_adapter(
                str(self.root), action="run", adapter="repomix",
                allow_execute=True, max_chars=1000,
            )
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["content"]["truncated"])
        self.assertLessEqual(len(result["content"]["text"]), 1000)
        self.assertGreaterEqual(result["content"]["estimated_tokens"], 1)
        self.assertTrue(result["content"]["content_hash"].startswith("sha256:"))

    def test_repomix_command_has_no_remote_or_security_bypass(self):
        calls = []

        def runner(argv, cwd, timeout):
            del cwd, timeout
            calls.append(list(argv))
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "1.18.1\n", "")
            return subprocess.CompletedProcess(argv, 0, "<pack/>", "")

        with mock.patch.object(dev_adapters, "_run_process", side_effect=runner):
            dev_adapters.run_adapter(
                str(self.root), action="run", adapter="repomix",
                allow_execute=True, token_budget=1000,
            )
        argv = calls[1]
        self.assertIn("--stdout", argv)
        self.assertIn("--no-git-sort-by-changes", argv)
        self.assertNotIn("--remote", argv)
        self.assertNotIn("--no-security-check", argv)

    def test_dependency_cruiser_excludes_node_modules(self):
        calls = []

        def runner(argv, cwd, timeout):
            del cwd, timeout
            calls.append(list(argv))
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "18.4.0\n", "")
            return subprocess.CompletedProcess(
                argv, 0,
                json.dumps({"summary": {"violations": [], "error": 0, "warn": 0, "info": 0}}),
                "",
            )

        with mock.patch.object(dev_adapters, "_run_process", side_effect=runner):
            dev_adapters.run_adapter(
                str(self.root), action="run", adapter="dependency-cruiser",
                allow_execute=True,
            )
        argv = calls[1]
        self.assertIn("--exclude", argv)
        self.assertIn("node_modules", argv)

    def test_timeout_is_unknown(self):
        def runner(argv, cwd, timeout):
            del cwd, timeout
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, "2.15\n", "")
            raise subprocess.TimeoutExpired(argv, 1, output=b"partial", stderr=b"slow")

        with mock.patch.object(dev_adapters, "_run_process", side_effect=runner):
            result = dev_adapters.run_adapter(
                str(self.root), action="run", adapter="import-linter",
                allow_execute=True, timeout=1,
            )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertTrue(result["command"]["timed_out"])
        self.assertEqual(result["command"]["exit_code"], None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
