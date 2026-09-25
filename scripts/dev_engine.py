#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic development tools for yotta-dev-mcp.

The engine is intentionally small and self-contained: Python 3.8+ standard
library only, offline by default, deterministic output, read-only unless a
tool explicitly documents a write.
"""

import argparse
import ast
import hashlib
import json
import math
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import dev_contract
from dev_rules import REVIEW_RULES

VERSION = "0.2.0"

IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", ".cache", ".tmp", ".tmp2",
}
SOURCE_EXTS = {
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".sh", ".ps1",
    ".go", ".rs", ".java", ".kt", ".kts", ".rb", ".php",
}
TEXT_EXTS = SOURCE_EXTS | {".json", ".md", ".txt", ".yml", ".yaml", ".toml", ".ini", ".cfg"}
MAX_FILE_BYTES = 2 * 1024 * 1024

JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
CONFIG_NAMES = {
    "package.json", "package-lock.json", "tsconfig.json", "pyproject.toml",
    "setup.cfg", "requirements.txt", "requirements-dev.txt", "Cargo.toml",
    "go.mod", "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".eslintrc.json", ".eslintrc.js", ".prettierrc", ".prettierrc.json",
}
CONFIG_SUFFIXES = (".toml", ".ini", ".cfg", ".yml", ".yaml")
STORAGE_SUFFIXES = {
    ".sqlite": "sqlite-file", ".sqlite3": "sqlite-file",
    ".db": "db-file", ".mdb": "db-file", ".dbf": "db-file",
}
TEST_NAME_RE = re.compile(
    r"(?i)^test_.*\.(py|js|ts|jsx|tsx)$|_test\.py$|\.(test|spec)\.[jt]sx?$"
)
UNKNOWN_LIMIT = 50
EVIDENCE_LIMIT = 100
RISK_ENUM_WEIGHTS = {"critical": 5, "high": 4, "medium": 2, "low": 1}
CONE_LIMIT = 500
SYMBOL_MATCH_LIMIT = 20
IMPORT_KINDS_DECISIVE = ("internal", "internal-file")
BLAST_LEVELS = ((9, "critical"), (6, "high"), (3, "medium"))
VERIFY_LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")
VERIFY_EXEC_LEVELS = ("L2", "L3", "L4")
VERIFY_DEFAULT_LEVELS = ("L0", "L1")
VERIFY_REQUIRED_SOURCE_FILES = (
    "SKILL.md", "package.json", "README.md", "README.zh-CN.md",
    "CHANGELOG.md", "LICENSE", "NOTICE", "server.json", "install.sh",
    "bin/yotta-dev-mcp.js", "bin/install.js",
    "scripts/dev_engine.py", "scripts/yotta_dev_mcp.py",
    "references/tools.md", "assets/banner.png",
)
VERIFY_REQUIRED_INSTALLED_FILES = ("SKILL.md", "assets/banner.png")
VERIFY_WRITE_GATES = (
    ("run_checks", "allow_execute"),
    ("scaffold_skill", "apply"),
    ("workflow_state", "apply"),
    ("verify_change", "allow_execute"),
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ERROR_RE = re.compile(
    r"(?i)(traceback|exception|\berror\b|\bfailed\b|\bfail\b|fatal|panic|"
    r"assertion|npm err!|\berr\b|\bwarn(?:ing)?\b)"
)


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _read_text(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("文件超过大小上限: %s" % path)
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError("二进制文件不参与文本扫描: %s" % path)
    return data.decode("utf-8", errors="replace")


def _iter_files(root, extensions=None, max_files=5000, all_files=False):
    root = Path(root)
    extensions = set(extensions or TEXT_EXTS)
    count = 0
    for current, dirs, files in os.walk(str(root)):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
        for name in sorted(files):
            path = Path(current) / name
            if path.is_symlink():
                continue
            if not all_files and extensions and path.suffix.lower() not in extensions:
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            yield path
            count += 1
            if count >= max_files:
                return


def _rel(root, path):
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
    except ValueError:
        return str(Path(path)).replace("\\", "/")


def _language(path):
    ext = Path(path).suffix.lower()
    if ext == ".py":
        return "python"
    if ext in (".js", ".jsx", ".mjs", ".cjs"):
        return "javascript"
    if ext in (".ts", ".tsx"):
        return "typescript"
    if ext in (".sh", ".ps1"):
        return "shell"
    return ext.lstrip(".") or "text"


def _resolve_python_import(source_rel, node):
    source = Path(source_rel)
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    if node.level:
        parts = list(source.with_suffix("").parts[:-1])
        if node.level > 1:
            parts = parts[:-(node.level - 1)] if len(parts) >= node.level - 1 else []
        prefix = "/".join(parts)
        module = node.module or ""
        target = (prefix + "/" + module.replace(".", "/")).strip("/")
        return [target + ".py" if target else source_rel]
    if node.module:
        return [node.module]
    return []


def _repo_map_python(path, rel):
    text = _read_text(path)
    imports = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return imports
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for target in _resolve_python_import(rel, node):
                imports.append({"source": rel, "target": target, "line": getattr(node, "lineno", 1)})
    return imports


def _repo_map_js(path, rel):
    text = _read_text(path)
    imports = []
    patterns = [
        re.compile(r"""(?:from\s+|import\s*\()\s*['"]([^'"]+)['"]"""),
        re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
    ]
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern in patterns:
            for match in pattern.finditer(line):
                imports.append({"source": rel, "target": match.group(1), "line": lineno})
    return imports


def repo_map(path, max_files=2000):
    root = Path(path)
    if not root.exists():
        raise ValueError("路径不存在: %s" % path)
    if not root.is_dir():
        raise ValueError("repo_map 需要目录: %s" % path)
    modules = []
    imports = []
    entrypoints = []
    truncated = False
    files = list(_iter_files(root, source_exts(), max_files=max_files + 1))
    if len(files) > max_files:
        truncated = True
        files = files[:max_files]
    for file_path in files:
        rel = _rel(root, file_path)
        try:
            text = _read_text(file_path)
        except (OSError, ValueError):
            continue
        language = _language(file_path)
        modules.append({"path": rel, "language": language, "lines": len(text.splitlines())})
        if file_path.suffix.lower() == ".py":
            imports.extend(_repo_map_python(file_path, rel))
        elif file_path.suffix.lower() in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
            imports.extend(_repo_map_js(file_path, rel))
        if (
            file_path.name in ("main.py", "cli.py", "app.py", "index.js", "index.ts")
            or "if __name__ == '__main__'" in text
            or 'if __name__ == "__main__"' in text
        ):
            entrypoints.append(rel)
    modules.sort(key=lambda item: item["path"])
    imports.sort(key=lambda item: (item["source"], item["line"], item["target"]))
    entrypoints = sorted(set(entrypoints))
    return {
        "root": str(root.resolve()),
        "modules": modules,
        "imports": imports,
        "entrypoints": entrypoints,
        "truncated": truncated,
    }


def source_exts():
    return set(SOURCE_EXTS)


def _classify_match(line, query):
    escaped = re.escape(query)
    if re.search(r"^\s*(?:async\s+)?def\s+%s\b" % escaped, line):
        return "definition"
    if re.search(r"^\s*class\s+%s\b" % escaped, line):
        return "definition"
    if re.search(r"^\s*(?:export\s+)?(?:async\s+)?function\s+%s\b" % escaped, line):
        return "definition"
    if re.search(r"^\s*(?:export\s+)?(?:const|let|var)\s+%s\b" % escaped, line):
        return "definition"
    return "reference"


def find_code(path, query, extensions=None, max_results=100, context_lines=0):
    root = Path(path)
    if not root.exists():
        raise ValueError("路径不存在: %s" % path)
    if not query:
        raise ValueError("find_code 需要 query")
    if max_results < 1:
        raise ValueError("max_results 必须大于 0")
    context_lines = max(0, min(int(context_lines), 5))
    matches = []
    truncated = False
    for file_path in _iter_files(root if root.is_dir() else root.parent, extensions=extensions):
        if root.is_file() and file_path.resolve() != root.resolve():
            continue
        try:
            lines = _read_text(file_path).splitlines()
        except (OSError, ValueError):
            continue
        for index, line in enumerate(lines):
            if query not in line:
                continue
            if len(matches) >= max_results:
                truncated = True
                break
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            matches.append({
                "path": _rel(root if root.is_dir() else root.parent, file_path),
                "line": index + 1,
                "kind": _classify_match(line, query),
                "text": line.strip()[:240],
                "context": lines[start:end] if context_lines else [],
            })
        if truncated:
            break
    return {"query": query, "matches": matches, "truncated": truncated}


def _dedupe_lines(lines):
    output = []
    index = 0
    while index < len(lines):
        line = lines[index]
        count = 1
        while index + count < len(lines) and lines[index + count] == line:
            count += 1
        output.append(line + (" [x%d]" % count if count > 1 else ""))
        index += count
    return output


def _fit_text(lines, max_chars):
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    if max_chars < 20:
        return text[:max_chars]
    output = []
    for line in lines:
        candidate = "\n".join(output + [line])
        if len(candidate) > max_chars - 20:
            break
        output.append(line)
    if output and output[-1] != "... [truncated]":
        output.append("... [truncated]")
    return "\n".join(output)[:max_chars]


def compress_output(text=None, file=None, max_chars=4000, head_lines=40, tail_lines=40):
    if file and text is None:
        text = _read_text(file)
    if text is None:
        raise ValueError("compress_output 需要 text 或 file")
    text = ANSI_RE.sub("", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = [line.rstrip() for line in text.splitlines()]
    deduped = _dedupe_lines(raw_lines)
    priority = [line for line in deduped if ERROR_RE.search(line)]
    head = deduped[:max(0, int(head_lines))]
    tail = deduped[-max(0, int(tail_lines)):] if tail_lines else []
    chosen = []
    for line in priority + head + tail:
        if line not in chosen:
            chosen.append(line)
    result_text = _fit_text(chosen, int(max_chars))
    return {
        "text": result_text,
        "original_lines": len(raw_lines),
        "kept_lines": len(result_text.splitlines()),
        "error_lines": len(priority),
        "truncated": len(result_text) < len("\n".join(deduped)),
    }


def _review_line(rel, line_no, line_text):
    findings = []
    for rule, severity, pattern, suggestion in REVIEW_RULES:
        if pattern.search(line_text):
            findings.append({
                "path": rel,
                "line": line_no,
                "rule": rule,
                "severity": severity,
                "evidence": line_text.strip()[:240],
                "suggestion": suggestion,
            })
    return findings


def review_code(path=None, text=None, max_findings=200):
    findings = []
    if text is not None:
        for line_no, line in enumerate(str(text).splitlines(), 1):
            findings.extend(_review_line("<text>", line_no, line))
    else:
        if not path:
            raise ValueError("review_code 需要 path 或 text")
        root = Path(path)
        if not root.exists():
            raise ValueError("路径不存在: %s" % path)
        files = [root] if root.is_file() else list(_iter_files(root, extensions=source_exts()))
        base = root.parent if root.is_file() else root
        for file_path in files:
            try:
                lines = _read_text(file_path).splitlines()
            except (OSError, ValueError):
                continue
            rel = _rel(base, file_path)
            for line_no, line in enumerate(lines, 1):
                findings.extend(_review_line(rel, line_no, line))
    findings.sort(key=lambda item: (item["path"], item["line"], item["rule"]))
    truncated = len(findings) > max_findings
    return {"findings": findings[:max_findings], "truncated": truncated}


def _parse_added_lines(diff_text):
    current_file = None
    line_no = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            current_file = raw[4:].strip()
            if current_file.startswith("b/"):
                current_file = current_file[2:]
            continue
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if match:
            line_no = int(match.group(1))
            continue
        if current_file and raw.startswith("+") and not raw.startswith("+++"):
            yield current_file, line_no, raw[1:]
            line_no += 1
        elif current_file and not raw.startswith("-") and not raw.startswith("\\"):
            line_no += 1


def review_diff(diff_text=None, path=None, base=None, max_findings=200):
    if diff_text is None:
        if not path:
            raise ValueError("review_diff 需要 diff_text 或 path")
        command = ["git", "-C", str(path), "diff", "--no-ext-diff", "--unified=0"]
        if base:
            command.append(str(base))
        proc = subprocess.run(command, capture_output=True, text=True)
        if proc.returncode != 0:
            raise ValueError("git diff 失败: %s" % (proc.stderr.strip() or proc.stdout.strip()))
        diff_text = proc.stdout
    findings = []
    files = []
    for rel, line_no, line in _parse_added_lines(diff_text):
        if rel not in files:
            files.append(rel)
        findings.extend(_review_line(rel, line_no, line))
    findings.sort(key=lambda item: (item["path"], item["line"], item["rule"]))
    truncated = len(findings) > max_findings
    return {"files": files, "findings": findings[:max_findings], "truncated": truncated}


def _frontmatter_version(text):
    match = re.search(r"(?m)^version:\s*[\"']?([^\"'\r\n]+)", text)
    return match.group(1).strip() if match else None


def _frontmatter_name(text):
    match = re.search(r"(?m)^name:\s*[\"']?([^\"'\r\n]+)", text)
    return match.group(1).strip() if match else None


def _default_skill_dirs():
    home = Path.home()
    candidates = [
        home / ".codex" / "skills",
        home / ".claude" / "skills",
        home / ".cursor" / "skills",
        home / ".config" / "opencode" / "skills",
    ]
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        candidates.insert(0, Path(codex_home) / "skills")
    claude_home = os.environ.get("CLAUDE_CONFIG_DIR")
    if claude_home:
        candidates.insert(0, Path(claude_home) / "skills")
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_home:
        candidates.insert(0, Path(xdg_home) / "opencode" / "skills")
    return candidates


def _default_config_paths():
    home = Path.home()
    return [
        home / ".codex" / "config.json",
        home / ".codex" / "mcp.json",
        home / ".config" / "opencode" / "opencode.json",
        home / ".claude" / "settings.json",
        home / ".cursor" / "mcp.json",
    ]


def mcp_doctor(skills_dirs=None, config_paths=None):
    skills = []
    issues = []
    for directory in (skills_dirs or _default_skill_dirs()):
        root = Path(directory)
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            skill_file = child / "SKILL.md"
            if not child.is_dir() or not skill_file.is_file():
                continue
            try:
                text = _read_text(skill_file)
            except (OSError, ValueError) as exc:
                issues.append("%s: %s" % (skill_file, exc))
                continue
            skills.append({
                "name": _frontmatter_name(text) or child.name,
                "version": _frontmatter_version(text),
                "path": str(skill_file),
            })
    mcp_configs = []
    for config_path in (config_paths or _default_config_paths()):
        path = Path(config_path)
        if not path.is_file():
            continue
        try:
            payload = json.loads(_read_text(path))
        except Exception as exc:  # noqa: BLE001
            issues.append("%s: %s" % (path, exc))
            continue
        servers = payload.get("mcpServers") if isinstance(payload, dict) else None
        mcp_configs.append({
            "path": str(path),
            "servers": sorted(servers.keys()) if isinstance(servers, dict) else [],
        })
    skills.sort(key=lambda item: (item["name"], item["path"]))
    mcp_configs.sort(key=lambda item: item["path"])
    return {
        "skills": skills,
        "mcp_configs": mcp_configs,
        "issues": issues,
        "checked_skills": len(skills),
        "checked_configs": len(mcp_configs),
    }


SECRET_KEY_RE = re.compile(
    r"""(?i)\b(api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=]\s*["']?([^"'\s#]{8,})"""
)
AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9_+/=\-]{32,}")


