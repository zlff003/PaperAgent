# PaperAgent 长短期记忆系统

## 概述

PaperAgent 采用 **双层记忆架构**，结合了 LangGraph 图状态中的**短期工作记忆**和 SQLite 数据库中的**长期持久记忆**，实现跨会话的上下文感知和多轮对话能力。

```
┌─────────────────────────────────────────────────────┐
│                    短期记忆 (图内)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │   messages   │  │papers_context│  │agent_hist │  │
│  │  (对话消息)    │  │ (论文上下文)   │  │(Agent调用链)│ │
│  └──────────────┘  └──────────────┘  └───────────┘  │
│                       ↑↓ 读写                       │
├─────────────────────────────────────────────────────┤
│                    长期记忆 (SQLite)                  │
│  ┌──────────────────────────────────────────────┐   │
│  │           conversations 表                     │   │
│  │  id | question | answer | cited_papers | ...  │   │
│  │  session_id | turn_index | created_at         │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

---

## 一、短期记忆（Short-term Memory）

短期记忆由 **LangGraph StateGraph** 的共享状态 `SupervisorState` 承载，在一次请求的图执行生命周期内流转。

### 1.1 状态定义

**文件**: `backend/app/supervisor/state.py`

```python
class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]   # 对话消息，自动合并
    next: str                                  # 路由决策
    papers_context: list[dict[str, Any]]       # 当前检索到的论文
    session_id: str                            # 会话标识
    agent_history: list[str]                   # Agent 调用历史
```

四个关键字段各自承担不同的记忆职责：

| 字段 | 作用 | 生命周期 |
|------|------|---------|
| `messages` | 存储完整对话历史（用户问题 + Agent 响应） | 单次图执行 |
| `papers_context` | 保存检索到的论文元信息，供分析 Agent 引用 | 单次图执行 |
| `agent_history` | 记录已调用的 Agent 名称列表 | 单次图执行 |
| `session_id` | 标识当前会话，关联长期记忆 | 跨越多次请求 |

### 1.2 消息累积机制

`messages` 字段使用 LangGraph 内置的 `add_messages` reducer，这是一个**追加式合并**算子：

- 图中每个节点返回的 `messages` 不会覆盖已有消息，而是追加合并
- 自动处理 `HumanMessage`、`AIMessage`、`SystemMessage` 等 LangChain 消息类型
- 同一 ID 的消息会被去重更新（支持流式 token 的增量更新）

这确保了消息在图内节点间流转时，每个节点都能看到**完整的对话历史**。

### 1.3 会话加载与恢复

**文件**: `backend/app/api/chat.py` (`event_generator()` 函数，第 66-93 行)

每次请求开始时，短期记忆从数据库中**重建**：

```python
# 1. 从 DB 加载最近 20 轮会话历史
prior_turns = db.list_conversations_by_session(payload.session_id, limit=20)
for turn in prior_turns:
    history_messages.append(HumanMessage(content=turn["question"]))
    # 旧回答截断到 600 字符以控制上下文长度
    answer = turn["answer"]
    if len(answer) > 600:
        answer = answer[:600] + "..."
    history_messages.append(AIMessage(content=answer))

# 2. 恢复最后一轮的论文上下文（供跟进问题引用）
if prior_turns:
    last_turn = prior_turns[-1]
    cited = last_turn.get("cited_papers", [])
    if cited:
        papers_ctx = [...]  # 重建 papers_context
```

**关键设计决策**:

- **上限 20 轮**: 防止上下文窗口溢出
- **回答截断**: 旧回答截断至 600 字符，保留语义但不浪费 token
- **论文上下文恢复**: 从最后一轮的 `cited_papers` 重建 `papers_context`，使 "第一篇讲了什么"、"那个论文的方法是什么" 等指代性跟进问题可以直接由分析 Agent 处理，无需重新检索

### 1.4 图中消息流转

```
用户请求 → [初始状态构建]
              │
              ▼
         ┌──────────┐
         │supervisor │ ← 读取完整 messages 做路由决策
         └─────┬────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌──────┐  ┌──────┐  ┌──────┐
