# PaperAgent 项目文档

## 概览

PaperAgent 是一个本地运行的多 Agent 论文管理与归纳系统。核心功能包括：PDF 上传与结构化信息提取、基于结构化摘要的语义检索、基于检索结果的研究型问答（QA）、以及将论文库能力以 MCP（Model Context Protocol）暴露给外部客户端。

## 技术栈（代码中使用）
- 后端：FastAPI（`backend/app`）
- 多 Agent：LangGraph + 自定义 Agent 封装（位于 `backend/app/agents`）
- LLM & Embeddings：阿里百炼兼容（DashScope）通过 OpenAI 兼容接口；LangChain 封装用于结构化输出
- 向量存储：Chroma（优先使用服务端 HttpClient，否则本地 PersistentClient；提供本地 JSON 回退）
- 关系型数据库：SQLite（`backend/app/db/sqlite.py`）
- MCP：使用 `mcp.server.fastmcp` 在 `backend/app/mcp/server` 下实现 Tools/Resources/Prompts
- 前端：React + Vite（位于 `frontend/`，未在本文具体展开）

## 项目运行（快速指南）

1. 在项目根或 `backend` 下准备环境变量（`.env`）：

   - `DASHSCOPE_API_KEY`：百炼 API Key
   - 可选：`DASHSCOPE_CHAT_MODEL`, `DASHSCOPE_EMBEDDING_MODEL`, `CHROMA_HOST`, `CHROMA_PORT`

2. 启动后端（开发模式）：

```
cd backend
# 推荐使用虚拟环境并安装 requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

3. 启动 MCP Server（可被 Claude Desktop 等客户端挂载）：

```
python -m app.mcp.server --transport sse --port 8002
# 或 stdio 模式（默认）以便直接挂载至本地 MCP 客户端
python -m app.mcp.server
```

4. （可选）使用 `docker compose up` 按项目中已有的 `docker-compose.yml` 启动整套服务（Chroma/后端/前端）。

## 架构与组件说明

高层组件：
- FastAPI 后端（入口 `backend/app/main.py`）负责路由聚合、生命周期启动（`parse_queue`）与 CORS 设置。
- Agents：
  - `PaperIngestionAgent` (`backend/app/agents/paper_ingestion.py`)：处理 PDF 上传、两阶段 LLM 提取（基础信息 + 深度内容）、写入 SQLite、生成并写入向量索引（Chroma）。使用 PyMuPDF（fitz）做文本抽取；使用 LangChain 的 `ChatOpenAI.with_structured_output` 生成结构化输出模型 `PaperBasicInfo` / `PaperDeepInfo`。
  - `PaperRetrievalAgent` (`backend/app/agents/paper_retrieval.py`)：封装语义检索（Chroma）与元数据检索（SQLite），提供同步/异步接口，合并和过滤结果。
  - `ResearchQAAgent` (`backend/app/agents/research_qa.py`)：基于项目内的 LangGraph `qa_graph` 执行 QA 流程，返回 `QAResponse`（含引用论文列表）。
- 数据层：
  - `SQLiteStore` (`backend/app/db/sqlite.py`)：实现论文、标签、对话与解析任务的 CRUD、迁移、查询与导出；包含解析任务队列表 `parse_tasks`。
  - `VectorStore` (`backend/app/db/chroma.py`)：对接 Chroma（HttpClient 或 PersistentClient），并提供本地 JSON 回退机制；使用 `app.core.llm.llm_client.embed_texts` 获取 embedding（embed 调用优先使用 DashScope API，否则降级为本地 hash embedding）。
- LLM 封装：
  - `app.core.llm.DashScopeClient`：包含 `chat()` 和 `embed_texts()` 的向后兼容实现以及 `local_embedding()` 作为回退。
  - `app.core.langchain_factory`：为 LangChain 提供 `get_chat_model()` 与 `get_embeddings()`，以 OpenAI 兼容方式调用 DashScope。
- MCP 层（`backend/app/mcp/server`）：
  - Tools: `search_papers`, `get_paper`, `list_papers`, `ask_paper_qa`, `list_tags`, `export_papers`
  - Resources: `paper://{paper_id}`, `paper://{paper_id}/summary`, `papers://list`
  - Prompts: `paper_qa`, `paper_review`

## 数据模型（代码级）

- Pydantic 模型：定义在 `backend/app/models/paper.py` 与 `backend/app/models/conversation.py`，主要模型：
  - `Paper`, `PaperCreate`, `PaperDeepInfo`, `PaperBrief`, `SearchQuery`, `ParseStatus`, `Tag`
  - `QARequest`, `QAResponse`, `Conversation`

- SQLite 模式（由 `SQLiteStore.init()` 创建，关键信息）：
  - `papers` 表：id, title, authors (JSON), year, abstract, abstract_zh, contributions, methods, results, limitations, conclusion, keywords (JSON), domain, file_path, page_count, is_favorite, parse_status, parse_progress, parse_step, parse_error, parsed_at, created_at, updated_at
  - `tags`, `paper_tags`（论文-标签关联）
  - `conversations`（对话历史）
  - `parse_tasks`（后台 PDF 解析任务队列）

## API 端点（摘自代码）

所有 API 默认前缀为 `/api`（`app.core.config.settings.api_prefix`）：

- Paper API (`backend/app/api/papers.py`)
  - `POST /api/papers/upload`：上传 PDF 并触发入库与异步解析（返回 Paper 对象）
  - `GET /api/papers`：分页/筛选查询论文（query/year/domain/tags/is_favorite）
  - `GET /api/papers/{paper_id}`：获取论文详情
  - `GET /api/papers/{paper_id}/parse-status`：获取解析进度
  - `POST /api/papers/{paper_id}/re-extract`：重新触发解析
  - `GET /api/papers/{paper_id}/download`：下载原始 PDF
  - `PUT /api/papers/{paper_id}`：更新可修改字段（domain/is_favorite/tags）
  - `DELETE /api/papers/{paper_id}`：删除论文（包括文件与向量索引）

