#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic development tools for yotta-dev-mcp.

The engine is intentionally small and self-contained: Python 3.8+ standard
library only, offline by default, deterministic output, read-only unless a
tool explicitly documents a write.
"""

import argparse
import ast
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from dev_rules import REVIEW_RULES

VERSION = "0.1.1"

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


def dispatch(name, arguments):
    handlers = {
        "repo_map": repo_map,
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
