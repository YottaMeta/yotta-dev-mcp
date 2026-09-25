---
name: yotta-dev-mcp
description: 元开（yotta-dev-mcp）—— 本地、确定性的开发工具 MCP，把只读默认的开发能力暴露为 stdio MCP server：repo_map / system_model / architecture_review / impact_analysis / verify_change / self_test / run_adapter / find_code / compress_output / review_code / review_diff / mcp_doctor / scan_secrets / scan_dependencies / check_publish_readiness / run_checks / scaffold_skill / workflow_state。触发：让 AI 在陌生项目里先做结构盘点、构建系统模型或架构契约（.yotta/architecture.json）、按契约评审架构、分析改动影响锥与回归面、按验证阶梯产出证据账本、对元开自身做完整性 / 反证自测、探测或显式运行 import-linter / dependency-cruiser / Repomix 可选适配器、定位代码、评审改动、扫描密钥/依赖、检查发布就绪、运行白名单检查、生成脚手架或读取 .workflow 状态时；或用户说 元开 / 开发能力 MCP / yotta-dev-mcp / 代码库地图 / 系统模型 / 架构契约 / 架构评审 / 影响分析 / 验证账本 / 反证自测 / 代码评审 MCP / 适配器 等。边界：Python 3.8+ 标准库、离线默认；除 run_checks（显式 allow_execute）、verify_change 的 L2-L4 策略检查（显式 allow_execute）与 self_test 的测试子集（显式 allow_execute）、run_adapter 的 action=run（显式 allow_execute）、scaffold_skill / workflow_state 的显式 apply 外均为只读；不上传源码、不自动修改、不提交、不联网查询包是否存在、不自动安装或下载适配器。
version: 0.2.1
license: MIT
---

# 元开（yotta-dev-mcp）

元开（YuanKai）把一组本地、确定性的开发工具做成一个 stdio MCP server。任何支持 MCP 的客户端接上后，
都能在真实开发任务里调用这些工具；输出带文件、行号、规则和证据，便于直接进入修复清单。

## 何时使用

- 接手陌生项目：先 `repo_map` 看模块、依赖和入口，再 `find_code` 定位。
- 架构与影响分析：先写 `.yotta/architecture.json`，再用 `system_model` 拿分层、依赖、测试映射与 `UNKNOWN` 清单。
- 契约评审：用 `architecture_review` 查依赖规则、边界可见性与数据归属，每条给证据与严重级。
- 改动前评估：用 `impact_analysis` 从改动文件 / diff / 目标符号走出影响锥、映射测试与回滚探针。
- 改动后验证：用 `verify_change` 跑 L0 / L1 默认检查，按需结合 `.yotta/verification.json` 与 `allow_execute=true` 执行 L2-L4 白名单检查，产出可复算账本。
- 自身完整性：用 `self_test` 检查文件、版本、工具契约、写闸门与 seeded defect / mutation 反证。
- 可选增强：用 `run_adapter` 先 `action=list` 探测 import-linter / dependency-cruiser / Repomix，再按需 `action=run` + `allow_execute=true`；缺工具或配置返回 UNKNOWN，不自动安装、不自动下载。
- 评审改动：`review_diff` 只看新增行；`review_code` 对文件或仓库做规则检查。
- 长日志 / 长命令输出：`compress_output` 保错误、错误栈和首尾上下文。
- 本机排查：`mcp_doctor` 只读检查技能目录和 MCP JSON 配置。

## MCP 接入

启动命令：

```bash
npx -y @yottameta/yotta-dev-mcp
```

或直接用本地脚本：

```bash
python scripts/yotta_dev_mcp.py
```

自动接入说明：AI 首次协助配置时，必须先展示目标配置文件、完整配置片段和影响，
并获得用户明确同意后再写入 `mcpServers`。用户拒绝时不要写文件，直接提供上面的启动命令
让用户手动接入。

## 工具