def _entropy(value):
    if not value:
        return 0.0
    counts = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = float(len(value))
    return -sum((count / length) * math.log(count / length, 2) for count in counts.values())


def _redact(value):
    if len(value) <= 8:
        return "[REDACTED]"
    return value[:4] + "...[REDACTED]"


def scan_secrets(path=None, text=None, max_findings=200, include_git_history=False):
    entries = []
    if text is not None:
        entries.append(("<text>", str(text)))
    else:
        if not path:
            raise ValueError("scan_secrets 需要 path 或 text")
        root = Path(path)
        if not root.exists():
            raise ValueError("路径不存在: %s" % path)
        files = [root] if root.is_file() else list(_iter_files(root, all_files=True))
        base = root.parent if root.is_file() else root
        for file_path in files:
            try:
                entries.append((_rel(base, file_path), _read_text(file_path)))
            except (OSError, ValueError):
                continue
        if include_git_history:
            entries.append(("git-history", _git_history_text(root)))
    findings = []
    for rel, content in entries:
        for line_no, line in enumerate(content.splitlines(), 1):
            if PRIVATE_KEY_RE.search(line):
                findings.append({
                    "path": rel, "line": line_no, "rule": "private-key",
                    "severity": "critical", "evidence": "[REDACTED private key marker]",
                    "suggestion": "Remove the private key from source control and rotate it.",
                })
            for match in AWS_KEY_RE.finditer(line):
                findings.append({
                    "path": rel, "line": line_no, "rule": "aws-access-key",
                    "severity": "critical", "evidence": _redact(match.group(0)),
                    "suggestion": "Rotate the key and move it to a secret manager.",
                })
            for match in SECRET_KEY_RE.finditer(line):
                name = match.group(1).lower()
                rule = "api-key" if "api" in name or "access" in name else (
                    "token" if "token" in name else "credential"
                )
                findings.append({
                    "path": rel, "line": line_no, "rule": rule,
                    "severity": "high", "evidence": "%s=%s" % (match.group(1), _redact(match.group(2))),
                    "suggestion": "Remove the literal credential and load it from the environment or a secret store.",
                })
            for match in HIGH_ENTROPY_RE.finditer(line):
                token = match.group(0)
                if _entropy(token) >= 4.0 and not PRIVATE_KEY_RE.search(line):
                    findings.append({
                        "path": rel, "line": line_no, "rule": "high-entropy-token",
                        "severity": "medium", "evidence": _redact(token),
                        "suggestion": "Verify whether this is a credential; if so, remove and rotate it.",
                    })
    unique = {}
    for item in findings:
        key = (item["path"], item["line"], item["rule"], item["evidence"])
        unique[key] = item
    ordered = sorted(unique.values(), key=lambda item: (item["path"], item["line"], item["rule"], item["evidence"]))
    return {"findings": ordered[:max_findings], "truncated": len(ordered) > max_findings}


def _git_history_text(root):
    """Return bounded git diff text for secret scanning; never fail the scan."""
    try:
        process = subprocess.run(
            ["git", "-C", str(root), "log", "-p", "--all", "--no-ext-diff", "--max-count=50"],
            capture_output=True, text=True, timeout=30,
        )
        if process.returncode != 0:
            return ""
        return process.stdout[:MAX_FILE_BYTES]
    except Exception:  # noqa: BLE001
        return ""


DEPENDENCY_LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lock", "poetry.lock", "uv.lock", "Pipfile.lock",
}


def _dependency_spec_issue(spec):
    value = str(spec).strip()
    if value in ("", "*", "latest") or value.startswith((">=", ">", "http://", "git+http://")):
        return "unpinned-dependency"
    if value.startswith(("file:", "link:")):
        return "local-dependency"
    return None


POPULAR_PACKAGES = (
    "requests", "numpy", "pytest", "lodash", "express", "react", "vue",
    "typescript", "fastapi", "pydantic", "openai", "axios",
)


def _edit_distance_one(left, right):
    if abs(len(left) - len(right)) > 1:
        return False
    if left == right:
        return False
    if len(left) == len(right):
        return sum(1 for a, b in zip(left, right) if a != b) == 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    index = 0
    skipped = False
    for char in long:
        if index < len(short) and char == short[index]:
            index += 1
        elif skipped:
            return False
        else:
            skipped = True
    return True


def _typosquat_suspicion(name):
    low = str(name).lower()
    return any(_edit_distance_one(low, known) for known in POPULAR_PACKAGES)


def scan_dependencies(path):
    root = Path(path)
    if not root.exists():
        raise ValueError("路径不存在: %s" % path)
    if not root.is_dir():
        raise ValueError("scan_dependencies 需要目录: %s" % path)
    manifests = []
    lockfiles = []
    issues = []
    for file_path in _iter_files(root, extensions={".json", ".txt", ".toml", ".lock"}, max_files=500):
        rel = _rel(root, file_path)
        if file_path.name in DEPENDENCY_LOCKFILES:
            lockfiles.append(rel)
    package_file = root / "package.json"
    if package_file.is_file():
        manifests.append("package.json")
        try:
            package = json.loads(_read_text(package_file))
        except Exception as exc:  # noqa: BLE001
            issues.append({"code": "invalid-manifest", "severity": "high",
                           "manifest": "package.json", "message": str(exc)})
            package = {}
        groups = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
        has_deps = False
        for group in groups:
            dependencies = package.get(group) if isinstance(package, dict) else None
            if not isinstance(dependencies, dict):
                continue
            has_deps = has_deps or bool(dependencies)
            for name, spec in sorted(dependencies.items()):
                code = _dependency_spec_issue(spec)
                if code:
                    issues.append({
                        "code": code, "severity": "medium", "manifest": "package.json",
                        "dependency": name, "message": "%s uses %s" % (name, spec),
                    })
                if _typosquat_suspicion(name):
                    issues.append({
                        "code": "typosquat-suspicion", "severity": "medium",
                        "manifest": "package.json", "dependency": name,
                        "message": "%s is one edit away from a popular package; verify the name" % name,
                        "requires_manual_review": True,
                    })
        if has_deps and not lockfiles:
            issues.append({
                "code": "missing-lockfile", "severity": "medium", "manifest": "package.json",
                "message": "Dependencies exist but no lockfile was found.",
            })
    for requirement in sorted(root.glob("requirements*.txt")):
        manifests.append(_rel(root, requirement))
        for line_no, line in enumerate(_read_text(requirement).splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith(("git+http://", "http://")):
                issues.append({"code": "insecure-source", "severity": "high",
                               "manifest": _rel(root, requirement), "line": line_no,
                               "message": stripped})
            elif "@" in stripped and "==" not in stripped and ">=" not in stripped:
                issues.append({"code": "unpinned-dependency", "severity": "medium",
                               "manifest": _rel(root, requirement), "line": line_no,
                               "message": stripped})
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        manifests.append("pyproject.toml")
        text = _read_text(pyproject)
        if "dependencies" in text and not (root / "poetry.lock").is_file() and not (root / "uv.lock").is_file():
            issues.append({
                "code": "missing-lockfile", "severity": "medium",
                "manifest": "pyproject.toml",
                "message": "Python dependencies exist but no poetry.lock / uv.lock was found.",
            })
    pipfile = root / "Pipfile"
    if pipfile.is_file():
        manifests.append("Pipfile")
        if not (root / "Pipfile.lock").is_file():
            issues.append({
                "code": "missing-lockfile", "severity": "medium",
                "manifest": "Pipfile",
                "message": "Pipfile exists but Pipfile.lock was not found.",
            })
    issues.sort(key=lambda item: (item.get("manifest", ""), item.get("line", 0), item["code"], item.get("dependency", "")))
    return {
        "manifests": sorted(manifests),
        "lockfiles": sorted(lockfiles),
        "issues": issues,
        "checked_manifests": len(manifests),
        "checked_lockfiles": len(lockfiles),
    }


def check_publish_readiness(path):
    root = Path(path)
    if not root.exists() or not root.is_dir():
        raise ValueError("check_publish_readiness 需要目录: %s" % path)
    required = ["package.json", "SKILL.md", "README.md", "LICENSE", "CHANGELOG.md"]
    files = {}
    issues = []
    for name in required:
        file_path = root / name
        files[name] = file_path.is_file()
        if not file_path.is_file():
            issues.append({"code": "missing-file", "severity": "high",
                           "message": "Missing required file: %s" % name})
    versions = {}
    package = {}
    package_path = root / "package.json"
    if package_path.is_file():
        try:
            package = json.loads(_read_text(package_path))
        except Exception as exc:  # noqa: BLE001
            issues.append({"code": "invalid-package-json", "severity": "high", "message": str(exc)})
            package = {}
        versions["package.json"] = package.get("version")
    skill_path = root / "SKILL.md"
    if skill_path.is_file():
        versions["SKILL.md"] = _frontmatter_version(_read_text(skill_path))
    changelog_path = root / "CHANGELOG.md"
    if changelog_path.is_file():
        match = re.search(r"(?m)^##\s+v?(\d+\.\d+\.\d+)", _read_text(changelog_path))
        versions["CHANGELOG.md"] = match.group(1) if match else None
    script_versions = []
    for script in sorted(root.glob("scripts/*.py")):
        try:
            match = re.search(r'(?m)^VERSION\s*=\s*["\']([^"\']+)["\']', _read_text(script))
        except (OSError, ValueError):
            continue
        if match:
            script_versions.append(match.group(1))
    if script_versions:
        versions["engine"] = script_versions[0]
    values = [value for value in versions.values() if value]
    if len(set(values)) > 1:
        issues.append({
            "code": "version-mismatch", "severity": "high",
            "message": "Version mismatch: %s" % json.dumps(versions, ensure_ascii=False, sort_keys=True),
        })
    repository = package.get("repository") if isinstance(package, dict) else None
    repository_url = repository.get("url") if isinstance(repository, dict) else repository
    if not repository_url:
        issues.append({"code": "missing-repository", "severity": "medium",
                       "message": "package.json repository.url is missing."})
    publish_config = package.get("publishConfig") if isinstance(package, dict) else None
    if not isinstance(publish_config, dict) or publish_config.get("access") != "public":
        issues.append({"code": "publish-access", "severity": "medium",
                       "message": "package.json publishConfig.access should be public."})
    issues.sort(key=lambda item: (item["code"], item.get("message", "")))
    return {
        "ok": not any(item["severity"] == "high" for item in issues),
        "root": str(root.resolve()),
        "files": files,
        "versions": versions,
        "issues": issues,
    }


CHECK_COMMANDS = {
    "python-unittest": lambda: [sys.executable, "-m", "unittest", "discover", "-v"],
    "pytest": lambda: [sys.executable, "-m", "pytest", "-q"],
    "python-compile": lambda: [sys.executable, "-m", "compileall", "-q", "."],
    "npm-test": lambda: ["npm", "test", "--silent"],
    "npm-lint": lambda: ["npm", "run", "lint", "--silent"],
}


def run_checks(kind, cwd, timeout=120, allow_execute=False):
    if kind not in CHECK_COMMANDS:
        raise ValueError("unsupported check kind: %s" % kind)
    if not allow_execute:
        raise PermissionError("run_checks is disabled by default; pass allow_execute=true explicitly")
    root = Path(cwd)
    if not root.is_dir():
        raise ValueError("cwd is not a directory: %s" % cwd)
    command = CHECK_COMMANDS[kind]()
    if os.name == "nt" and command[0] == "npm":
        command = ["cmd", "/c"] + command
    try:
        process = subprocess.run(
            command, cwd=str(root), capture_output=True, text=True, timeout=timeout
        )
        output = (process.stdout or "") + (process.stderr or "")
        result = compress_output(output, max_chars=4000)
        summary_lines = [
            line.strip() for line in output.splitlines()
            if re.search(r"(?i)(ran \d+ test|ok\b|failed\b|error\b|\d+ passed)", line)
        ]
        return {
            "kind": kind,
            "cwd": str(root.resolve()),
            "exit_code": process.returncode,
            "passed": process.returncode == 0,
            "summary": "\n".join(summary_lines[:8]) or "no summary matched",
            "output": result["text"],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "kind": kind, "cwd": str(root.resolve()), "exit_code": 124,
            "passed": False, "summary": "timeout after %ss" % timeout,
            "output": "", "timed_out": True,
        }


def _license_text():
    return (
        "MIT License\n\nCopyright (c) 2026 YottaMeta\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
        "of this software and associated documentation files (the \"Software\"), to deal\n"
        "in the Software without restriction, including without limitation the rights\n"
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
        "copies of the Software, and to permit persons to whom the Software is\n"
        "furnished to do so, subject to the following conditions:\n\n"
        "The above copyright notice and this permission notice shall be included in all\n"
        "copies or substantial portions of the Software.\n\n"
        "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n"
        "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
        "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
        "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
        "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
        "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
        "SOFTWARE.\n"
    )


def scaffold_skill(name, output_dir, apply=False, description=None):
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", str(name)):
        raise ValueError("skill name must match ^[a-z0-9][a-z0-9-]*$")
    target = Path(output_dir) / name
    description = description or "Deterministic local helper skill."
    files = {
        "SKILL.md": (
            "---\nname: %s\ndescription: %s\nversion: 0.1.0\nlicense: MIT\n---\n\n"
            "# %s\n\n%s\n" % (name, description, name, description)
        ),
        "package.json": json.dumps({
            "name": "@yottameta/%s" % name,
            "version": "0.1.0",
            "description": description,
            "license": "MIT",
            "repository": {"type": "git", "url": "git+https://github.com/YottaMeta/%s.git" % name},
            "publishConfig": {"access": "public"},
            "files": ["SKILL.md", "README.md", "scripts", "LICENSE", "NOTICE"],
        }, ensure_ascii=False, indent=2) + "\n",
        "README.md": "# %s\n\n%s\n" % (name, description),
        "CHANGELOG.md": "# Changelog\n\n## v0.1.0 (2026-09-25)\n\n- Initial scaffold.\n",
        "NOTICE": "# NOTICE\n\nGenerated by yotta-dev-mcp scaffold_skill.\n",
        "LICENSE": _license_text(),
        "scripts/%s.py" % name: (
            "#!/usr/bin/env python3\n"
            "# -*- coding: utf-8 -*-\n"
            "\"\"\"%s.\"\"\"\n\n"
            "def main():\n"
            "    return 0\n\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n" % description
        ),
    }
    if apply and target.exists() and any(target.iterdir()):
        raise ValueError("target already exists and is not empty: %s" % target)
    if apply:
        for rel, content in files.items():
            file_path = target / rel
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
    return {
        "name": name,
        "target": str(target),
        "applied": bool(apply),
        "files": [{"path": rel, "bytes": len(content.encode("utf-8"))}
                  for rel, content in sorted(files.items())],
    }


def _atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(str(path), str(backup))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(str(temporary), str(path))
    return str(backup) if backup else None


def workflow_state(root, action="read", date=None, text=None, file=None, apply=False):
    workflow = Path(root) / ".workflow"
    files = ["STATE.md", "TASKS.md", "DECISIONS.md", "ROADMAP.md"]
    if action == "read":
        existing = [name for name in files if (workflow / name).is_file()]
        missing = [name for name in files if name not in existing]
        excerpts = {}
        for name in existing:
            try:
                content = _read_text(workflow / name)
            except (OSError, ValueError):
                content = ""
            excerpts[name] = content[:1200]
        return {
            "ok": not missing,
            "workflow": str(workflow.resolve()),
            "files": existing,
            "missing": missing,
            "excerpts": excerpts,
            "applied": False,
        }
    if action == "append-log":
        if not date or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date)):
            raise ValueError("append-log requires date=YYYY-MM-DD")
        target = workflow / "logs" / ("%s.md" % date)
    elif action == "append-file":
        if file not in files:
            raise ValueError("append-file requires one of: %s" % ", ".join(files))
        target = workflow / file
    else:
        raise ValueError("unsupported workflow action: %s" % action)
    body = (text or "").rstrip() + "\n"
    preview = body
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = _read_text(target)
            if existing and not existing.endswith("\n"):
                existing += "\n"
            _atomic_write(target, existing + "\n" + body)
        else:
            header = ""
            if action == "append-log":
                header = "# 流水日志 %s\n\n" % date
            _atomic_write(target, header + body)
    return {
        "ok": True,
        "action": action,
        "target": str(target.resolve()),
        "applied": bool(apply),
        "preview": preview,
    }


