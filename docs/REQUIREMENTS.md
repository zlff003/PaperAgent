# PaperAgent — 多Agent科研论文管理与归纳助手 需求文档

> **版本**: v2.0 | **日期**: 2026-05-26 | **状态**: 待评审

---

## 1. 项目概述

### 1.1 项目背景

科研工作者在日常工作中需要管理大量论文，面临的核心痛点：
- **海量论文难以管理**：论文数量日益增长，缺乏高效的检索、分类和筛选手段。
- **关键信息提取耗时**：手动阅读全文并提炼核心贡献、方法、结论等信息效率低下。
- **跨论文信息整合困难**：写 related work 或做调研时，需要横向对比多篇论文的方法、结果等，依赖人工记忆和来回翻阅。

### 1.2 项目目标

构建一个**以论文管理与内容归纳为核心**的多 Agent 论文助手。用户上传 PDF 后，系统自动调用 LLM 提取论文的题目、作者、摘要、主要贡献、方法/模型、实验结果、局限性、结论等结构化信息；用户可对论文进行搜索、筛选、分组、标签和收藏管理。对话问答功能基于所有论文的结构化归纳结果，支持跨论文对比与综述。

### 1.3 目标用户

- 单用户桌面场景（本地运行，无需多用户系统）
- 典型用户：研究生、博士后、科研工程师

---

## 2. 技术栈

| 层次 | 技术 | 用途 |
|------|------|------|
| **LLM** | 阿里百炼平台 API | 结构化信息提取、对话生成 |
| **Embedding** | 阿里百炼 text-embedding API | 文本向量化（摘要+方法+结论等拼接） |
| **Agent 编排** | LangGraph | 多 Agent 工作流编排 |
| **Agent 框架** | LangChain | Agent 工具链、Prompt 模板、RAG Chain |
| **RAG** | LangChain RAG + Chroma | 基于结构化摘要的语义检索 |
| **向量数据库** | Chroma | 本地持久化向量存储 |
| **关系数据库** | SQLite | 论文管理、对话历史 |
| **后端框架** | FastAPI + Pydantic | REST API + 数据校验 |
| **外部集成** | MCP (Model Context Protocol) | 将论文库记忆能力封装为标准 MCP 协议，可挂载到 Claude 等任意 MCP 客户端 |
| **前端** | React (Vite) | 论文管理界面 + 对话界面 |
| **容器化** | Docker + Docker Compose | 一键启动所有组件 |

---

## 3. 功能需求

### 3.1 论文摄入 (Paper Ingestion)

**F1.1 PDF 上传与结构化提取**
- 用户通过前端上传 PDF 论文文件
- 后端解析 PDF 纯文本，调用 LLM 分两阶段提取结构化信息：

  **阶段一 — 基础信息提取：**
  - 题目 (title)
  - 作者 (authors)
  - 发表年份 (year)

  **阶段二 — 深度内容提取：**
  - 摘要 (abstract)：保留原文摘要，同时可由 LLM 生成中文改写版本
  - 主要贡献 (contributions)
  - 方法/模型 (methods)
  - 实验与结果 (results)
  - 局限性 (limitations)
  - 结论 (conclusion)
  - 关键词/标签 (keywords)：LLM 自动提取

- 原始 PDF 文件保存在本地 `data/papers/` 目录，支持下载
- 不保留全文正文，仅存储 LLM 提取后的结构化结果
- 提取的文本字段拼接后 embedding 存入 Chroma `paper_summaries` collection
- 论文元数据及所有提取字段存入 SQLite `papers` 表

**F1.2 重新提取**
- 支持对已上传论文手动触发"重新提取"，适用于提取结果不理想的情况

### 3.2 论文管理 (Paper Management)

**F2.1 论文列表与浏览**
- 首页展示已上传论文列表，支持卡片视图和列表视图
- 每条展示：标题、作者、年份、关键词标签、领域分类、收藏状态

**F2.2 搜索与筛选**
- 关键词搜索（模糊匹配标题、作者、关键词字段）
- 语义搜索（基于 Chroma 向量检索，匹配论文摘要/方法/结论等）
- 筛选条件：年份范围、领域分类、自定义标签、收藏状态