| 工具 | 用途 | 写入 |
|---|---|---|
| `repo_map` | 模块、导入、入口点地图；支持 `from . import X` / 别名 / 包，忽略临时与探针目录 | 否 |
| `system_model` | 系统模型：模块、依赖、入口、测试映射、配置与数据归属；附带契约分层，输出 PASS / FAIL / UNKNOWN | 否 |
| `architecture_review` | 按契约评审依赖规则、边界可见性与数据归属；critical / high 判 FAIL，medium / low 只告警 | 否 |
| `impact_analysis` | 变更影响锥：直接消费者、受影响层 / 边界 / 存储 / 不变量、映射测试、风险分级与回滚探针 | 否 |
| `verify_change` | L0-L5 验证阶梯与证据账本：L0 / L1 默认，L2-L4 需策略声明 + 显式执行，L5 始终留在未验证项 | 仅显式 allow_execute 的 L2-L4 |
| `self_test` | 文件 / 版本 / 工具契约 / 写闸门 / seeded defect 与 mutation 反证自测 | 仅显式 allow_execute 的测试子集 |
| `run_adapter` | 探测或显式运行 import-linter / dependency-cruiser / Repomix；固定 argv、项目内 cwd、有界输出与哈希 | 仅 action=run + 显式 allow_execute |
| `find_code` | 符号 / 文本定位，结果有上限 | 否 |
| `compress_output` | 保留错误与首尾的长输出压缩 | 否 |
| `review_code` | 规则化代码评审，带行号与建议；忽略 `.workflow` / `scratch` / `_probe` 等临时目录 | 否 |
| `review_diff` | 只评审 diff 的新增行 | 否 |
| `mcp_doctor` | 技能版本与多宿主 MCP 配置体检；返回 coverage，只列 server 名，不含 command / env | 否 |
| `scan_secrets` | 密钥 / 凭据 / 高熵令牌扫描（强制脱敏；路径 / 哈希 / 文件名噪声过滤） | 否 |
| `scan_dependencies` | 依赖清单、lockfile、来源与 typosquat 启发式检查 | 否 |
| `check_publish_readiness` | 版本四件、发布文件、仓库与 publishConfig 检查 | 否 |
| `run_checks` | 白名单测试 / lint / compile 并返回结构化摘要 | 仅显式 allow_execute |
| `scaffold_skill` | 生成最小技能脚手架，默认 dry-run | 仅显式 apply |
| `workflow_state` | 读取 `.workflow`，可选显式追加日志 | 仅显式 apply |

详细契约见 `references/tools.md`；架构契约、评审语义与影响锥字段见 `references/architecture-contract.md`。

## 边界

- 会执行项目代码的工具默认关闭：`run_checks`、`verify_change` 的 L2-L4 策略检查、`self_test` 的测试子集都必须显式 `allow_execute=true`，且只运行白名单检查或已声明的策略检查。
- 外部适配器只在用户已安装时接入：`run_adapter` 不安装、不下载、不访问远端、不接收任意命令参数；工具缺失或配置缺失一律返回 `UNKNOWN` 并给出 next_step。
- `scaffold_skill` / `workflow_state` 默认只预览，显式 `apply=true` 才写入；脚手架拒绝覆盖已有非空目录，状态写入使用原子替换并为被覆盖文件保留 `.bak`。
- 不联网，不查询包是否存在于公共仓库。
- 不读取或修改 YottaCode 仓库。
- 结论是确定性静态判断，不替代人工评审与最终决策。

## 当前版本

- v0.2.1（2026-09-25）：缺陷修复批次——`mcp_doctor` 改为多宿主 / 多根 / JSON+JSONC+TOML 子集发现并返回 coverage；`self_test(mode="installed")` 按精简分发副本 / npm / plugin / r0 形态判断 banner；`repo_map` 修复 `from . import X` 解析；`scan_secrets` 过滤路径 / 哈希 / 文件名噪声；`review_code` / `repo_map` 忽略 `.workflow` / `scratch` / `_probe` 等临时目录。
- v0.2.0（2026-09-25）：新增 `system_model`、`architecture_review`、`impact_analysis`、`verify_change`、`self_test`、`run_adapter`，以及 `.yotta/architecture.json` 与可选 `.yotta/verification.json` 契约（版本 1）；工具总数 18，原 12 个工具行为不变。
- v0.1.1：品牌显示名统一为「元开」；功能与 12 个工具不变。
