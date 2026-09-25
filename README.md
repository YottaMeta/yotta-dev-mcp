<p align="center"><img src="assets/banner.png" alt="YuanKai banner" width="100%"></p>
<h1 align="center">YuanKai (yotta-dev-mcp)</h1>
<p align="center"><b>Language</b>: English · <a href="README.zh-CN.md">中文</a></p>

Deterministic local development tools exposed over a stdio MCP server.

## What it does

| Tool | Purpose |
|---|---|
| `repo_map` | Map modules, imports and entrypoints in a repository. |
| `system_model` | Build a system model with layer data from the architecture contract. |
| `architecture_review` | Review dependency rules, boundaries and data ownership with evidence. |
| `impact_analysis` | Build the change impact cone with tests, blast radius and rollback probes. |
| `verify_change` | Run the L0-L5 verification ladder and return an evidence ledger. |
| `self_test` | Check files, versions, tool contracts, write gates and seeded-defect counterexamples. |
| `run_adapter` | Probe or explicitly run optional import-linter / dependency-cruiser / Repomix adapters. |
| `find_code` | Locate symbols and text with bounded results. |
| `compress_output` | Compress long logs while keeping errors and head/tail context. |
| `review_code` | Apply deterministic review rules with file, line and evidence. |
| `review_diff` | Review only added lines in a unified diff. |
| `mcp_doctor` | Inspect installed skills and MCP JSON configuration files. |
| `scan_secrets` | Scan for credentials and high-entropy tokens with redacted evidence. |
| `scan_dependencies` | Check manifests, lockfiles, insecure sources and typosquat suspicion. |
| `check_publish_readiness` | Check version alignment, release files and package metadata. |
| `run_checks` | Run whitelisted tests, lint or compile checks; execution is explicit. |
| `scaffold_skill` | Plan or create a minimal skill scaffold; dry-run by default. |
| `workflow_state` | Read `.workflow` and optionally append an explicit log entry. |

Tools are read-only by default; `run_checks`, the L2-L4 policy checks in
`verify_change`, the test subset in `self_test`, and `run_adapter` with
`action=run` require `allow_execute=true`. `scaffold_skill` / `workflow_state`
require `apply=true` before writing. Python 3.8+ standard library is sufficient;
no network access is required.

### Architecture contract

`system_model` reads an optional `.yotta/architecture.json` (version 1) that declares
layers, dependency rules, boundaries, data ownership, invariants and risk weights. The
result reports `PASS`, `FAIL` or `UNKNOWN`, lists every unknown that still needs
evidence, and keeps verification levels that were not executed in `unverified_claims`.
`architecture_review` then checks that contract: dependency rules, boundary visibility
and data ownership each produce findings with file, line and severity. `critical` and
`high` findings fail the review, `medium` and `low` stay advisory, and anything that
cannot be decided from the model becomes `UNKNOWN` instead of a silent pass.

`impact_analysis` starts from `changed_files`, a unified diff or target symbols and
walks reverse dependencies into a bounded cone: direct consumers, affected layers,
boundaries, data stores, invariants, mapped tests, an explainable blast radius and
rollback probes. It executes nothing.

`verify_change` runs the post-change ladder: L0 syntax and contract schema, L1
architecture checks inside the impact cone, L2-L4 whitelisted checks declared in
`.yotta/verification.json` with explicit `allow_execute=true`, and L5 independent
review kept as unverified work. Ledger entries carry a claim, status, evidence,
check name and output hash so the result can be recomputed; skipped levels are never
written as passing.

`self_test` checks the integrity of yotta-dev-mcp itself: required files, version
alignment across package / SKILL / CHANGELOG / server / engine, protocol tool schema
drift, fail-closed write gates, and seeded-defect plus mutation controls that prove
the verifier turns red or unknown. The test suite is not executed by default.

`run_adapter` is an optional enhancement layer. `action=list` only probes the
project-local `node_modules/.bin`, project virtualenvs and `PATH`; `action=run`
requires `allow_execute=true` and runs one fixed adapter command. Missing tools,
missing config, invalid output and timeouts return `UNKNOWN` with a next step.
No package is installed or downloaded, and adapter results do not silently change
the status of the core architecture tools. Details: `references/adapters.md`.

Schema, glob rules, finding codes and cone fields: `references/architecture-contract.md`.

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
python scripts/dev_engine.py system-model .
python scripts/dev_engine.py architecture-review .
python scripts/dev_engine.py impact-analysis . --changed src/core/store.py
python scripts/dev_engine.py verify-change . --changed src/core/store.py
python scripts/dev_engine.py verify-change . --changed src/core/store.py --level L2 --allow-execute
python scripts/dev_engine.py self-test .
python scripts/dev_engine.py adapter . --action list
python scripts/dev_engine.py adapter . --action run --adapter dependency-cruiser --allow-execute
python scripts/dev_engine.py find-code . "helper"
python scripts/dev_engine.py review-code .
python scripts/dev_engine.py mcp-doctor
```

## Boundaries

- No code execution, no automatic edits, no commits.
- No network access, no package-existence lookup.
- Static deterministic findings only; human review remains the final decision.

## Current version

`0.2.0` (2026-09-25) adds `system_model`, `architecture_review`,
`impact_analysis`, `verify_change`, `self_test`, `run_adapter`, and the
`.yotta/architecture.json` plus optional `.yotta/verification.json` contracts on
top of the twelve deterministic tools.
