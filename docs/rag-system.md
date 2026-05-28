# PaperAgent RAG 检索增强生成系统

## 概述

PaperAgent 的 RAG（Retrieval-Augmented Generation）系统实现了从 PDF 论文上传到智能问答的完整链路。采用 **LLM 驱动的结构化元数据提取** 替代传统文本分块，结合 **语义向量检索 + 元数据过滤 + LLM Reranking + Query Rewriting** 的多级检索优化，并通过 LangGraph Supervisor 多 Agent 编排实现灵活的研究问答能力。

```
PDF 上传 → 异步解析 → LLM 提取元数据 → 论文级向量索引 → ChromaDB
                                                           ↓
用户提问 → Query Rewriting → 混合检索 → Reranking → Analysis Agent → 流式输出
```

---
## 一、文档摄入管线 (Ingestion Pipeline)

**核心文件**: `backend/app/agents/paper_ingestion.py`

### 1.1 整体流程

```
upload → save PDF → enqueue task → background worker
                                        │
                    ┌───────────────────┘
                    ▼
              extract_text (PyMuPDF)
                    │
                    ▼
              extract_basic (LLM)  ← Phase 1: title, authors, year
                    │
                    ▼
              extract_deep (LLM)   ← Phase 2: abstract, methods, contributions...
                    │
                    ▼
              normalize → update DB → index vector
```

### 1.2 PDF 文本提取

使用 PyMuPDF (fitz) 逐页提取文本，全文截断至 **32000 字符**，防止超出 LLM 上下文窗口。

```python
doc = fitz.open(path)
texts = [page.get_text("text") for page in doc if page.get_text("text").strip()]
full = "

".join(texts)
return full[:32000]
```

### 1.3 两阶段 LLM 元数据提取

采用 Pydantic 模型 + LangChain `with_structured_output(method="json_schema")` 进行结构化抽取，分两阶段执行：

| 阶段 | Pydantic 模型 | 提取字段 | 输入长度 | 重试 |
|------|-------------|---------|---------|------|
| Phase 1 | `PaperBasicInfo` | title, authors, year | 前 12000 字符 | 最多 3 次 |
| Phase 2 | `PaperDeepInfo` | abstract, abstract_zh, contributions, methods, results, limitations, conclusion, keywords, domain | 前 28000 字符 | 最多 3 次 |

**设计要点**：
- 两阶段分离：基础信息和深度内容分开提取，减少单次 LLM 调用复杂度
- 中文摘要 (`abstract_zh`)：LLM 直接生成中文摘要，降低后续中文问答的语义鸿沟
- 重试机制：每个阶段最多重试 3 次，处理 LLM 偶发的结构化输出解析失败
- 字段归一化：`_normalize_merged()` 处理 list/string 类型混用问题

### 1.4 异步任务队列

**文件**: `backend/app/core/task_queue.py`

基于 `threading.Thread` 的后台工作线程，支持任务状态追踪和异常恢复：

- 队列状态：`queued → running → done / failed`
- 论文解析状态：`queued → extracting → analyzing_basic → analyzing_deep → indexing → ready`
- 故障恢复：重启后自动将 `running` 状态任务重置为 `queued`

---
## 二、文本索引策略

**核心文件**: `backend/app/agents/paper_ingestion.py:239-268`

### 2.1 论文级摘要索引

PaperAgent **不使用传统的段落级文本分块 (chunking)**，而是采用 **论文级摘要索引**：

```python
parts = [
    paper.get("abstract") or "",
    paper.get("contributions") or "",
    paper.get("methods") or "",
    paper.get("results") or "",
    paper.get("conclusion") or "",
]
summary_text = " ".join(p for p in parts if p)
```

每篇论文生成 **一个向量**，向量内容为 LLM 提取的各字段拼接。

### 2.2 设计考量

