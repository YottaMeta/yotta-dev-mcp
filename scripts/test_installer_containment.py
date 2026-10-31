#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Installer containment tests for symlink / junction targets."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


def make_dir_link_or_skip(testcase, source, link):
    try:
        os.symlink(str(source), str(link), target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        if os.name == "nt":
            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(source)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode == 0:
                return "junction"
        testcase.skipTest("directory symlink/junction is not available on this host")


class InstallerContainmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pkg_root = Path(__file__).resolve().parent.parent
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("node is not available")

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, base):
        return subprocess.run(
            [
                self.node,
                str(self.pkg_root / "bin" / "install.js"),
                "--dir", str(base),
                "--yes",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    def test_normal_install_keeps_version_file(self):
        base = self.root / "skills"
        base.mkdir()
        result = self.install(base)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        target = base / "yotta-dev-mcp"
        self.assertTrue((target / "SKILL.md").is_file())
        self.assertTrue((target / "VERSION").is_file())
        package = json.loads((self.pkg_root / "package.json").read_text(encoding="utf-8"))
        self.assertEqual((target / "VERSION").read_text(encoding="utf-8").strip(),
                         package["version"])

    def test_target_root_link_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        base = self.root / "skills"
        base.mkdir()
        target = base / "yotta-dev-mcp"
        make_dir_link_or_skip(self, outside, target)
        result = self.install(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((outside / "SKILL.md").exists())

    def test_managed_hub_target_link_is_allowed(self):
        hub = self.root / "skills-hub"
        hub.mkdir()
        (hub / ".yotta-hub.json").write_text("{}", encoding="utf-8")
        target_dir = hub / "yotta-dev-mcp"
        target_dir.mkdir()
        base = self.root / "skills"
        base.mkdir()
        make_dir_link_or_skip(self, target_dir, base / "yotta-dev-mcp")
        result = self.install(base)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertTrue((target_dir / "SKILL.md").is_file())
        self.assertTrue((target_dir / "VERSION").is_file())

    def test_nested_link_is_rejected_without_writing_outside(self):
        outside = self.root / "outside"
        outside.mkdir()
        base = self.root / "skills"
        target = base / "yotta-dev-mcp"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            "---\nname: yotta-dev-mcp\nversion: 0.0.0\n---\n",
            encoding="utf-8",
        )
        make_dir_link_or_skip(self, outside, target / "scripts")
        result = self.install(base)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((outside / "dev_engine.py").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
