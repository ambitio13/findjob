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

// --- Current user profile & job-search preferences ---

export interface UserProfile {
  id: string;
  display_name: string;
  email: string | null;
  career_direction: string | null;
  base_location: string | null;
  preferred_locations: string[] | null;
  salary_min: number | null;
  salary_max: number | null;
  strengths: string[] | null;
  constraints: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

/**
 * Partial update payload. All fields optional; omitting a field leaves it
 * untouched, sending `null` clears the underlying nullable column.
 */
export interface UserProfileUpdate {
  display_name?: string;
  email?: string | null;
  career_direction?: string | null;
  base_location?: string | null;
  preferred_locations?: string[] | null;
  salary_min?: number | null;
  salary_max?: number | null;
  strengths?: string[] | null;
  constraints?: Record<string, unknown> | null;
}