| 维度 | 论文级索引 | 传统段落级分块 |
|------|-----------|-------------|
| 检索粒度 | 以论文为单位 | 以段落为单位 |
| 元数据丰富度 | 高（标题/作者/年份/领域/关键词存入 metadata） | 低（通常仅存 chunk 序号） |
| 适用场景 | 找相关论文 | 找论文中某段细节 |
| 向量数量 | 少（论文数级别） | 多（论文数 × chunks/论文） |
| 索引维护 | 简单（单论文单向量） | 复杂（chunk 增删需协调） |

**适用性**：论文检索场景下，用户的典型需求是找到讨论某个主题的论文，而非找到某个公式在第几页。论文级索引恰好匹配这一需求模式。

### 2.3 元数据存储

除文本向量外，每条向量记录还存储丰富的结构化元数据：

| 字段 | 用途 |
|------|------|
| `paper_id` | 关联 SQLite 中的完整论文记录 |
| `title` | 检索结果展示 |
| `authors` | 作者筛选 |
| `year` | 年份范围过滤 |
| `domain` | 领域精确匹配 |
| `keywords` | 关键词标签 |

---
## 三、Embedding 生成

**核心文件**: `backend/app/core/llm.py`, `backend/app/core/langchain_factory.py`

### 3.1 双通道架构

```
llm_client.embed_texts()
    │
    ├── API 可用 → DashScope text-embedding-v3 → 真实语义向量
    │
    └── API 不可用 → local_embedding() → 哈希伪嵌入 (384维)
```

### 3.2 主通道：DashScope API

- **模型**: `text-embedding-v3`（阿里云百炼平台）
- **接入方式 1**: `OpenAIEmbeddings`（LangChain，OpenAI 兼容端点）
- **接入方式 2**: `DashScopeClient._dashscope_embeddings()`（原生 API 直调，实际使用路径）

### 3.3 降级通道：本地哈希伪嵌入

当 DashScope API 不可用时，使用基于哈希的本地伪嵌入作为降级方案：

```python
def local_embedding(text, dims=384):
    tokens = tokenize(text)          # 字母数字 + 中文字符分词
    counts = Counter(tokens)
    vector = [0.0] * dims
    for token, count in counts.items():
        digest = hashlib.sha256(token.encode()).digest()
        idx = int.from_bytes(digest[:4], "big") % dims  # 哈希映射到维度
        sign = 1.0 if digest[4] % 2 == 0 else -1.0      # 随机符号
        vector[idx] += sign * (1.0 + math.log(count))    # TF 加权
    return [v / norm for v in vector]  # L2 归一化
```

**设计特点**：
- **维度**：384（轻量，适合少论文场景）
- **确定性**：相同 token 总是映射到相同维度，保证检索一致性
- **无外部依赖**：不依赖任何 API 或模型文件，纯计算实现
- **局限性**：仅保留 token 存在性信息，无法捕捉语义相似度

---
## 四、向量存储

**核心文件**: `backend/app/db/chroma.py`

### 4.1 双模架构

```
VectorStore
    │
    ├── ChromaDB 可用 → PersistentClient (本地) / HttpClient (远程)
    │
    └── ChromaDB 不可用 → JSON 文件 + 手动余弦相似度
```

### 4.2 Collection 设计

- **单一 Collection**: `paper_summaries`
- **存储内容**: `id`(paper_id) + `document`(summary text) + `metadata`(结构化元数据) + `embedding`(向量)
- **相似度转换**: `score = 1.0 / (1.0 + distance)`，将 ChromaDB 距离转为 0~1 相似度

### 4.3 JSON 降级存储

```python
def _local_query(self, text, top_k):
    query_vector = llm_client.embed_texts([text])[0]
    rows = []
    for row in self._read_local():
        score = cosine(query_vector, row.get("embedding", []))
        rows.append({"id": ..., "score": score, ...})
    return sorted(rows, key=lambda x: x["score"], reverse=True)[:top_k]
```

- 全量遍历 + 余弦相似度计算
- 适合论文量 < 1000 的场景（遍历开销可接受）
- 数据以 JSON 文件存储，便于调试和迁移

---
## 五、检索策略与 Reranking

**核心文件**: `backend/app/agents/paper_retrieval.py`

### 5.1 混合检索管线