def _resolve_relative_module(raw_target, source_rel, module_set, root=None):
    base = posixpath.normpath(posixpath.join(posixpath.dirname(source_rel), raw_target))
    if base.startswith("..") or base.startswith("/"):
        return None, None
    candidates = [base + ext for ext in JS_EXTS]
    candidates.extend(base + "/index" + ext for ext in JS_EXTS)
    candidates.append(base)
    for candidate in candidates:
        if candidate in module_set:
            return candidate, "module"
    if root is not None and (Path(root) / base).is_file():
        return base, "file"
    return None, None


def _classify_python_import(raw_target, source_rel, module_set):
    if raw_target.endswith(".py"):
        if raw_target in module_set:
            return "internal", raw_target
        return "unresolved", raw_target
    dotted = raw_target.replace(".", "/")
    source_dir = posixpath.dirname(source_rel)
    bases = [dotted]
    if source_dir:
        bases.insert(0, posixpath.join(source_dir, dotted))
    for base in bases:
        for candidate in (base + ".py", base + "/__init__.py"):
            if candidate in module_set:
                return "internal", candidate
    return "external", raw_target


def _classify_js_import(raw_target, source_rel, module_set, root=None):
    if raw_target.startswith("."):
        resolved, kind = _resolve_relative_module(raw_target, source_rel, module_set, root)
        if kind == "module":
            return "internal", resolved
        if kind == "file":
            return "internal-file", resolved
        return "unresolved", raw_target
    return "external", raw_target


def _is_test_file(rel):
    parts = rel.split("/")
    if any(part in ("tests", "test", "__tests__", "spec") for part in parts[:-1]):
        return True
    return bool(TEST_NAME_RE.match(parts[-1]))


def _config_kind(name):
    suffix = Path(name).suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".toml":
        return "toml"
    if suffix in (".yml", ".yaml"):
        return "yaml"
    if suffix in (".ini", ".cfg"):
        return "ini"
    return "text"


def _collect_files_by_suffix(root, suffixes, limit=200):
    root = Path(root)
    found = []
    for current, dirs, files in os.walk(str(root)):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
        for name in sorted(files):
            path = Path(current) / name
            if path.is_symlink():
                continue
            if path.suffix.lower() in suffixes:
                found.append(path)
                if len(found) >= limit:
                    return found
    return found


def _collect_configs(root, limit=200):
    root = Path(root)
    configs = []
    for current, dirs, files in os.walk(str(root)):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
        for name in sorted(files):
            path = Path(current) / name
            if path.is_symlink():
                continue
            if name in CONFIG_NAMES or path.suffix.lower() in CONFIG_SUFFIXES:
                configs.append({"path": _rel(root, path), "kind": _config_kind(name)})
                if len(configs) >= limit:
                    return configs
    return configs


def _collect_store_files(root, limit=200):
    root = Path(root)
    stores = []
    for path in _collect_files_by_suffix(root, set(STORAGE_SUFFIXES), limit=limit):
        stores.append({
            "path": _rel(root, path),
            "kind": STORAGE_SUFFIXES[path.suffix.lower()],
        })
    return stores


def _unverified_claims():
    return [
        {
            "claim": "architecture rules hold for this model",
            "level": "L1",
            "status": "UNVERIFIED",
            "reason": "system_model only builds the model; rule checking runs in architecture_review",
        },
        {
            "claim": "changed code still passes its tests",
            "level": "L2-L4",
            "status": "UNVERIFIED",
            "reason": "system_model executes no tests, mutations or property checks",
        },
    ]