│retriev│ │analys│ │library│  ← 各自追加 AIMessage 到 messages
└──┬───┘  └──┬───┘  └──┬───┘
   │         │         │
   └─────────┼─────────┘
             ▼
       ┌──────────┐
       │supervisor │ ← 基于完整上下文决定是继续还是结束
       └─────┬────┘
             │ (FINISH)
             ▼
       ┌──────────┐
       │responder │ ← 读取 messages + papers_context 生成最终回答
       └──────────┘
```

每个 Agent 节点都通过 `_append_history()` 将自己的名称追加到 `agent_history`，Supervisor 据此实施**硬性防护**：

- **上限守卫**: 累计调用 ≥ 4 次 Agent → 强制结束
- **防重复守卫**: 连续两次调用同一 Agent → 强制结束

这两个守卫防止无限循环，同时确保单次请求不会耗尽 LLM 配额。

---

## 二、长期记忆（Long-term Memory）

长期记忆基于 **SQLite** 持久化，存储完整的对话记录，实现跨会话的上下文感知。

### 2.1 数据模型

**文件**: `backend/app/db/sqlite.py` (第 30-93 行, conversations 表)

```sql
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,           -- UUID
    question TEXT NOT NULL,        -- 用户问题（原始）
    answer TEXT NOT NULL,          -- AI 最终回答（完整）
    cited_papers TEXT,             -- JSON: 引用的论文列表
    session_id TEXT,               -- 会话标识（UUID）
    turn_index INTEGER DEFAULT 0,  -- 会话内轮次序号
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

每条记录保存**完整的**问题和回答（不截断），以及引用的论文信息。

### 2.2 写入时机与策略

**文件**: `backend/app/api/chat.py` (第 158-185 行)

对话在 SSE 流**完全结束**后才写入数据库（`finally` 块中）：

```python
finally:
    if final_answer and payload.message:
        db.insert_conversation({
            "id": str(uuid.uuid4()),
            "question": payload.message,       # 原始问题
            "answer": final_answer,            # 完整回答
            "cited_papers": cited,             # 引用的论文
            "session_id": session_id or None,
            "turn_index": turn_index,          # 自增轮次
        })
```

**特点**:
- **尽力保存** (best-effort): 写入失败只静默忽略，不影响用户响应
- **关联引用论文**: `cited_papers` 记录每轮对话引用了哪些论文，支撑后续的跟进问题
- **轮次自增**: 每个 session 内的 turn_index 自动递增
- **完整保存**: 与短期记忆中截断历史不同，长期记忆保存完整回答

### 2.3 跨会话记忆注入（Long-term Context）

**文件**: `backend/app/api/chat.py` (第 53-63 行, 第 96-100 行)

对于**新会话**（无 `session_id`），系统从长期记忆中提取全局上下文并注入：

```python
def _format_recent_context(recent: list[dict]) -> str:
    lines = [
        "[System: Below are recent conversations for context. "
        "Use them to understand user references but do not mention them unless relevant.]\n"
    ]
    for conv in recent:
        lines.append(f"Q: {conv['question'][:200]}")     # 截断至 200 字符
        answer_brief = conv["answer"][:200].replace("\n", " ")
        lines.append(f"A: {answer_brief}...\n")
    return "\n".join(lines)

# 新会话时注入
if not payload.session_id:
    recent = db.get_last_conversations(limit=5)  # 获取最近 5 条对话
    if recent:
        summary = _format_recent_context(recent)
        history_messages.insert(0, SystemMessage(content=summary))
```

**设计要点**:
- **仅在无会话时注入**: 有 session_id 的恢复会话不需要重复注入
- **取最近 5 条**: 提供有限的全局上下文，不过度占用 token
- **双向截断**: 问题和回答各截断至 200 字符，仅保留核心语义
- **System 角色**: 以 `SystemMessage` 注入，指令模型将其作为背景知识而非对话内容
- **指令约束**: 明确告诉模型 "不要提及这些上下文，除非与当前问题相关"