```
用户 Query
    │
    ├──→ 语义搜索 (ChromaDB, top_k×3)
    │
    └──→ 元数据过滤 (SQLite, 领域/年份/标签/收藏)
    │
    └──→ 合并去重 (语义优先，元数据补充)
    │
    └──→ LLM Reranking (listwise 重排序)
    │
    └──→ top_k 结果
```

### 5.2 同步 vs 异步检索

| 方法 | 调用场景 | 特点 |
|------|---------|------|
| `search()` | FastAPI REST API (`/api/search`) | 同步，语义搜索 + 后过滤 |
| `search_async()` | LangGraph Supervisor 节点 | 异步并行（语义 + 元数据），合并后 Rerank |

`search_async()` 使用 `asyncio.gather` 并行执行两条搜索路径：

```python
semantic_hits, metadata_papers = await asyncio.gather(
    semantic(),   # 向量语义搜索
    metadata(),   # SQLite 元数据过滤
)
# 合并：语义结果优先，元数据结果去重补充
# 超过 top_k 则触发 Reranking
```

### 5.3 元数据过滤维度

| 过滤器 | 实现 | 代码位置 |
|--------|------|---------|
| 年份范围 | `year >= year_from AND year <= year_to` | SQLite WHERE |
| 领域 | `domain = ?` 精确匹配 | SQLite WHERE |
| 标签 | `JOIN paper_tags` 交集匹配 | SQLite JOIN |
| 收藏 | `is_favorite = ?` 布尔过滤 | SQLite WHERE |

### 5.4 LLM Listwise Reranking

通过 **单次 LLM 调用** 对所有候选论文进行排序：

```python
class RerankResult(BaseModel):
    ranked_indices: list[int]  # 按相关性降序排列的索引

RERANK_PROMPT = """
Query: {query}
Candidates:
[0] Title — Authors (Year): snippet
[1] Title — Authors (Year): snippet
...
Return "ranked_indices" sorted by relevance.
"""
```

**设计要点**：
- **扩量检索**：初检取 `top_k × 3`（最少 20）个候选，提高召回
- **Listwise 排序**：一次 LLM 调用处理所有候选，效率高于逐对 Pointwise 比较
- **结构化输出**：`with_structured_output(json_schema)` 确保返回格式可靠
- **索引容错**：`_apply_rank()` 处理 LLM 返回的无效/缺失索引，自动补到末尾
- **静默降级**：Reranking 异常时返回原始向量排序结果，不影响可用性

---
## 六、Query Rewriting（查询改写）

**核心文件**: `backend/app/supervisor/retrieval_agent.py`

### 6.1 改写流程

```
用户原始 Query + 对话历史 (最近 10 条消息)
    │
    ▼
_rewrite_query() → LLM 改写
    │
    ├── 成功 → 独立的、关键词丰富的搜索 query
    └── 失败 → 保持原始 query
    │
    ▼
传递到 PaperRetrievalAgent.search_async()
```

### 6.2 改写策略

```python
QUERY_REWRITE_PROMPT = """
Given the conversation history, rewrite the user's latest question
into a standalone, keyword-rich search query.

Rules:
- Resolve pronouns and references
  (e.g., "the first one", "that paper", "his method")
  into specific terms from the conversation
- Preserve the original search intent and technical terms
- Output ONLY the rewritten query on a single line
"""
```

### 6.3 设计要点

| 要素 | 说明 |
|------|------|
| 上下文窗口 | 最近 10 条消息，每条截断至 300 字符 |
| 角色标注 | 对话历史标记 User/Assistant 角色 |
| 输出安全 | 改写结果长度 < 3 字符视为失败，回退原始 query |
| 异常处理 | LLM 调用异常时静默回退，不阻断检索 |

**典型改写效果**：

| 原始 Query | 改写后 Query |
|-----------|-------------|
| "第一篇的贡献是什么？" | "Attention Is All You Need 论文的主要贡献和创新点" |
| "那个用 GNN 的论文" | "Graph Neural Network 相关论文" |
| "transformer" | "transformer 模型架构 论文" |