def system_model(path, max_files=2000, contract_file=None):
    """Build the system model and attach architecture contract layer data."""
    root = Path(path)
    if not root.exists():
        raise ValueError("路径不存在: %s" % path)
    if not root.is_dir():
        raise ValueError("system_model 需要目录: %s" % path)
    if max_files < 1:
        raise ValueError("max_files 必须 >= 1")

    contract_result = dev_contract.load_contract(root, contract_file=contract_file)
    contract = contract_result["contract"]
    contract_usable = bool(
        contract_result["present"] and contract_result["ok"] and contract
    )

    unknowns = []
    evidence = []
    if not contract_result["present"]:
        unknowns.append({
            "kind": "contract-missing",
            "id": contract_result["path"],
            "detail": "no architecture contract found",
            "next_step": "add %s with layers, rules and data ownership" % contract_result["path"],
        })
    elif not contract_result["ok"]:
        blocking = [item for item in contract_result["findings"]
                    if item["severity"] in dev_contract.BLOCKING_SEVERITIES]
        unknowns.append({
            "kind": "contract-invalid",
            "id": contract_result["path"],
            "detail": "%d blocking finding(s); layer data is not applied" % len(blocking),
            "next_step": "fix the contract findings and rerun system_model",
        })
    for finding in contract_result["findings"]:
        evidence.append({
            "path": finding["path"],
            "pointer": finding["pointer"],
            "detail": "%s: %s" % (finding["severity"], finding["message"]),
        })

    files = list(_iter_files(root, source_exts(), max_files=max_files + 1))
    truncated = len(files) > max_files
    if truncated:
        files = files[:max_files]
    module_set = {_rel(root, item) for item in files}

    modules = []
    imports = []
    entrypoints = []
    tests = []
    for file_path in files:
        rel = _rel(root, file_path)
        try:
            text = _read_text(file_path)
        except (OSError, ValueError):
            continue
        language = _language(file_path)
        layer = None
        if contract_usable:
            matched = dev_contract.match_layers(rel, contract)
            if not matched:
                unknowns.append({
                    "kind": "unassigned-module",
                    "id": rel,
                    "detail": "no layer glob matches this module",
                    "next_step": "add a layer paths glob in %s" % contract_result["path"],
                })
            elif len(matched) > 1:
                layer = matched[0]
                unknowns.append({
                    "kind": "layer-overlap",
                    "id": rel,
                    "layers": matched,
                    "detail": "module matches %d layers; first declaration wins" % len(matched),
                    "next_step": "narrow the overlapping layer globs",
                })
            else:
                layer = matched[0]
        modules.append({
            "id": rel,
            "language": language,
            "lines": len(text.splitlines()),
            "layer": layer,
        })
        suffix = file_path.suffix.lower()
        if suffix == ".py":
            raw_imports = _repo_map_python(file_path, rel)
            classifier = lambda raw: _classify_python_import(raw, rel, module_set)  # noqa: E731
        elif suffix in JS_EXTS:
            raw_imports = _repo_map_js(file_path, rel)
            classifier = lambda raw: _classify_js_import(raw, rel, module_set, root)  # noqa: E731
        else:
            raw_imports = []
            classifier = None
        module_imports = []
        for raw in raw_imports:
            target_raw = raw["target"]
            if target_raw == rel:
                continue
            kind, target = classifier(target_raw)
            entry = {
                "source": rel,
                "target": target,
                "kind": kind,
                "line": raw["line"],
                "raw": target_raw,
            }
            module_imports.append(entry)
            imports.append(entry)
            if kind == "unresolved":
                unknowns.append({
                    "kind": "unresolved-import",
                    "id": rel,
                    "line": raw["line"],
                    "detail": "relative import does not resolve: %s" % target_raw,
                    "next_step": "check the import path or add the missing module",
                })
                evidence.append({
                    "path": rel,
                    "line": raw["line"],
                    "detail": "unresolved relative import: %s" % target_raw,
                })
        if _is_test_file(rel):
            tests.append({
                "path": rel,
                "targets": sorted({item["target"] for item in module_imports
                                   if item["kind"] == "internal"}),
            })
        if (
            file_path.name in ("main.py", "cli.py", "app.py", "index.js", "index.ts")
            or "if __name__ == '__main__'" in text
            or 'if __name__ == "__main__"' in text
        ):
            entrypoints.append(rel)

    layer_summaries = []
    if contract_usable:
        for layer in contract["layers"]:
            layer_summaries.append({
                "id": layer["id"],
                "title": layer["title"],
                "risk": layer["risk"],
                "paths": layer["paths"],
                "modules": sorted(item["id"] for item in modules
                                  if item["layer"] == layer["id"]),
            })

    data_stores = []
    if contract_usable:
        for store in contract["data_ownership"]:
            data_stores.append({
                "store": store["store"],
                "owner": store["owner"],
                "kind": store["kind"] or "unknown",
                "paths": store["paths"],
                "owner_modules": sorted(item["id"] for item in modules
                                        if item["layer"] == store["owner"]),
                "detected": False,
            })
            for store_path in store["paths"]:
                evidence.append({
                    "path": store_path,
                    "detail": "declared data ownership: %s" % store["store"],
                })
    for item in _collect_store_files(root):
        matched = dev_contract.match_layers(item["path"], contract) if contract_usable else []
        owner = matched[0] if matched else None
        data_stores.append({
            "store": item["path"],
            "owner": owner,
            "kind": item["kind"],
            "paths": [item["path"]],
            "owner_modules": sorted(m["id"] for m in modules if owner and m["layer"] == owner),
            "detected": True,
        })
        evidence.append({
            "path": item["path"],
            "detail": "detected local data store file (%s)" % item["kind"],
        })
    data_stores.sort(key=lambda item: item["store"])

    model = {
        "modules": sorted(modules, key=lambda item: item["id"]),
        "layers": layer_summaries,
        "imports": sorted(imports, key=lambda item: (item["source"], item["line"], item["target"])),
        "entrypoints": sorted(set(entrypoints)),
        "tests": sorted(tests, key=lambda item: item["path"]),
        "configs": _collect_configs(root),
        "data_stores": data_stores,
    }
    digest = hashlib.sha256(
        json.dumps(model, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    unknowns.sort(key=lambda item: (item["kind"], item["id"], item.get("line", 0)))
    unknowns_truncated = len(unknowns) > UNKNOWN_LIMIT
    evidence.sort(key=lambda item: (item["path"], item.get("line", 0), item["detail"]))
    if len(evidence) > EVIDENCE_LIMIT:
        evidence = evidence[:EVIDENCE_LIMIT]
    if contract_result["present"] and not contract_result["ok"]:
        status = "FAIL"
    elif unknowns:
        status = "UNKNOWN"
    else:
        status = "PASS"
    return {
        "status": status,
        "root": str(root.resolve()),
        "contract": {
            "path": contract_result["path"],
            "present": contract_result["present"],
            "ok": contract_result["ok"],
            "version": contract_result["version"],
            "layers": contract_result["layers"],
            "rules": contract_result["rules"],
            "findings": contract_result["findings"],
        },
        "model": model,
        "unknowns": unknowns[:UNKNOWN_LIMIT],
        "unknowns_truncated": unknowns_truncated,
        "unverified_claims": _unverified_claims(),
        "evidence": evidence,
        "truncated": truncated,
        "model_digest": "sha256:" + digest,
    }


def _module_layers(model):
    return {item["id"]: item.get("layer") for item in model["modules"]}


def _risk_weight(contract, layer_id):
    """Risk weight for a layer: explicit risk_weights, else the risk enum."""
    if not layer_id:
        return 0.0
    weights = (contract or {}).get("risk_weights") or {}
    value = weights.get(layer_id)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        value = None
        for layer in (contract or {}).get("layers") or []:
            if layer.get("id") == layer_id:
                value = RISK_ENUM_WEIGHTS.get(layer.get("risk"), 1)
                break
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        value = 1
    return float(max(0, min(5, value)))


def _decisive_edges(model):
    return [edge for edge in model["imports"] if edge["kind"] in IMPORT_KINDS_DECISIVE]


def _edge_evidence(edge, detail):
    return {
        "path": edge["source"],
        "line": edge["line"],
        "detail": detail,
        "snippet": edge.get("raw"),
    }


def _review_rules(contract, model, unknown_sink, violation_sink):
    module_layers = _module_layers(model)
    edges = sorted(_decisive_edges(model),
                   key=lambda item: (item["source"], item["line"], item["target"]))
    checked = []
    for rule in contract["rules"]:
        severity = rule.get("severity") or dev_contract.RULE_DEFAULT_SEVERITY
        violations = 0
        undecided = 0
        decisive = 0
        seen_targets = set()
        for edge in edges:
            if module_layers.get(edge["source"]) != rule["from"]:
                continue
            target_layer = module_layers.get(edge["target"])
            if target_layer is None:
                if edge["kind"] == "internal" and edge["target"] not in seen_targets:
                    seen_targets.add(edge["target"])
                    undecided += 1
                    unknown_sink.append({
                        "kind": "rule-target-unassigned",
                        "id": "%s -> %s" % (rule["id"], edge["target"]),
                        "rule": rule["id"],
                        "target": edge["target"],
                        "detail": "the target module has no layer, so the rule cannot be decided",
                        "next_step": "assign the module to a layer or narrow the rule",
                    })
                continue
            decisive += 1
            if rule["type"] == "forbid-dependency":
                broke = target_layer == rule["to"]
                message = rule.get("claim") or (
                    "%s must not import %s" % (rule["from"], rule["to"]))
                code = "rule-forbid-dependency"
            else:
                broke = target_layer not in (rule["to"] or [])
                message = rule.get("claim") or (
                    "%s may only import %s" % (rule["from"], ", ".join(rule["to"] or [])))
                code = "rule-allow-dependency"
            if not broke:
                continue
            violations += 1
            violation_sink.append({
                "code": code,
                "rule": rule["id"],
                "severity": severity,
                "message": message,
                "from_layer": rule["from"],
                "to_layer": target_layer,
                "evidence": [_edge_evidence(
                    edge,
                    "imports %s (layer %s), which breaks %s"
                    % (edge["target"], target_layer, rule["id"]),
                )],
            })
        if violations and severity in dev_contract.BLOCKING_SEVERITIES:
            status = "FAIL"
        elif violations:
            status = "WARN"
        elif undecided:
            status = "UNKNOWN"
        else:
            status = "PASS"
        checked.append({
            "id": rule["id"],
            "type": rule["type"],
            "from": rule["from"],
            "to": rule["to"],
            "severity": severity,
            "claim": rule.get("claim"),
            "status": status,
            "decided_imports": decisive,
            "violations": violations,
            "undecided_imports": undecided,
        })
    return checked


def _review_boundaries(contract, model, unknown_sink, violation_sink):
    module_layers = _module_layers(model)
    edges = sorted(_decisive_edges(model),
                   key=lambda item: (item["source"], item["line"], item["target"]))
    checked = []
    for boundary in contract["boundaries"]:
        paths = boundary["paths"] or []
        protected = sorted(item["id"] for item in model["modules"]
                           if dev_contract.match_any(item["id"], paths))
        visibility = boundary.get("visibility") or "internal"
        severity = "high" if visibility == "private" else "medium"
        violations = 0
        undecided = 0
        consumers = 0
        seen_importers = set()
        if not protected:
            unknown_sink.append({
                "kind": "boundary-no-modules",
                "id": boundary["id"],
                "detail": "no module matches the boundary globs",
                "next_step": "point the boundary at existing modules or drop it",
            })
        protected_set = set(protected)
        for edge in edges:
            if edge["target"] not in protected_set:
                continue
            consumers += 1
            importer = edge["source"]
            if dev_contract.match_any(importer, paths) or visibility == "public":
                continue
            importer_layer = module_layers.get(importer)
            if importer_layer is None:
                if importer not in seen_importers:
                    seen_importers.add(importer)
                    undecided += 1
                    unknown_sink.append({
                        "kind": "boundary-importer-unassigned",
                        "id": "%s <- %s" % (boundary["id"], importer),
                        "boundary": boundary["id"],
                        "target": importer,
                        "detail": "the importer has no layer, so boundary visibility is undecided",
                        "next_step": "assign the importer to a layer",
                    })
                continue
            if visibility == "internal" and importer_layer == boundary["layer"]:
                continue
            violations += 1
            violation_sink.append({
                "code": "boundary-visibility",
                "rule": boundary["id"],
                "severity": severity,
                "message": "module outside the %s boundary imports it (visibility %s)"
                           % (boundary["id"], visibility),
                "boundary": boundary["id"],
                "layer": boundary["layer"],
                "visibility": visibility,
                "evidence": [_edge_evidence(
                    edge,
                    "imports %s, protected by the %s boundary (%s)"
                    % (edge["target"], boundary["id"], visibility),
                )],
            })
        if violations and severity in dev_contract.BLOCKING_SEVERITIES:
            status = "FAIL"
        elif violations:
            status = "WARN"
        elif undecided or not protected:
            status = "UNKNOWN"
        else:
            status = "PASS"
        checked.append({
            "id": boundary["id"],
            "layer": boundary["layer"],
            "visibility": visibility,
            "paths": list(paths),
            "status": status,
            "protected_modules": protected,
            "imports_of_protected_modules": consumers,
            "violations": violations,
            "undecided_imports": undecided,
        })
    return checked


def _glob_literal_prefix(pattern):
    text = str(pattern or "")
    for marker in ("*", "?"):
        index = text.find(marker)
        if index >= 0:
            text = text[:index]
    return text.rstrip("/")


def _review_data_ownership(contract, model, root, violation_sink):
    module_layers = _module_layers(model)
    stores = contract["data_ownership"]
    prefixes = {}
    for store in stores:
        candidates = [_glob_literal_prefix(item) for item in store["paths"] or []]
        prefixes[store["store"]] = sorted(item for item in candidates if len(item) >= 4)
    needed = sorted({item for values in prefixes.values() for item in values})
    texts = {}
    if needed:
        for module in model["modules"]:
            if module.get("layer") is None:
                continue
            try:
                texts[module["id"]] = _read_text(root / module["id"])
            except (OSError, ValueError):
                continue
    checked = []
    for store in stores:
        owner = store["owner"]
        paths = store["paths"] or []
        violations = 0
        mismatched = sorted(item["id"] for item in model["modules"]
                            if dev_contract.match_any(item["id"], paths)
                            and item.get("layer") != owner)
        for rel in mismatched:
            violations += 1
            violation_sink.append({
                "code": "data-ownership-mismatch",
                "rule": store["store"],
                "severity": "medium",
                "message": "module inside the store paths belongs to layer %s, not the owner %s"
                           % (module_layers.get(rel), owner),
                "store": store["store"],
                "owner": owner,
                "evidence": [{"path": rel, "line": 1, "detail":
                              "matches the declared paths of store %s" % store["store"]}],
            })
        references = 0
        store_prefixes = prefixes.get(store["store"]) or []
        if store_prefixes:
            for rel in sorted(texts):
                if module_layers.get(rel) == owner:
                    continue
                for lineno, line in enumerate(texts[rel].splitlines(), 1):
                    if any(prefix in line for prefix in store_prefixes):
                        references += 1
                        violations += 1
                        violation_sink.append({
                            "code": "data-store-access-outside-owner",
                            "rule": store["store"],
                            "severity": "medium",
                            "message": "layer %s references the store owned by %s"
                                       % (module_layers.get(rel), owner),
                            "store": store["store"],
                            "owner": owner,
                            "evidence": [{
                                "path": rel,
                                "line": lineno,
                                "detail": "references the %s store path" % store["store"],
                                "snippet": line.strip()[:200],
                            }],
                        })
                        break
        owner_modules = sorted(item["id"] for item in model["modules"]
                               if item.get("layer") == owner)
        checked.append({
            "store": store["store"],
            "owner": owner,
            "kind": store.get("kind"),
            "paths": list(paths),
            "status": "WARN" if violations else "PASS",
            "owner_modules": len(owner_modules),
            "mismatched_modules": mismatched,
            "references_outside_owner": references,
        })
    return checked


def _review_invariants(contract, unverified_sink):
    checked = []
    for invariant in contract["invariants"]:
        check = invariant.get("check") or "manual"
        if check == "static":
            reason = "no built-in evaluator covers this claim yet"
        elif check == "command":
            reason = "architecture_review never executes commands"
        else:
            reason = "human review is required"
        unverified_sink.append({
            "claim": invariant["claim"],
            "level": "L1",
            "status": "UNVERIFIED",
            "reason": reason,
            "invariant": invariant["id"],
            "check": check,
            "severity": invariant.get("severity") or dev_contract.RULE_DEFAULT_SEVERITY,
            "paths": list(invariant.get("paths") or []),
        })
        checked.append({
            "id": invariant["id"],
            "claim": invariant["claim"],
            "check": check,
            "severity": invariant.get("severity") or dev_contract.RULE_DEFAULT_SEVERITY,
            "paths": list(invariant.get("paths") or []),
            "status": "UNVERIFIED",
            "reason": reason,
        })
    return checked


def _architecture_review_core(model_result, contract_result, root):
    """Shared review core: evaluate the contract against one system model."""
    contract = contract_result.get("contract") if contract_result.get("ok") else None
    violations = []
    unknowns = [dict(item) for item in model_result["unknowns"]]
    unverified = []
    evidence = []
    checked = {"rules": [], "boundaries": [], "data_stores": [], "invariants": []}
    if contract:
        checked["rules"] = _review_rules(contract, model_result["model"], unknowns, violations)
        checked["boundaries"] = _review_boundaries(
            contract, model_result["model"], unknowns, violations)
        checked["data_stores"] = _review_data_ownership(
            contract, model_result["model"], root, violations)
        checked["invariants"] = _review_invariants(contract, unverified)
    if model_result["truncated"]:
        unknowns.append({
            "kind": "model-truncated",
            "id": model_result["root"],
            "detail": "the file limit cut the scan short; some modules were not reviewed",
            "next_step": "raise max_files and rerun the review",
        })
    blocking = [item for item in violations
                if item["severity"] in dev_contract.BLOCKING_SEVERITIES]
    advisory = [item for item in violations
                if item["severity"] not in dev_contract.BLOCKING_SEVERITIES]
    contract_blocking = [item for item in model_result["contract"]["findings"]
                         if item["severity"] in dev_contract.BLOCKING_SEVERITIES]
    for item in violations:
        for entry in item["evidence"]:
            evidence.append({
                "path": entry["path"],
                "line": entry.get("line"),
                "detail": "%s: %s" % (item["code"], entry["detail"]),
            })
    for item in model_result["contract"]["findings"]:
        evidence.append({
            "path": model_result["contract"]["path"],
            "pointer": item["pointer"],
            "detail": "%s: %s" % (item["severity"], item["message"]),
        })
    evidence.sort(key=lambda item: (item["path"], item.get("line") or 0, item["detail"]))
    if len(evidence) > EVIDENCE_LIMIT:
        evidence = evidence[:EVIDENCE_LIMIT]
    if contract_blocking or blocking:
        status = "FAIL"
    elif not model_result["contract"]["present"] or unknowns:
        status = "UNKNOWN"
    else:
        status = "PASS"
    violations.sort(key=lambda item: (item["code"], item["rule"], item["evidence"][0]["path"]))
    unknowns.sort(key=lambda item: (item["kind"], item["id"]))
    return {
        "status": status,
        "checked": checked,
        "violations": violations,
        "blocking_findings": len(blocking) + len(contract_blocking),
        "advisory_findings": len(advisory),
        "unknowns": unknowns,
        "unverified_claims": unverified,
        "evidence": evidence,
    }


def architecture_review(path, max_files=2000, contract_file=None):
    """Review a repository against `.yotta/architecture.json`. Read-only."""
    root = Path(path)
    if not root.exists():
        raise ValueError("路径不存在: %s" % path)
    if not root.is_dir():
        raise ValueError("architecture_review 需要目录: %s" % path)
    model_result = system_model(str(root), max_files=max_files, contract_file=contract_file)
    contract_result = dev_contract.load_contract(root, contract_file=contract_file)
    core = _architecture_review_core(model_result, contract_result, root)
    return {
        "status": core["status"],
        "root": model_result["root"],
        "contract": model_result["contract"],
        "checked": core["checked"],
        "violations": core["violations"],
        "blocking_findings": core["blocking_findings"],
        "advisory_findings": core["advisory_findings"],
        "unknowns": core["unknowns"],
        "unverified_claims": core["unverified_claims"],
        "evidence": core["evidence"],
        "truncated": model_result["truncated"],
        "model_digest": model_result["model_digest"],
    }


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
SYMBOL_PATTERNS = (
    ("python", r"^\s*(?:async\s+)?def\s+%s\s*\("),
    ("python", r"^\s*class\s+%s\s*[\(:]"),
    ("js", r"^\s*(?:export\s+)?(?:async\s+)?function\s+%s\s*\("),
    ("js", r"^\s*(?:export\s+)?(?:const|let|var)\s+%s\s*="),
    ("js", r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+%s\b"),
    ("go", r"^\s*func\s+(?:\([^)]*\)\s*)?%s\s*\("),
)
SYMBOL_LANGUAGES = {
    "python": ("python",),
    "js": ("javascript", "typescript"),
    "go": ("go",),
}


def _strip_diff_path(raw):
    text = str(raw).strip().split("\t")[0].strip()
    if not text or text == "/dev/null":
        return None
    if text.startswith("a/") or text.startswith("b/"):
        text = text[2:]
    return text.replace("\\", "/")


def _parse_unified_diff(diff_text):
    """Parse a unified diff into changed files plus added/removed line numbers."""
    changes = []
    current = None
    old_path = None
    old_line = 0
    new_line = 0
    for line in str(diff_text).splitlines():
        if line.startswith("--- "):
            old_path = _strip_diff_path(line[4:])
            current = None
            continue
        if line.startswith("+++ "):
            new_path = _strip_diff_path(line[4:])
            if new_path is None:
                if old_path is None:
                    continue
                path, change = old_path, "deleted"
            elif old_path is None:
                path, change = new_path, "added"
            else:
                path, change = new_path, "modified"
            current = {"path": path, "change": change,
                       "changed_lines": [], "removed_lines": []}
            changes.append(current)
            continue
        match = HUNK_RE.match(line)
        if match:
            old_line = int(match.group(1))
            new_line = int(match.group(2))
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current["changed_lines"].append(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            current["removed_lines"].append(old_line)
            old_line += 1
        elif line.startswith(" "):
            old_line += 1
            new_line += 1
    for item in changes:
        item["changed_lines"] = sorted(set(item["changed_lines"]))
        item["removed_lines"] = sorted(set(item["removed_lines"]))
    return changes


def _symbol_locations(model, root, symbol):
    escaped = re.escape(symbol)
    hits = []
    for module in model["modules"]:
        language = module["language"]
        patterns = [
            re.compile(pattern % escaped)
            for kind, pattern in SYMBOL_PATTERNS
            if language in SYMBOL_LANGUAGES[kind]
        ]
        if not patterns:
            continue
        try:
            text = _read_text(root / module["id"])
        except (OSError, ValueError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(pattern.match(line) for pattern in patterns):
                hits.append({"path": module["id"], "line": lineno, "language": language})
                if len(hits) >= SYMBOL_MATCH_LIMIT:
                    return hits
    return hits


def _dependency_cone(model, changed_paths, depth):
    """Reverse-dependency breadth-first cone from the changed modules."""
    consumers = {}
    for edge in _decisive_edges(model):
        consumers.setdefault(edge["target"], []).append(edge)
    module_layers = _module_layers(model)
    test_paths = {item["path"] for item in model["tests"]}
    nodes = {}
    order = []
    for path in sorted(set(changed_paths)):
        nodes[path] = {
            "path": path, "depth": 0, "via": None, "line": None,
            "layer": module_layers.get(path), "is_test": path in test_paths,
        }
        order.append(path)
    truncated = False
    index = 0
    while index < len(order):
        current = order[index]
        index += 1
        node = nodes[current]
        if node["depth"] >= depth:
            continue
        for edge in sorted(consumers.get(current, []),
                           key=lambda item: (item["source"], item["line"])):
            consumer = edge["source"]
            if consumer in nodes:
                continue
            if len(nodes) >= CONE_LIMIT:
                truncated = True
                break
            nodes[consumer] = {
                "path": consumer,
                "depth": node["depth"] + 1,
                "via": current,
                "line": edge["line"],
                "layer": module_layers.get(consumer),
                "is_test": consumer in test_paths,
            }
            order.append(consumer)
        if truncated:
            break
    return nodes, truncated


def _relevant_tests(model, nodes):
    relevant = []
    for item in model["tests"]:
        hits = sorted({target for target in item["targets"] if target in nodes})
        in_cone = item["path"] in nodes
        if not hits and not in_cone:
            continue
        if in_cone:
            depth = nodes[item["path"]]["depth"]
        elif hits:
            depth = min(nodes[target]["depth"] for target in hits) + 1
        else:
            depth = 0
        relevant.append({"path": item["path"], "targets_hit": hits, "depth": depth})
    return sorted(relevant, key=lambda item: item["path"])


def _affected_boundaries(contract, paths):
    affected = []
    for boundary in (contract or {}).get("boundaries") or []:
        matched = sorted(path for path in paths
                         if dev_contract.match_any(path, boundary["paths"] or []))
        if matched:
            affected.append({
                "id": boundary["id"],
                "layer": boundary["layer"],
                "visibility": boundary.get("visibility") or "internal",
                "matched_paths": matched,
            })
    return sorted(affected, key=lambda item: item["id"])


def _affected_data_stores(model, affected_layers, paths):
    affected = []
    for store in model["data_stores"]:
        if store.get("detected"):
            matched = sorted(path for path in paths if path == store["store"])
            if matched:
                affected.append({
                    "store": store["store"],
                    "owner": store.get("owner"),
                    "kind": store.get("kind"),
                    "reason": "changed path is the detected store file",
                    "matched_paths": matched,
                })
            continue
        reasons = []
        matched = sorted(path for path in paths
                         if dev_contract.match_any(path, store["paths"] or []))
        if store.get("owner") in affected_layers:
            reasons.append("owner layer %s is in the impact cone" % store["owner"])
        if matched:
            reasons.append("changed path matches the declared store globs")
        if reasons:
            affected.append({
                "store": store["store"],
                "owner": store.get("owner"),
                "kind": store.get("kind"),
                "reason": "; ".join(reasons),
                "matched_paths": matched,
            })
    return sorted(affected, key=lambda item: item["store"])


def _affected_invariants(contract, paths):
    affected = []
    for invariant in (contract or {}).get("invariants") or []:
        globs = invariant.get("paths") or []
        matched = sorted(path for path in paths
                         if globs and dev_contract.match_any(path, globs))
        if globs and not matched:
            continue
        affected.append({
            "id": invariant["id"],
            "claim": invariant["claim"],
            "severity": invariant.get("severity") or dev_contract.RULE_DEFAULT_SEVERITY,
            "check": invariant.get("check") or "manual",
            "scope": "paths" if globs else "repo",
            "matched_paths": matched,
        })
    return sorted(affected, key=lambda item: item["id"])


def _blast_radius(contract, nodes, affected_layers, boundaries, stores, in_scope):
    reasons = []
    score = 0
    if affected_layers:
        base = max(_risk_weight(contract, layer) for layer in affected_layers)
        weight = int(min(5, base))
        if weight:
            score += weight
            reasons.append({
                "factor": "layer-risk",
                "weight": weight,
                "detail": "highest declared risk among affected layers is %g" % base,
            })
    if len(affected_layers) >= 5:
        score += 2
        reasons.append({"factor": "layer-count", "weight": 2,
                        "detail": "%d layers are affected" % len(affected_layers)})
    elif len(affected_layers) >= 3:
        score += 1
        reasons.append({"factor": "layer-count", "weight": 1,
                        "detail": "%d layers are affected" % len(affected_layers)})
    depth = max((node["depth"] for node in nodes.values()), default=0)
    if depth >= 2:
        score += 1
        reasons.append({"factor": "cone-depth", "weight": 1,
                        "detail": "consumer chain reaches depth %d" % depth})
    public_surface = [item["id"] for item in boundaries if item["visibility"] == "public"]
    if public_surface:
        score += 1
        reasons.append({"factor": "public-boundary", "weight": 1,
                        "detail": "public boundary in scope: %s" % ", ".join(public_surface)})
    if stores:
        weight = min(2, len(stores))
        score += weight
        reasons.append({"factor": "data-store", "weight": weight,
                        "detail": "%d data store(s) touched" % len(stores)})
    blocking = [item for item in in_scope
                if item["severity"] in dev_contract.BLOCKING_SEVERITIES]
    if blocking:
        critical = any(item["severity"] == "critical" for item in blocking)
        weight = 3 if critical else 2
        score += weight
        reasons.append({"factor": "architecture-violation", "weight": weight,
                        "detail": "%d blocking architecture violation(s) in scope"
                                  % len(blocking)})
    capped = min(10, score)
    level = "low"
    for threshold, name in BLAST_LEVELS:
        if capped >= threshold:
            level = name
            break
    return {
        "level": level,
        "score": score,
        "capped_score": capped,
        "capped": score > capped,
        "reasons": reasons,
    }


def _rollback_probes(model, nodes, stores, tests, invariants):
    probes = []
    # Test files that guard on __main__ are runnable, but they are covered by the
    # test probe below; treating them as startup surfaces only adds noise.
    entrypoints = sorted(path for path in set(model["entrypoints"]) & set(nodes)
                         if not _is_test_file(path))[:5]
    for store in stores:
        probes.append({
            "kind": "data-store",
            "target": store["store"],
            "probe": "revert the change and verify %s integrity, or restore it from backup"
                     % store["store"],
            "evidence": store["reason"],
        })
    for entry in entrypoints:
        probes.append({
            "kind": "entrypoint",
            "target": entry,
            "probe": "revert and run %s once to confirm startup still works" % entry,
            "evidence": "entrypoint in the impact cone",
        })
    if tests:
        probes.append({
            "kind": "tests",
            "target": ", ".join(item["path"] for item in tests[:5]),
            "probe": "run the mapped tests before and after revert",
            "evidence": "%d mapped test file(s)" % len(tests),
        })
    for invariant in invariants:
        if invariant["check"] != "command":
            continue
        probes.append({
            "kind": "invariant",
            "target": invariant["id"],
            "probe": "run the declared check for %s after revert" % invariant["id"],
            "evidence": invariant["claim"],
        })
    if not probes:
        probes.append({
            "kind": "model",
            "target": "L0/L1",
            "probe": "no store, entrypoint or test mapping was in scope; rerun the review after revert",
            "evidence": "impact cone produced no probe anchor",
        })
    return probes


def _impact_unverified_claims():
    return [
        {
            "claim": "the change passes its tests",
            "level": "L2-L3",
            "status": "UNVERIFIED",
            "reason": "impact_analysis executes no tests",
        },
        {
            "claim": "behaviour survives mutation and property checks",
            "level": "L4",
            "status": "UNVERIFIED",
            "reason": "impact_analysis runs no mutation or property checks",
        },
        {
            "claim": "an independent reviewer agrees with the change",
            "level": "L5",
            "status": "UNVERIFIED",
            "reason": "independent review stays a human or separate-agent step",
        },
    ]


def impact_analysis(path, changed_files=None, diff=None, symbols=None, depth=3,
                    max_files=2000, contract_file=None):
    """Build a deterministic change impact cone for local changes. Read-only."""
    root = Path(path)
    if not root.exists():
        raise ValueError("路径不存在: %s" % path)
    if not root.is_dir():
        raise ValueError("impact_analysis 需要目录: %s" % path)
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 10:
        raise ValueError("depth 必须是 1 到 10 之间的整数")
    requested = [str(item).replace("\\", "/").lstrip("./") for item in (changed_files or [])
                 if str(item).strip()]
    wanted_symbols = [str(item).strip() for item in (symbols or []) if str(item).strip()]
    if not requested and not wanted_symbols and not str(diff or "").strip():
        raise ValueError("impact_analysis 需要 changed_files、diff 或 symbols 至少一项")

    model_result = system_model(str(root), max_files=max_files, contract_file=contract_file)
    model = model_result["model"]
    contract_result = dev_contract.load_contract(root, contract_file=contract_file)
    contract = contract_result["contract"] if contract_result["ok"] else None
    module_layers = _module_layers(model)
    unknowns = [dict(item) for item in model_result["unknowns"]]
    changes = {}
    evidence = []

    def register_changed(rel, change, changed_lines=None, symbols_hit=None, reason=None):
        rel = str(rel).replace("\\", "/").lstrip("./")
        layer = module_layers.get(rel)
        if layer is None and contract:
            matched = dev_contract.match_layers(rel, contract)
            layer = matched[0] if matched else None
        if layer is None and not (root / rel).exists():
            unknowns.append({
                "kind": "change-not-found",
                "id": rel,
                "detail": "the changed path is not in the repository and matches no layer",
                "next_step": "check the path spelling or add a layer glob",
            })
        entry = changes.setdefault(rel, {
            "path": rel,
            "change": change,
            "layer": layer,
            "changed_lines": [],
            "symbols": [],
        })
        if changed_lines:
            entry["changed_lines"] = sorted(set(entry["changed_lines"]) | set(changed_lines))
        if symbols_hit:
            entry["symbols"] = sorted(set(entry["symbols"]) | set(symbols_hit))
        if reason and not entry.get("reason"):
            entry["reason"] = reason
        return entry

    for item in _parse_unified_diff(diff or ""):
        entry = register_changed(item["path"], item["change"],
                                 changed_lines=item["changed_lines"],
                                 reason="unified diff")
        if item["removed_lines"]:
            entry["removed_lines"] = item["removed_lines"]
    for rel in requested:
        in_model = rel in module_layers
        layer_hit = bool(contract and dev_contract.match_layers(rel, contract))
        register_changed(rel, "modified", reason="changed_files")
        if (root / rel).exists() and not in_model and not layer_hit:
            unknowns.append({
                "kind": "change-not-in-model",
                "id": rel,
                "detail": "the file is not part of the source model and matches no layer",
                "next_step": "add a layer glob or check the file type",
            })
    for symbol in wanted_symbols:
        hits = _symbol_locations(model, root, symbol)
        if not hits:
            unknowns.append({
                "kind": "symbol-not-found",
                "id": symbol,
                "detail": "no definition of this symbol was found in the model",
                "next_step": "check the symbol spelling or add the file that defines it",
            })
            continue
        for hit in hits:
            register_changed(hit["path"], "symbol", changed_lines=[hit["line"]],
                             symbols_hit=[symbol], reason="target symbol")

    changed_paths = sorted(changes)
    nodes, cone_truncated = _dependency_cone(model, changed_paths, depth)
    if cone_truncated:
        unknowns.append({
            "kind": "cone-truncated",
            "id": model_result["root"],
            "detail": "the impact cone reached the %d node limit" % CONE_LIMIT,
            "next_step": "raise depth/targets precision and rerun impact_analysis",
        })
    scoped_paths = sorted(nodes)
    direct_consumers = sorted(node["path"] for node in nodes.values() if node["depth"] == 1)
    affected_layers = sorted({node["layer"] for node in nodes.values() if node["layer"]})
    boundaries = _affected_boundaries(contract, scoped_paths)
    stores = _affected_data_stores(model, affected_layers, scoped_paths)
    invariants = _affected_invariants(contract, scoped_paths)
    tests = _relevant_tests(model, nodes)

    core = _architecture_review_core(model_result, contract_result, root)
    scoped = set(scoped_paths)
    for violation in core["violations"]:
        violation["in_cone"] = any(item["path"] in scoped for item in violation["evidence"])
    in_scope = [item for item in core["violations"] if item["in_cone"]]
    blocking_in_scope = [item for item in in_scope
                         if item["severity"] in dev_contract.BLOCKING_SEVERITIES]
    radius = _blast_radius(contract, nodes, affected_layers, boundaries, stores, in_scope)
    probes = _rollback_probes(model, nodes, stores, tests, invariants)

    for path_ in changed_paths:
        entry = changes[path_]
        evidence.append({
            "path": path_,
            "line": (entry["changed_lines"] or [None])[0],
            "detail": "changed (%s)%s" % (
                entry["change"],
                ", layer %s" % entry["layer"] if entry["layer"] else ", no layer",
            ),
        })
    for node in sorted(nodes.values(), key=lambda item: (item["depth"], item["path"])):
        if node["depth"] == 0:
            continue
        evidence.append({
            "path": node["path"],
            "line": node["line"],
            "detail": "consumer of %s at depth %d" % (node["via"], node["depth"]),
        })
    for item in blocking_in_scope:
        for entry in item["evidence"]:
            evidence.append({
                "path": entry["path"],
                "line": entry.get("line"),
                "detail": "%s: %s" % (item["code"], entry["detail"]),
            })
    evidence.sort(key=lambda item: (item["path"], item.get("line") or 0, item["detail"]))
    if len(evidence) > EVIDENCE_LIMIT:
        evidence = evidence[:EVIDENCE_LIMIT]

    unknowns.sort(key=lambda item: (item["kind"], item["id"]))
    deduped = []
    seen_unknowns = set()
    for item in unknowns:
        key = (item["kind"], item.get("id"))
        if key in seen_unknowns:
            continue
        seen_unknowns.add(key)
        deduped.append(item)
    unknowns = deduped
    if blocking_in_scope:
        status = "FAIL"
    elif unknowns or cone_truncated:
        status = "UNKNOWN"
    else:
        status = "PASS"
    return {
        "status": status,
        "root": model_result["root"],
        "inputs": {
            "changed_files": requested,
            "symbols": wanted_symbols,
            "diff_provided": bool(str(diff or "").strip()),
            "depth": depth,
        },
        "changed": [changes[path_] for path_ in changed_paths],
        "direct_consumers": direct_consumers,
        "cone": {
            "nodes": [nodes[path_] for path_ in sorted(
                nodes, key=lambda item: (nodes[item]["depth"], item))],
            "max_depth": max((node["depth"] for node in nodes.values()), default=0),
            "limit": CONE_LIMIT,
            "truncated": cone_truncated,
        },
        "affected_layers": affected_layers,
        "affected_boundaries": boundaries,
        "affected_data_stores": stores,
        "affected_invariants": invariants,
        "relevant_tests": tests,
        "architecture": {
            "status": core["status"],
            "violations_total": len(core["violations"]),
            "violations_in_scope": in_scope,
            "unknowns": core["unknowns"],
        },
        "blast_radius": radius,
        "rollback_probes": probes,
        "unknowns": unknowns,
        "unverified_claims": _impact_unverified_claims(),
        "evidence": evidence,
        "truncated": model_result["truncated"],
        "model_digest": model_result["model_digest"],
    }


def _is_within(root, target):
    try:
        Path(target).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _ledger_entry(check_id, level, claim, status, severity="info", evidence=None,
                  next_step=None, command=None, confidence="high"):
    return {
        "id": check_id,
        "level": level,
        "claim": claim,
        "status": status,
        "severity": severity,
        "check": "verify_change",
        "confidence": confidence,
        "evidence": sorted(
            list(evidence or []),
            key=lambda item: (item.get("path") or "", item.get("line") or 0,
                              item.get("detail") or ""),
        ),
        "next_step": next_step,
        "command": command,
    }


def _verify_contract_result(contract_result):
    if not contract_result["present"]:
        return "UNKNOWN", [{
            "path": contract_result["path"],
            "line": None,
            "detail": "architecture contract is missing",
        }], "add .yotta/architecture.json"
    if not contract_result["ok"]:
        evidence = []
        for item in contract_result["findings"]:
            if item["severity"] in dev_contract.BLOCKING_SEVERITIES:
                evidence.append({
                    "path": item["path"],
                    "line": None,
                    "detail": "%s: %s" % (item["code"], item["message"]),
                })
        return "FAIL", evidence, "fix the contract findings and rerun verify_change"
    return "PASS", [], None


def _verify_syntax(changed, root):
    evidence = []
    unknown = []
    for item in changed:
        rel = item["path"]
        if item.get("change") == "deleted":
            continue
        target = root / rel
        if not target.is_file():
            continue
        suffix = target.suffix.lower()
        if suffix not in (".py", ".json") and suffix not in JS_EXTS:
            continue
        if suffix in JS_EXTS:
            unknown.append({
                "path": rel,
                "line": None,
                "detail": "no zero-dependency JavaScript parser is available at L0",
            })
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            unknown.append({
                "path": rel,
                "line": None,
                "detail": "file could not be read: %s" % exc,
            })
            continue
        try:
            if suffix == ".py":
                ast.parse(text, filename=rel)
            else:
                json.loads(text)
        except SyntaxError as exc:
            evidence.append({
                "path": rel,
                "line": exc.lineno,
                "detail": "syntax error: %s" % exc.msg,
            })
        except json.JSONDecodeError as exc:
            evidence.append({
                "path": rel,
                "line": exc.lineno,
                "detail": "JSON error: %s" % exc.msg,
            })
    if evidence:
        return "FAIL", evidence, "fix the syntax error(s) and rerun verify_change", []
    if unknown:
        return "UNKNOWN", unknown, "use an L2-L4 adapter or a language-specific parser", unknown
    return "PASS", [], None, []


def _verify_architecture(impact):
    architecture = impact["architecture"]
    blocking = [
        item for item in architecture["violations_in_scope"]
        if item["severity"] in dev_contract.BLOCKING_SEVERITIES
    ]
    if blocking:
        evidence = []
        for item in blocking:
            for entry in item["evidence"]:
                evidence.append({
                    "path": entry["path"],
                    "line": entry.get("line"),
                    "detail": "%s (%s): %s" % (
                        item["rule"], item["code"], entry.get("detail") or "",
                    ),
                })
        return "FAIL", evidence, "fix the in-scope architecture violation(s)"
    scoped = {item["path"] for item in impact["cone"]["nodes"]}
    always_relevant = {
        "contract-missing", "contract-invalid", "model-truncated", "cone-truncated",
        "change-not-found", "symbol-not-found",
    }
    unknowns = []
    for item in list(architecture.get("unknowns") or []) + list(impact.get("unknowns") or []):
        kind = item.get("kind")
        identifier = str(item.get("id") or "")
        if kind in always_relevant or not identifier:
            unknowns.append(item)
        elif identifier in scoped:
            unknowns.append(item)
        elif any(path and path in identifier for path in scoped):
            unknowns.append(item)
    if unknowns:
        evidence = [{
            "path": item.get("id") or item.get("path") or "",
            "line": None,
            "detail": "%s: %s" % (item.get("kind"), item.get("detail") or ""),
        } for item in unknowns]
        return "UNKNOWN", evidence, "resolve the unknown evidence and rerun verify_change"
    return "PASS", [], None


def _verify_policy_entries(root, policy, execution_levels, allow_execute, timeout):
    ledger = []
    unverified = []
    for level in execution_levels:
        checks = [item for item in policy["checks"] if item["level"] == level]
        if not checks:
            ledger.append(_ledger_entry(
                "%s-policy" % level, level,
                "%s verification is declared" % level,
                "UNKNOWN", severity="high",
                evidence=[{"path": policy["path"], "line": None,
                           "detail": "no %s checks are declared" % level}],
                next_step="declare a whitelisted %s check in .yotta/verification.json" % level,
            ))
            unverified.append({
                "level": level,
                "claim": "%s verification is declared and executed" % level,
                "status": "UNVERIFIED",
                "reason": "verification-level-not-declared",
                "required": True,
                "next_step": "declare a whitelisted check in .yotta/verification.json",
            })
            continue
        for check in checks:
            check_id = "%s-%s" % (level, check["id"])
            claim = check["claim"] or "%s check %s passes" % (level, check["id"])
            if not allow_execute:
                ledger.append(_ledger_entry(
                    check_id, level, claim, "UNVERIFIED",
                    severity="high" if check["required"] else "medium",
                    next_step="rerun with allow_execute=true",
                ))
                unverified.append({
                    "level": level,
                    "claim": claim,
                    "status": "UNVERIFIED",
                    "reason": "allow_execute=false",
                    "required": check["required"],
                    "next_step": "rerun with allow_execute=true",
                })
                continue
            cwd = (root / check["cwd"]).resolve()
            if not _is_within(root, cwd) or not cwd.is_dir():
                ledger.append(_ledger_entry(
                    check_id, level, claim, "FAIL", severity="high",
                    evidence=[{"path": check["cwd"], "line": None,
                               "detail": "check cwd is not a directory inside the repository"}],
                    next_step="fix the policy cwd and rerun verify_change",
                ))
                continue
            try:
                result = run_checks(
                    check["kind"], str(cwd),
                    timeout=min(timeout, check["timeout"]),
                    allow_execute=True,
                )
            except Exception as exc:  # noqa: BLE001
                ledger.append(_ledger_entry(
                    check_id, level, claim, "FAIL", severity="high",
                    evidence=[{"path": check["cwd"], "line": None,
                               "detail": "check could not run: %s" % exc}],
                    next_step="fix the verification policy or local toolchain",
                ))
                continue
            output = result.get("output") or ""
            command = {
                "kind": result["kind"],
                "cwd": check["cwd"],
                "exit_code": result["exit_code"],
                "output_hash": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                "summary": result.get("summary") or "",
                "timed_out": bool(result.get("timed_out")),
            }
            status = "PASS" if result.get("passed") else "FAIL"
            ledger.append(_ledger_entry(
                check_id, level, claim, status,
                severity="high" if check["required"] else "medium",
                evidence=[{"path": check["cwd"], "line": None,
                           "detail": result.get("summary") or "check finished"}],
                next_step=None if status == "PASS"
                          else "inspect the check output and fix the failure",
                command=command,
            ))
    return sorted(ledger, key=lambda item: (item["level"], item["id"])), unverified


def verify_change(path, changed_files=None, diff=None, symbols=None, depth=3,
                  levels=None, allow_execute=False, timeout=120,
                  max_files=2000, contract_file=None, policy_file=None):
    """Run the L0-L5 verification ladder and return a deterministic evidence ledger.

    L0/L1 always run in-process. L2-L4 run only when both a whitelisted
    .yotta/verification.json check is declared and allow_execute is true.
    L5 is always recorded as manual work and is never auto-verified.
    """
    root = Path(path)
    if not root.exists():
        raise ValueError("路径不存在: %s" % path)
    if not root.is_dir():
        raise ValueError("verify_change 需要目录: %s" % path)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 600:
        raise ValueError("timeout 必须是 1 到 600 之间的整数")
    requested = list(levels or [])
    for level in requested:
        if level not in VERIFY_LEVELS:
            raise ValueError("level 必须是 L0-L5 之一: %s" % level)
    execution_levels = [level for level in VERIFY_EXEC_LEVELS if level in requested]

    impact = impact_analysis(
        str(root), changed_files=changed_files, diff=diff, symbols=symbols,
        depth=depth, max_files=max_files, contract_file=contract_file,
    )
    contract_result = dev_contract.load_contract(root, contract_file=contract_file)
    policy = dev_contract.load_verification_policy(root, policy_file=policy_file)

    ledger = []
    unverified = []

    contract_status, contract_evidence, contract_next = _verify_contract_result(contract_result)
    ledger.append(_ledger_entry(
        "L0-contract", "L0", "the architecture contract is valid",
        contract_status, severity="high",
        evidence=contract_evidence, next_step=contract_next,
    ))

    syntax_status, syntax_evidence, syntax_next, syntax_unknown = _verify_syntax(
        impact["changed"], root
    )
    ledger.append(_ledger_entry(
        "L0-syntax", "L0", "changed source files parse",
        syntax_status, severity="high",
        evidence=syntax_evidence, next_step=syntax_next,
    ))
    if syntax_unknown:
        unverified.append({
            "level": "L0",
            "claim": "changed JavaScript or TypeScript files parse",
            "status": "UNVERIFIED",
            "reason": "no zero-dependency JavaScript parser",
            "required": False,
            "next_step": "add a language adapter or run an explicit parser check",
        })

    architecture_status, architecture_evidence, architecture_next = _verify_architecture(impact)
    advisory = [
        item for item in impact["architecture"]["violations_in_scope"]
        if item["severity"] not in dev_contract.BLOCKING_SEVERITIES
    ]
    if architecture_status == "PASS" and advisory:
        architecture_evidence = [{
            "path": entry["path"],
            "line": entry.get("line"),
            "detail": "%s (%s): %s" % (
                item["rule"], item["code"], entry.get("detail") or "",
            ),
        } for item in advisory for entry in item["evidence"]]
    ledger.append(_ledger_entry(
        "L1-architecture", "L1", "in-scope architecture rules and boundaries hold",
        architecture_status, severity="high",
        evidence=architecture_evidence, next_step=architecture_next,
    ))

    for invariant in impact["affected_invariants"]:
        unverified.append({
            "level": "L1",
            "claim": invariant["claim"],
            "status": "UNVERIFIED",
            "reason": "invariant requires a static, command or manual check",
            "required": False,
            "next_step": "add a verification policy check or complete the manual review",
        })

    if execution_levels:
        if not policy["present"]:
            for level in execution_levels:
                ledger.append(_ledger_entry(
                    "%s-policy" % level, level,
                    "%s verification policy is present" % level,
                    "UNKNOWN", severity="high",
                    evidence=[{"path": policy["path"], "line": None,
                               "detail": "verification policy is missing"}],
                    next_step="add .yotta/verification.json with whitelisted checks",
                ))
                unverified.append({
                    "level": level,
                    "claim": "%s verification is declared and executed" % level,
                    "status": "UNVERIFIED",
                    "reason": "verification-policy-missing",
                    "required": True,
                    "next_step": "add .yotta/verification.json",
                })
        elif not policy["ok"]:
            evidence = [{
                "path": item["path"],
                "line": None,
                "detail": "%s: %s" % (item["code"], item["message"]),
            } for item in policy["findings"]
                if item["severity"] in dev_contract.BLOCKING_SEVERITIES]
            for level in execution_levels:
                ledger.append(_ledger_entry(
                    "%s-policy" % level, level,
                    "%s verification policy is valid" % level,
                    "FAIL", severity="high", evidence=evidence,
                    next_step="fix .yotta/verification.json and rerun verify_change",
                ))
        else:
            execution_ledger, execution_unverified = _verify_policy_entries(
                root, policy, execution_levels, allow_execute, timeout,
            )
            ledger.extend(execution_ledger)
            unverified.extend(execution_unverified)

    verified_levels = {
        item["level"] for item in ledger
        if item["status"] in ("PASS", "FAIL")
    }
    for level in VERIFY_EXEC_LEVELS:
        if level not in verified_levels and not any(
            item["level"] == level for item in unverified
        ):
            unverified.append({
                "level": level,
                "claim": "%s verification level is covered by evidence" % level,
                "status": "UNVERIFIED",
                "reason": "level-not-requested",
                "required": False,
                "next_step": "request this level and provide a verification policy",
            })
    if "L5" not in requested:
        unverified.append({
            "level": "L5",
            "claim": "independent review or manual architecture approval",
            "status": "UNVERIFIED",
            "reason": "manual verification required",
            "required": False,
            "next_step": "complete an independent review or manual architecture decision",
        })
    else:
        unverified.append({
            "level": "L5",
            "claim": "independent review or manual architecture approval",
            "status": "UNVERIFIED",
            "reason": "manual verification required",
            "required": True,
            "next_step": "complete an independent review or manual architecture decision",
        })

    unverified.sort(key=lambda item: (item["level"], item["claim"], item["reason"]))
    ledger.sort(key=lambda item: (item["level"], item["id"]))
    failed = any(item["status"] == "FAIL" for item in ledger)
    unknown = any(item["status"] == "UNKNOWN" for item in ledger) or any(
        item["required"] for item in unverified
    )
    if failed:
        status = "FAIL"
    elif unknown:
        status = "UNKNOWN"
    else:
        status = "PASS"
    required_levels = ["L0", "L1"]
    for item in ledger:
        if item["level"] in VERIFY_EXEC_LEVELS and item["status"] in ("PASS", "FAIL"):
            required_levels.append(item["level"])
    required_levels = sorted(set(required_levels), key=lambda item: int(item[1:]))
    evidence = []
    for item in ledger:
        evidence.extend(item["evidence"])
    if len(evidence) > EVIDENCE_LIMIT:
        evidence = evidence[:EVIDENCE_LIMIT]
    ledger_digest = hashlib.sha256(
        json.dumps(ledger, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "status": status,
        "root": str(root.resolve()),
        "inputs": {
            "changed_files": list(changed_files or []),
            "symbols": list(symbols or []),
            "diff_provided": bool(str(diff or "").strip()),
            "levels": requested,
            "allow_execute": bool(allow_execute),
            "depth": depth,
        },
        "required_levels": required_levels,
        "ledger": ledger,
        "unverified_claims": unverified,
        "policy": policy,
        "evidence": evidence,
        "ledger_digest": ledger_digest,
        "model_digest": impact["model_digest"],
        "impact_status": impact["status"],
    }


def _function_default(source_text, function_name, parameter_name):
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return False, None
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        positional = list(node.args.args)
        defaults = list(node.args.defaults)
        offset = len(positional) - len(defaults)
        for index, argument in enumerate(positional):
            if argument.arg != parameter_name or index < offset:
                continue
            try:
                return True, ast.literal_eval(defaults[index - offset])
            except (ValueError, SyntaxError):
                return True, "<non-literal>"
    return False, None


def _string_constant(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _protocol_tool_contracts(source_text):
    tree = ast.parse(source_text)
    function = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "mcp_tools":
            function = node
            break
    if function is None:
        raise ValueError("mcp_tools() not found")
    list_node = None
    for node in ast.walk(function):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.List):
            list_node = node.value
            break
    if list_node is None:
        raise ValueError("mcp_tools() does not return a literal tool list")
    contracts = []
    for element in list_node.elts:
        if not isinstance(element, ast.Dict):
            raise ValueError("mcp_tools() contains a non-literal tool entry")
        values = {}
        for key, value in zip(element.keys, element.values):
            if _string_constant(key):
                values[key.value] = value
        name_node = values.get("name")
        if name_node is None:
            raise ValueError("tool entry is missing name")
        try:
            name = ast.literal_eval(name_node)
        except (ValueError, SyntaxError):
            raise ValueError("tool name is not a literal string")
        schema = values.get("inputSchema")
        additional = False
        if isinstance(schema, ast.Dict):
            for key, value in zip(schema.keys, schema.values):
                if _string_constant(key) and key.value == "additionalProperties":
                    try:
                        additional = ast.literal_eval(value) is False
                    except (ValueError, SyntaxError):
                        additional = False
        contracts.append({"name": name, "additional_properties": additional})
    return contracts


def _dispatch_tool_names(source_text):
    tree = ast.parse(source_text)
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "dispatch":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "handlers"
                       for target in child.targets):
                continue
            if not isinstance(child.value, ast.Dict):
                continue
            names = []
            for key in child.value.keys:
                if _string_constant(key):
                    names.append(key.value)
                else:
                    try:
                        names.append(ast.literal_eval(key))
                    except (ValueError, SyntaxError):
                        pass
            return sorted(names)
    raise ValueError("dispatch() handler map not found")


def _self_test_counterexamples():
    probes = []

    def record(kind, name, expectation, observed, passed, detail):
        probes.append({
            "kind": kind,
            "name": name,
            "expectation": expectation,
            "observed": observed,
            "passed": bool(passed),
            "detail": detail,
        })

    with tempfile.TemporaryDirectory(prefix="yotta-dev-mcp-selftest-") as tmp:
        root = Path(tmp)

        def write(rel, text):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

        contract = {
            "version": 1,
            "layers": [
                {"id": "core", "paths": ["core/**"]},
                {"id": "ui", "paths": ["ui/**"]},
            ],
            "rules": [{
                "id": "core-no-ui",
                "type": "forbid-dependency",
                "from": "core",
                "to": "ui",
                "severity": "high",
                "claim": "core must not import ui",
            }],
        }
        write(".yotta/architecture.json", json.dumps(contract))
        write("core/leak.py", "from ui.view import VALUE\n")
        write("ui/view.py", "VALUE = 1\n")

        try:
            observed = architecture_review(str(root))["status"]
            detail = "seeded core -> ui dependency was reviewed"
        except Exception as exc:  # noqa: BLE001
            observed = "error"
            detail = str(exc)
        record("seeded-defect", "forbidden dependency", "FAIL", observed,
               observed == "FAIL", detail)

        contract["rules"] = []
        write(".yotta/architecture.json", json.dumps(contract))
        try:
            observed = architecture_review(str(root))["status"]
            detail = "same defect with the rule removed"
        except Exception as exc:  # noqa: BLE001
            observed = "error"
            detail = str(exc)
        record("mutation-control", "remove the rule", "not FAIL", observed,
               observed != "FAIL", detail)

        contract["version"] = 3
        write(".yotta/architecture.json", json.dumps(contract))
        try:
            observed = architecture_review(str(root))["status"]
            detail = "unsupported contract version"
        except Exception as exc:  # noqa: BLE001
            observed = "error"
            detail = str(exc)
        record("invalid-contract", "unsupported version", "FAIL or UNKNOWN",
               observed, observed in ("FAIL", "UNKNOWN"), detail)

        (root / ".yotta" / "architecture.json").unlink()
        try:
            observed = architecture_review(str(root))["status"]
            detail = "missing contract"
        except Exception as exc:  # noqa: BLE001
            observed = "error"
            detail = str(exc)
        record("missing-contract", "no architecture contract", "UNKNOWN",
               observed, observed == "UNKNOWN", detail)

        write("secret.env", "TOKEN=abcdefghijklmnopqrstuvwxyz123456\n")
        try:
            observed = len(scan_secrets(str(root))["findings"])
            passed = observed >= 1
            detail = "seeded token was scanned"
        except Exception as exc:  # noqa: BLE001
            observed = "error"
            passed = False
            detail = str(exc)
        record("secret-scan", "seeded credential", "at least one finding",
               observed, passed, detail)

        write("package.json", json.dumps({"name": "probe", "version": "1.0.0"}))
        write("SKILL.md", "---\nname: probe\nversion: 2.0.0\n---\n")
        write("README.md", "# probe\n")
        write("LICENSE", "MIT\n")
        write("CHANGELOG.md", "## v1.0.0\n")
        try:
            readiness = check_publish_readiness(str(root))
            codes = {item["code"] for item in readiness["issues"]}
            observed = "FAIL" if not readiness["ok"] else "PASS"
            passed = not readiness["ok"] and "version-mismatch" in codes
            detail = "seeded package/SKILL version mismatch"
        except Exception as exc:  # noqa: BLE001
            observed = "error"
            passed = False
            detail = str(exc)
        record("publish-check", "seeded version mismatch", "FAIL",
               observed, passed, detail)
    return probes


def _self_test_version_check(root, mode):
    if mode == "installed":
        skill = root / "SKILL.md"
        version = _frontmatter_version(_read_text(skill)) if skill.is_file() else None
        if not version:
            return "FAIL", [{"code": "missing-version", "path": "SKILL.md",
                             "detail": "SKILL.md has no version field"}], \
                "restore a valid SKILL.md version"
        return "PASS", [], None

    versions = {}
    try:
        package = json.loads(_read_text(root / "package.json"))
        versions["package.json"] = package.get("version")
    except Exception as exc:  # noqa: BLE001
        return "FAIL", [{"code": "invalid-package-json", "path": "package.json",
                         "detail": str(exc)}], "fix package.json"
    for rel in ("SKILL.md", "CHANGELOG.md", "server.json"):
        target = root / rel
        if not target.is_file():
            versions[rel] = None
            continue
        if rel == "server.json":
            try:
                versions[rel] = json.loads(_read_text(target)).get("version")
            except Exception:  # noqa: BLE001
                versions[rel] = None
        elif rel == "CHANGELOG.md":
            match = re.search(r"(?m)^##\s+v?(\d+\.\d+\.\d+)", _read_text(target))
            versions[rel] = match.group(1) if match else None
        else:
            versions[rel] = _frontmatter_version(_read_text(target))
    engine_path = root / "scripts" / "dev_engine.py"
    if engine_path.is_file():
        match = re.search(r'(?m)^VERSION\s*=\s*["\']([^"\']+)["\']',
                          _read_text(engine_path))
        versions["engine"] = match.group(1) if match else None
    else:
        versions["engine"] = None
    missing = sorted(key for key, value in versions.items() if not value)
    if missing:
        return "FAIL", [{
            "code": "missing-version", "path": missing[0],
            "detail": "version is missing from: %s" % ", ".join(missing),
        }], "restore version alignment"
    values = {value for value in versions.values() if value}
    if len(values) != 1:
        return "FAIL", [{
            "code": "version-mismatch", "path": "package.json",
            "detail": json.dumps(versions, ensure_ascii=False, sort_keys=True),
        }], "align package, SKILL, CHANGELOG, server and engine versions"
    return "PASS", [], None


def _self_test_tool_contracts(root):
    protocol_path = root / "scripts" / "yotta_dev_mcp.py"
    engine_path = root / "scripts" / "dev_engine.py"
    if not protocol_path.is_file() or not engine_path.is_file():
        return "FAIL", [{
            "code": "missing-protocol-source", "path": "scripts",
            "detail": "protocol or engine source is missing",
        }], "restore the protocol and engine sources"
    try:
        contracts = _protocol_tool_contracts(_read_text(protocol_path))
        dispatch_names = _dispatch_tool_names(_read_text(engine_path))
    except (OSError, ValueError, SyntaxError) as exc:
        return "FAIL", [{
            "code": "tool-contract-parse-error", "path": "scripts",
            "detail": str(exc),
        }], "fix the protocol or engine source"
    protocol_names = [item["name"] for item in contracts]
    evidence = []
    if len(protocol_names) != len(set(protocol_names)):
        evidence.append({"code": "tool-name-duplicate", "path": "scripts/yotta_dev_mcp.py",
                         "detail": "duplicate tool names in mcp_tools()"})
    if set(protocol_names) != set(dispatch_names):
        evidence.append({
            "code": "tool-name-drift", "path": "scripts/yotta_dev_mcp.py",
            "detail": "protocol=%s dispatch=%s" % (
                ",".join(sorted(protocol_names)), ",".join(sorted(dispatch_names)),
            ),
        })
    drifted_schema = sorted(
        item["name"] for item in contracts if not item["additional_properties"]
    )
    if drifted_schema:
        evidence.append({
            "code": "tool-schema-drift", "path": "scripts/yotta_dev_mcp.py",
            "detail": "inputSchema.additionalProperties is not false for: %s"
                      % ", ".join(drifted_schema),
        })
    if evidence:
        return "FAIL", evidence, "align the protocol schemas with the engine handlers"
    return "PASS", [], None


def _self_test_write_gates(root):
    engine_path = root / "scripts" / "dev_engine.py"
    if not engine_path.is_file():
        return "FAIL", [{"code": "missing-engine-source", "path": "scripts/dev_engine.py",
                         "detail": "engine source is missing"}], "restore the engine source"
    text = _read_text(engine_path)
    evidence = []
    for function_name, parameter in VERIFY_WRITE_GATES:
        found, value = _function_default(text, function_name, parameter)
        if not found:
            evidence.append({
                "code": "write-gate-missing",
                "path": "scripts/dev_engine.py",
                "detail": "%s(%s=...) was not found" % (function_name, parameter),
            })
        elif value is not False:
            evidence.append({
                "code": "write-gate-drift",
                "path": "scripts/dev_engine.py",
                "detail": "%s.%s default is %r, expected False"
                          % (function_name, parameter, value),
            })
    if evidence:
        return "FAIL", evidence, "restore fail-closed defaults for write and execute gates"
    return "PASS", [], None


def _self_test_detect_test_kind(root):
    package_path = root / "package.json"
    if package_path.is_file():
        try:
            package = json.loads(_read_text(package_path))
            scripts = package.get("scripts") or {}
            if isinstance(scripts, dict) and scripts.get("test"):
                return "npm-test"
        except Exception:  # noqa: BLE001
            pass
    if list(root.glob("test*.py")) or list(root.glob("tests/test*.py")):
        return "python-unittest"
    return None


def self_test(path, mode="auto", allow_execute=False, timeout=120):
    """Run deterministic integrity and counterexample checks on yotta-dev-mcp itself."""
    root = Path(path)
    if not root.exists():
        raise ValueError("路径不存在: %s" % path)
    if not root.is_dir():
        raise ValueError("self_test 需要目录: %s" % path)
    if mode not in ("auto", "source", "installed"):
        raise ValueError("mode 必须是 auto、source 或 installed")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 600:
        raise ValueError("timeout 必须是 1 到 600 之间的整数")
    if mode == "auto":
        if ((root / "package.json").is_file()
                or (root / "scripts" / "yotta_dev_mcp.py").is_file()):
            mode = "source"
        elif (root / "SKILL.md").is_file():
            mode = "installed"
        else:
            mode = "source"

    required = (VERIFY_REQUIRED_SOURCE_FILES if mode == "source"
                else VERIFY_REQUIRED_INSTALLED_FILES)
    missing = [rel for rel in required if not (root / rel).is_file()]
    files_check = {
        "id": "files",
        "status": "FAIL" if missing else "PASS",
        "severity": "high",
        "claim": "required %s files are present" % mode,
        "evidence": [{
            "code": "missing-file", "path": rel,
            "detail": "required file is missing",
        } for rel in missing],
        "next_step": "restore the missing files" if missing else None,
    }

    version_status, version_evidence, version_next = _self_test_version_check(root, mode)
    version_check = {
        "id": "versions" if mode == "source" else "skill-version",
        "status": version_status,
        "severity": "high",
        "claim": ("package, SKILL, CHANGELOG, server and engine versions align"
                  if mode == "source" else "SKILL.md declares a version"),
        "evidence": version_evidence,
        "next_step": version_next,
    }

    checks = [files_check, version_check]
    unverified = []
    if mode == "source":
        tool_status, tool_evidence, tool_next = _self_test_tool_contracts(root)
        checks.append({
            "id": "tool-contracts",
            "status": tool_status,
            "severity": "high",
            "claim": "protocol tool schemas match the engine dispatch handlers",
            "evidence": tool_evidence,
            "next_step": tool_next,
        })
        gate_status, gate_evidence, gate_next = _self_test_write_gates(root)
        checks.append({
            "id": "write-gates",
            "status": gate_status,
            "severity": "high",
            "claim": "write and execute gates default to fail-closed",
            "evidence": gate_evidence,
            "next_step": gate_next,
        })
    else:
        unverified.extend([
            {"level": "L0", "claim": "protocol tool schemas match engine handlers",
             "status": "UNVERIFIED", "reason": "installed mode has no protocol source",
             "required": False, "next_step": "run self_test on the source checkout"},
            {"level": "L0", "claim": "write and execute gates default to fail-closed",
             "status": "UNVERIFIED", "reason": "installed mode has no engine source",
             "required": False, "next_step": "run self_test on the source checkout"},
        ])

    probes = _self_test_counterexamples()
    probe_failed = [item for item in probes if not item["passed"]]
    checks.append({
        "id": "verifier-counterexamples",
        "status": "FAIL" if probe_failed else "PASS",
        "severity": "high",
        "claim": "seeded defects and mutation controls turn the verifier red or unknown",
        "evidence": probes,
        "next_step": "fix the verifier so every counterexample is detected"
                     if probe_failed else None,
    })

    if allow_execute:
        kind = _self_test_detect_test_kind(root)
        if kind is None:
            checks.append({
                "id": "tests",
                "status": "UNKNOWN",
                "severity": "medium",
                "claim": "the project test suite passes",
                "evidence": [{"code": "test-runner-missing", "path": ".",
                              "detail": "no whitelisted test runner was detected"}],
                "next_step": "declare a test script or add test_*.py files",
            })
        else:
            try:
                result = run_checks(kind, str(root), timeout=timeout,
                                    allow_execute=True)
                output = result.get("output") or ""
                checks.append({
                    "id": "tests",
                    "status": "PASS" if result.get("passed") else "FAIL",
                    "severity": "high",
                    "claim": "the project test suite passes",
                    "evidence": [{"code": "test-run", "path": ".",
                                  "detail": result.get("summary") or "test run finished"}],
                    "next_step": None if result.get("passed")
                                else "fix the failing tests and rerun self_test",
                    "command": {
                        "kind": kind,
                        "cwd": ".",
                        "exit_code": result.get("exit_code"),
                        "output_hash": hashlib.sha256(
                            output.encode("utf-8")
                        ).hexdigest(),
                        "timed_out": bool(result.get("timed_out")),
                    },
                })
            except Exception as exc:  # noqa: BLE001
                checks.append({
                    "id": "tests",
                    "status": "FAIL",
                    "severity": "high",
                    "claim": "the project test suite passes",
                    "evidence": [{"code": "test-run-error", "path": ".",
                                  "detail": str(exc)}],
                    "next_step": "fix the local test runner",
                })
    else:
        unverified.append({
            "level": "L2",
            "claim": "the project test suite passes",
            "status": "UNVERIFIED",
            "reason": "allow_execute=false",
            "required": False,
            "next_step": "rerun self_test with allow_execute=true",
        })

    failed = any(item["status"] == "FAIL" for item in checks)
    unknown = any(item["status"] == "UNKNOWN" for item in checks)
    if failed:
        status = "FAIL"
    elif unknown:
        status = "UNKNOWN"
    else:
        status = "PASS"
    counts = {
        "passed": sum(1 for item in checks if item["status"] == "PASS"),
        "failed": sum(1 for item in checks if item["status"] == "FAIL"),
        "unknown": sum(1 for item in checks if item["status"] == "UNKNOWN"),
        "unverified": len(unverified),
    }
    evidence = []
    for item in checks:
        evidence.extend(item.get("evidence") or [])
    return {
        "status": status,
        "root": str(root.resolve()),
        "mode": mode,
        "checks": checks,
        "unverified_claims": unverified,
        "summary": counts,
        "evidence": evidence[:EVIDENCE_LIMIT],
    }


def dispatch(name, arguments):
    handlers = {
        "repo_map": repo_map,
        "system_model": system_model,
        "architecture_review": architecture_review,
        "impact_analysis": impact_analysis,
        "verify_change": verify_change,
        "self_test": self_test,
        "find_code": find_code,
        "compress_output": compress_output,
        "review_code": review_code,
        "review_diff": review_diff,
        "mcp_doctor": mcp_doctor,
        "scan_secrets": scan_secrets,
        "scan_dependencies": scan_dependencies,
        "check_publish_readiness": check_publish_readiness,
        "run_checks": run_checks,
        "scaffold_skill": scaffold_skill,
        "workflow_state": workflow_state,
    }
    if name not in handlers:
        raise ValueError("未知工具: %s" % name)
    return handlers[name](**(arguments or {}))


def main():
    parser = argparse.ArgumentParser(description="Deterministic development tools for yotta-dev-mcp")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command")
    repo = sub.add_parser("repo-map")
    repo.add_argument("path")
    model = sub.add_parser("system-model")
    model.add_argument("path")
    model.add_argument("--contract", help="contract path relative to the repository root")
    review = sub.add_parser("architecture-review")
    review.add_argument("path")
    review.add_argument("--contract", help="contract path relative to the repository root")
    impact = sub.add_parser("impact-analysis")
    impact.add_argument("path")
    impact.add_argument("--changed", action="append",
                        help="repository-relative changed file (repeatable)")
    impact.add_argument("--diff-file", help="read a unified diff from this file")
    impact.add_argument("--symbol", action="append",
                        help="target symbol whose definition site is the change (repeatable)")
    impact.add_argument("--depth", type=int, default=3,
                        help="reverse-dependency depth, 1-10 (default 3)")
    impact.add_argument("--contract", help="contract path relative to the repository root")
    verify = sub.add_parser("verify-change")
    verify.add_argument("path")
    verify.add_argument("--changed", action="append",
                       help="repository-relative changed file (repeatable)")
    verify.add_argument("--diff-file", help="read a unified diff from this file")
    verify.add_argument("--symbol", action="append",
                        help="target symbol whose definition site is the change (repeatable)")
    verify.add_argument("--level", action="append", choices=VERIFY_LEVELS,
                        help="additional verification level (repeatable)")
    verify.add_argument("--depth", type=int, default=3,
                        help="reverse-dependency depth, 1-10 (default 3)")
    verify.add_argument("--contract", help="contract path relative to the repository root")
    verify.add_argument("--policy-file", help="verification policy path relative to the root")
    verify.add_argument("--allow-execute", action="store_true",
                        help="run whitelisted L2-L4 policy checks")
    verify.add_argument("--timeout", type=int, default=120,
                        help="upper bound for each check in seconds")
    self_test_parser = sub.add_parser("self-test")
    self_test_parser.add_argument("path")
    self_test_parser.add_argument("--mode", choices=("auto", "source", "installed"),
                                  default="auto")
    self_test_parser.add_argument("--allow-execute", action="store_true",
                                  help="run the target test suite")
    self_test_parser.add_argument("--timeout", type=int, default=120,
                                  help="test timeout in seconds")
    find = sub.add_parser("find-code")
    find.add_argument("path")
    find.add_argument("query")
    compress = sub.add_parser("compress-output")
    compress.add_argument("file")
    review = sub.add_parser("review-code")
    review.add_argument("path")
    doctor = sub.add_parser("mcp-doctor")
    doctor.add_argument("--skills-dir", action="append")
    doctor.add_argument("--config", action="append")
    args = parser.parse_args()
    if args.command == "repo-map":
        result = repo_map(args.path)
    elif args.command == "system-model":
        result = system_model(args.path, contract_file=args.contract)
    elif args.command == "architecture-review":
        result = architecture_review(args.path, contract_file=args.contract)
    elif args.command == "impact-analysis":
        diff_text = None
        if args.diff_file:
            diff_text = Path(args.diff_file).read_text(encoding="utf-8")
        result = impact_analysis(args.path, changed_files=args.changed, diff=diff_text,
                                 symbols=args.symbol, depth=args.depth,
                                 contract_file=args.contract)
    elif args.command == "verify-change":
        diff_text = None
        if args.diff_file:
            diff_text = Path(args.diff_file).read_text(encoding="utf-8")
        result = verify_change(
            args.path, changed_files=args.changed, diff=diff_text,
            symbols=args.symbol, depth=args.depth, levels=args.level,
            allow_execute=args.allow_execute, timeout=args.timeout,
            contract_file=args.contract, policy_file=args.policy_file,
        )
    elif args.command == "self-test":
        result = self_test(
            args.path, mode=args.mode, allow_execute=args.allow_execute,
            timeout=args.timeout,
        )
    elif args.command == "find-code":
        result = find_code(args.path, args.query)
    elif args.command == "compress-output":
        result = compress_output(file=args.file)
    elif args.command == "review-code":
        result = review_code(args.path)
    elif args.command == "mcp-doctor":
        result = mcp_doctor(skills_dirs=args.skills_dir, config_paths=args.config)
    else:
        parser.print_help()
        return 2
    sys.stdout.write(json.dumps(_json_safe(result), ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
