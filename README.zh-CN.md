<p align="center"><img src="assets/banner.png" alt="开发能力 MCP banner" width="100%"></p>
<h1 align="center">开发能力 MCP（yotta-dev-mcp）</h1>
<p align="center"><b>Language</b>: <a href="README.md">English</a> · 中文</p>

把本地、确定性的开发工具做成 stdio MCP server，任何支持 MCP 的客户端接上即可使用。

## 能做什么

| 工具 | 用途 |
|---|---|
| `repo_map` | 代码库模块、导入关系与入口点地图。 |
| `find_code` | 符号与文本定位，结果有数量上限。 |
| `compress_output` | 压缩长日志，保留错误、错误栈和首尾上下文。 |
| `review_code` | 规则化代码评审，输出文件、行号、证据与建议。 |
| `review_diff` | 只评审 unified diff 的新增行。 |
| `mcp_doctor` | 只读检查技能目录与 MCP JSON 配置。 |

六个工具全部本地运行、确定性输出、只读；核心只用 Python 3.8+ 标准库，默认不联网。

## 安装

### 启动 MCP server

```bash
npx -y @yottameta/yotta-dev-mcp
```

### 安装技能载荷

```bash
npx -y @yottameta/yotta-dev-mcp --agent codex
npx -y @yottameta/yotta-dev-mcp --dir <技能目录>
npx -y @yottameta/yotta-dev-mcp --list
```

安装器只写入显式目标。`--global` 必须同时给 `--yes`；`--dry-run` 只预览不写入。

其他安装方式：

```bash
git clone https://github.com/YottaMeta/yotta-dev-mcp.git <技能目录>/yotta-dev-mcp
bash <技能目录>/yotta-dev-mcp/install.sh --agent codex
```

也可以使用 GitHub 的 **Download ZIP**，解压到技能目录。

## 直接使用 CLI

```bash
python scripts/dev_engine.py repo-map .
python scripts/dev_engine.py find-code . "helper"
python scripts/dev_engine.py review-code .
python scripts/dev_engine.py mcp-doctor
```

## 边界

- 不执行代码、不自动改文件、不自动提交。
- 不联网，不查询公共仓库中包是否存在。
- 仅做确定性静态判断，最终评审由人负责。

## 当前版本

`0.1.0` 提供协议内核与六个只读工具；其余六个工具进入下一批。