**F2.3 分组与标签**
- 用户可创建自定义标签（如"读过"、"待精读"、"Related Work 素材"）
- 论文可关联多个标签
- 支持按领域(domain)自动分类（LLM 提取时自动判定，用户可修改）

**F2.4 收藏**
- 支持收藏/取消收藏论文
- 可按收藏状态筛选

**F2.5 论文详情**
- 结构化展示论文所有提取字段
- 提供原始 PDF 下载链接

**F2.6 论文删除**
- 支持删除论文（同时清理 PDF 文件、SQLite 记录、Chroma 向量、关联标签）

### 3.3 对话问答 (Research QA)

**F3.1 自由提问**
- 对话界面输入自然语言问题
- 支持对话历史上下文（同一会话内的多轮对话）

**F3.2 论文检索**
- 系统在 Chroma `paper_summaries` 中语义检索与问题相关的论文结构化摘要
- 返回 top-k 篇最相关的论文及其匹配的字段内容

**F3.3 答案生成**
- LLM 结合检索到的论文结构化信息生成回答
- 支持跨论文对比（如"A 和 B 的方法有什么不同？"）
- 支持综述类问题（如"这些论文在 XX 领域的主要贡献是什么？"）
- 回答中引用论文时附带标题、作者、年份，可点击跳转到论文详情页

**F3.4 对话历史**
- 保存每次问答记录到 SQLite `conversations` 表
- 支持查看历史对话

### 3.4 系统配置

**F4.1 阿里百炼 API 配置**
- 通过环境变量或配置文件设置 API Key、模型名称、Embedding 模型名称

**F4.2 数据管理**
- 支持导出所有论文的结构化信息（JSON/Markdown 格式）
- 支持清空/重置数据库

---

## 4. 多 Agent 架构设计

系统包含 **3 个 Agent**，由 **LangGraph** 编排协作：

```
┌─────────────────────────────────────────────────────────┐
│                     LangGraph Orchestrator               │
│                                                          │
│  ┌──────────────────┐  ┌──────────────────┐             │
│  │ Paper            │  │ Paper            │             │
│  │ Ingestion        │  │ Retrieval        │             │
│  │ Agent            │  │ Agent            │             │
│  └────────┬─────────┘  └────────┬─────────┘             │
│           │                     │                        │
│           └─────────────────────┼────────────────────────│
│                                 │                        │
│                          ┌──────┴───────┐               │
│                          │ Research QA  │               │
│                          │ Agent        │               │
│                          └──────────────┘               │
└─────────────────────────────────────────────────────────┘
```

### Agent 1: Paper Ingestion Agent

| 项目 | 说明 |
|------|------|
| **触发** | 用户上传 PDF 或手动触发重新提取 |
| **输入** | PDF 文件路径 |
| **职责** | 1. 调用 PDF 解析器提取全文纯文本<br>2. 分两阶段调用 LLM：先提取基础信息（标题、作者、年份），再提取深度内容（摘要、贡献、方法、结果、局限性、结论、关键词）<br>3. LLM 自动判定领域分类<br>4. 拼接摘要+方法+贡献+结论等字段，调用 embedding API<br>5. 写入 SQLite + Chroma |
| **工具** | PDF 解析器、百炼 LLM API、百炼 Embedding API、SQLite Client、Chroma Client |
| **输出** | `paper_id`，入库确认及提取结果 |

### Agent 2: Paper Retrieval Agent

| 项目 | 说明 |
|------|------|
| **触发** | 用户搜索、筛选、或 QA Agent 调用 |
| **输入** | 查询文本 / 筛选条件 |
| **职责** | 1. 元数据筛选（SQLite 查询：年份、领域、标签、收藏等）<br>2. 语义检索（Chroma 向量检索 matching 结构化摘要）<br>3. 合并结果，按相关度排序，返回匹配论文及对应片段 |
| **工具** | SQLite Client、Chroma Client、百炼 Embedding API |
| **输出** | `List[(paper_id, paper_metadata, matched_snippet, relevance_score)]` |

