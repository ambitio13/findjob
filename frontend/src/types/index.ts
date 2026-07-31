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
  created_at: string | null;
}

/** Sanitized single step within an agent run (mirrors AgentStepOut). */
export interface AgentStepOut {
  id: string;
  run_id: string;
  step_no: number;
  name: string;
  status: string;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string | null;
}

/** Run detail carrying the ordered step trail (mirrors AgentRunDetailOut). */
export interface AgentRunDetailOut {
  id: string;
  workflow_type: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
  created_at: string | null;
  steps: AgentStepOut[];
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

// --- Resume-aware JD analysis (mirrors backend schemas/jd_analysis.py) ---

export type JdAnalysisSeverity = "low" | "medium" | "high";

export type JdAnalysisEvidenceSource = "jd" | "resume" | "profile";

export type JdAnalysisRecommendation =
  | "strong_match"
  | "possible_match"
  | "weak_match"
  | "not_enough_info";

export interface JdAnalysisRiskPoint {
  title: string;
  detail: string;
  severity: JdAnalysisSeverity;
}

export interface JdAnalysisEvidence {
  claim: string;
  source: JdAnalysisEvidenceSource;
  quote?: string | null;
}

/**
 * Validated structured output produced by the model gateway. Mirrors
 * `JdAnalysisModelOutput` in backend/app/schemas/jd_analysis.py.
 */
export interface JdAnalysisModelOutput {
  role_summary: string;
  responsibilities: string[];
  hard_requirements: string[];
  nice_to_have_requirements: string[];
  resume_match_evidence: JdAnalysisEvidence[];
  risk_points: JdAnalysisRiskPoint[];
  salary_note: string;
  growth_note: string;
  stability_note: string;
  match_score: number | null;
  risk_score: number | null;
  skill_gaps: string[];
  interview_preparation: string[];
  recommendation: JdAnalysisRecommendation;
}

/** Body of POST /api/v1/jobs/{job_id}/analyses. */
export interface RunJdAnalysisRequest {
  resume_version_id: string;
}

/** Outbound view of a persisted JobAnalysis row. */
export interface JobAnalysisOut {
  id: string;
  job_id: string;
  agent_run_id?: string | null;
  match_score?: number | null;
  risk_score?: number | null;
  summary?: string | null;
  salary_analysis?: Record<string, unknown> | null;
  growth_analysis?: Record<string, unknown> | null;
  stability_analysis?: Record<string, unknown> | null;
  created_at?: string | null;
}

/** Outbound view of a persisted GeneratedArtifact row. */
export interface GeneratedArtifactOut {
  id: string;
  user_id?: string | null;
  job_id?: string | null;
  resume_version_id?: string | null;
  agent_run_id?: string | null;
  artifact_type: string;
  source_ids?: Record<string, unknown> | null;
  prompt_version?: string | null;
  model_name?: string | null;
  content: string;
  created_at?: string | null;
}

/** Response shape for POST /jobs/{job_id}/analyses (design.md §5.1). */
export interface RunJdAnalysisResponse {
  agent_run: AgentRunOut;
  analysis: JobAnalysisOut;
  artifact: GeneratedArtifactOut;
  structured: JdAnalysisModelOutput;
}

/**
 * Analysis + latest artifact + re-parsed structured output. Carries enough
 * data to reconstruct the UI from persisted rows (mirrors JobAnalysisDetailOut).
 */
export interface JobAnalysisDetailOut {
  analysis: JobAnalysisOut;
  artifact: GeneratedArtifactOut | null;
  structured: JdAnalysisModelOutput | null;
}

/** Paginated list of analyses for a job (design.md §5.2). */
export interface JobAnalysisListOut {
  meta: Record<string, unknown>;
  items: JobAnalysisDetailOut[];
}
