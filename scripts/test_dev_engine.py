#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contract tests for the deterministic development tool engine."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import dev_engine


class DevEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "pkg" / "util.py").write_text(
            "def helper(value):\n"
            "    return value + 1\n",
            encoding="utf-8",
        )
        (self.root / "pkg" / "core.py").write_text(
            "import os\n"
            "from .util import helper\n\n"
            "def run(value):\n"
            "    return helper(value)\n",
            encoding="utf-8",
        )
        (self.root / "main.py").write_text(
            "from pkg.core import run\n\n"
            "if __name__ == '__main__':\n"
            "    run(1)\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_repo_map_finds_modules_imports_and_entrypoints(self):
        result = dev_engine.repo_map(str(self.root))
        paths = [item["path"] for item in result["modules"]]
        self.assertIn("pkg/core.py", paths)
        self.assertIn("main.py", result["entrypoints"])
        pairs = {(item["source"], item["target"]) for item in result["imports"]}
        self.assertIn(("pkg/core.py", "os"), pairs)
        self.assertIn(("pkg/core.py", "pkg/util.py"), pairs)

    def test_find_code_classifies_definition_and_reference(self):
        result = dev_engine.find_code(str(self.root), "helper")
        kinds = {item["kind"] for item in result["matches"]}
        self.assertIn("definition", kinds)
        self.assertIn("reference", kinds)

    def test_find_code_respects_max_results(self):
        result = dev_engine.find_code(str(self.root), "helper", max_results=1)
        self.assertEqual(len(result["matches"]), 1)
        self.assertTrue(result["truncated"])

    def test_compress_output_keeps_errors_and_is_bounded(self):
        lines = ["noise"] * 40
        lines[20] = "ERROR boom"
        text = "\n".join(lines)
        result = dev_engine.compress_output(text, max_chars=220)
        self.assertLessEqual(len(result["text"]), 220)
        self.assertIn("ERROR boom", result["text"])
        self.assertGreater(result["original_lines"], result["kept_lines"])

    def test_review_code_detects_rules_with_evidence(self):
        bad = self.root / "bad.py"
        bad.write_text(
            "import subprocess\n\n"
            "def run(value):\n"
            "    try:\n"
            "        eval(value)\n"
            "        print('debug')\n"
            "        subprocess.run(value, shell=True)\n"
            "    except:\n"
            "        pass\n",
            encoding="utf-8",
        )
        result = dev_engine.review_code(str(bad))
        rules = {item["rule"] for item in result["findings"]}
        self.assertIn("eval-exec", rules)
        self.assertIn("debug-print", rules)
        self.assertIn("shell-true", rules)
        self.assertIn("bare-except", rules)
        lines = [item["line"] for item in result["findings"]]
        self.assertEqual(lines, sorted(lines))
        for item in result["findings"]:
            self.assertTrue(item["evidence"])
            self.assertTrue(item["suggestion"])

    def test_review_code_clean_file_has_no_findings(self):
        clean = self.root / "clean.py"
        clean.write_text(
            "def add(left, right):\n"
            "    \"\"\"Return the sum.\"\"\"\n"
            "    return left + right\n",
            encoding="utf-8",
        )
        result = dev_engine.review_code(str(clean))
        self.assertEqual(result["findings"], [])

    def test_review_diff_only_reviews_added_lines(self):
        diff_text = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-    except:\n"
            "+    return 1\n"
        )
        clean = dev_engine.review_diff(diff_text=diff_text)
        self.assertEqual(clean["findings"], [])

        risky = dev_engine.review_diff(diff_text=(
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-    return 1\n"
            "+    eval(value)\n"
        ))
        self.assertEqual([item["rule"] for item in risky["findings"]], ["eval-exec"])
        self.assertEqual(risky["findings"][0]["line"], 1)

    def test_mcp_doctor_reads_skill_versions_and_configs(self):
        skills = self.root / "skills"
        skill = skills / "demo-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: demo-skill\n"
            "version: 0.2.0\n"
            "---\n"
            "# Demo\n",
            encoding="utf-8",
        )
        config = self.root / "mcp.json"
        config.write_text(
            json.dumps({"mcpServers": {"demo": {"command": "python"}}}),
            encoding="utf-8",
        )
        result = dev_engine.mcp_doctor(
            skills_dirs=[str(skills)],
            config_paths=[str(config)],
        )
        self.assertEqual(result["skills"][0]["name"], "demo-skill")
        self.assertEqual(result["skills"][0]["version"], "0.2.0")
        self.assertEqual(len(result["mcp_configs"]), 1)
        self.assertEqual(result["issues"], [])

    def test_mcp_doctor_reports_malformed_config(self):
        config = self.root / "broken.json"
        config.write_text("{not json", encoding="utf-8")
        result = dev_engine.mcp_doctor(config_paths=[str(config)])
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("broken.json", result["issues"][0])

    def test_scan_secrets_detects_and_redacts(self):
        config = self.root / "config.env"
        config.write_text(
            "API_KEY=test-only-placeholder-value\n",
            encoding="utf-8",
        )
        result = dev_engine.scan_secrets(str(self.root))
        rules = {item["rule"] for item in result["findings"]}
        self.assertIn("api-key", rules)
        for item in result["findings"]:
            self.assertNotIn("test-only-placeholder-value", item["evidence"])
            self.assertIn("REDACTED", item["evidence"])

    def test_scan_dependencies_reports_lockfile_and_unpinned(self):
        (self.root / "package.json").write_text(
            json.dumps({
                "name": "demo",
                "version": "1.0.0",
                "dependencies": {
                    "left-pad": "^1.0.0",
                    "lodash": "latest",
                },
            }),
            encoding="utf-8",
        )
        result = dev_engine.scan_dependencies(str(self.root))
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("missing-lockfile", codes)
        self.assertIn("unpinned-dependency", codes)

    def test_scan_dependencies_flags_typosquat_suspicion(self):
        (self.root / "package.json").write_text(
            json.dumps({"name": "demo", "version": "1.0.0", "dependencies": {"request": "^2.0.0"}}),
            encoding="utf-8",
        )
        result = dev_engine.scan_dependencies(str(self.root))
        issue = next(item for item in result["issues"] if item["code"] == "typosquat-suspicion")
        self.assertTrue(issue["requires_manual_review"])

    def test_check_publish_readiness_reports_version_mismatch(self):
        package = self.root / "package.json"
        package.write_text(
            json.dumps({
                "name": "@yottameta/demo",
                "version": "0.1.0",
                "repository": {"url": "git+https://github.com/YottaMeta/demo.git"},
                "publishConfig": {"access": "public"},
            }),
            encoding="utf-8",
        )
        (self.root / "SKILL.md").write_text(
            "---\nname: demo\nversion: 0.1.0\n---\n# Demo\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / "LICENSE").write_text("MIT\n", encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text("## v0.1.0 (2026-09-25)\n", encoding="utf-8")
        result = dev_engine.check_publish_readiness(str(self.root))
        self.assertTrue(result["ok"])

        (self.root / "SKILL.md").write_text(
            "---\nname: demo\nversion: 0.2.0\n---\n# Demo\n",
            encoding="utf-8",
        )
        result = dev_engine.check_publish_readiness(str(self.root))
        self.assertFalse(result["ok"])
        self.assertIn("version-mismatch", {item["code"] for item in result["issues"]})

    def test_check_publish_readiness_checks_engine_version(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        (scripts / "demo.py").write_text('VERSION = "0.2.0"\n', encoding="utf-8")
        package = self.root / "package.json"
        package.write_text(
            json.dumps({
                "name": "@yottameta/demo",
                "version": "0.1.0",
                "repository": {"url": "git+https://github.com/YottaMeta/demo.git"},
                "publishConfig": {"access": "public"},
            }),
            encoding="utf-8",
        )
        (self.root / "SKILL.md").write_text("---\nname: demo\nversion: 0.1.0\n---\n# Demo\n", encoding="utf-8")
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / "LICENSE").write_text("MIT\n", encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text("## v0.1.0 (2026-09-25)\n", encoding="utf-8")
        result = dev_engine.check_publish_readiness(str(self.root))
        self.assertIn("version-mismatch", {item["code"] for item in result["issues"]})

    def test_run_checks_uses_whitelist(self):
        (self.root / "test_sample.py").write_text(
            "import unittest\n\n"
            "class SampleTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertEqual(1, 1)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )
        with self.assertRaises(PermissionError):
            dev_engine.run_checks("python-unittest", str(self.root), timeout=20)
        result = dev_engine.run_checks("python-unittest", str(self.root), timeout=20, allow_execute=True)
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["passed"])
        self.assertIn("Ran 1 test", result["summary"])
        with self.assertRaises(ValueError):
            dev_engine.run_checks("rm -rf /", str(self.root))

    def test_scaffold_skill_dry_run_then_apply(self):
        out = self.root / "out"
        plan = dev_engine.scaffold_skill("demo-skill", str(out), apply=False)
        self.assertFalse(plan["applied"])
        self.assertFalse((out / "demo-skill").exists())
        result = dev_engine.scaffold_skill("demo-skill", str(out), apply=True)
        self.assertTrue(result["applied"])
        self.assertIn("name: demo-skill", (out / "demo-skill" / "SKILL.md").read_text(encoding="utf-8"))
        self.assertIn('"version": "0.1.0"', (out / "demo-skill" / "package.json").read_text(encoding="utf-8"))
        self.assertTrue((out / "demo-skill" / "NOTICE").is_file())
        with self.assertRaises(ValueError):
            dev_engine.scaffold_skill("../bad", str(out), apply=False)

    def test_workflow_state_reads_and_plans_append(self):
        workflow = self.root / ".workflow"
        workflow.mkdir()
        (workflow / "STATE.md").write_text("# Project state\n", encoding="utf-8")
        (workflow / "TASKS.md").write_text("# Tasks\n", encoding="utf-8")
        (workflow / "DECISIONS.md").write_text("# Decisions\n", encoding="utf-8")
        (workflow / "ROADMAP.md").write_text("# Roadmap\n", encoding="utf-8")
        read = dev_engine.workflow_state(str(self.root))
        self.assertTrue(read["ok"])
        self.assertIn("STATE.md", read["files"])
        plan = dev_engine.workflow_state(
            str(self.root), action="append-log", date="2026-09-25",
            text="done", apply=False,
        )
        self.assertFalse(plan["applied"])
        self.assertFalse((workflow / "logs" / "2026-09-25.md").exists())
        written = dev_engine.workflow_state(
            str(self.root), action="append-log", date="2026-09-25",
            text="done", apply=True,
        )
        self.assertTrue(written["applied"])
        self.assertIn("done", (workflow / "logs" / "2026-09-25.md").read_text(encoding="utf-8"))
        dev_engine.workflow_state(
            str(self.root), action="append-log", date="2026-09-25",
            text="again", apply=True,
        )
        self.assertTrue((workflow / "logs" / "2026-09-25.md.bak").is_file())

    def test_scan_skips_symlinks_and_binary_files(self):
        outside = self.root.parent / ("outside-" + self.root.name)
        outside.write_text("API_KEY=outside-placeholder-value\n", encoding="utf-8")
        link = self.root / "outside.env"
        try:
            os.symlink(str(outside), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("symlink is not available on this host")
        (self.root / "binary.dat").write_bytes(b"\x00\x01\x02secret")
        result = dev_engine.scan_secrets(str(self.root))
        self.assertFalse(any("OUTSIDE" in item["evidence"] for item in result["findings"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