### 2.4 上下文长度管理策略

| 场景 | 截断策略 | 原因 |
|------|---------|------|
| 恢复会话历史 | 回答截断至 600 字符 | 节省上下文窗口，保留语义 |
| 跨会话注入 | Q&A 各截断至 200 字符 | 最小化注入开销 |
| Supervisor 决策 | AI 响应截断至 800 字符 | Supervisor 只需概要，不需细节 |
| 论文上下文 | snippet 截断至 400 字符 | 控制每条论文的上下文大小 |
| Agent 调用上限 | 最多 4 次 | 防止图循环过长 |
| 会话轮次上限 | 最近 20 轮 | 控制 messages 总长度 |
| 长期存储 | **完整保存**（不截断） | 保证历史记录完整性 |

### 2.5 CRUD 接口

**文件**: `backend/app/api/chat.py` (第 26-47 行)

| 端点 | 方法 | 功能 |
|------|------|------|
| `/chat/history` | GET | 列出对话（支持按 session 过滤或去重展示） |
| `/chat/history/{id}` | DELETE | 删除单条对话 |
| `/chat/history/session/{id}` | DELETE | 删除整个会话的所有轮次 |

### 2.6 会话管理（前端视角）

**文件**: `frontend/src/pages/ChatPage.tsx`

前端的会话管理策略：

- **Session ID 生成**: 每次「新建对话」使用 `crypto.randomUUID()` 生成新的 session_id
- **会话恢复**: 点击历史记录时，根据 session_id 加载该会话的**所有轮次**，完整恢复对话上下文
- **会话去重**: 侧边栏历史列表按 session_id 去重，每个会话只显示第一条
- **SESSION 粒度删除**: 删除操作以 session 为单位（而非单条），删除整个会话的所有轮次

---

## 三、记忆与论文上下文的交互

`papers_context` 是连接短期记忆和论文知识的桥梁：

```
                  ┌──────────────────┐
                  │   用户提问         │
                  └────────┬─────────┘
                           │
              ┌────────────▼────────────┐
              │   retrieval Agent       │
              │   语义搜索 → 返回论文列表  │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │   papers_context        │ ← 论文 ID + 标题 + 作者 + 年份 + snippet
              │   存入短期记忆 state     │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │   analysis Agent        │
              │   从 DB 补全论文摘要      │
              │   基于论文生成回答         │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │   responder Agent       │
              │   格式化最终输出+参考文献   │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │   存入长期记忆            │
              │   cited_papers 关联      │
              └─────────────────────────┘
```

**跟进问题的记忆联动** (存储于 `backend/app/api/chat.py:79-93`):

```
用户: "Transformer 论文有哪些？"       → retrieval → papers_context = [A, B, C]
用户: "第一篇的贡献是什么？"            → 从 DB 恢复 papers_context → analysis 直接回答
                                          （无需重新检索）
```

---

## 四、记忆生命周期总结

```
创建 ──→ 每次对话完成后，完整 Q&A + 引用论文写入 SQLite
  │
读取 ──→ 短期: 请求开始，从 DB 加载会话历史 → 注入 messages
  │      长期: 新会话时，注入最近 5 条跨会话摘要 → SystemMessage
  │
更新 ──→ 图执行中，add_messages 自动合并节点输出
  │      papers_context 随 retrieval/library 更新
  │
删除 ──→ API: 单条删除或按 session 批量删除
  │      (无自动过期机制，依赖用户手动管理)
  │
上下文 ──→ 多层截断策略：200/400/600/800 字符逐级控制 token 消耗
```

---

## 五、设计决策：为何长期记忆不用 ChromaDB

长期记忆选择了 **SQLite** 而非 ChromaDB 向量数据库，这是基于操作模式和数据访问特征的审慎决策。

### 5.1 核心原则：根据查询模式选择存储

长期记忆涉及的操作及其查询特征：