---
## 七、上下文组装与 Prompt 构建

### 7.1 Analysis Agent 的上下文构建

**文件**: `backend/app/supervisor/analysis_agent.py`

```
papers_context → 从 DB 补全 (get_paper) → 格式化 → Prompt 组装
```

```python
# 上下文格式
[1] Paper Title — Authors (Year)
    abstract/snippet (截断 400 字符)

[2] Another Paper — Authors (Year)
    abstract/snippet (截断 400 字符)

# Prompt 模板
"""You are a research assistant. Answer based on paper context.
Cite sources inline as [n].
If info is insufficient, say so clearly.

Question: {question}
Paper library context: {context}"""
```

### 7.2 Responder 输出增强

**文件**: `backend/app/supervisor/responder.py`

- 根据 `agent_history` 切换响应模式：研究 QA / 信息呈现 / 通用对话
- 自动追加 **References 章节**：

```
---
**References:**
[1] *Attention Is All You Need* — Vaswani et al. (2017)
[2] *BERT: Pre-training of Deep Bidirectional Transformers* — Devlin et al. (2019)
```

### 7.3 SSE 流式输出

**文件**: `backend/app/api/chat.py`

- 使用 `supervisor_graph.astream_events(version="v2")` 实现事件驱动流式
- **仅 Responder 节点**的 LLM 调用产生 token 事件（`on_chat_model_stream` + `node == "responder"`）
- 其他 Agent 节点的中间 LLM 调用不暴露给前端
- 最终通过 `node_done` 事件发送 `cited_papers` 供前端展示引用列表

---
## 八、多 Agent RAG 编排

**核心文件**: `backend/app/supervisor/supervisor.py`

### 8.1 图结构

```
         ┌──────────┐
         │Supervisor│  ← LLM 动态路由决策
         └────┬─────┘
      ┌───────┼───────┐
      ▼       ▼       ▼
  retrieval analysis library   ← 子 Agent（不产生流式输出）
      │       │       │
      └───────┼───────┘
              ▼
         Responder   ← 唯一流式输出节点
```

### 8.2 Supervisor 路由规则

- 搜索/查找论文 → **retrieval**
- 研究问题 (what/how/why/compare) → **retrieval → analysis**
- 管理论文库 → **library**
- 指代追问 + 已有 papers_context → 直接 **analysis**
- 任务完成 → **FINISH**

### 8.3 硬性护栏 (Guardrails)

| 防护规则 | 触发条件 | 动作 |
|---------|---------|------|
| 调用次数上限 | `len(agent_history) >= 4` | 强制路由到 Responder |
| 禁止连续重复 | `next_agent == agent_history[-1]` | 强制路由到 Responder |
| 异常降级 | LLM 结构化输出解析失败 | 基于 agent_history 的规则性 fallback |

### 8.4 状态流转

```python
class SupervisorState(TypedDict):
    messages: list          # 对话历史 (add_messages reducer)
    next: str               # Supervisor 的路由决策
    papers_context: list    # 检索/列出的论文上下文 (跨节点共享)
    session_id: str         # 会话标识
    agent_history: list     # 已调用的 Agent 名称序列
```

**关键桥接**：`papers_context` 在 `retrieval_node` 中写入，在 `analysis_node` 中读取，实现检索结果到分析引擎的数据传递。

---
## 九、完整数据流

```
1. 用户上传 PDF
2. parse_queue 后台线程:
   PyMuPDF 提取文本 → LLM Phase 1 (basic info) → LLM Phase 2 (deep info)
   → 归一化 → 写入 SQLite → 拼接 summary → 向量化 → upsert ChromaDB
                                                              │
3. 用户提问                                                    │
4. retrieval_node:                                             │
   _rewrite_query(原始query + 对话历史) → 改写 query            │
   search_async(query, top_k=6):                               │
     ├── 语义搜索 (top_k×3=18) ←────── ChromaDB ──────────────┘
     └── 元数据过滤 ←────────────── SQLite
     ↓
   merge + dedup → 候选列表 (≤18)
     ↓
   _rerank_async() → LLM listwise 排序 → top_k=6
     ↓
   papers_context → SupervisorState
     ↓
5. analysis_node:
   从 DB 补全论文摘要 → 格式化上下文 → LLM 生成答案 (内联引用 [n])
     ↓
6. responder_node:
   组装最终回答 + 参考文献列表 → chat.astream() → SSE token 流 → 前端
     ↓
7. 保存对话到 SQLite (question + answer + cited_papers)
```

