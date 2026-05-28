# PaperAgent MCP Server 技术文档

## 概述

PaperAgent 将自身的**论文库记忆能力**封装为标准的 [MCP (Model Context Protocol)](https://modelcontextprotocol.io) Server，可挂载到 Claude Code、Claude Desktop 等任意 MCP 客户端使用。

通过 MCP，外部 AI 助手可以直接：
- **语义搜索**你的论文库（基于向量检索）
- **查阅论文详情**（结构化提取的完整信息）
- **提问并获取 AI 回答**（基于论文库的 RAG 问答）
- **导出论文库**为 Markdown
- **管理标签**

PaperAgent 自身的前端对话功能也通过 SSE 模式接入同一 MCP Server。

---

## 架构

```
┌─────────────────────┐        stdio / SSE        ┌──────────────────────┐
│   MCP Client         │ ◄──────────────────────► │   PaperAgent          │
│ (Claude Code,        │    JSON-RPC 2.0           │   MCP Server          │
│  Claude Desktop,     │                           │   (FastMCP 2.x)       │
│  VS Code, etc.)      │                           │                       │
└─────────────────────┘                           │  ┌─────────────────┐  │
                                                   │  │ Tools (6)       │  │
                                                   │  │ Resources (3)   │  │
                                                   │  │ Prompts (2)     │  │
                                                   │  └────────┬────────┘  │
                                                   └───────────┼───────────┘
                                                               │ 直接复用
                                                   ┌───────────▼───────────┐
                                                   │  SQLite + Chroma       │
                                                   │  + Paper Retrieval     │
                                                   │  + LLM Client          │
                                                   └───────────────────────┘
```

### 关键设计

- **不经过 FastAPI**：MCP Server 直接访问数据层（SQLite + Chroma + LLM），与 REST API 共享同一套代码但不经过 HTTP 层
- **stdio / SSE 双传输模式**：同一套 Tools/Resources/Prompts 代码，通过命令行参数切换传输协议，无需任何代码修改
- **装饰器注册**：Tools / Resources / Prompts 通过 `@mcp.tool()` 等装饰器声明式注册

### 代码结构

```
backend/app/mcp/
├── __init__.py                # 包入口，导出 mcp 实例
├── server/
│   ├── __init__.py            # 导出 mcp 实例
│   ├── __main__.py            # 入口：python -m app.mcp.server
│   ├── server.py              # FastMCP 实例创建 + main()
│   ├── tools.py               # MCP Tools (6 个)
│   ├── resources.py           # MCP Resources (3 个)
│   └── prompts.py             # MCP Prompts (2 个)
└── __pycache__/
```

### 启动流程

```
用户启动命令
  │
  ├── stdio 模式: python -m app.mcp.server
  └── SSE  模式: python -m app.mcp.server --transport sse --port 8002
  │
  ▼
__main__.py（入口包装）
  ├─► 1. 强制 sys.stdout/stderr UTF-8 编码（Windows 兼容）
  ├─► 2. Monkey-patch 禁用 chromadb posthog 遥测
  └─► 3. 调用 server.main()
        │
        ├─► 解析 --transport 参数（stdio / sse）
        │
        ├─► stdio 模式: 使用模块级 mcp 实例（已通过装饰器注册）
        └─► SSE  模式: 重建 mcp = FastMCP(host="0.0.0.0", port=8002)
        │
        ├─► import tools / resources / prompts → 装饰器注册到 mcp 实例
        │
        └─► mcp.run(transport="stdio" | "sse")
              │
              ├─ stdio: 监听 stdin/stdout，等待 JSON-RPC 消息
              └─ SSE:  启动 HTTP 服务器，监听 /sse 端点
```

> **Windows 兼容性**：`__main__.py` 中强制 `sys.stdout/stderr.reconfigure(encoding="utf-8")`，确保 JSON-RPC 协议的 UTF-8 要求在中文字符环境下不被破坏。同时禁用 chromadb 的 posthog 遥测以避免新版 posthog SDK 的 API 不兼容。

---

## MCP 能力清单

### Tools (6 个)

#### `search_papers` — 语义搜索论文

检索论文库中与查询语义相关的论文，返回匹配片段和相关性评分。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | `string` | 是 | 搜索查询（自然语言，语义匹配） |
| `top_k` | `int` | 否 | 返回数量，默认 6 |
| `year_from` | `int \| None` | 否 | 起始年份 |
| `year_to` | `int \| None` | 否 | 结束年份 |
| `domain` | `string \| None` | 否 | 领域筛选 |
| `tags` | `list[str] \| None` | 否 | 标签筛选 |
| `is_favorite` | `bool \| None` | 否 | 仅收藏 |

**返回**：匹配论文列表，含标题、作者、年份、相关片段、ID

**适用场景**：按主题、概念、研究问题查找论文（语义匹配，非关键词匹配）

**实现原理**：调用 `paper_retrieval_agent.search()`，底层使用 Chroma 向量检索 + SQLite 元数据筛选的组合策略。

---

#### `get_paper` — 获取论文完整信息

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `paper_id` | `string` | 是 | 论文 UUID |

**返回**：论文所有字段的 Markdown 格式输出，包括：
- 元数据：标题、作者、年份、领域、关键词、标签、收藏状态
- 内容：原文摘要、中文摘要、主要贡献、方法、实验与结果、局限性、结论

**适用场景**：需要完整了解某篇论文时，或对比多篇论文前需要加载详情

---

#### `list_papers` — 浏览筛选论文

按元数据条件筛选论文列表（**不做语义搜索**）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | `string \| None` | 否 | 关键词过滤（模糊匹配标题） |
| `year_from` | `int \| None` | 否 | 起始年份 |
| `year_to` | `int \| None` | 否 | 结束年份 |
| `domain` | `string \| None` | 否 | 领域 |
| `tags` | `list[str] \| None` | 否 | 标签 |
| `is_favorite` | `bool \| None` | 否 | 仅收藏 |

**返回**：论文列表（标题、作者、年份、领域、标签、收藏状态、ID）

**与 `search_papers` 的区别**：`list_papers` 做 SQLite 元数据过滤，`search_papers` 做 Chroma 语义向量检索。前者适合"列出 2024 年 CV 领域的论文"，后者适合"找到关于 attention mechanism 的论文"。

---

#### `ask_paper_qa` — AI 问答

基于论文库的 RAG 问答：检索相关论文 → LLM 综合生成答案 → 附带引用。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | `string` | 是 | 研究问题（支持跨论文对比、综述） |
| `top_k` | `int` | 否 | 检索论文数，默认 6 |

**返回**：AI 生成的回答 + 引用论文列表

**适用场景**：
- "这些论文中 attention 机制的主要改进方向有哪些？"
- "对比 A 和 B 的方法有什么不同"
- "这个领域的主要贡献是什么"

**实现原理**：语义检索 top-k 论文 → 取每篇的 snippet（400 字符）拼接为上下文 → 调用 LLM 生成答案（在 prompt 中要求内联引用 `[n]`）

---

#### `list_tags` — 列出所有标签

| 参数 | 无 |

**返回**：标签名及关联论文数

---

#### `export_papers` — 导出论文库

| 参数 | 无 |

**返回**：所有论文的结构化信息，Markdown 格式。每篇论文含标题、作者、年份、领域、关键词、摘要、贡献、方法、结果、结论。

---

### Resources (3 个)

Resources 以 URI 标识，客户端可按需读取（类似 REST GET）。

| Resource URI | 内容 |
|-------------|------|
| `paper://{paper_id}` | 论文完整结构化信息（Markdown 格式） |
| `paper://{paper_id}/summary` | 论文精简摘要：摘要 + 方法 + 贡献（每个字段 ≤500 字符），适合 LLM 上下文窗口 |
| `papers://list` | 所有论文的 ID \| 标题 \| 作者 \| 年份 \| 领域列表 |

**使用示例**（Claude Code 中自动处理）：

```
用户：读一下 paper://abc123 这篇论文
→ Claude 自动通过 resource 读取该论文的全部结构化信息
```

```
用户：对 paper://abc123 写一份评审
→ Claude 先用 paper_review prompt 构建指令，再通过 resource 读取论文
```

---

### Prompts (2 个)

Prompts 是预置的对话模板，引导 LLM 以特定方式使用工具和资源。

#### `paper_qa` — 研究问答模板

| 参数 | 类型 | 说明 |
|------|------|------|
| `question` | `string` | 研究问题 |

**生成的模板指令**：

```
You are a research assistant with access to a personal paper library.
The user wants to know: {question}

First, use the `search_papers` tool to find relevant papers in the library.
Then use `get_paper` to read the full details of the most relevant ones.
Finally, synthesize an answer that cites specific papers with their IDs.
If comparing papers, structure your response with clear comparison points.
If the library doesn't contain enough information, say so clearly.
```

---

#### `paper_review` — 论文评审模板

| 参数 | 类型 | 说明 |
|------|------|------|
| `paper_id` | `string` | 论文 UUID |

**生成的模板指令**：

```
Please review the following academic paper: **{title}**

Read the full paper details using the `paper://{paper_id}` resource
or the `get_paper` tool.

Then provide a structured review covering:
1. Summary: What problem does this paper address and what is the key idea?
2. Strengths: What are the main contributions and novel aspects?
3. Weaknesses/Limitations: What are the acknowledged or potential shortcomings?
4. Impact: How does this work relate to or advance the broader field?
5. Questions/Further Reading: What questions remain open?
```

---

## 外部使用指南

### 方式一：Claude Code（推荐）

在项目根目录创建 `.mcp.json`：

```json
{
  "mcpServers": {
    "paperagent": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "backend",
      "env": {
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

> **说明**：
> - `command` 可指定 Python 解释器的绝对路径（如 `C:\\ProgramData\\anaconda3\\python.exe`），也可直接用 `python`
> - `cwd` 相对路径基于 `.mcp.json` 所在目录（即项目根目录）
> - Claude Code 启动时会自动识别 `.mcp.json`，询问是否信任 MCP Server

**前提条件**：
1. 已安装 Python 依赖：`pip install -r backend/requirements.txt`
2. `data/paperagent.db` 已存在（通过后端导入过论文）
3. 环境变量 `DASHSCOPE_API_KEY` 已设置（用于 `ask_paper_qa` 工具）

**使用方法**：
1. 在 PaperAgent 项目目录启动 Claude Code
2. 首次启动时批准 MCP Server 的信任提示
3. 直接在对话中使用工具，例如：
   - "帮我搜索关于 transformer 的论文"
   - "列出我所有计算机视觉领域的论文"
   - "对比 paper_abc123 和 paper_def456 的方法"

---

### 方式二：Claude Desktop

在 Claude Desktop 的配置文件中添加（路径因平台而异）：

**Windows** — `%APPDATA%\Claude\claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "paperagent": {
      "command": "C:\\ProgramData\\anaconda3\\python.exe",
      "args": ["-m", "app.mcp.server"],
      "cwd": "D:\\Projects\\Agent\\PaperAgent\\backend",
      "env": {
        "PYTHONIOENCODING": "utf-8",
        "DASHSCOPE_API_KEY": "sk-xxxxxxxx"
      }
    }
  }
}
```

**macOS / Linux** — `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "paperagent": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/path/to/PaperAgent/backend",
      "env": {
        "DASHSCOPE_API_KEY": "sk-xxxxxxxx"
      }
    }
  }
}
```

配置完成后重启 Claude Desktop，在对话界面会出现锤子图标，点击可查看可用工具。

---

### 方式三：SSE HTTP 模式

用于远程客户端或 PaperAgent 自身前端接入：

```bash
cd backend
python -m app.mcp.server --transport sse --port 8002
```

服务启动后监听 `http://0.0.0.0:8002/sse`，任何 MCP SSE 客户端均可连接。

> PaperAgent 前端和后端通过 SSE 模式接入同一个 MCP Server，实现工具调用能力共享。

---

### 方式四：编程接入

```python
from app.mcp.server.server import mcp

# 使用 mcp 实例以编程方式调用工具
# （与 MCP 协议无关，直接调用底层函数）

# 列出所有已注册的工具
# tools 模块中的函数也可作为普通 Python 函数直接调用
from app.mcp.server.tools import search_papers, get_paper, ask_paper_qa

result = search_papers("transformer attention", top_k=5, year_from=2020)
paper = get_paper("some-paper-uuid")
answer = ask_paper_qa("What are the main contributions of these papers?")
```

---

## 调试与排查

### 验证 MCP Server 是否正常

```bash
cd backend
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | python -m app.mcp.server
```

预期输出包含 `"serverInfo":{"name":"paperagent-memory","version":"..."}`。

### 验证工具注册

```bash
cd backend
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python -m app.mcp.server
```

预期返回 6 个 tool 的完整 schema。

### 常见问题

#### `ImportError: cannot import name 'mcp'`

**原因**：MCP Server 代码中 `mcp` 实例在函数内部创建，而 `__init__.py` / 装饰器文件在模块级别 import `mcp`。

**解决**：已在 `server.py` 中将 `mcp = FastMCP(...)` 提升至模块级别。确保代码已更新到最新版本。

#### `ModuleNotFoundError: No module named 'app.mcp'`

**原因**：当前工作目录不是 `backend/`，Python 找不到 `app` 包。

**解决**：
- 确保 `cwd` 指向 `backend/` 目录
- 或在启动前 `cd backend`
- 或设置 `PYTHONPATH=./backend`

#### `Failed to reconnect` (Claude Code)

**原因**：Claude Code 无法连接到 MCP Server 进程。可能原因：
1. 首次启动时未批准 `.mcp.json` 中的 MCP Server（查看信任提示）
2. Python 路径不正确（`.mcp.json` 中的 `command` 字段）
3. 依赖缺失（运行 `pip install -r backend/requirements.txt`）
4. 数据库文件不存在（先通过后端导入论文）

**解决步骤**：
1. 先在终端验证 MCP Server 可独立启动（见上方验证命令）
2. 检查 `.mcp.json` 中 `command` 的 Python 路径是否正确
3. 在 Claude Code 中运行 `/mcp` 查看 MCP Server 状态
4. 如仍失败，尝试重启 Claude Code

#### Windows 下 MCP 通信乱码

**原因**：Windows 默认系统编码（如 cp936）与 MCP 协议要求的 UTF-8 不兼容。

**解决**：已在 `__main__.py` 中强制 `sys.stdout/stderr.reconfigure(encoding="utf-8")`，且 `.mcp.json` 中设置了 `PYTHONIOENCODING=utf-8` 环境变量。

#### chromadb posthog 崩溃

**原因**：新版 posthog Python SDK 的 `capture()` API 签名变更，与 chromadb 内置的遥测调用不兼容。

**解决**：已在 `__main__.py` 中通过 monkey-patch 禁用 chromadb 遥测。

---

## 技术实现要点

### 装饰器注册模式

```python
# server.py — 模块级 FastMCP 实例
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("paperagent-memory")

# tools.py — 通过装饰器自动注册
from app.mcp.server.server import mcp

@mcp.tool()
def search_papers(query: str, top_k: int = 6, ...) -> str:
    """Semantic search across the paper library."""
    ...
```

装饰器在 `import` 时自动将函数注册到 `mcp` 实例的工具列表中，无需手动注册。

### 双传输模式详解

PaperAgent MCP Server 完整支持 **stdio** 和 **SSE** 两种传输协议，共用一个代码入口，通过命令行参数切换：

```bash
# stdio 模式（默认）
python -m app.mcp.server

# SSE 模式
python -m app.mcp.server --transport sse --port 8002
```

#### stdio 模式

**工作原理**：MCP 客户端（Claude Code / Claude Desktop）通过 `subprocess` 启动 Python 进程，通过该进程的 **stdin / stdout** 管道进行 JSON-RPC 2.0 双向通信。

```
┌──────────────┐    spawn subprocess    ┌──────────────────┐
│ MCP Client   │ ◄──────────────────► │ Python Process    │
│ (Claude Code)│   stdin / stdout       │ (app.mcp.server) │
└──────────────┘   JSON-RPC 2.0         └──────────────────┘
```

**特点**：
- 进程生命周期与客户端绑定（客户端启动 → spawn 进程，客户端退出 → kill 进程）
- 零网络开销，通信完全在本地进行
- 每个客户端拥有独立的 MCP Server 进程实例
- 不需要端口配置，无防火墙/NAT 问题

**配置示例**（`.mcp.json` 或 `claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "paperagent": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "backend"
    }
  }
}
```

**客户端无需额外操作**：Claude Code / Desktop 自动根据配置启动和管理进程，用户只需在对话中直接使用 MCP 工具。

#### SSE 模式

**工作原理**：MCP Server 启动一个 HTTP 服务器，通过 **Server-Sent Events (SSE)** 端点进行通信。客户端通过 HTTP POST 发送 JSON-RPC 请求，通过 SSE 长连接接收响应和通知。

```
┌──────────────┐    HTTP POST /sse     ┌──────────────────────┐
│ MCP Client   │ ◄──────────────────► │ HTTP Server          │
│ (远程/前端)   │    SSE Event Stream   │ (0.0.0.0:8002)       │
└──────────────┘                       └──────────────────────┘
```

**特点**：
- 支持远程连接（跨网络、跨机器）
- 支持多个客户端同时连接
- 需要指定监听端口（默认 8002）
- PaperAgent 前端通过此模式接入，实现工具调用能力共享
- 服务进程独立运行，不与客户端生命周期绑定

**启动命令**：

```bash
python -m app.mcp.server --transport sse --port 8002
```

#### 两种模式的关键区别

| 维度 | stdio | SSE |
|------|-------|-----|
| 通信方式 | stdin/stdout 管道 | HTTP + Server-Sent Events |
| 连接范围 | 仅本地进程 | 本地 + 远程网络 |
| 多客户端 | 不支持（单进程单连接） | 支持（HTTP 服务器多连接） |
| 端口占用 | 无 | 需要指定端口 |
| 进程管理 | 客户端 spawn/kill | 用户手动启停 |
| 适用场景 | Claude Code/Desktop 本地集成 | 远程接入、前端自消费、Docker 部署 |
| 网络依赖 | 无 | 需要网络可达 |

#### 实现细节

**代码位置**: `backend/app/mcp/server/server.py:24-56`

```python
# 模块级实例 — stdio 模式使用（装饰器在 import 时注册到此实例）
mcp = FastMCP("paperagent-memory")

def main():
    global mcp
    args = parser.parse_args()

    # SSE 模式：重建实例（需要绑定 host/port）
    if args.transport == "sse":
        mcp = FastMCP("paperagent-memory", host="0.0.0.0", port=args.port)

    # import 触发装饰器注册 — 注册到当前 mcp 实例（stdio 或 SSE）
    from app.mcp.server import tools, resources, prompts

    # 启动对应的传输协议
    if args.transport == "sse":
        mcp.run(transport="sse")   # 启动 HTTP 服务器
    else:
        mcp.run(transport="stdio") # 监听 stdin/stdout
```

**关键设计点**：
1. **装饰器注册时机**：`import tools/resources/prompts` 必须在 `mcp` 实例确定之后执行。stdio 模式使用模块级 `mcp`，SSE 模式先 `global mcp` 覆盖后再 import，确保装饰器始终注册到正确的实例
2. **Windows UTF-8 兼容**：`run_mcp_server.py` 包装脚本在调用 `main()` 前强制 `sys.stdout/stderr.reconfigure(encoding="utf-8")`，确保 JSON-RPC 协议不因中文系统编码（cp936）而乱码
3. **chromadb 遥测屏蔽**：monkey-patch posthog 防止 chromadb 内置遥测在新版 posthog SDK 下崩溃

---

## 相关文件

| 文件 | 职责 |
|------|------|
| `backend/app/mcp/server/server.py` | FastMCP 实例、主函数 |
| `backend/app/mcp/server/__main__.py` | 入口 + Windows 兼容 |
| `backend/app/mcp/server/tools.py` | 6 个 MCP Tool |
| `backend/app/mcp/server/resources.py` | 3 个 MCP Resource |
| `backend/app/mcp/server/prompts.py` | 2 个 MCP Prompt |
| `.mcp.json` | Claude Code MCP 配置 |
| `backend/run_mcp_server.py` | 旧版入口（已弃用，保留备用） |

---

## 扩展指南

### 新增 Tool

在 `backend/app/mcp/server/tools.py` 中添加：

```python
from app.mcp.server.server import mcp

@mcp.tool()
def my_new_tool(param1: str, param2: int = 10) -> str:
    """Description of what this tool does (shown to the LLM)."""
    # 实现逻辑
    return result
```

类型注解自动生成 JSON Schema。重启 MCP Server 后即可使用。

### 新增 Resource

在 `backend/app/mcp/server/resources.py` 中添加：

```python
from app.mcp.server.server import mcp

@mcp.resource("myresource://{id}")
def my_resource(id: str) -> str:
    """Resource description."""
    return f"Content for {id}"
```

### 新增 Prompt

在 `backend/app/mcp/server/prompts.py` 中添加：

```python
from app.mcp.server.server import mcp

@mcp.prompt()
def my_prompt(topic: str) -> str:
    """Prompt description."""
    return f"Please help me with: {topic}"
```
