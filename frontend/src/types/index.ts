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

// --- Resumes ---

/** Full resume version, including extracted ``raw_text``. Detail view only. */
export interface ResumeVersionOut {
  id: string;
  version_no: number;
  parsed_facts: Record<string, unknown> | null;
  raw_text: string | null;
  created_at: string | null;
}

/** Version summary without ``raw_text`` (keeps list payloads small). */
export interface ResumeVersionListItem {
  id: string;
  version_no: number;
  created_at: string | null;
  parser_status: string | null;
  parser_name: string | null;
}

/** Resume summary used in list responses. */
export interface ResumeOut {
  id: string;
  filename: string;
  mime_type: string | null;
  created_at: string | null;
  latest_version_no: number | null;
}

export interface ResumeListOut {
  meta: PaginatedMeta;
  items: ResumeOut[];
}

/** Resume detail, including the latest version's raw text. */
export interface ResumeDetailOut {
  id: string;
  filename: string;
  mime_type: string | null;
  storage_uri: string | null;
  created_at: string | null;
  latest_version: ResumeVersionOut | null;
}
