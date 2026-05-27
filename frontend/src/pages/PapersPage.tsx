import { Download, RefreshCw, Search, Star, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { UploadDropzone } from "../components/UploadDropzone";
import type { Paper, Tag as TagType } from "../types";

export function PapersPage() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [tags, setTags] = useState<TagType[]>([]);
  const [query, setQuery] = useState("");
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [domain, setDomain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [searchParams] = useSearchParams();

  const filterTags = searchParams.get("tags") || undefined;
  const filterFav = searchParams.get("favorite") === "true" ? true : undefined;

  async function load() {
    try {
      const data = await api.listPapers({
        tags: filterTags,
        is_favorite: filterFav,
      });
      setPapers(data);
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    void load();
    api.listTags().then(setTags).catch(() => {});
  }, [filterTags, filterFav]);

  useEffect(() => {
    if (!papers.some((p) =>
      ["queued", "extracting", "analyzing_basic", "analyzing_deep", "indexing"].includes(p.parse_status)
    )) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [papers]);

  const domains = useMemo(() => {
    const set = new Set(papers.map((p) => p.domain).filter(Boolean) as string[]);
    return [...set].sort();
  }, [papers]);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    let result = papers;
    if (q) {
      result = result.filter((p) =>
        [p.title, p.authors.join(" "), p.keywords.join(" "), String(p.year || "")]
          .join(" ").toLowerCase().includes(q)
      );
    }
    if (yearFrom) result = result.filter((p) => p.year && p.year >= Number(yearFrom));
    if (yearTo) result = result.filter((p) => p.year && p.year <= Number(yearTo));
    if (domain) result = result.filter((p) => p.domain === domain);
    return result;
  }, [papers, query, yearFrom, yearTo, domain]);

  return (
    <section className="page-stack">
      <div className="toolbar">
        <div>
          <h1>论文库</h1>
          <p>本地已保存 {papers.length} 篇论文</p>
        </div>
        <UploadDropzone
          onUpload={async (file) => {
            setError(null);
            try {
              await api.uploadPaper(file);
              await load();
            } catch (err) {
              setError(err instanceof Error ? err.message : "上传失败");
            }
          }}
        />
      </div>

      {error && <div className="error-line">{error}</div>}

      <div className="filter-bar">
        <label className="search-box">
          <Search size={18} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索标题、作者、关键词..."
          />
        </label>
        <input
          className="filter-input"
          type="number"
          value={yearFrom}
          onChange={(e) => setYearFrom(e.target.value)}
          placeholder="年份从"
        />
        <input
          className="filter-input"
          type="number"
          value={yearTo}
          onChange={(e) => setYearTo(e.target.value)}
          placeholder="年份到"
        />
        <select className="filter-input" value={domain} onChange={(e) => setDomain(e.target.value)}>
          <option value="">全部领域</option>
          {domains.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>

      {filterTags && (
        <div className="active-filter">
          <span>标签筛选: {filterTags}</span>
          <Link to="/papers" className="clear-filter">清除</Link>
        </div>
      )}

      <div className="paper-grid">
        {filtered.map((paper) => (
          <article className="paper-card" key={paper.id}>
            <Link to={`/papers/${paper.id}`}>
              <h2>{paper.title}</h2>
            </Link>
            <p>{paper.authors.length ? paper.authors.slice(0, 4).join(", ") : "未知作者"}</p>
            <div className="paper-meta">
              <span>{paper.year || "年份未知"}</span>
              <span>{paper.domain || "未分类"}</span>
              <span>{paper.page_count} 页</span>
            </div>
            <div className={`status-pill ${paper.parse_status}`}>
              <span>{statusText(paper.parse_status)}</span>
              <span>{paper.parse_progress}%</span>
            </div>
            {paper.keywords.length > 0 && (
              <div className="keyword-row">
                {paper.keywords.slice(0, 5).map((kw) => (
                  <span className="keyword-tag" key={kw}>{kw}</span>
                ))}
              </div>
            )}
            {paper.abstract_zh && <p className="abstract">{paper.abstract_zh}</p>}
            {paper.tags.length > 0 && (
              <div className="tag-row">
                {paper.tags.map((t) => <span className="user-tag" key={t}>{t}</span>)}
              </div>
            )}
            <div className="card-actions">
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
              <a className="icon-button" href={api.paperDownloadUrl(paper.id)} title="下载PDF" download>
                <Download size={16} />
              </a>
              {paper.parse_status === "failed" && (
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
              )}
              <button
                className="icon-button danger"
                type="button"
                title="删除论文"
                onClick={async () => {
                  if (!confirm("确认删除这篇论文？")) return;
                  await api.deletePaper(paper.id);
                  await load();
                }}
              >
                <Trash2 size={16} />
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function statusText(status: string) {
  const map: Record<string, string> = {
    queued: "排队中",
    extracting: "提取文本",
    analyzing_basic: "提取基础信息",
    analyzing_deep: "提取深度内容",
    indexing: "索引中",
    ready: "已完成",
    partial_ready: "部分完成",
    failed: "提取失败",
  };
  return map[status] ?? status;
}