### Agent 3: Research QA Agent

| 项目 | 说明 |
|------|------|
| **触发** | 用户提交问题 |
| **输入** | 用户问题 + 对话历史 |
| **职责** | 1. 调用 Paper Retrieval Agent 获取相关论文上下文<br>2. 构建 Prompt（含论文结构化信息 + 跨论文对比/综述要求）<br>3. 调用百炼 LLM 生成回答<br>4. 格式化论文引用<br>5. 保存对话记录 |
| **工具** | Paper Retrieval Agent（子 Agent 调用）、百炼 Chat API |
| **输出** | `(answer_text, cited_papers: List[Paper])` |

### LangGraph 工作流

```
START
  │
  ▼
[Router] ── 请求类型判断
  │
  ├── "上传/重新提取" ──▶ Paper Ingestion Agent ──▶ END
  │
  ├── "搜索/筛选" ──▶ Paper Retrieval Agent ──▶ END
  │
  └── "提问" ──▶ Paper Retrieval Agent
                      │
                      ▼
                 Context Builder (去重 + 排序 + 截断)
                      │
                      ▼
                 Research QA Agent (LLM 生成)
                      │
                      ▼
                 Citation Formatter
                      │
                      ▼
                     END
```

---

## 5. 数据模型

### 5.1 SQLite Schema

```sql
-- 论文表
CREATE TABLE papers (
    id            TEXT PRIMARY KEY,       -- UUID
    title         TEXT NOT NULL,
    authors       TEXT,                   -- JSON array string
    year          INTEGER,
    abstract      TEXT,                   -- 原文摘要
    abstract_zh   TEXT,                   -- LLM 改写的中文摘要
    contributions TEXT,                   -- 主要贡献
    methods       TEXT,                   -- 方法/模型
    results       TEXT,                   -- 实验与结果
    limitations   TEXT,                   -- 局限性
    conclusion    TEXT,                   -- 结论
    keywords      TEXT,                   -- JSON array string (LLM 自动提取)
    domain        TEXT,                   -- 领域分类 (LLM 自动判定)
    file_path     TEXT NOT NULL,          -- PDF 本地路径
    page_count    INTEGER,
    is_favorite   INTEGER DEFAULT 0,      -- 收藏状态
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 用户标签表
CREATE TABLE tags (
    id   TEXT PRIMARY KEY,                -- UUID
    name TEXT NOT NULL UNIQUE
);

-- 论文-标签关联表
CREATE TABLE paper_tags (
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    tag_id   TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (paper_id, tag_id)
);

-- 对话记录表
CREATE TABLE conversations (
    id          TEXT PRIMARY KEY,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    cited_papers TEXT,                    -- JSON: [paper_id, ...]
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 5.2 Chroma Collections

| Collection | 存储内容 | 元数据字段 |
|------------|---------|-----------|
| `paper_summaries` | 拼接后的论文结构化摘要（abstract + contributions + methods + results + conclusion） | `paper_id`, `title`, `authors`, `year`, `domain`, `keywords` |

### 5.3 Pydantic Models (核心)

```python
class PaperCreate(BaseModel):
    """上传 PDF 后由 Ingestion Agent 填充"""
    title: str
    authors: list[str]
    year: int | None
    abstract: str | None
    abstract_zh: str | None
    contributions: str | None
    methods: str | None
    results: str | None
    limitations: str | None
    conclusion: str | None
    keywords: list[str]
    domain: str | None

class Paper(PaperCreate):
    id: str
    file_path: str
    page_count: int
    is_favorite: bool
    tags: list[str]
    created_at: datetime
    updated_at: datetime

class PaperUpdate(BaseModel):
    """用户可修改的字段"""
    domain: str | None
    is_favorite: bool | None
    tags: list[str] | None

class SearchQuery(BaseModel):
    query: str | None               # 关键词或语义搜索文本
    year_from: int | None
    year_to: int | None
    domain: str | None
    tags: list[str] | None
    is_favorite: bool | None

class QAResponse(BaseModel):
    answer: str
    cited_papers: list[PaperBrief]

