# Changelog

## v0.1.1 (2026-09-25)

- 品牌改名：对外显示名统一为「元开（yotta-dev-mcp）」，不再把功能描述「开发能力 MCP」当产品名。
- 功能与 12 个工具保持不变；协议、参数、输出与 0.1.0 兼容。

## v0.1.0 (2026-09-25)

- 首个本地候选：MCP 双时代协议内核（2026-07-28 modern + 2025-11-25 legacy）。
- 十二个确定性工具：`repo_map` / `find_code` / `compress_output` /
  `review_code` / `review_diff` / `mcp_doctor` / `scan_secrets` /
  `scan_dependencies` / `check_publish_readiness` / `run_checks` /
  `scaffold_skill` / `workflow_state`。
- Python 3.8+ 标准库实现；离线默认运行；输出带证据、排序稳定。
- `run_checks` 默认关闭执行；`scaffold_skill` / `workflow_state` 默认只预览。
