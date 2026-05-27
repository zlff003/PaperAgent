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

  useEffect(() => {
    void api.history().then(setHistory);
  }, []);

  async function refreshHistory() {
    setHistory(await api.history());
  }

  function newConversation() {
    setMessages([]);
    setQuestion("");
    // keep server-side history untouched; focus input
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  async function deleteConversation(id: string) {
    try {
      await api.deleteConversation(id);
      // if currently showing that conversation, clear messages
      setMessages((cur) => {
        if (cur.length === 2 && cur[0].content && cur[1].content) {
          // best-effort: if the first message matches deleted id's question, we can't know id here
          // simply clear selection to avoid stale display
          return [];
        }
        return cur;
      });
      await refreshHistory();
    } catch (err) {
      // ignore for now
      console.error("删除会话失败", err);
    }
  }

  const messageListRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

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
    setMessages((items) => [...items, { role: "user", content: trimmed }]);
    setBusy(true);
    // optimistic add assistant placeholder
    setMessages((items) => [...items, { role: "assistant", content: "" }]);
    try {
      const resp = await api.askStream(trimmed);
      if (!resp || !resp.body) {
        // fallback to non-stream
        const full = await api.ask(trimmed);
        setMessages((items) => {
          // replace last assistant message
          const copy = [...items];
          copy[copy.length - 1] = { role: "assistant", content: full.answer, cited_papers: full.cited_papers };
          return copy;
        });
        setHistory(await api.history());
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
              // append token to last assistant message
              setMessages((items) => {
                const copy = [...items];
                const last = copy[copy.length - 1];
                copy[copy.length - 1] = { ...last, content: (last.content || "") + data };
                return copy;
              });
            } else if (ev === "node_done") {
              try {
                const obj = JSON.parse(data);
                const nodeName = obj.node;
                const output = obj.data || {};
                // If the graph saved/finished the formatted answer, attach it to the message
                if (output && (output.final_answer || output.answer || nodeName === "format_save")) {
                  const finalText = output.final_answer || output.answer || "";
                  const cited = output.cited_papers || output.cited || [];
                  setMessages((items) => {
                    const copy = [...items];
                    const last = copy[copy.length - 1] || { role: "assistant", content: "" };
                    copy[copy.length - 1] = {
                      ...last,
                      content: finalText || last.content,
                      cited_papers: cited.length ? cited : last.cited_papers,
                    };
                    return copy;
                  });
                  // refresh history to ensure saved conversation appears in history panel
                  void api.history().then(setHistory).catch(() => {});
                }
              } catch (e) {
                // ignore parse errors
              }
            } else if (ev === "error") {
              setMessages((items) => {
                const copy = [...items];
                copy[copy.length - 1] = { ...copy[copy.length - 1], content: (copy[copy.length - 1].content || "") + "\n[错误] " + data };
                return copy;
              });
            } else if (ev === "done") {
              // finalize: fetch history and cited papers from server
              const h = await api.history();
              setHistory(h);
            }
          }
        }
      }
    } catch (err) {
      console.error("stream error", err);
      // fallback: show error in assistant message
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
          <div style={{ display: "flex", gap: 8 }}>
            <button className="icon-button" type="button" onClick={refreshHistory} title="刷新">
              <ExternalLink size={14} />
            </button>
            <button className="icon-button primary" type="button" onClick={newConversation} title="新建对话">
              <Plus size={14} />
            </button>
          </div>
        </div>
        {history.length === 0 && <div className="empty-state compact">暂无历史对话</div>}
        {history.map((item) => (
          <div className="history-item-row" key={item.id}>
            <button
              className="history-item"
              type="button"
              onClick={() =>
                setMessages([
                  { role: "user", content: item.question },
                  { role: "assistant", content: item.answer, cited_papers: item.cited_papers },
                ])
              }
            >
              {item.question}
            </button>
            <button className="icon-button danger" type="button" title="删除" onClick={() => deleteConversation(item.id)}>
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
          {messages.map((message, index) => (
            <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
              <div className="md-content"><ReactMarkdown>{message.content}</ReactMarkdown></div>
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
          ))}
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
