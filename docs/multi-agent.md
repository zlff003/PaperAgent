# PaperAgent 多 Agent 协作编排

## 架构概览

PaperAgent 采用 **Decide → Execute → Respond** 三阶段架构，基于 LangGraph Supervisor 模式进行多 Agent 协作编排。一个 LLM Supervisor 根据用户意图动态决策，按需调度 Retrieval、Analysis、Library 三个子 Agent，所有 Agent 执行完毕后由 Responder 节点统一产生流式输出。

```
                        ┌──────────────────────────────┐
                        │      Supervisor (LLM)         │
                        │                              │
                        │  · 接收用户消息 + 对话历史      │
                        │  · 分析意图，决定调用哪个 Agent │
                        │  · 子 Agent 返回后再次决策      │
                        │  · 任务完成时输出 FINISH       │
                        └─────────────┬────────────────┘
                                      │
              ┌───────────────────────┼──────────────────────┐
              │                       │                      │
              ▼                       ▼                      ▼
    ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
    │ Retrieval Agent │   │ Analysis Agent  │   │  Library Agent  │
    │                 │   │                 │   │                 │
    │ · 语义搜索论文   │   │ · 回答研究问题   │   │ · 列出所有论文   │
    │ · 按条件过滤     │   │ · 对比多篇论文   │   │ · 处理状态查询   │
    │ · 结构化输出     │   │ · 生成文献综述   │   │ · 论文库统计     │
    │                 │   │                 │   │ · 删除 / 重新提取 │
    └────────┬────────┘   └────────┬────────┘   └────────┬────────┘
             │                     │                      │
             └─────────────────────┼──────────────────────┘
                                   │
                            返回 Supervisor
                                   │
                              ┌────┴────┐
                              │ FINISH  │
                              └────┬────┘
                                   │
                                   ▼
                        ┌─────────────────────┐
                        │   Responder Node    │
                        │                     │
                        │ · 读取完整对话上下文  │
                        │ · chat.astream()    │
                        │ · 唯一 SSE token 源  │
                        └──────────┬──────────┘
                                   │
                                  END
```

**核心原则：执行与呈现分离。** 子 Agent 只做数据操作，返回结构化结果。Responder 是唯一使用 LLM 流式输出的节点，保证 Supervisor 看到的始终是简洁、非对话化的 Agent 输出，从而做出可靠的路由决策。

## Supervisor 决策机制

Supervisor 本身由 LLM 驱动，使用结构化输出（`with_structured_output`）生成精确的路由决策：

- **输入**：完整的对话历史（LangChain Messages）
- **输出**：`{"next": "retrieval" | "analysis" | "library" | "FINISH"}`
- **路由规则**（LLM prompt 指导）：
  - "找论文/搜索" → `retrieval`
  - "这篇论文讲了什么/对比/综述" → `retrieval` → `analysis`
  - "管理论文库（列出/状态/统计/删除/重新提取）" → `library`
  - 追问已有论文上下文中的具体论文 → 直接 `analysis`
  - 任务完成 → `FINISH`

### 代码级 Guard（防循环）

路由规则由 `_route()` 函数**在代码层面强制执行**，不依赖 LLM 遵守 prompt：

```python
MAX_AGENT_CALLS = 4

def _route(state: SupervisorState):
    next_agent = state.get("next", "FINISH")
    history = state.get("agent_history", [])

    # Guard 1: 总调用次数硬上限
    if len(history) >= MAX_AGENT_CALLS:
        return "responder"

    # Guard 2: 禁止连续调用同一 Agent
    if history and next_agent == history[-1]:
        return "responder"

    # FINISH 路由到 Responder 而非直接 END
    if next_agent == "FINISH":
        return "responder"

    return next_agent
```

无论 Supervisor LLM 返回什么决策，Guard 都会兜底。当 LLM 调用异常时，fallback 根据 `agent_history` 和 `papers_context` 做出安全决策。

## 子 Agent 职责

所有子 Agent **不调用 LLM 进行流式输出**，只做数据操作并返回结构化结果。用户可见的最终回复统一由 Responder 生成。

### Retrieval Agent (`supervisor/retrieval_agent.py`)

搜索论文库，返回结构化论文列表。

- 从对话历史中提取最新用户消息作为搜索查询
- 调用 `PaperRetrievalAgent.search_async()` 执行异步并行搜索（语义搜索 + 元数据过滤）
- 返回简洁的预格式化文本（`[retrieval] Found N papers for: query` + 编号列表），用于 Supervisor 上下文
- 返回 `papers_context` 供后续 Analysis 和 Responder 使用
- 追加 `agent_history` 记录

### Analysis Agent (`supervisor/analysis_agent.py`)

基于检索到的论文上下文生成研究答案。

- 读取 `papers_context` 和用户问题
- 通过 DB 丰富论文上下文（获取完整摘要等详细信息）
- 构造 LLM Prompt，包含论文上下文和内联引用要求
- 使用 `chat.ainvoke()` 一次性生成完整答案（不流式）
- 返回 `papers_context`（含丰富后的数据）供 Responder 引用
- 追加 `agent_history` 记录

### Library Agent (`supervisor/library_agent.py`)

管理论文库，支持列表查询、状态监控、统计分析、删除等操作。

