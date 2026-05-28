import { ExternalLink, Send, Trash2, Plus } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Conversation, PaperBrief } from "../types";

type Message = {
  role: "user" | "assistant";
  content: string;
  cited_papers?: PaperBrief[];
};

export function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Conversation[]>([]);
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);

  function dedupHistory(items: Conversation[]): Conversation[] {
    // Show one entry per session (the first turn), plus legacy items without session_id
    const seen = new Set<string>();
    return items.filter((item) => {
      if (item.session_id) {
        if (seen.has(item.session_id)) return false;
        seen.add(item.session_id);
      }
      return true;
    });
  }

  useEffect(() => {
    void api.history().then((items) => setHistory(dedupHistory(items)));
  }, []);

  async function refreshHistory() {
    setHistory(dedupHistory(await api.history()));
  }

  function newConversation() {
    setMessages([]);
    setQuestion("");
    setSessionId(crypto.randomUUID());
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  async function deleteConversation(item: Conversation) {
    try {
      if (item.session_id) {
        await api.deleteSession(item.session_id);
      } else {
        await api.deleteConversation(item.id);
      }
      // Clear messages if currently viewing the deleted session
      if (sessionId === item.session_id || (!item.session_id && !sessionId)) {
        setMessages([]);
        setSessionId(null);
      }
      await refreshHistory();
    } catch (err) {
      console.error("删除会话失败", err);
    }
  }

  const messageListRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const isStreamingAnalysis = useRef(false);

  useEffect(() => {
    const el = messageListRef.current;
    if (!el) return;
    // Scroll to bottom when messages change or assistant is busy
    // Use requestAnimationFrame to ensure layout is updated
    requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    });
  }, [messages, busy]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed) return;
    setQuestion("");
    isStreamingAnalysis.current = false;
    setMessages((items) => [...items, { role: "user", content: trimmed }]);
    setBusy(true);
    // optimistic add assistant placeholder
    setMessages((items) => [...items, { role: "assistant", content: "" }]);
    try {
      const sid = sessionId || crypto.randomUUID();
      if (!sessionId) setSessionId(sid);
      const resp = await api.chat(trimmed, sid);
      if (!resp || !resp.body) {
        setMessages((items) => {
          const copy = [...items];
          copy[copy.length - 1] = { role: "assistant", content: "[错误] 无法连接到聊天服务" };
          return copy;
        });
        setBusy(false);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let done = false;
      while (!done) {
        const { value, done: rdone } = await reader.read();
        done = !!rdone;
        if (value) {
          buf += decoder.decode(value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop() || "";
          for (const part of parts) {
            const lines = part.split("\n");
            let ev = "message";
            let data = "";
            for (const line of lines) {
              if (line.startsWith("event:")) ev = line.slice(6).trim();
              else if (line.startsWith("data:")) data += line.slice(5).trim() + "\n";
            }
            data = data.replace(/\n$/, "");
            if (ev === "token") {
              setMessages((items) => {
                const copy = [...items];
                if (isStreamingAnalysis.current) {
                  // Append to the existing streaming message
                  const last = copy[copy.length - 1];
                  copy[copy.length - 1] = { ...last, content: (last.content || "") + data };
                } else {
                  // First token — push a new message bubble
                  isStreamingAnalysis.current = true;
                  copy.push({ role: "assistant", content: data });
                }
                return copy;
              });
            } else if (ev === "node_done") {
              try {
                const obj = JSON.parse(data);
                const cited = obj.cited_papers || obj.data?.cited_papers || [];
                if (cited.length > 0) {
                  setMessages((items) => {
                    const copy = [...items];
                    const last = copy[copy.length - 1] || { role: "assistant", content: "" };
                    copy[copy.length - 1] = {
                      ...last,
                      cited_papers: cited,
                    };
                    return copy;
                  });
                }
                // Refresh history
                void api.history().then((items) => setHistory(dedupHistory(items))).catch(() => {});
              } catch (e) {
                // ignore parse errors
              }
            } else if (ev === "done") {
              isStreamingAnalysis.current = false;
              // Remove trailing empty assistant placeholder if any
              setMessages((items) => {
                const copy = [...items];
                const last = copy[copy.length - 1];
                if (last && last.role === "assistant" && !last.content && !last.cited_papers?.length) {
                  copy.pop();
                }
                return copy;
              });
              // Fetch latest history
              const h = await api.history();
              setHistory(dedupHistory(h));
            } else if (ev === "error") {
              setMessages((items) => {
                const copy = [...items];
                copy[copy.length - 1] = { ...copy[copy.length - 1], content: (copy[copy.length - 1].content || "") + "\n[错误] " + data };
                return copy;
              });
            }
          }
        }
      }
    } catch (err) {
      console.error("stream error", err);
      setMessages((items) => {
        const copy = [...items];
        copy[copy.length - 1] = { ...copy[copy.length - 1], content: "[流式错误] " + String(err) };
        return copy;
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="chat-layout">
      <aside className="history-panel">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <h2>历史记录</h2>
          <button className="icon-button primary" type="button" onClick={newConversation} title="新建对话">
            <Plus size={14} />
          </button>
        </div>
        {history.length === 0 && <div className="empty-state compact">暂无历史对话</div>}
        {history.map((item) => (
          <div className="history-item-row" key={item.id}>
            <button
              className="history-item"
              type="button"
              onClick={async () => {
                if (item.session_id) {
                  // Restore the full session so follow-ups continue the same conversation
                  setSessionId(item.session_id);
                  try {
                    const turns = await api.sessionHistory(item.session_id);
                    const msgs: Message[] = [];
                    for (const turn of turns) {
                      msgs.push({ role: "user", content: turn.question });
                      msgs.push({ role: "assistant", content: turn.answer, cited_papers: turn.cited_papers });
                    }
                    setMessages(msgs);
                  } catch {
                    // Fallback: show just this turn
                    setMessages([
                      { role: "user", content: item.question },
                      { role: "assistant", content: item.answer, cited_papers: item.cited_papers },
                    ]);
                  }
                } else {
                  // Legacy conversation without session_id
                  setSessionId(null);
                  setMessages([
                    { role: "user", content: item.question },
                    { role: "assistant", content: item.answer, cited_papers: item.cited_papers },
                  ]);
                }
              }}
            >
              {item.question}
            </button>
            <button className="icon-button danger" type="button" title="删除" onClick={() => deleteConversation(item)}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </aside>
      <div className="chat-main">
        <div className="message-list" ref={messageListRef}>
          {messages.length === 0 && (
            <div className="empty-state">
              向论文库提问，AI 会基于所有论文的结构化信息回答，支持跨论文对比和综述。
            </div>
          )}
          {messages.map((message, index) => {
            const isStreamingMsg = busy && index === messages.length - 1 && message.role === "assistant";
            return (
            <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
              {isStreamingMsg ? (
                <pre className="streaming-content">{message.content}</pre>
              ) : (
                <div className="md-content"><ReactMarkdown>{message.content}</ReactMarkdown></div>
              )}
              {message.cited_papers && message.cited_papers.length > 0 && (
                <div className="citation-list">
                  {message.cited_papers.map((paper, ci) => (
                    <Link
                      className="citation-link"
                      to={`/papers/${paper.id}`}
                      key={`${paper.id}-${ci}`}
                    >
                      <span>[{ci + 1}] {paper.title}</span>
                      <ExternalLink size={14} />
                    </Link>
                  ))}
                </div>
              )}
            </article>
          );
          })}
          {busy && messages[messages.length - 1]?.role === "assistant" && !messages[messages.length - 1]?.content && (
            <div className="message assistant">思考中...</div>
          )}
        </div>
        <form className="chat-input" onSubmit={submit}>
          <input
            ref={inputRef}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="输入你的研究问题，例如：这些论文中有哪些使用了 Transformer？"
            onFocus={() => {
              // ensure input is visible when focused
              const el = messageListRef.current;
              if (el) requestAnimationFrame(() => el.scrollTo({ top: el.scrollHeight }));
            }}
          />
          <button className="icon-button primary" type="submit" title="发送" disabled={busy}>
            <Send size={18} />
          </button>
        </form>
      </div>
    </section>
  );
}
