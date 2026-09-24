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
import os
import re
import subprocess
import sys
from pathlib import Path

from dev_rules import REVIEW_RULES

VERSION = "0.1.0"

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
    return path.read_text(encoding="utf-8", errors="replace")


def _iter_files(root, extensions=None, max_files=5000):
    root = Path(root)
    extensions = set(extensions or TEXT_EXTS)
    count = 0
    for current, dirs, files in os.walk(str(root)):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
        for name in sorted(files):
            path = Path(current) / name
            if extensions and path.suffix.lower() not in extensions:
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


def dispatch(name, arguments):
    handlers = {
        "repo_map": repo_map,
        "find_code": find_code,
        "compress_output": compress_output,
        "review_code": review_code,
        "review_diff": review_diff,
        "mcp_doctor": mcp_doctor,
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