---
## 十、记忆系统与 RAG 联动

### 10.1 论文上下文跨轮次恢复

```
Turn 1: "Transformer 论文有哪些？"
  → retrieval → papers_context = [A, B, C]
  → cited_papers 存入 SQLite

Turn 2: "第一篇的贡献是什么？"
  → 从 SQLite 恢复 papers_context = [A, B, C]
  → Supervisor 看到已有上下文 → 直接路由到 analysis
  → Analysis 基于 [A, B, C] + "第一篇" 生成回答
```

这使得指代追问无需重新检索，既节省 LLM 调用，又保证回答一致性。

### 10.2 上下文长度控制

| 场景 | 截断策略 | 原因 |
|------|---------|------|
| LLM 提取输入 | 全文 32000 字符 | 模型上下文窗口限制 |
| Reranking snippet | 300 字符 | 候选展示的信息密度 |
| Analysis snippet | 400 字符 | 平衡信息量和对 LLM 的干扰 |
| 会话历史恢复 | 回答 600 字符 | 保留语义但节约 token |

---
## 十一、降级与容错机制

整个 RAG 管线设计了多层降级保护：

| 组件 | 正常路径 | 降级路径 | 降级触发条件 |
|------|---------|---------|------------|
| Embedding | DashScope API (text-embedding-v3) | 本地哈希伪嵌入 (384维) | API 调用异常 |
| 向量存储 | ChromaDB (本地/远程) | JSON 文件 + 手动余弦相似度 | ChromaDB 不可用 |
| 元数据存储 | SQLite | (无降级，必须组件) | - |
| LLM 提取 | qwen-plus 结构化输出 | 重试 3 次后返回空 dict | 解析失败 |
| Reranking | LLM listwise 排序 | 保留原始向量排序 | LLM 调用异常 |
| Query Rewriting | LLM 改写 query | 保持原始 query | LLM 调用异常 |
| Supervisor 路由 | LLM 结构化决策 | 基于 agent_history 的规则 | LLM 调用异常 |

---
## 十二、相关文件索引

| 文件 | 职责 |
|------|------|
| `backend/app/agents/paper_ingestion.py` | PDF 解析 + LLM 元数据提取 + 向量索引写入 |
| `backend/app/agents/paper_retrieval.py` | 混合检索 + LLM Reranking |
| `backend/app/supervisor/retrieval_agent.py` | Query Rewriting + LangGraph 检索节点 |
| `backend/app/db/chroma.py` | 向量存储（ChromaDB + JSON 降级） |
| `backend/app/db/sqlite.py` | 论文元数据 CRUD + 对话记忆存储 |
| `backend/app/core/llm.py` | Embedding 生成（API + 本地降级） |
| `backend/app/core/langchain_factory.py` | LangChain Chat/Embedding 工厂 |
| `backend/app/core/config.py` | 模型/存储/API 配置 |
| `backend/app/models/paper.py` | Pydantic 数据模型（结构化输出） |
| `backend/app/supervisor/supervisor.py` | LangGraph Supervisor 图编排 |
| `backend/app/supervisor/analysis_agent.py` | 上下文组装 + 研究问答生成 |
| `backend/app/supervisor/responder.py` | 流式输出 + 参考文献格式化 |
| `backend/app/supervisor/state.py` | SupervisorState 定义 |
| `backend/app/api/chat.py` | SSE 流式接口 + 记忆加载/持久化 |
| `backend/app/api/search.py` | REST 检索 API |
| `backend/app/core/task_queue.py` | 异步解析任务队列 |
