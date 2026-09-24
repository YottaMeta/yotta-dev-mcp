#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contract tests for the deterministic development tool engine."""

import json
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
