#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contract tests for the deterministic development tool engine."""

import json
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

import dev_contract
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


class SystemModelTest(unittest.TestCase):
    """Contract tests for dev_engine.system_model (S1.1)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        files = {
            "src/core/__init__.py": "",
            "src/core/util.py": "def helper(value):\n    return value + 1\n",
            "src/core/store.py": (
                "import os\n\n"
                "from src.core.util import helper\n\n"
                "def save(value):\n"
                "    return helper(value)\n"
            ),
            "src/api/__init__.py": "",
            "src/api/handler.py": "from src.core import store\n",
            "src/api/client.js": "export function call() { return 1; }\n",
            "src/ui/app.js": "import { call } from '../api/client';\n",
            "tests/test_store.py": (
                "from src.core.store import save\n\n"
                "def test_save():\n"
                "    assert save(1) == 2\n"
            ),
            "main.py": (
                "from src.core.store import save\n\n"
                "if __name__ == '__main__':\n"
                "    save(1)\n"
            ),
            "package.json": json.dumps({"name": "demo", "version": "1.0.0"}),
        }
        for rel, text in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def write_contract(self, data, rel=dev_contract.CONTRACT_PATH):
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
        return target

    def base_contract(self):
        return {
            "version": 1,
            "layers": [
                {"id": "core", "title": "Core", "paths": ["src/core/**"], "risk": "high"},
                {"id": "api", "paths": ["src/api/**"]},
                {"id": "ui", "paths": ["src/ui/**"]},
                {"id": "boot", "paths": ["main.py"]},
                {"id": "tests", "paths": ["tests/**"]},
            ],
            "rules": [
                {
                    "id": "core-no-ui",
                    "type": "forbid-dependency",
                    "from": "core",
                    "to": "ui",
                    "severity": "high",
                    "claim": "core must not import ui",
                },
            ],
            "data_ownership": [
                {
                    "store": "memory-db",
                    "owner": "core",
                    "paths": ["var/memory/**"],
                    "kind": "sqlite",
                },
            ],
        }

    def snapshot(self):
        digest = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                rel = str(path.relative_to(self.root)).replace("\\", "/")
                digest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return digest

    def test_system_model_builds_modules_imports_and_layers(self):
        self.write_contract(self.base_contract())
        result = dev_engine.system_model(str(self.root))
        self.assertEqual(result["status"], "PASS", result["unknowns"])
        self.assertTrue(result["contract"]["present"])
        modules = {item["id"]: item for item in result["model"]["modules"]}
        self.assertEqual(modules["src/core/store.py"]["layer"], "core")
        self.assertEqual(modules["src/ui/app.js"]["layer"], "ui")
        self.assertEqual(modules["src/core/store.py"]["language"], "python")
        pairs = {(item["source"], item["target"], item["kind"])
                 for item in result["model"]["imports"]}
        self.assertIn(("src/core/store.py", "src/core/util.py", "internal"), pairs)
        self.assertIn(("src/core/store.py", "os", "external"), pairs)
        self.assertIn(("src/ui/app.js", "src/api/client.js", "internal"), pairs)
        self.assertIn("main.py", result["model"]["entrypoints"])
        self.assertEqual(result["unknowns"], [])

    def test_system_model_reports_missing_contract_as_unknown(self):
        result = dev_engine.system_model(str(self.root))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["contract"]["present"])
        self.assertIn("contract-missing", {item["kind"] for item in result["unknowns"]})
        self.assertTrue(all(item["layer"] is None for item in result["model"]["modules"]))

    def test_system_model_fails_on_invalid_contract(self):
        data = self.base_contract()
        data["version"] = 3
        self.write_contract(data)
        result = dev_engine.system_model(str(self.root))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn(
            "contract-unsupported-version",
            {finding["code"] for finding in result["contract"]["findings"]},
        )
        self.assertIn("contract-invalid", {item["kind"] for item in result["unknowns"]})

    def test_system_model_reports_unassigned_modules(self):
        data = self.base_contract()
        data["layers"] = [layer for layer in data["layers"] if layer["id"] != "ui"]
        data["rules"] = [rule for rule in data["rules"] if rule.get("to") != "ui"]
        self.write_contract(data)
        result = dev_engine.system_model(str(self.root))
        self.assertEqual(result["status"], "UNKNOWN")
        unknown = next(item for item in result["unknowns"]
                       if item["kind"] == "unassigned-module")
        ids = {item["id"] for item in result["unknowns"]
               if item["kind"] == "unassigned-module"}
        self.assertIn("src/ui/app.js", ids)
        self.assertTrue(unknown["next_step"])

    def test_system_model_reports_unresolved_relative_import(self):
        self.write_contract(self.base_contract())
        broken = self.root / "src/api/broken.py"
        broken.write_text("from .gone import thing\n", encoding="utf-8")
        result = dev_engine.system_model(str(self.root))
        unknown = next(item for item in result["unknowns"] if item["kind"] == "unresolved-import")
        self.assertEqual(unknown["id"], "src/api/broken.py")
        self.assertEqual(unknown["line"], 1)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_system_model_resolves_relative_file_imports(self):
        self.write_contract(self.base_contract())
        (self.root / "src/ui/data.js").write_text(
            "const meta = require('../../package.json');\n",
            encoding="utf-8",
        )
        result = dev_engine.system_model(str(self.root))
        pairs = {(item["source"], item["target"], item["kind"])
                 for item in result["model"]["imports"]}
        self.assertIn(("src/ui/data.js", "package.json", "internal-file"), pairs)
        self.assertEqual(result["status"], "PASS", result["unknowns"])

    def test_system_model_reports_layer_overlap(self):
        data = self.base_contract()
        data["layers"].append({"id": "core-alias", "paths": ["src/core/**"]})
        self.write_contract(data)
        result = dev_engine.system_model(str(self.root))
        overlaps = {item["id"]: item for item in result["unknowns"]
                    if item["kind"] == "layer-overlap"}
        self.assertIn("src/core/store.py", overlaps)
        self.assertEqual(overlaps["src/core/store.py"]["layers"], ["core", "core-alias"])
        module = next(m for m in result["model"]["modules"] if m["id"] == "src/core/store.py")
        self.assertEqual(module["layer"], "core")
        self.assertEqual(result["status"], "UNKNOWN")

    def test_system_model_maps_tests_to_modules(self):
        self.write_contract(self.base_contract())
        result = dev_engine.system_model(str(self.root))
        entry = next(item for item in result["model"]["tests"]
                     if item["path"] == "tests/test_store.py")
        self.assertEqual(entry["targets"], ["src/core/store.py"])

    def test_system_model_reads_data_ownership(self):
        self.write_contract(self.base_contract())
        result = dev_engine.system_model(str(self.root))
        store = next(item for item in result["model"]["data_stores"]
                     if item["store"] == "memory-db")
        self.assertEqual(store["owner"], "core")
        self.assertIn("src/core/store.py", store["owner_modules"])

    def test_system_model_detects_data_store_files(self):
        self.write_contract(self.base_contract())
        target = self.root / "var" / "state.sqlite"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"SQLite format 3\x00")
        result = dev_engine.system_model(str(self.root))
        detected = next(item for item in result["model"]["data_stores"] if item.get("detected"))
        self.assertEqual(detected["kind"], "sqlite-file")
        self.assertEqual(detected["store"], "var/state.sqlite")

    def test_system_model_is_deterministic(self):
        self.write_contract(self.base_contract())
        first = dev_engine.system_model(str(self.root))
        second = dev_engine.system_model(str(self.root))
        self.assertTrue(first["model_digest"].startswith("sha256:"))
        self.assertEqual(first["model_digest"], second["model_digest"])
        util = self.root / "src/core/util.py"
        util.write_text(
            util.read_text(encoding="utf-8") + "\ndef extra():\n    return 2\n",
            encoding="utf-8",
        )
        third = dev_engine.system_model(str(self.root))
        self.assertNotEqual(first["model_digest"], third["model_digest"])

    def test_system_model_is_bounded(self):
        self.write_contract(self.base_contract())
        result = dev_engine.system_model(str(self.root), max_files=2)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["model"]["modules"]), 2)

    def test_system_model_is_read_only(self):
        self.write_contract(self.base_contract())
        before = self.snapshot()
        dev_engine.system_model(str(self.root))
        self.assertEqual(before, self.snapshot())

    def test_system_model_contract_override(self):
        self.write_contract(self.base_contract(), rel="config/arch.json")
        result = dev_engine.system_model(str(self.root), contract_file="config/arch.json")
        self.assertTrue(result["contract"]["present"])
        self.assertEqual(result["contract"]["path"], "config/arch.json")

    def test_system_model_lists_unverified_levels(self):
        self.write_contract(self.base_contract())
        result = dev_engine.system_model(str(self.root))
        claims = {item["level"]: item for item in result["unverified_claims"]}
        self.assertIn("L1", claims)
        self.assertIn("L2-L4", claims)
        for item in result["unverified_claims"]:
            self.assertEqual(item["status"], "UNVERIFIED")

    def test_system_model_resolves_script_style_imports(self):
        repo = self.root / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "scripts" / "dev_contract.py").write_text("VALUE = 1\n", encoding="utf-8")
        (repo / "scripts" / "dev_engine.py").write_text(
            "import dev_contract\n", encoding="utf-8"
        )
        (repo / ".yotta").mkdir()
        (repo / ".yotta" / "architecture.json").write_text(
            json.dumps({
                "version": 1,
                "layers": [{"id": "engine", "paths": ["scripts/**"]}],
            }),
            encoding="utf-8",
        )
        result = dev_engine.system_model(str(repo))
        pairs = {(item["source"], item["target"], item["kind"])
                 for item in result["model"]["imports"]}
        self.assertIn(("scripts/dev_engine.py", "scripts/dev_contract.py", "internal"), pairs)
        self.assertEqual(result["status"], "PASS", result["unknowns"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