class PaperBrief(BaseModel):
    id: str
    title: str
    authors: list[str]
    year: int | None
    snippet: str  # 匹配到的结构化摘要片段
```

---

## 6. API 设计

### 6.1 Paper API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/papers/upload` | 上传 PDF，触发 Ingestion Agent，返回 `paper_id` + 提取结果 |
| `POST` | `/api/papers/{id}/re-extract` | 重新触发 LLM 提取 |
| `GET` | `/api/papers` | 获取论文列表（支持分页、筛选、排序） |
| `GET` | `/api/papers/{id}` | 获取论文详情（所有结构化字段） |
| `GET` | `/api/papers/{id}/download` | 下载原始 PDF 文件 |
| `PUT` | `/api/papers/{id}` | 编辑论文管理字段（domain, is_favorite, tags） |
| `DELETE` | `/api/papers/{id}` | 删除论文及关联数据 |

### 6.2 Search API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/search` | 综合搜索（关键词筛选 + 语义搜索） |
| `GET` | `/api/search/semantic?q=...` | 纯语义搜索（快捷方式） |

### 6.3 Tag API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tags` | 获取用户所有标签 |
| `POST` | `/api/tags` | 创建新标签 |
| `DELETE` | `/api/tags/{id}` | 删除标签 |

### 6.4 QA API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/qa/ask` | 提交问题，返回回答 + 引用论文列表 |
| `GET` | `/api/qa/history` | 获取历史问答记录 |
| `GET` | `/api/qa/history/{id}` | 获取某次问答详情 |

### 6.5 System API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/export` | 导出所有论文结构化信息为 Markdown |

---

## 7. 前端页面设计

### 页面结构

```
┌────────────────────────────────────────────────────┐
│  Sidebar                         Main Content      │
│  ┌─────────────┐                                   │
│  │ 📁 Papers   │   ┌─────────────────────────────┐ │
│  │   + 上传     │   │                             │ │
│  │             │   │    Paper List / Detail       │ │
│  │ 全部论文    │   │    Chat View                 │ │
│  │ 已收藏      │   │                             │ │
│  │             │   │                             │ │
│  │ ─────────  │   │                             │ │
│  │ 🏷 标签     │   │                             │ │
│  │  标签A(3)  │   │                             │ │
│  │  标签B(5)  │   │                             │ │
│  │             │   │                             │ │
│  │ ─────────  │   │                             │ │
│  │ 💬 Chat    │   │                             │ │
│  └─────────────┘   └─────────────────────────────┘ │
└────────────────────────────────────────────────────┘
```

### 页面 1: 论文库 (`/papers`)
- 论文卡片网格或列表展示（标题、作者、年份、关键词标签、领域、收藏状态）
- 顶部搜索栏（关键词搜索）+ 筛选面板（年份、领域、标签、收藏）
- 上传按钮（拖拽/点击上传 PDF）
- 侧边栏快捷筛选：全部 / 已收藏 / 按标签

### 页面 2: 论文详情 (`/papers/:id`)
- 结构化信息展示面板：
  - 元数据区：标题、作者、年份、DOI、领域 —— [下载PDF] [收藏★] 按钮
  - 摘要区：原文摘要 / 中文改写（可切换 tab）
  - 主要内容区：主要贡献、方法/模型、实验与结果、局限性、结论
  - 关键词标签（可点击筛选同标签论文）
- 侧边栏：同作者 / 同领域 / 同标签论文推荐
- "重新提取"按钮（手动触发 LLM 重新分析）

### 页面 3: AI 对话 (`/chat`)
- 类似 ChatGPT 的对话界面
- 用户输入问题，AI 综合论文库回答
- 回答中内嵌论文引用（标题 + 作者 + 年份），点击跳转到论文详情页
- 左侧可查看历史对话列表

---

## 8. MCP 集成设计

PaperAgent 将自身的**论文库记忆能力**封装为标准的 MCP (Model Context Protocol) Server，可挂载到 Claude Desktop、Claude Code 等任意 MCP 客户端，PaperAgent 自身也可接入使用。

