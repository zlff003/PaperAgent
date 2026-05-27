import type { Conversation, Paper, PaperBrief, PaperUpdate, ParseStatus, QAResponse, SearchQuery, Tag } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: options?.body instanceof FormData
      ? options.headers
      : { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || response.statusText);
  }
  return response.json() as Promise<T>;
}

export const api = {
  // Papers
  listPapers: (params?: {
    q?: string;
    year_from?: number;
    year_to?: number;
    domain?: string;
    tags?: string;
    is_favorite?: boolean;
  }) => {
    const searchParams = new URLSearchParams();
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null) searchParams.set(k, String(v));
      });
    }
    const qs = searchParams.toString();
    return request<Paper[]>(`/papers${qs ? `?${qs}` : ""}`);
  },

  getPaper: (id: string) => request<Paper>(`/papers/${id}`),

  uploadPaper: async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<Paper>("/papers/upload", { method: "POST", body });
  },

  updatePaper: (id: string, payload: PaperUpdate) =>
    request<Paper>(`/papers/${id}`, { method: "PUT", body: JSON.stringify(payload) }),

  deletePaper: (id: string) =>
    request<{ status: string }>(`/papers/${id}`, { method: "DELETE" }),

  parseStatus: (id: string) => request<ParseStatus>(`/papers/${id}/parse-status`),

  reExtractPaper: (id: string) =>
    request<ParseStatus>(`/papers/${id}/re-extract`, { method: "POST" }),

  paperDownloadUrl: (id: string) => `${API_BASE}/papers/${id}/download`,

  // Search
  search: (payload: SearchQuery) =>
    request<PaperBrief[]>("/search", { method: "POST", body: JSON.stringify(payload) }),

  semanticSearch: (q: string) =>
    request<PaperBrief[]>(`/search/semantic?q=${encodeURIComponent(q)}`),

  // Tags
  listTags: () => request<Tag[]>("/tags"),

  createTag: (name: string) =>
    request<Tag>("/tags", { method: "POST", body: JSON.stringify({ name }) }),

  deleteTag: (id: string) =>
    request<{ status: string }>(`/tags/${id}`, { method: "DELETE" }),

  // QA
  ask: (question: string, top_k = 6) =>
    request<QAResponse>("/qa/ask", { method: "POST", body: JSON.stringify({ question, top_k }) }),

  history: () => request<Conversation[]>("/qa/history"),
  deleteConversation: (id: string) => request<{ status: string }>(`/qa/history/${id}`, { method: "DELETE" }),
  askStream: (question: string, top_k = 6) =>
    fetch(`${API_BASE}/qa/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, top_k }),
    }),

  // System
  exportPapers: async () => {
    const res = await fetch(`${API_BASE}/export`, { method: "POST" });
    return res.text();
  },
};