- Search API (`backend/app/api/search.py`)
  - `POST /api/search`：综合检索（语义 + 元数据）
  - `GET /api/search/semantic?q=...`：纯语义检索

- QA API (`backend/app/api/qa.py`)
  - `POST /api/qa/ask`：提交问题，返回 AI 回答与引用论文
  - `GET /api/qa/history`：获取历史对话列表
  - `GET /api/qa/history/{conversation_id}`：获取某次对话详情
  - `DELETE /api/qa/history/{conversation_id}`：删除历史记录

- Tags API (`backend/app/api/tags.py`)
  - `GET /api/tags`：列出标签
  - `POST /api/tags`：创建标签
  - `DELETE /api/tags/{tag_id}`：删除标签

- System API (`backend/app/api/system.py`)
  - `GET /api/health`：健康检查
  - `POST /api/export`：导出所有论文为 Markdown 文本
  - `POST /api/reset`：重置（清空）数据库与向量存储

## Agent 工作流（实现细节）

- 文档中实现的“摄入”流程：
  1. 用户通过 `POST /api/papers/upload` 上传 PDF。
  2. `PaperIngestionAgent.ingest_upload()` 保存 PDF 到 `data/papers/{uuid}.pdf`，写入 SQLite 初始元数据，并将解析任务放入 `parse_queue`。
  3. 后台 worker (`parse_queue`) 调用 `PaperIngestionAgent.process_paper()`：
     - 使用 PyMuPDF 提取全文文本（截断至一定长度）
     - 两阶段调用 LLM：先 `PaperBasicInfo`，再 `PaperDeepInfo`（使用 LangChain 的结构化输出 JSON schema）
     - 归一化字段并写回 SQLite
     - 拼接摘要/方法/贡献/结论等字段生成向量描述，调用 `vector_store.upsert()` 写入 Chroma（或本地 JSON 回退）

- 检索与问答：
  - `PaperRetrievalAgent` 提供同步与异步检索，支持语义检索与元数据过滤的并行合并。
  - QA 流由 `qa_graph`（LangGraph）编排，`ResearchQAAgent` 提供同步结果和流式输出接口。

## MCP 集成（内置记忆能力）

- MCP server 入口：`python -m app.mcp.server`（见 `backend/app/mcp/server/server.py`）。
- 已注册的 Tools（可直接被 MCP 客户端调用）：
  - `search_papers(query, top_k, ...)`：语义检索并返回文本化结果
  - `get_paper(paper_id)`：获取结构化论文详情（Markdown）
  - `list_papers(...)`：按元数据筛选并列表化
  - `ask_paper_qa(question, top_k)`：调用 QA Agent 并返回回答（含引用）
  - `list_tags()` / `export_papers()` 等辅助工具
- 已注册的 Resources：`paper://{paper_id}`, `paper://{paper_id}/summary`, `papers://list`，用于在 Prompt 或 Tool 中作为外部资源引用。
- Prompts：`paper_qa(question)`、`paper_review(paper_id)`，用于指导 LLM 执行基于库的 QA 或论文点评。

## 依赖与注意事项

- 必要 Python 包（示例）：FastAPI、uvicorn、langchain、langchain-openai 兼容库、chromadb、pymupdf、python-dotenv、mcp-server。实际版本请参见 `backend/requirements.txt`。
- Chroma：若启用远程 Chroma 服务，请在 `.env` 中配置 `CHROMA_HOST` / `CHROMA_PORT`。否则使用本地 PersistentClient 与 JSON 回退。
- LLM/Embedding：需要有效的 `DASHSCOPE_API_KEY`（百炼兼容）。当没有 API Key 时，系统会使用本地 hash embedding 与某些降级路径。

## 开发者指南（代码位置速览）

- 后端主入口: [backend/app/main.py](backend/app/main.py)
- 配置: [backend/app/core/config.py](backend/app/core/config.py)
- LLM 封装: [backend/app/core/llm.py](backend/app/core/llm.py)
- LangChain 封装: [backend/app/core/langchain_factory.py](backend/app/core/langchain_factory.py)
- Agents: [backend/app/agents/paper_ingestion.py](backend/app/agents/paper_ingestion.py), [backend/app/agents/paper_retrieval.py](backend/app/agents/paper_retrieval.py), [backend/app/agents/research_qa.py](backend/app/agents/research_qa.py)
- DB: [backend/app/db/sqlite.py](backend/app/db/sqlite.py), [backend/app/db/chroma.py](backend/app/db/chroma.py)
- API 路由: [backend/app/api/papers.py](backend/app/api/papers.py), [backend/app/api/search.py](backend/app/api/search.py), [backend/app/api/qa.py](backend/app/api/qa.py), [backend/app/api/tags.py](backend/app/api/tags.py), [backend/app/api/system.py](backend/app/api/system.py)
- MCP 实现: [backend/app/mcp/server/server.py](backend/app/mcp/server/server.py), [backend/app/mcp/server/tools.py](backend/app/mcp/server/tools.py), [backend/app/mcp/server/resources.py](backend/app/mcp/server/resources.py), [backend/app/mcp/server/prompts.py](backend/app/mcp/server/prompts.py)

## 下一步建议

- 若希望我继续：
  - 生成对外用户文档（README + API 使用示例）
  - 编写或运行后端单元测试（`tests/` 下）并修复潜在问题
  - 为前端添加 API 调用示例或 E2E 测试脚本

