// Centralized API types mirroring backend schemas.

export interface HealthResponse {
  status: string;
  app: string;
  env: string;
  version: string;
  db: string | null;
  redis: string | null;
}

export interface PaginatedMeta {
  page: number;
  page_size: number;
  total: number;
}

export interface JobOut {
  id: string;
  platform: string;
  company: string;
  title: string;
  location: string | null;
  salary_range: string | null;
  direction: string | null;
  jd_raw: string;
  created_at: string | null;
}

export interface JobListOut {
  meta: PaginatedMeta;
  items: JobOut[];
}

export interface JobCreate {
  company: string;
  title: string;
  location?: string | null;
  salary_range?: string | null;
  direction?: string | null;
  jd_raw: string;
  platform?: string;
}

export interface AgentRunOut {
  id: string;
  workflow_type: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
}

export interface ManualJdAnalysisDemoResponse {
  agent_run: AgentRunOut;
  artifact_id: string;
  artifact_type: string;
  content: string;
}
