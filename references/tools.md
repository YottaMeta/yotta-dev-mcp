# Tool contracts

All tools are local, deterministic and read-only in v0.1.0.

## repo_map

Input:

- `path` (required): repository or source directory.
- `max_files` (optional, default 2000): source file limit.

Output: `root`, `modules`, `imports`, `entrypoints`, `truncated`.

## find_code

Input:

- `path` (required): repository, directory or file.
- `query` (required): symbol or text.
- `extensions` (optional): file extensions such as `.py`.
- `max_results` (optional, default 100).
- `context_lines` (optional, 0-5).

Output: `query`, `matches` (`path`, `line`, `kind`, `text`, `context`), `truncated`.

## compress_output

Input:

- `text` or `file` (one required).
- `max_chars` (optional, default 4000).
- `head_lines` / `tail_lines` (optional).

Output: text plus `original_lines`, `kept_lines`, `error_lines`, `truncated`.

## review_code

Input:

- `path` or `text` (one required).
- `max_findings` (optional, default 200).

Rules: bare-except, eval-exec, shell-true, debug-print, todo-comment,
mutable-default.

Each finding contains `path`, `line`, `rule`, `severity`, `evidence`, `suggestion`.

## review_diff

Input:

- `diff_text` or `path` (one required).
- `base` (optional git revision when `path` is used).
- `max_findings` (optional).

Only added lines are reviewed. Output: `files`, `findings`, `truncated`.

## mcp_doctor

Input:

- `skills_dirs` (optional array).
- `config_paths` (optional array).

Output: `skills`, `mcp_configs`, `issues`, `checked_skills`, `checked_configs`.

## scan_secrets

Input: `path` or `text`, optional `max_findings`, optional `include_git_history`
(bounded, default false).

Output: `findings` with `path`, `line`, `rule`, `severity`, redacted `evidence`,
`suggestion`; `truncated`.

## scan_dependencies

Input: `path`.

Output: `manifests`, `lockfiles`, `issues` (`missing-lockfile`,
`unpinned-dependency`, `insecure-source`, `local-dependency`,
`typosquat-suspicion`), and counts. Offline heuristics only.

## check_publish_readiness

Input: `path`.

Output: `ok`, `files`, `versions` (package / skill / changelog / engine when
present), `issues`.

## run_checks

Input: `kind` (`python-unittest` / `pytest` / `python-compile` / `npm-test` /
`npm-lint`), `cwd`, optional `timeout`, explicit `allow_execute=true`.

Output: `exit_code`, `passed`, bounded `summary`, compressed `output`,
`timed_out`.

## scaffold_skill

Input: `name`, `output_dir`, optional `description`, explicit `apply=true`.

Output: `target`, `files`, `applied`. Default is dry-run; existing non-empty
targets are rejected.

## workflow_state

Input: `root`, `action` (`read` / `append-log` / `append-file`), optional
`date`, `text`, `file`, explicit `apply=true`.

Output: `ok`, `files`, `missing`, `excerpts` for read; `target`, `applied`,
`preview` for writes. Writes are atomic and keep a `.bak` of an existing file.