| 操作 | 查询方式 | 代码位置 |
|------|---------|---------|
| 会话恢复 | `WHERE session_id = ? ORDER BY turn_index DESC LIMIT 20` | `chat.py:70` |
| 跨会话注入 | `ORDER BY created_at DESC LIMIT 5` | `chat.py:97` |
| 历史列表 | 按 session 过滤或全局去重 | `chat.py:31-32` |
| 删除 | 按 id 或 session_id 精确删除 | `chat.py:38-46` |

以上全部是**精确匹配 + 时序排序 + 分页**操作，属于关系型数据库的原生优势领域。ChromaDB 是向量数据库，核心能力是语义相似度搜索，与上述操作模式不匹配。

### 5.2 ChromaDB 在此场景的具体劣势

**查询模式不匹配**：ChromaDB 的查询围绕向量距离展开，排序依据是相似度（cosine/euclidean distance），而不是时间戳。会话恢复需要的 "最近 20 轮" 和跨会话注入需要的 "最近 5 条" 都是时间排序，在 ChromaDB 中只能通过 metadata filter 笨重地实现。

**增量写入开销大**：每轮对话保存时，若使用 ChromaDB 需要额外调用 embedding API 将 Q&A 文本向量化，增加 API 延迟和费用（百炼 embedding 按量计费）。而 SQLite 只需一行 INSERT，零网络开销。

**删除操作笨重**：ChromaDB 的 collection 删除依赖 metadata filter，无法像 SQLite 的 `DELETE FROM conversations WHERE session_id = ?` 那样一条语句完成。需要先按 metadata 查出 id 集合，再逐个删除。

**语义搜索在此场景价值有限**：长期记忆中唯一的"召回"类操作是跨会话注入（获取最近 5 条对话作为背景上下文）。但用户的相邻对话往往主题不同（今天问 Transformer，昨天问 GNN），语义匹配在时间跨度小的对话中收益不大。即使需要语义回忆，也可以对 SQLite 中存储的对话进行 on-the-fly embedding 检索，无需将整个记忆系统迁移。

### 5.3 未来可能的增强方向

如果后续确实需要"回忆与当前话题语义相关的历史对话"，建议采用**混合方案**而非替代方案：

| 方案 | 改动量 | 说明 |
|------|--------|------|
| **SQLite + 可选语义回忆** | 小 | 长期记忆仍存 SQLite，仅在需要时对"最近 N 条"做 on-the-fly embedding 匹配 |
| **SQLite + FTS5 全文索引** | 小 | SQLite 内置的全文搜索引擎，支持模糊关键词回忆，无外部依赖 |
| **混合：SQLite + Chroma 双写** | 中 | SQLite 负责精确查询（主路径），Chroma 负责"跟我之前聊过的那个话题有关"的模糊回忆（辅助路径） |

**核心结论**：ChromaDB 在 PaperAgent 中的正确职责是论文语义检索（`paper_summaries` collection），而非对话记忆存储。长期记忆的增删改查负载是典型的 OLTP 场景，SQLite 是匹配这一模式的正确选择。

---

## 六、相关文件索引

| 文件 | 职责 |
|------|------|
| `backend/app/supervisor/state.py` | 短期记忆状态定义 |
| `backend/app/supervisor/supervisor.py` | 图路由与硬性守卫 |
| `backend/app/api/chat.py` | 记忆加载、注入、持久化（核心编排逻辑） |
| `backend/app/db/sqlite.py` | 长期记忆 CRUD（conversations 表） |
| `backend/app/supervisor/retrieval_agent.py` | 检索结果写入 papers_context |
| `backend/app/supervisor/analysis_agent.py` | 从 DB 补全论文信息 |
| `backend/app/supervisor/library_agent.py` | 库操作结果写入 papers_context |
| `backend/app/supervisor/responder.py` | 读取记忆生成最终回答 |
| `frontend/src/pages/ChatPage.tsx` | 前端会话管理、历史展示 |
| `frontend/src/api/client.ts` | 前端 API 调用（history/session/chat） |
| `frontend/src/types/index.ts` | Conversation 类型定义 |
