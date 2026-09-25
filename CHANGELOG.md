# Changelog

## v0.2.0 (unreleased)

- 新增 `system_model`：模块、依赖、入口、测试映射、配置与数据归属的确定性系统模型，
  附带 `.yotta/architecture.json` 契约分层。
- 新增架构契约（版本 1）：分层、依赖规则、边界、数据归属、不变量、风险权重；
  校验输出 code / severity / JSON pointer / evidence。
- 结论只给 `PASS` / `FAIL` / `UNKNOWN`；缺证据项写入 `unknowns`，未执行的验证级别
  写入 `unverified_claims`；模型带稳定 `model_digest`。
- 原 12 个工具行为不变；`system_model` 只读、离线、零依赖。

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
