import { ArrowLeft, Download, RefreshCw, Star, Tag } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Paper, Tag as TagType } from "../types";

export function PaperDetailPage() {
  const { paperId } = useParams();
  const [paper, setPaper] = useState<Paper | null>(null);
  const [allTags, setAllTags] = useState<TagType[]>([]);
  const [editingTags, setEditingTags] = useState(false);
  const [newTag, setNewTag] = useState("");

  async function load() {
    if (!paperId) return;
    try {
      setPaper(await api.getPaper(paperId));
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    void load();
    api.listTags().then(setAllTags).catch(() => {});
  }, [paperId]);

  useEffect(() => {
    if (!paperId || !paper || !isProcessing(paper.parse_status)) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [paperId, paper]);

  if (!paper || !paperId) {
    return <div className="empty-state">正在加载论文...</div>;
  }

  return (
    <section className="page-stack">
      <div className="toolbar">
        <Link to="/papers" className="back-link">
          <ArrowLeft size={20} />
          <span>返回论文库</span>
        </Link>
        <div className="reader-actions">
          <button
            className={`icon-button ${paper.is_favorite ? "primary" : ""}`}
            type="button"
            title={paper.is_favorite ? "取消收藏" : "收藏"}
            onClick={async () => {
              await api.updatePaper(paper.id, { is_favorite: !paper.is_favorite });
              await load();
            }}
          >
            <Star size={16} />
          </button>
          <a className="download-link" href={api.paperDownloadUrl(paper.id)} download>
            <Download size={16} />
            <span>下载 PDF</span>
          </a>
          <button
            className="icon-button"
            type="button"
            title="重新提取"
            onClick={async () => {
              await api.reExtractPaper(paper.id);
              await load();
            }}
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {isProcessing(paper.parse_status) && (
        <div className="parse-progress">
          <div className="progress-row">
            <span>{paper.parse_step || "正在解析"}</span>
            <strong>{paper.parse_progress}%</strong>
          </div>
          <div className="progress-track">
            <div style={{ width: `${paper.parse_progress}%` }} />
          </div>
        </div>
      )}

      {paper.parse_status === "failed" && (
        <div className="error-line">提取失败：{paper.parse_error || "未知错误"}</div>
      )}

      <div className="paper-detail">
        {/* Header */}
        <div className="detail-header">
          <h1>{paper.title}</h1>
          <div className="detail-meta">
            <span className="meta-item">
              {paper.authors.length ? paper.authors.join(", ") : "未知作者"}
            </span>
            {paper.year && <span className="meta-item">{paper.year}</span>}
            {paper.domain && <span className="meta-badge">{paper.domain}</span>}
            {paper.page_count > 0 && <span className="meta-item">{paper.page_count} 页</span>}
          </div>
        </div>

        {/* Keywords */}
        {paper.keywords.length > 0 && (
          <div className="detail-section">
            <h3>关键词</h3>
            <div className="keyword-row">
              {paper.keywords.map((kw) => (
                <span className="keyword-tag" key={kw}>{kw}</span>
              ))}
            </div>
          </div>
        )}

        {/* Tags */}
        <div className="detail-section">
          <div className="detail-section-header">
            <h3><Tag size={16} /> 标签</h3>
            <button
              className="link-button"
              type="button"
              onClick={() => setEditingTags(!editingTags)}
            >
              {editingTags ? "完成" : "编辑标签"}
            </button>
          </div>
          {editingTags && (
            <div className="tag-editor">
              <input
                className="tag-input"
                value={newTag}
                onChange={(e) => setNewTag(e.target.value)}
                placeholder="输入标签名，回车添加"
                onKeyDown={async (e) => {
                  if (e.key === "Enter" && newTag.trim()) {
                    const updated = [...new Set([...(paper.tags || []), newTag.trim()])];
                    await api.updatePaper(paper.id, { tags: updated });
                    setNewTag("");
                    await load();
                  }
                }}
              />
              <div className="tag-suggestions">
                {allTags
                  .filter((t) => !(paper.tags || []).includes(t.name))
                  .map((t) => (
                    <button
                      key={t.id}
                      className="tag-suggest"
                      type="button"
                      onClick={async () => {
                        const updated = [...new Set([...(paper.tags || []), t.name])];
                        await api.updatePaper(paper.id, { tags: updated });
                        await load();
                      }}
                    >
                      + {t.name}
                    </button>
                  ))}
              </div>
            </div>
          )}
          <div className="tag-row">
            {(paper.tags || []).length === 0 && (
              <span className="muted">暂无标签</span>
            )}
            {(paper.tags || []).map((t) => (
              <span className="user-tag removable" key={t}>
                {t}
                {editingTags && (
                  <button
                    className="tag-remove"
                    type="button"
                    onClick={async () => {
                      const updated = (paper.tags || []).filter((x) => x !== t);
                      await api.updatePaper(paper.id, { tags: updated });
                      await load();
                    }}
                  >
                    x
                  </button>
                )}
              </span>
            ))}
          </div>
        </div>

        {/* Abstract */}
        <div className="detail-section">
          <div className="tab-row">
            <h3>摘要</h3>
          </div>
          {paper.abstract_zh && (
            <div className="detail-card">
              <span className="card-label">中文概要</span>
              <p>{paper.abstract_zh}</p>
            </div>
          )}
          {paper.abstract && (
            <div className="detail-card">
              <span className="card-label">原文摘要</span>
              <p>{paper.abstract}</p>
            </div>
          )}
          {!paper.abstract && !paper.abstract_zh && (
            <p className="muted">未提取到摘要</p>
          )}
        </div>

        {/* Contributions */}
        <div className="detail-section">
          <h3>主要贡献</h3>
          {paper.contributions ? (
            <div className="detail-card"><p>{paper.contributions}</p></div>
          ) : <p className="muted">未提取</p>}
        </div>

        {/* Methods */}
        <div className="detail-section">
          <h3>方法 / 模型</h3>
          {paper.methods ? (
            <div className="detail-card"><p>{paper.methods}</p></div>
          ) : <p className="muted">未提取</p>}
        </div>

        {/* Results */}
        <div className="detail-section">
          <h3>实验与结果</h3>
          {paper.results ? (
            <div className="detail-card"><p>{paper.results}</p></div>
          ) : <p className="muted">未提取</p>}
        </div>

        {/* Limitations */}
        <div className="detail-section">
          <h3>局限性</h3>
          {paper.limitations ? (
            <div className="detail-card"><p>{paper.limitations}</p></div>
          ) : <p className="muted">未提取</p>}
        </div>

        {/* Conclusion */}
        <div className="detail-section">
          <h3>结论</h3>
          {paper.conclusion ? (
            <div className="detail-card"><p>{paper.conclusion}</p></div>
          ) : <p className="muted">未提取</p>}
        </div>
      </div>
    </section>
  );
}

function isProcessing(status: string) {
  return ["queued", "extracting", "analyzing_basic", "analyzing_deep", "indexing"].includes(status);
}
