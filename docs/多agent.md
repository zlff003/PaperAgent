# PaperAgent 多 Agent 工作流详解

## 整体架构：三 Agent + 一 Graph + 一队列

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI 层                                │
│  /api/papers/*    /api/search/*    /api/qa/*    /api/tags/*     │
└──────┬──────────────┬────────────────┬──────────────┬───────────┘
       │              │                │              │
       ▼              ▼                ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Ingestion    │ │ Retrieval    │ │ Research QA  │ │ Tags/Sys     │
│ Agent        │ │ Agent        │ │ Agent        │ │ (API直调DB)  │
│              │ │              │ │              │ │              │
│ Pipeline     │ │ Sync + Async │ │ → qa_graph   │ │              │
│ (无Graph)    │ │ (无Graph)    │ │              │ │              │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────────────┘
       │                │                │
       │                │                ▼
       │                │         ┌────────────────────┐
       │                │         │   QA Graph          │
       │                │         │   (唯一的LangGraph) │
       │                │         │                     │
       │                │         │ 7节点 2条件分支     │
       │                │         │ 1个反思循环         │
       │                │         └────────────────────┘
       │                │
       ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        数据层                                    │
│   SQLite (论文元数据/对话/标签)    Chroma (语义向量)             │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────┐
│  Task Queue      │  ← 后台线程，轮询 parse_tasks 表
│  (parse_queue)   │    处理 PDF → LLM 提取 → 入库
└──────────────────┘
```

---

## Agent 1: Paper Ingestion Agent（论文摄入）

**定位**：线性 Pipeline，**不使用 Graph**。

**为什么不用 Graph**：流程是固定的 5 步串行——解析 PDF → 提取基础信息 → 提取深度内容 → 写入 SQLite → 索引 Chroma。没有分支、没有循环（除了单个步骤的 retry）、不需要并行。Graph 在这里纯属套壳。

### 流程详解

```
用户上传 PDF
     │
     ▼
[1] ingest_upload()               ← FastAPI 路由直接调用
     │
     ├─ 校验文件后缀 .pdf
     ├─ 生成 UUID 作为 paper_id
     ├─ 保存 PDF 到 data/papers/{uuid}.pdf
     ├─ INSERT 到 SQLite papers 表（状态: queued, 进度: 0%）
     └─ 调用 parse_queue.enqueue_parse(paper_id)
          │
          ▼
     [返回 paper 对象给前端]       ← 此时只存了文件名，还没提取
          │
          ▼
     ═══ 以下在后台线程中异步执行 ═══
          │
          ▼
[2] process_paper(paper_id)       ← Task Queue Worker 调用
     │
     ├─ 进度 15%: 用 PyMuPDF 打开 PDF，逐页提取文本
     │   └─ _extract_full_text() → 返回纯文本，截断到 32000 字符
     │
     ├─ 进度 35%: LLM 提取基础信息
     │   └─ _llm_extract_basic(text)
     │       ├─ get_chat_model() → ChatOpenAI (百炼 qwen-plus)
     │       ├─ with_structured_output(PaperBasicInfo, method="json_schema")
     │       │   PaperBasicInfo = {title: str, authors: list[str], year: int|None}
     │       └─ 失败自动重试最多 3 次
     │
     ├─ 进度 65%: LLM 提取深度内容
     │   └─ _llm_extract_deep(text, basic_info)
     │       ├─ with_structured_output(PaperDeepInfo, method="json_schema")
     │       │   PaperDeepInfo = {abstract, abstract_zh, contributions,
     │       │                    methods, results, limitations, conclusion,
     │       │                    keywords, domain}
     │       └─ 失败自动重试最多 3 次
     │
     ├─ _normalize_merged()        ← 清洗：list→string、类型转换
     │
     ├─ db.update_paper_metadata() ← 将提取结果写回 SQLite
     │
     ├─ 进度 90%: _index_paper()   ← 向量索引
     │   ├─ 拼接 abstract + contributions + methods + results + conclusion
     │   ├─ llm_client.embed_texts() → 调用百炼 Embedding API 生成向量
     │   └─ vector_store.upsert() → 存入 Chroma collection "paper_summaries"
     │
     └─ 进度 100%: 状态 → "ready"
```

### 关键技术点

**`with_structured_output(PaperBasicInfo, method="json_schema")`** 是 LangChain 的核心能力：

1. 把 Pydantic 模型转换为 JSON Schema
2. 通过 `response_format` 参数告诉 LLM 按此 schema 输出
3. LLM 返回的 JSON 自动解析为 `PaperBasicInfo` 对象
4. 如果 JSON 不合法，LangChain 自动重试

对比之前手动正则抠 JSON 的方式，可靠性大幅提升。

---

## Agent 2: Paper Retrieval Agent（论文检索）

**定位**：纯工具层，**不使用 Graph**。提供同步和异步两套接口。

**为什么不用 Graph**：检索本质就是"调两个 API 然后合并结果"。并行用 `asyncio.gather` 一行搞定。

### 双通道并行检索

```
query = "Transformer attention mechanism"
           │
           ▼
    search_async(query)
           │
    ┌──────┴──────┐
    │  asyncio.gather  │          ← 同步并行执行
    └──────┬──────┘
           │
    ┌──────┴──────────────────┐
    ▼                         ▼
[semantic()]              [metadata()]
    │                         │
Chroma 向量检索            SQLite 元数据查询
query → embedding              │
→ cosine 相似度             按年份/领域/标签
→ top_k 结果                 筛选所有论文
    │                         │
    ▼                         ▼
 论文A (score 0.92)        论文C (年份匹配)
 论文B (score 0.87)        论文D (领域匹配)
    │                         │
    └──────┬──────────────────┘
           ▼
       [合并去重]
    语义结果在前(按相关度排序)
    元数据结果补充到队尾
    去重(按 paper_id)
           │
           ▼
    return PaperBrief[] (最多 top_k 条)
```

### 两层接口设计

| 方法 | 调用方 | 模式 |
|------|--------|------|
| `search()` | FastAPI 路由 (`/api/search`) | 同步 |
| `search_async()` | QA Graph 节点 | 异步并行 |
| `retrieve_for_qa()` | MCP Server tools | 同步 |
| `retrieve_for_qa_async()` | QA Graph `_simple_retrieve` / `_reformulate_query` | 异步并行 |

同步/异步分开是因为：FastAPI 的同步路由在线程池里跑，不需要 async；但 QA Graph 的节点是 async 函数，需要 `await`。

---

## Agent 3: Research QA Agent + QA Graph

**定位**：整个系统唯一使用 **LangGraph StateGraph** 的地方。

**为什么值得用 Graph**：
- **条件分支**：问题类型不同，走不同的检索路径
- **循环**：答案不满意 → 重构查询 → 重新检索 → 重新生成
- **状态管理**：13 个字段在 7 个节点之间流转，Graph 自动管理
- **可观测**：每个节点的输入/输出都可追溯

### QAState（Graph 全局状态）

```
┌──────────────────────────────────────┐
│            QAState                    │
├──────────────────────────────────────┤
│ question:        "对比 Attention     │  ← 用户输入
│                  和 SSM 的优缺点"     │
│ question_type:   "comparison"        │  ← classify 节点产出
│ sub_questions:   []                  │  ← decompose 节点产出
│ top_k:           6                   │  ← 用户设定
│ retrieved_papers:[PaperBrief...]     │  ← retrieve 节点产出
│ draft_answer:    "Attention 的优势   │  ← generate 节点产出
│                  在于..."             │
│ critique:        "YES, 回答完整"     │  ← critique 节点产出
│ critique_passed: True                │
│ iteration:       0                   │  ← reformulate 节点累加
│ max_iterations:  2                   │
│ final_answer:    "..."               │  ← format_save 节点产出
│ conversation_id: "uuid-..."          │
│ error:           ""                  │
└──────────────────────────────────────┘
```

State 在节点之间自动传递，每个节点返回 `dict` 表示对 State 的部分更新，LangGraph 自动 merge。

### 完整执行流程

```
                    START
                      │
                      ▼
┌──────────────────────────────────────────────┐
│  Node 1: classify_question                   │
│                                              │
│  调用 LLM 对问题分类:                         │
│    Prompt: "Classify this question..."       │
│    输出: "simple" / "comparison" /           │
│           "review" / "complex"               │
│                                              │
│  更新 State: question_type = "comparison"    │
└──────────────────────┬───────────────────────┘
                       │
              ┌────────┴────────┐
              │ _route_after_   │  ← 条件分支 1
              │   classify()    │
              └────────┬────────┘
                       │
          question_type == "complex"?
          ├── NO ────────────────────┐
          │                          │
          ▼                          ▼
┌──────────────────────┐  ┌──────────────────────┐
│ Node 2a:             │  │ Node 2b: decompose    │
│ simple_retrieve      │  │                      │
│                      │  │ 仅当 question_type    │
│ 直接对原始问题做      │  │ == "complex" 时触发   │
│ 语义+元数据并行检索   │  │                      │
│                      │  │ Step 1: LLM 拆解问题  │
│ retrieve_for_qa_     │  │  "Attention 机制有    │
│ async(question)      │  │   哪些主要变体？"     │
│                      │  │  "SSM 相比 Attention  │
│ 更新:                │  │   的优势在哪？"       │
│ retrieved_papers     │  │  "两者计算复杂度如何   │
│                      │  │   对比？"             │
└──────────┬───────────┘  │                      │
           │              │ Step 2: 并行检索      │
           │              │ asyncio.gather(       │
           │              │   search(sub_q1),     │
           │              │   search(sub_q2),     │
           │              │   search(sub_q3)      │
           │              │ )                     │
           │              │                      │
           │              │ Step 3: 合并去重      │
           │              │ 更新: retrieved_papers│
           │              │ 更新: sub_questions   │
           └──────┬───────┘                      │
                  │ ◄────────────────────────────┘
                  ▼
┌──────────────────────────────────────────────┐
│  Node 3: generate_answer                     │
│                                              │
│  根据 question_type 选择不同的生成策略:       │
│    comparison → "提供结构化对比，可用表格"     │
│    review     → "按主题组织，写成 mini survey" │
│    complex    → "逐部分回答，每部分引用论文"   │
│    simple     → "直接回答，引用论文证据"       │
│                                              │
│  构造 context prompt:                        │
│    [1] Attention Is All You Need — ...       │
│    [2] Mamba: Linear-Time Sequence ...       │
│    ...                                       │
│                                              │
│  调用 LLM 生成答案                            │
│  更新: draft_answer                          │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│  Node 4: critique                            │
│                                              │
│  LLM 自我评估生成的答案:                       │
│    1. 是否回答了问题的所有部分？               │
│    2. 每个论述是否有论文支撑？                 │
│    3. 是否有遗漏或不足？                       │
│                                              │
│  回复 "YES" → critique_passed = True         │
│  回复 "NO: 缺少计算复杂度对比"                │
│       → critique_passed = False              │
│                                              │
│  更新: critique, critique_passed             │
└──────────────────────┬───────────────────────┘
                       │
              ┌────────┴────────┐
              │ _route_after_   │  ← 条件分支 2
              │   critique()    │
              └────────┬────────┘
                       │
         critique_passed == True?
         OR iteration >= max_iterations?
         ├── YES ──────────────────────┐
         │                              │
         ▼                              ▼
┌────────────────────┐  ┌──────────────────────────┐
│ Node 5:            │  │ Node 6: reformulate_query │
│ format_save        │  │                          │
│                    │  │ 仅当答案不满足且           │
│ 格式化最终答案:     │  │ iteration < 2 时触发      │
│   答案正文          │  │                          │
│   ---              │  │ Step 1: LLM 根据 critique │
│   参考文献:         │  │ 生成更精准的搜索 query    │
│   [1] ...          │  │                          │
│                    │  │ Step 2: 用新 query 检索   │
│ 持久化到 SQLite:    │  │ retrieve_for_qa_async()  │
│   conversations 表  │  │                          │
│                    │  │ Step 3: 合并新论文到      │
│ 更新:              │  │ 已有 retrieved_papers     │
│   final_answer     │  │ 去重，iteration += 1      │
│   conversation_id  │  │                          │
└────────┬───────────┘  └──────────┬───────────────┘
         │                         │
         │                         │
         ▼                         │
        END      ◄─────────────────┘
                 (reformulate → generate 形成循环)
```

### 两个条件分支

**分支 1 — `_route_after_classify`**：问题类型路由

```python
def _route_after_classify(state):
    if state["question_type"] == "complex":
        return "decompose"        # → 拆解 + 并行检索
    return "simple_retrieve"      # → 直接检索
```

**分支 2 — `_route_after_critique`**：反思循环

```python
def _route_after_critique(state):
    if state["critique_passed"] or state["iteration"] >= state["max_iterations"]:
        return "format_save"      # → 通过，或达到最大轮次，结束
    return "reformulate"          # → 不满意，重构查询再来一轮
```

最多执行 `max_iterations=2` 次 reformulate，加上初始的一次 generate，**最多 3 次 LLM 生成**。

### 设计决策：为什么 fan-out 不用 Send API

LangGraph 提供了 `Send` API 做 fan-out：
```python
return [Send("retrieve_sub", {query: q1}), Send("retrieve_sub", {query: q2})]
```

这需要额外的 `retrieve_sub` 节点 + reducer 函数。对于 3-4 个子问题并行，`asyncio.gather` 更直接，效果完全相同。**不为了用框架而用框架。**

---

## 辅助组件

### Task Queue（后台解析队列）

```
FastAPI lifespan
      │
      ▼
 parse_queue.start()
      │
      ├── _recover_stuck_tasks()      ← 把上次异常退出时
      │   UPDATE parse_tasks           卡在 "running" 的任务
      │   SET status='queued'          重置为 queued
      │   WHERE status='running'
      │
      └── 启动 daemon 线程
            │
            ▼
          while not stopped:
            │
            ├── db.claim_next_parse_task() ← SELECT queued LIMIT 1
            │                                 UPDATE SET status='running'
            │
            ├── paper_ingestion_agent.process_paper(paper_id)
            │       │
            │       ├── 成功 → db.complete_parse_task()
            │       └── 失败 → db.fail_parse_task() + update_parse_status("failed")
            │
            └── sleep(1) → 继续轮询
```

基于 SQLite 表的简单轮询队列。对于单用户本地场景（论文量 < 1000），完全够用。

### 依赖关系图

```
research_qa.py (Agent 3)
  └─→ qa_graph.py (LangGraph)
        ├─→ _classify_question      → get_chat_model()
        ├─→ _simple_retrieve        → paper_retrieval_agent (Agent 2)
        ├─→ _decompose              → get_chat_model() + Agent 2
        ├─→ _generate_answer        → get_chat_model()
        ├─→ _critique               → get_chat_model()
        ├─→ _reformulate_query      → get_chat_model() + Agent 2
        └─→ _format_save            → db (SQLite)

paper_retrieval.py (Agent 2)
  ├─→ vector_store (Chroma)
  └─→ db (SQLite)

paper_ingestion.py (Agent 1)
  ├─→ get_chat_model() + with_structured_output()
  ├─→ db (SQLite)
  ├─→ vector_store (Chroma)
  └─→ parse_queue (后台线程)

langchain_factory.py
  ├─→ get_chat_model()     → ChatOpenAI → 百炼 DashScope
  └─→ get_embeddings()     → OpenAIEmbeddings → 百炼 DashScope
```

---

## QA Graph 代码结构

```
qa_graph.py ──── build → compile
│
├── Node: _classify_question       → LLM 判断问题类型
├── Node: _simple_retrieve         → 直接检索
├── Node: _decompose               → 拆解复杂问题 + 并行检索
├── Node: _generate_answer         → LLM 生成答案
├── Node: _critique                → LLM 自我评估
├── Node: _reformulate_query       → 重构查询 + 补充检索
├── Node: _format_save             → 格式化 + 持久化
│
├── ConditionalEdge: _route_after_classify   → simple / complex
├── ConditionalEdge: _route_after_critique   → done / loop
│
└── Edge: reformulate → generate  (循环边)
```
