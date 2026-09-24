<p align="center"><img src="assets/banner.png" alt="YottaDev MCP banner" width="100%"></p>
<h1 align="center">YottaDev MCP (yotta-dev-mcp)</h1>
<p align="center"><b>Language</b>: English · <a href="README.zh-CN.md">中文</a></p>

Deterministic local development tools exposed over a stdio MCP server.

## What it does

| Tool | Purpose |
|---|---|
| `repo_map` | Map modules, imports and entrypoints in a repository. |
| `find_code` | Locate symbols and text with bounded results. |
| `compress_output` | Compress long logs while keeping errors and head/tail context. |
| `review_code` | Apply deterministic review rules with file, line and evidence. |
| `review_diff` | Review only added lines in a unified diff. |
| `mcp_doctor` | Inspect installed skills and MCP JSON configuration files. |

All six tools are local, deterministic and read-only. Python 3.8+ standard library
is sufficient; no network access is required.

## Install

### Start the MCP server

```bash
npx -y @yottameta/yotta-dev-mcp
```

### Install the skill payload

```bash
npx -y @yottameta/yotta-dev-mcp --agent codex
npx -y @yottameta/yotta-dev-mcp --dir <skill-directory>
npx -y @yottameta/yotta-dev-mcp --list
```

The installer uses explicit targets. `--global` additionally requires `--yes`, and
`--dry-run` prints the target without writing.

Other installation methods:

```bash
git clone https://github.com/YottaMeta/yotta-dev-mcp.git <skill-directory>/yotta-dev-mcp
bash <skill-directory>/yotta-dev-mcp/install.sh --agent codex
```

You can also use GitHub's **Download ZIP** action and unpack the archive into the
skill directory.

## Direct CLI

The engine can also be used without MCP:

```bash
python scripts/dev_engine.py repo-map .
python scripts/dev_engine.py find-code . "helper"
python scripts/dev_engine.py review-code .
python scripts/dev_engine.py mcp-doctor
```

## Boundaries

- No code execution, no automatic edits, no commits.
- No network access, no package-existence lookup.
- Static deterministic findings only; human review remains the final decision.

## Current version

`0.1.0` ships the protocol core and six read-only tools. The remaining six tools are
planned for the next batch.
