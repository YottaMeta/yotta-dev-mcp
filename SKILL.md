---
name: yotta-dev-mcp
description: 开发能力 MCP（yotta-dev-mcp）—— 把确定性、只读默认、本地的开发工具暴露为 stdio MCP server：repo_map / find_code / compress_output / review_code / review_diff / mcp_doctor / scan_secrets / scan_dependencies / check_publish_readiness / run_checks / scaffold_skill / workflow_state。触发：让 AI 在陌生项目里先做结构盘点、定位代码、评审改动、扫描密钥/依赖、检查发布就绪、运行白名单检查、生成脚手架或读取 .workflow 状态时；或用户说 开发能力 MCP / yotta-dev-mcp / 代码库地图 / 代码评审 MCP 等。边界：Python 3.8+ 标准库、离线默认；除 run_checks（显式 allow_execute）与 scaffold_skill / workflow_state 的显式 apply 外均为只读；不上传源码、不自动修改、不提交、不联网查询包是否存在。
version: 0.1.0
license: MIT
---

# 开发能力 MCP

把一组本地、确定性的开发工具做成一个 stdio MCP server。任何支持 MCP 的客户端接上后，
都能在真实开发任务里调用这些工具；输出带文件、行号、规则和证据，便于直接进入修复清单。

## 何时使用

- 接手陌生项目：先 `repo_map` 看模块、依赖和入口，再 `find_code` 定位。
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
| `repo_map` | 模块、导入、入口点地图 | 否 |
| `find_code` | 符号 / 文本定位，结果有上限 | 否 |
| `compress_output` | 保留错误与首尾的长输出压缩 | 否 |
| `review_code` | 规则化代码评审，带行号与建议 | 否 |
| `review_diff` | 只评审 diff 的新增行 | 否 |
| `mcp_doctor` | 技能版本与 MCP JSON 配置体检 | 否 |
| `scan_secrets` | 密钥 / 凭据 / 高熵令牌扫描（强制脱敏） | 否 |
| `scan_dependencies` | 依赖清单、lockfile、来源与 typosquat 启发式检查 | 否 |
| `check_publish_readiness` | 版本四件、发布文件、仓库与 publishConfig 检查 | 否 |
| `run_checks` | 白名单测试 / lint / compile 并返回结构化摘要 | 仅显式 allow_execute |
| `scaffold_skill` | 生成最小技能脚手架，默认 dry-run | 仅显式 apply |
| `workflow_state` | 读取 `.workflow`，可选显式追加日志 | 仅显式 apply |

详细契约见 `references/tools.md`。

## 边界

- `run_checks` 是唯一会执行项目代码的工具，默认关闭，必须显式 `allow_execute=true`；只运行白名单检查。
- `scaffold_skill` / `workflow_state` 默认只预览，显式 `apply=true` 才写入；写入前做原子替换并保留 `.bak`。
- 不联网，不查询包是否存在于公共仓库。
- 不读取或修改 YottaCode 仓库。
- 结论是确定性静态判断，不替代人工评审与最终决策。

## 当前版本

- v0.1.0：协议内核 + 上述 12 个工具。
