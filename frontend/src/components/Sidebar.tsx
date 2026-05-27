import { BookOpen, MessageSquare, ScrollText, Star, Tag } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { Tag as TagType } from "../types";

export function Sidebar() {
  const [tags, setTags] = useState<TagType[]>([]);
  const location = useLocation();
  const searchParams = new URLSearchParams(location.search);

  useEffect(() => {
    api.listTags().then(setTags).catch(() => {});
  }, []);

  return (
    <aside className="sidebar">
      <div className="brand">
        <ScrollText size={22} />
        <span>PaperAgent</span>
      </div>
      <nav className="nav-list">
        <NavLink
          to="/papers"
          className={() => `nav-item ${location.pathname === "/papers" && !searchParams.has("favorite") && !searchParams.has("tags") ? "active" : ""}`}
        >
          <BookOpen size={18} />
          <span>论文库</span>
        </NavLink>
        <NavLink
          to="/papers?favorite=true"
          className={() => `nav-item ${location.pathname === "/papers" && searchParams.get("favorite") === "true" ? "active" : ""}`}
        >
          <Star size={18} />
          <span>已收藏</span>
        </NavLink>
        <NavLink to="/chat" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}>
          <MessageSquare size={18} />
          <span>AI 对话</span>
        </NavLink>
      </nav>
      {tags.length > 0 && (
        <div className="nav-section">
          <div className="nav-section-title">
            <Tag size={14} />
            <span>标签</span>
          </div>
          <div className="tag-cloud">
            {tags.map((tag) => (
              <NavLink
                key={tag.id}
                to={`/papers?tags=${encodeURIComponent(tag.name)}`}
                className="tag-link"
              >
                {tag.name}
                {tag.paper_count ? <span className="tag-count">{tag.paper_count}</span> : null}
              </NavLink>
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}