### 8.1 架构

```
┌─────────────────────┐        stdio / SSE        ┌──────────────────────┐
│   MCP Client         │ ◄──────────────────────► │   PaperAgent          │
│ (Claude Desktop,     │                           │   MCP Server          │
│  Claude Code, etc.)  │                           │                       │
└─────────────────────┘                           │  ┌─────────────────┐  │
                                                   │  │ Tools (6)       │  │
                                                   │  │ Resources (3)   │  │
                                                   │  │ Prompts (2)     │  │
                                                   │  └────────┬────────┘  │
                                                   └───────────┼───────────┘
                                                               │ 复用
                                                   ┌───────────▼───────────┐
                                                   │  SQLite + Chroma + LLM │
                                                   └───────────────────────┘
```

### 8.2 MCP Tools

| Tool | 参数 | 功能 |
|------|------|------|
| `search_papers` | `query`, `top_k`, `year_from`, `year_to`, `domain`, `tags`, `is_favorite` | 语义搜索论文库，返回匹配论文及相关性片段 |
| `get_paper` | `paper_id` | 获取论文完整结构化信息 |
| `list_papers` | `query`, `year_from`, `year_to`, `domain`, `tags`, `is_favorite` | 按条件筛选论文列表 |
| `ask_paper_qa` | `question`, `top_k` | 基于论文库的 AI 问答（检索 → LLM 生成 → 返回答案及引用） |
| `list_tags` | — | 列出所有标签及关联论文数 |
| `export_papers` | — | 导出所有论文结构化信息为 Markdown |

### 8.3 MCP Resources

| Resource URI | 内容 |
|-------------|------|
| `paper://{paper_id}` | 论文完整结构化信息（Markdown） |
| `paper://{paper_id}/summary` | 论文精简摘要（摘要+方法+贡献，适合 LLM 上下文） |
| `papers://list` | 所有论文的 ID + 标题 + 年份列表 |

### 8.4 MCP Prompts

| Prompt | 参数 | 用途 |
|--------|------|------|
| `paper_qa` | `question` | 构造"基于论文库回答问题"的对话模板 |
| `paper_review` | `paper_id` | 构造"对指定论文进行评审/总结"的对话模板 |

### 8.5 启动方式

**stdio 模式**（挂载到 Claude Desktop / Claude Code）：

```json
{
  "mcpServers": {
    "paperagent": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/path/to/PaperAgent/backend",
      "env": { "PYTHONPATH": ".", "DASHSCOPE_API_KEY": "..." }
    }
  }
}
```

**SSE HTTP 模式**（远程客户端或 PaperAgent 自身接入）：

```bash
python -m app.mcp.server --transport sse --port 8002
```

> **注意**：MCP Server 直接复用现有数据层（SQLite + Chroma + LLM Client），不经过 FastAPI 路由层。

---

## 9. 项目目录结构

```
PaperAgent/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── papers.py          # Paper API 路由
│   │   │   ├── search.py          # Search API 路由
│   │   │   ├── tags.py            # Tag API 路由
│   │   │   └── qa.py              # QA API 路由
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── paper_ingestion.py # Agent 1: 论文摄入与LLM提取
│   │   │   ├── paper_retrieval.py # Agent 2: 论文检索
│   │   │   └── research_qa.py     # Agent 3: 问答生成
│   │   ├── graph/
│   │   │   ├── __init__.py
│   │   │   └── workflow.py        # LangGraph 工作流编排
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── paper.py           # Pydantic models
│   │   │   └── conversation.py
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── sqlite.py          # SQLite CRUD
│   │   │   └── chroma.py          # Chroma 向量操作
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py          # 配置管理（环境变量）
│   │   │   └── llm.py             # 百炼 API 封装
│   │   ├── mcp/
│   │   │   ├── __init__.py
│   │   │   └── server/
│   │   │       ├── __init__.py
│   │   │       ├── server.py           # MCP Server 主体 + FastMCP 实例
│   │   │       ├── tools.py            # MCP Tool 实现
│   │   │       ├── resources.py        # MCP Resource 实现
│   │   │       └── prompts.py          # MCP Prompt 模板
│   │   └── main.py                # FastAPI 入口
│   ├── tests/
│   │   ├── test_papers.py
│   │   ├── test_search.py
│   │   └── test_qa.py
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── api/                   # API 调用封装
│   │   └── App.tsx
│   ├── Dockerfile
│   └── package.json
├── data/                          # 运行时数据（挂载卷）
│   ├── papers/                    # PDF 文件存储
│   └── chroma/                    # Chroma 持久化数据
├── docker-compose.yml
├── .env.example
└── REQUIREMENTS.md                # 本文档
```

