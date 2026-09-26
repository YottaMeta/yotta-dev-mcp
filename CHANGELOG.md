## v0.2.2 (2026-09-26)

新增工具分组 `--tools core|full`（与元忆同口径），供需要把工具表常驻上下文的宿主使用。

- `core` = 5 工具：`repo_map` / `find_code` / `review_code` / `review_diff` / `verify_change`；紧凑 JSON schema 3,883 字符。
- `full` = 全部 18 工具（默认，行为与 0.2.1 一致）；同一口径下 schema 13,230 字符。
- 背景：产品侧实测 18 工具 `schemaChars 8174`，超过其 8,000 硬上限，无法默认常驻；core 档按同一比例约 2.4k，留有充足余量。
- `core` 模式下调用 full 专属工具会返回明确提示（"属于 full 分组，请用 --tools full 重新启动"），不做静默失败。
- `--help` / `--version` 支持；非法分组值以中文用法错误 + 退出码 2 拒绝。
- 回归：新增 7 项分档用例（默认 full / core 列表与顺序 / core 拒绝 full 专属工具 / schema 预算 / 非法值拒绝 / stdio 两种端到端）；元开全量 189/189（1 skip）。

# Changelog

## v0.2.1 (2026-09-25)

**缺陷修复批次（LedgerHouse 实测 + 元开自审）**

- `mcp_doctor` 改为统一发现层：多宿主注册表（Codex / Cursor / WorkBuddy /
  OpenCode / Claude Code / Windsurf / Continue / Gemini / Qwen / Trae /
  Comate / CodeBuddy / Kimi / Kiro / VS Code / Zed 等）、环境根
  （`CODEX_HOME` / `XDG_CONFIG_HOME` / `CLAUDE_CONFIG_DIR` / `USERPROFILE` /
  `HOME`）、JSON / JSONC / TOML 关键字子集、`mcpServers` / `mcp` /
  `[mcp_servers]` 规范化；输出新增 `coverage` / `coverage_gaps` /
  `unknown_hosts` / `coverage_confidence`，只返回 server 名，不返回值 /
  command / env；显式 `config_paths` 仍可单独使用，`include_defaults` 可叠加默认扫描。
- `self_test(mode="installed")` 按分发形态判断必需文件：精简分发副本
  （`_icon.png`）不要求被平台排除的 `assets/banner.png`；
  npm / plugin / r0 副本仍要求 banner；精简副本若自带 `assets/` 也仍要求 banner。
- `repo_map` 修复 `from . import a, b` / `from . import b as bb`：逐 alias
  返回包内子模块，交给统一分类器解析为 `.py` 或 `/__init__.py`，不再生成
  不存在的 `<包目录>.py`。
- `scan_secrets` 增加窄上下文过滤：Windows / UNC / POSIX 绝对路径、URL /
  `file://` 百分号编码路径、常见二进制 / 源码 / 文档后缀、带 `sha*` / hash /
  checksum / digest / integrity 上下文的 32/40/64/128 位十六进制；
  `api-key` / token / AWS / 私钥规则不放宽，真随机高熵 token 仍会命中。
- `review_code` / `repo_map` 共用忽略规则：新增 `.workflow` / `.codex` /
  `.cursor` / `.claude` / `.agents` / `scratch` / `_probe` / `probe` /
  `sandbox` / `debug` 等目录与 `probe_*` / `_probe_*` / `scratch_*` /
  `tmp_*` / `temp_*` / `*_tmp` 文件名兜底；`scan_secrets` 仍扫描全部文件，
  避免漏掉临时文件中的真实凭据。
- 回归：新增 `test_mcp_doctor.py` 13 项 + self_test / repo_map /
  scan_secrets / review_code 形态与精度测试；元开全量 182/182（1 skip）。

## v0.2.0 (2026-09-25)

- 新增 `system_model`：模块、依赖、入口、测试映射、配置与数据归属的确定性系统模型，
  附带 `.yotta/architecture.json` 契约分层。
- 新增 `architecture_review`：按契约评审依赖规则、边界可见性与数据归属，
  每条产出 file / line / severity 证据；critical / high 判 FAIL，medium / low 只告警，
  无法判定的部分进 UNKNOWN，声明的不变量一律进 `unverified_claims`。
- 新增 `impact_analysis`：从 changed_files / unified diff / 目标符号生成变更影响锥，
  含直接消费者、受影响层与边界、数据存储、不变量、映射测试、可解释风险分级与回滚探针。
- 新增 `verify_change`：L0-L5 验证阶梯与确定性证据账本；L0 / L1 默认运行，
  L2-L4 必须同时有 `.yotta/verification.json` 白名单声明与 `allow_execute=true`，
  L5 人工复核始终留在 `unverified_claims`；账本以 output hash 与 ledger digest 复算。
- 新增 `self_test`：检查必需文件、版本五件、协议工具 schema / engine dispatch 漂移、
  写入与执行闸门是否 fail-closed，并用 seeded defect 与 mutation control 反证验证器会红。
- 新增 `run_adapter`：探测或显式运行项目已安装的 import-linter /
  dependency-cruiser / Repomix；固定 argv、项目内 cwd、有界输出与 hash，
  缺工具 / 缺配置 / 超时 / 非法输出一律返回 UNKNOWN，且不自动安装、不下载、不访问远端。
- 新增架构契约（版本 1）：分层、依赖规则、边界、数据归属、不变量、风险权重；
  校验输出 code / severity / JSON pointer / evidence。
- 新增可选验证策略（版本 1）：只允许白名单 runner、仓库内相对 cwd 与显式执行；
  拒绝任意命令、绝对路径与越界 cwd。
- 结论只给 `PASS` / `FAIL` / `UNKNOWN`；缺证据项写入 `unknowns`，未执行的验证级别
  写入 `unverified_claims`；模型带稳定 `model_digest`。
- 引擎按共享层拆分：`dev_common` / `dev_model` / `dev_architecture` /
  `dev_impact` / `dev_verify` / `dev_selftest` / `dev_adapters`；`dev_engine`
  保留原公开函数与 dispatch 作为稳定门面。
- 原 12 个工具行为不变；新增的六个工具离线、零依赖（适配器只在用户已安装时调用），默认只读。

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
