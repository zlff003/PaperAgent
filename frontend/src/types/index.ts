export type Paper = {
  id: string;
  title: string;
  authors: string[];
  year: number | null;
  abstract: string | null;
  abstract_zh: string | null;
  contributions: string | null;
  methods: string | null;
  results: string | null;
  limitations: string | null;
  conclusion: string | null;
  keywords: string[];
  domain: string | null;
  file_path: string;
  page_count: number;
  is_favorite: boolean;
  tags: string[];
  parse_status: string;
  parse_progress: number;
  parse_step: string | null;
  parse_error: string | null;
  parsed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type PaperUpdate = {
  domain?: string | null;
  is_favorite?: boolean | null;
  tags?: string[] | null;
};

export type PaperBrief = {
  id: string;
  title: string;
  authors: string[];
  year: number | null;
  snippet: string;
};

export type ParseStatus = {
  paper_id: string;
  status: string;
  progress: number;
  current_step: string | null;
  error: string | null;
};

export type SearchQuery = {
  query?: string | null;
  year_from?: number | null;
  year_to?: number | null;
  domain?: string | null;
  tags?: string[] | null;
  is_favorite?: boolean | null;
};

export type QAResponse = {
  answer: string;
  cited_papers: PaperBrief[];
  conversation_id: string;
};

export type Conversation = {
  id: string;
  question: string;
  answer: string;
  cited_papers: PaperBrief[];
  session_id?: string | null;
  turn_index?: number;
  created_at: string;
};

export type Tag = {
  id: string;
  name: string;
  paper_count?: number;
};