---

## 10. Docker 部署方案

### 10.1 容器组成

| 服务 | 镜像 | 端口 |
|------|------|------|
| `backend` | 自建 (FastAPI + uvicorn) | 8000 |
| `frontend` | 自建 (React + nginx) | 3000 |
| `chroma` | `chromadb/chroma` | 8001 |

### 10.2 docker-compose.yml 概览

```yaml
services:
  backend:
    build: ./backend
    ports: ["8000:8000"]
    volumes: ["./data:/app/data"]
    environment:
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - CHROMA_HOST=chroma
      - CHROMA_PORT=8001
    depends_on: [chroma]

  frontend:
    build: ./frontend
    ports: ["3000:80"]
    depends_on: [backend]

  chroma:
    image: chromadb/chroma
    ports: ["8001:8001"]
    volumes: ["./data/chroma:/chroma/chroma"]
```

### 10.3 启动方式

```bash
# 1. 配置 API Key
cp .env.example .env
# 编辑 .env 填入 DASHSCOPE_API_KEY

# 2. 一键启动
docker compose up -d

# 3. 访问
# 前端: http://localhost:3000
# API 文档: http://localhost:8000/docs
```

---

## 11. 非功能性需求

### 11.1 性能
- PDF 文本提取 + LLM 结构化分析：单篇论文 < 60s（30 页以内，含两阶段 LLM 调用）
- 语义搜索延迟：< 3s
- 问题回答延迟：< 15s（含检索 + LLM 生成）
- 前端论文列表加载：< 1s

### 11.2 可靠性
- PDF 文本提取失败时返回明确错误提示，不崩溃
- LLM 提取字段缺失时（如 PDF 中无明确 limitations），对应字段标记为 null
- 百炼 API 不可用时返回友好降级提示
- Chroma 持久化保证数据不丢失

### 11.3 安全性
- API Key 仅通过环境变量注入，不硬编码
- 前端 nginx 配置 CORS 仅允许本地访问
- SQLite 数据库本地存储，无网络暴露

---

## 12. 开发阶段规划

| 阶段 | 内容 | 预计产出 |
|------|------|---------|
| **Phase 1: 基础架构** | FastAPI 骨架、SQLite CRUD、Chroma 集成、百炼 API 封装 | 可启动的后端骨架 |
| **Phase 2: 论文摄入** | PDF 文本提取、LLM 结构化分析（两阶段 Prompt）、embedding 入库、Paper API | 上传论文 → LLM 提取 → 可查询 |
| **Phase 3: 论文管理** | 搜索/筛选、标签 CRUD、收藏、论文列表/详情 API | 完整的论文管理后端 |
| **Phase 4: Agent + QA** | 3 个 Agent 实现、LangGraph 编排、QA API | 提问 → 检索 → 综合回答 |
| **Phase 5: 前端** | React 页面（论文库、详情、对话、标签管理） | 完整 UI |
| **Phase 6: MCP + 集成** | PaperAgent MCP Server（Tools/Resources/Prompts）、导出功能、Docker Compose | 完整可交付系统 |
| **Phase 7: 测试** | 前后端测试用例编写 | 测试覆盖 |

---

## 13. 变更记录

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-05-26 | 初版：标注驱动的长期记忆论文助手 |
| v2.0 | 2026-05-26 | 重构：去除标注系统，转向 LLM 结构化提取 + 论文管理与归纳 |

---

> **文档维护**: 本文档随项目迭代持续更新。架构/API 变更需同步修改本文档。