- **列出所有论文** — 按领域、年份筛选，显示标题/作者/处理状态/收藏标记
- **处理状态查询** — 按 ready/extracting/queued/failed 分组展示解析进度
- **论文库统计** — 总论文数、领域分布、年份分布、总页数、已提取比例
- **收藏列表** — 列出所有星标论文
- **删除论文** — 先语义搜索匹配论文，确认后删除，同步清理向量索引和数据库
- **重新提取** — 先语义搜索匹配论文，确认后触发重新解析

采用函数路由模式：根据用户消息中的关键词（删除/状态/统计/收藏/列出/重新提取）分发到对应的处理函数，避免 LLM 调用开销。

**关键改动：填充 `papers_context`。** 列出论文、处理状态、收藏列表、搜索匹配等操作都会将相关论文数据写入 `papers_context`，使后续追问（如"第一篇论文详细介绍一下"）能够直接从上下文恢复，无需重复检索。

## Responder 节点

`supervisor/responder.py` — 唯一的 LLM 流式输出节点。Supervisor 决策 FINISH 后由 `_route()` 路由到此节点。

- 读取完整对话历史 + `papers_context`
- 根据 `agent_history` 判断响应模式：
  - 含 `analysis` 或检索到论文 → **研究 QA 模式**：基于论文上下文回答，内联引用 `[n]`
  - 纯 library 操作 → **信息呈现模式**：自然语言格式化结构化数据
- 使用 `chat.astream()` 逐 token 流式生成
- 自动追加参考文献列表（`[n] *Title* — Authors (Year)` 格式）

**这是唯一产生 SSE `token` 事件的地方。** 前端只接收来自 `responder` 节点的 token 流，不再有多源拼接问题。

## 状态管理

所有 Agent 通过 `SupervisorState` 共享状态：

```python
class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]  # 对话历史（自动累积）
    next: str                                 # Supervisor 路由决策
    papers_context: list[dict[str, Any]]      # 当前检索/列出的论文上下文
    session_id: str                           # 当前会话 ID
    agent_history: list[str]                  # 已调用的 Agent 序列
```

- `messages` 使用 LangGraph 的 `add_messages` reducer，每个子 Agent 返回的 `AIMessage` 自动追加到对话历史
- `papers_context` 在 Retrieval → Analysis 链中传递论文数据；Library 在列表/状态/收藏操作中也会填充，支持追问
- `agent_history` 每个子 Agent 执行后追加自身名称（如 `["retrieval", "analysis"]`），用于代码级循环防护
- `next` 由 Supervisor 设置，驱动条件路由

## Graph 结构

```python
builder = StateGraph(SupervisorState)

builder.add_node("supervisor", supervisor_node)
builder.add_node("retrieval", retrieval_node)
builder.add_node("analysis", analysis_node)
builder.add_node("library", library_node)
builder.add_node("responder", responder_node)   # 唯一流式节点

builder.set_entry_point("supervisor")
builder.add_conditional_edges("supervisor", _route)  # → agent or responder
builder.add_edge("retrieval", "supervisor")           # → back to supervisor
builder.add_edge("analysis", "supervisor")            # → back to supervisor
builder.add_edge("library", "supervisor")             # → back to supervisor
builder.add_edge("responder", END)                    # → terminal
```

关键变化：Supervisor 返回 FINISH 时不再直接 END，而是路由到 Responder 产生流式输出后再 END。

## SSE 流式输出

前端通过 `POST /api/chat` 获取 Server-Sent Events 流：

| Event | 触发时机 | 内容 |
|-------|---------|------|
| `token` | Responder 逐 token 生成时 | 单个 token 文本 |
| `node_done` | Graph 运行结束 | `final_answer` + `cited_papers` |
| `done` | 流结束 | 空 |
| `error` | 异常时 | 错误信息 |

流式过滤使用 `astream_events` API 的 `metadata.langgraph_node` 字段，**仅转发 `responder` 节点的 LLM token**。所有子 Agent（retrieval、analysis、library）和 Supervisor 的内部 LLM 调用均不暴露给前端。

## 对话记忆策略

- **短时记忆**：当前 session 的历史消息（最近 20 轮），旧回答截断到 600 字符以内
- **论文上下文恢复**：从上轮对话的 `cited_papers` 恢复 `papers_context`，支持"第一篇讲了什么"等指代追问。Library 的列表操作也会写入 `cited_papers`，确保追问可用
- **跨会话记忆**：新 session 时注入最近 5 条对话摘要作为 `SystemMessage`

## 端到端数据流

```
1. 前端 POST /api/chat { message, session_id }
2. Chat API 构建 initial_state（加载历史 + papers_context + agent_history=[]）
3. supervisor_node: LLM 决策 → next
4. _route(): 代码级 guard 检查 → 路由到子 Agent 或 responder
5. 子 Agent: 数据操作 → 返回结构化结果 + agent_history → 回到 supervisor
6. 重复 3-5 直到 FINISH（或 guard 强制终止）
7. responder_node: chat.astream() → token SSE 事件 → 前端流式渲染
8. Graph 结束: yield node_done + done → 保存对话到 SQLite
```

## 追问处理

多轮追问依赖 `papers_context` 在会话间的持久化：

1. Turn 1 的 Agent 返回 `papers_context`（包含检索结果或论文列表）
2. Chat API 在 `node_done` 中提取 `cited_papers` 并存入 SQLite
3. Turn 2 加载历史时，从上一轮的 `cited_papers` 恢复 `papers_context`
4. Supervisor 看到已有论文上下文，对于"第一篇/第二篇"等指代追问直接路由到 `analysis`
5. Library 列出/状态/收藏操作也会填充 `papers_context`，确保追问可用
