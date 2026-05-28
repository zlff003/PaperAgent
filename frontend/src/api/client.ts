import type { Conversation, Paper, PaperBrief, PaperUpdate, ParseStatus, SearchQuery, Tag } from "../types";

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

  // Chat history
  history: () => request<Conversation[]>("/chat/history"),
  sessionHistory: (sessionId: string) => request<Conversation[]>(`/chat/history?session_id=${encodeURIComponent(sessionId)}`),
  deleteConversation: (id: string) => request<{ status: string }>(`/chat/history/${id}`, { method: "DELETE" }),
  deleteSession: (sessionId: string) => request<{ status: string; count: number }>(`/chat/history/session/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),

  // Chat (Supervisor mode)
  chat: (message: string, sessionId?: string) =>
    fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
    }),

  // System
  exportPapers: async () => {
    const res = await fetch(`${API_BASE}/export`, { method: "POST" });
    return res.text();
  },
};
